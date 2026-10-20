from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError

import pandas as pd
import pytest

from evidence.daily_acquisition_checkpoint import (
    BUDGET_SCHEMA,
    DECISION_SCHEMA,
    LEDGER_SCHEMA,
    MAX_ATTEMPTS_PER_URL,
    MAX_ELAPSED_SECONDS,
    REFERENCE_CLOSING,
    REFERENCE_OPENING,
    CheckpointError,
    SharedBudgetExhausted,
    SharedUrlBudget,
    aggregate_seed_counts,
    assess_checkpoint,
    missing_session_targets,
    run_bounded_checkpoint,
)
from ingest.daily_cash import (
    HttpResponse,
    collect_daily_cash,
    nsearchives_sec_bhavdata_full_url,
    verify_retained_session,
)
from ingest.nse_bhavcopy import nse_sec_bhavdata_full_url


ROUTE = "nsearchives_full_bhavdata"
START = date(2026, 1, 1)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)


def _dates(count: int, start: date = START) -> list[str]:
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def _day(value: str) -> date:
    return date.fromisoformat(value)


def _body(session: date) -> bytes:
    row = {
        "SYMBOL": "AAA",
        "SERIES": "EQ",
        "DATE1": session.strftime("%d-%b-%Y"),
        "PREV_CLOSE": "98",
        "OPEN_PRICE": "100",
        "HIGH_PRICE": "105",
        "LOW_PRICE": "99",
        "LAST_PRICE": "103",
        "CLOSE_PRICE": "104",
        "AVG_PRICE": "102",
        "TTL_TRD_QNTY": "1200",
        "TURNOVER_LACS": "12.345",
        "NO_OF_TRADES": "42",
        "DELIV_QTY": "600",
        "DELIV_PER": "50",
    }
    return pd.DataFrame([row]).to_csv(index=False).encode()


def _response(url: str, session: date) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={
            "Content-Type": "text/csv",
            "Last-Modified": session.strftime("%a, %d %b %Y 12:00:00 GMT"),
        },
        body=_body(session),
        final_url=url,
        retrieved_at="2026-12-31T00:00:00+00:00",
    )


def _date_from_url(url: str) -> date:
    name = url.rsplit("/", 1)[-1]
    stamp = name[len("sec_bhavdata_full_") : -len(".csv")]
    return datetime.strptime(stamp, "%d%m%Y").date()


class FakeTransport:
    def __init__(self, *, fail: bool = False, fail_dates: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self.timeouts: list[tuple[float, float]] = []
        self.deadlines: list[datetime] = []
        self.fail = fail
        self.fail_dates = fail_dates

    def __call__(
        self,
        url: str,
        connect_timeout: float,
        read_timeout: float,
        deadline: datetime | None = None,
    ) -> HttpResponse:
        self.calls.append(url)
        self.timeouts.append((connect_timeout, read_timeout))
        if deadline is not None:
            self.deadlines.append(deadline)
        session = _date_from_url(url)
        if self.fail or session.isoformat() in self.fail_dates:
            raise URLError("synthetic transport failure")
        return _response(url, session)


class ForbiddenTransport:
    def __call__(
        self,
        url: str,
        connect_timeout: float,
        read_timeout: float,
        deadline: datetime | None = None,
    ) -> HttpResponse:
        raise AssertionError(f"transport must not be called: {url}")


def _report(
    dates: list[str],
    *,
    qualified: bool = True,
    expected: object = None,
    qualified_count: object = ...,
    blockers: list[dict] | None = None,
) -> dict:
    expected_count = len(dates) if expected is None else expected
    resolved_count = (
        (expected_count if qualified else None)
        if qualified_count is ...
        else qualified_count
    )
    return {
        "schema": LEDGER_SCHEMA,
        "as_of": "2026-12-31T00:00:00+00:00",
        "start": dates[0] if dates else START.isoformat(),
        "latest_completed_session": dates[-1] if (dates and qualified) else None,
        "denominator": {
            "expected_session_count": expected_count,
            "qualified_expected_denominator": resolved_count,
            "denominator_qualified": qualified,
            "historical_checkpoint_blocked": not qualified,
        },
        "blockers": blockers if blockers is not None else (
            [] if qualified else [{"kind": "calendar_review_unresolved", "scope": "review"}]
        ),
    }


def _seed_root(root: Path, dates: list[str], clock: Clock | None = None) -> Clock:
    clock = clock or Clock()
    for value in dates:
        session = _day(value)
        result = collect_daily_cash(
            session,
            root,
            transport=lambda url, _c, _r, session=session: _response(url, session),
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.status == "accepted", result.reason
    return clock


def _run(
    report: dict,
    *,
    expected: list[str],
    output_root: Path,
    shared_budget_path: Path,
    transport=FakeTransport(),
    existing: tuple[Path, ...] = (),
    targets: list[dict] | None = None,
    clock: Clock | None = None,
    **kwargs,
) -> dict:
    clock = clock or Clock()
    if clock.t != datetime(2026, 1, 1, tzinfo=timezone.utc):
        clock.t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return run_bounded_checkpoint(
        report,
        output_root=output_root,
        transport=transport,
        route=ROUTE,
        expected_session_dates=expected,
        existing_cash_roots=existing,
        shared_budget_path=shared_budget_path,
        targets=targets,
        now=clock.now,
        sleep=clock.sleep,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def test_unqualified_denominator_yields_precise_not_run() -> None:
    decision = assess_checkpoint(_report(_dates(3), qualified=False))
    assert decision["status"] == "NOT_RUN"
    assert decision["schema"] == DECISION_SCHEMA
    assert decision["denominator_qualified"] is False
    assert decision["denominator_blocker_count"] == 1
    assert decision["acquisition"]["started"] is False
    assert decision["acquisition"]["request_count"] == 0
    assert decision["acquisition"]["recovered_session_denominator"] is None
    assert decision["acquisition"]["max_elapsed_seconds"] == MAX_ELAPSED_SECONDS
    assert decision["acquisition"]["max_attempts_per_url"] == MAX_ATTEMPTS_PER_URL
    assert decision["reference_convention"]["selected"] is None
    assert decision["strategy_outcomes_inspected"] is False


def test_qualified_denominator_is_ready_but_not_executed() -> None:
    decision = assess_checkpoint(_report(_dates(3), qualified=True))
    assert decision["status"] == "READY"
    assert decision["denominator_qualified"] is True
    assert decision["acquisition"]["started"] is False
    assert decision["reference_convention"]["selected"] is None


def test_wrong_schema_fails_closed() -> None:
    with pytest.raises(CheckpointError):
        assess_checkpoint({"schema": "something-else"})


def test_missing_denominator_fails_closed() -> None:
    with pytest.raises(CheckpointError):
        assess_checkpoint({"schema": LEDGER_SCHEMA, "blockers": []})


def test_legacy_blockers_do_not_gate_but_are_reported() -> None:
    decision = assess_checkpoint(_report(_dates(3), qualified=False, blockers=[
        {"kind": "legacy_expected_date_duplicate", "scope": "legacy"},
        {"kind": "calendar_review_unresolved", "scope": "review"},
    ]))
    assert decision["denominator_blocker_count"] == 1
    assert decision["qualification_blocker_count"] == 1


def test_qualified_with_blocking_scope_fails_closed() -> None:
    with pytest.raises(CheckpointError, match="qualified denominator while calendar"):
        assess_checkpoint(_report(_dates(3), qualified=True, blockers=[
            {"kind": "calendar_review_unresolved", "scope": "review"},
        ]))


def test_qualified_count_mismatch_fails_closed() -> None:
    with pytest.raises(CheckpointError, match="does not match"):
        assess_checkpoint(_report(_dates(3), qualified=True, expected=99, qualified_count=98))


def test_unqualified_with_published_count_fails_closed() -> None:
    with pytest.raises(CheckpointError, match="still publishes a qualified count"):
        assess_checkpoint(_report(_dates(3), qualified=False, qualified_count=3))


def test_unknown_blocker_scope_fails_closed() -> None:
    with pytest.raises(CheckpointError, match="unknown or malformed scope"):
        assess_checkpoint(_report(_dates(3), qualified=False, blockers=[{"kind": "mystery", "scope": "mystery"}]))


def test_malformed_blocker_scope_fails_closed() -> None:
    with pytest.raises(CheckpointError, match="unknown or malformed scope"):
        assess_checkpoint(_report(_dates(3), qualified=False, blockers=[{"kind": "review"}]))


def test_boolean_qualified_count_cannot_masquerade_as_int() -> None:
    report = _report(_dates(1), qualified=True)
    report["denominator"]["qualified_expected_denominator"] = True
    with pytest.raises(CheckpointError, match="non-boolean integer"):
        assess_checkpoint(report)


def test_contradictory_split_and_main_blockers_fail_closed() -> None:
    report = _report(_dates(3), qualified=False)
    report["blockers"] = [{"kind": "review", "scope": "review"}, {"kind": "legacy", "scope": "legacy"}]
    report["denominator_blockers"] = [{"kind": "review", "scope": "review"}]
    report["qualification_blockers"] = []
    with pytest.raises(CheckpointError, match="contradict"):
        assess_checkpoint(report)


def test_split_blockers_without_main_list_fail_closed() -> None:
    report = _report(_dates(3), qualified=False)
    report.pop("blockers")
    report["denominator_blockers"] = [{"kind": "review", "scope": "review"}]
    report["qualification_blockers"] = []
    with pytest.raises(CheckpointError, match="no main blockers list"):
        assess_checkpoint(report)


def test_split_blockers_with_wrong_partition_fail_closed() -> None:
    report = _report(_dates(3), qualified=False)
    report["blockers"] = [{"kind": "legacy", "scope": "legacy"}]
    report["denominator_blockers"] = [{"kind": "legacy", "scope": "legacy"}]
    report["qualification_blockers"] = []
    with pytest.raises(CheckpointError, match="non-gating scope"):
        assess_checkpoint(report)


def test_decision_never_names_a_strategy_outcome() -> None:
    decision = assess_checkpoint(_report(_dates(3), qualified=False))
    serialized = json.dumps(decision, sort_keys=True)
    for forbidden in ("sharpe", "return", "pnl", "profit", "win_rate", "equity_curve"):
        assert forbidden not in serialized


# ---------------------------------------------------------------------------
# Shared budget
# ---------------------------------------------------------------------------


def _budget(path: Path, clock: Clock, **kwargs) -> SharedUrlBudget:
    return SharedUrlBudget(
        path,
        deadline=clock.now() + timedelta(seconds=kwargs.pop("max_elapsed_seconds", MAX_ELAPSED_SECONDS)),
        run_id=kwargs.pop("run_id", "run"),
        **kwargs,
    )


def test_shared_budget_is_per_url_and_durable(tmp_path: Path) -> None:
    clock = Clock()
    budget = _budget(tmp_path / "budget.jsonl", clock)
    calls: list[str] = []
    guarded = budget.guard(
        lambda url, _c, _r, _deadline=None: calls.append(url) or object(),
        now=clock.now,
        sleep=clock.sleep,
    )
    for _ in range(3):
        guarded("https://nsearchives.nseindia.com/content/cm/example.zip", 10, 30)
    assert budget.attempts("https://nsearchives.nseindia.com/content/cm/example.zip") == 3
    assert budget.attempts("https://archives.nseindia.com/other.zip") == 0
    assert len(calls) == 3
    with pytest.raises(SharedBudgetExhausted):
        guarded("https://nsearchives.nseindia.com/content/cm/example.zip", 10, 30)


def test_seed_counts_are_imported_once(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "budget.jsonl"
    first = _budget(path, clock, seed_counts={"u": 3})
    assert first.attempts("u") == 3
    with pytest.raises(SharedBudgetExhausted):
        first.guard(lambda *_a: object(), now=clock.now, sleep=clock.sleep)("u", 10, 30)
    text = path.read_text(encoding="utf-8")
    assert text.count('"record": "seed"') == 1
    # A later budget with a different seed cannot re-import or forget prior exhaustion.
    second = _budget(path, clock, seed_counts={"u": 0})
    assert second.attempts("u") == 3
    assert path.read_text(encoding="utf-8").count('"record": "seed"') == 1


def test_corrupt_shared_budget_journal_fails_closed(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "budget.jsonl"
    valid = {
        "schema": BUDGET_SCHEMA,
        "record": "reservation",
        "url": "u",
        "attempt_number": 1,
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(valid) + '\n{"truncated": ', encoding="utf-8")
    budget = _budget(path, clock)
    with pytest.raises(CheckpointError, match="corrupt"):
        budget.attempts("u")


def test_non_object_shared_budget_entry_fails_closed(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "budget.jsonl"
    path.write_text('["not", "an", "object"]\n', encoding="utf-8")
    budget = _budget(path, clock)
    with pytest.raises(CheckpointError, match="not an object"):
        budget.attempts("u")


def test_shared_budget_schema_and_record_kinds_are_validated(tmp_path: Path) -> None:
    clock = Clock()
    bad_records = [
        {"record": "reservation", "url": "u"},
        {"schema": BUDGET_SCHEMA, "record": "mystery"},
        {
            "schema": BUDGET_SCHEMA,
            "record": "seed",
            "counts": {"u": True},
            "imported_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "schema": BUDGET_SCHEMA,
            "record": "seed",
            "counts": {"u": -1},
            "imported_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "schema": BUDGET_SCHEMA,
            "record": "reservation",
            "url": "u",
            "attempt_number": 1,
            "started_at": "not-a-time",
        },
        {
            "schema": BUDGET_SCHEMA,
            "record": "outcome",
            "url": "u",
            "received": "yes",
            "finished_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "schema": BUDGET_SCHEMA,
            "record": "reservation",
            "url": "u",
            "attempt_number": 1,
            "started_at": "2026-01-01T00:00:00",
        },
    ]
    for index, record in enumerate(bad_records):
        path = tmp_path / f"bad-{index}.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with pytest.raises(CheckpointError):
            _budget(path, clock).attempts("u")


def test_exhausted_urls_includes_seeded_only_urls(tmp_path: Path) -> None:
    clock = Clock()
    url = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_01012026.csv"
    budget = _budget(tmp_path / "budget.jsonl", clock, seed_counts={url: MAX_ATTEMPTS_PER_URL})
    assert budget.attempts(url) == MAX_ATTEMPTS_PER_URL
    assert budget.exhausted_urls() == [url]


def test_shared_run_start_cannot_reset_across_fresh_roots(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "budget.jsonl"
    first = _budget(path, clock, run_id="shared")
    start = first.resolve_run_start(clock.now)

    later = Clock()
    later.t = datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
    second = _budget(path, later, run_id="shared")
    assert second.resolve_run_start(later.now) == start

    other = _budget(path, later, run_id="different")
    assert other.resolve_run_start(later.now) == later.now()


# ---------------------------------------------------------------------------
# Runner integration (real collect_daily_cash + fake HttpResponse)
# ---------------------------------------------------------------------------


def test_preexisting_valid_bytes_reuse_zero_network_and_full_coverage(tmp_path: Path) -> None:
    dates = _dates(5)
    existing = tmp_path / "cash-evidence-v1"
    _seed_root(existing, dates)
    forbidden = ForbiddenTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=forbidden,
        existing=(existing,),
    )
    assert run["status"] == "COMPLETE"
    assert run["recovered_session_denominator"] == 5
    assert run["recovery_ratio"] == 1.0
    assert run["reference_convention"]["selected"] == REFERENCE_OPENING
    assert run["acquisition"]["request_count"] == 0
    assert [item["status"] for item in run["invocation"]["targets"]] == ["reused"] * 5


def test_ninety_eight_retained_plus_two_new_reaches_full_coverage(tmp_path: Path) -> None:
    dates = _dates(100)
    existing = tmp_path / "cash-evidence-v1"
    _seed_root(existing, dates[:98])
    transport = FakeTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        existing=(existing,),
    )
    assert run["status"] == "COMPLETE"
    assert run["recovered_session_denominator"] == 100
    assert run["acquisition"]["request_count"] == 2
    assert len(transport.calls) == 2
    assert run["reference_convention"]["selected"] == REFERENCE_OPENING


def test_below_threshold_selects_closing_convention(tmp_path: Path) -> None:
    dates = _dates(100)
    missing = frozenset(dates[97:])
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=FakeTransport(fail_dates=missing),
    )
    assert run["recovered_session_denominator"] == 97
    assert run["recovery_ratio"] == 0.97
    assert run["reference_convention"]["selected"] == REFERENCE_CLOSING
    assert run["reference_convention"]["admits_missing_sessions"] is False


def test_duplicate_targets_cannot_inflate_coverage(tmp_path: Path) -> None:
    dates = _dates(2)
    with pytest.raises(CheckpointError, match="duplicate target"):
        _run(
            _report(dates),
            expected=dates,
            output_root=tmp_path / "run",
            shared_budget_path=tmp_path / "budget.jsonl",
            targets=[{"session_date": dates[0], "route": ROUTE}, {"session_date": dates[0], "route": ROUTE}],
        )


def test_target_not_expected_out_of_range_or_invalid_rejected(tmp_path: Path) -> None:
    dates = _dates(3)
    for bad in ("2099-01-01", "not-a-date", dates[0] + "T00:00:00"):
        with pytest.raises(CheckpointError):
            _run(
                _report(dates),
                expected=dates,
                output_root=tmp_path / "run",
                shared_budget_path=tmp_path / "budget.jsonl",
                targets=[{"session_date": bad, "route": ROUTE}],
            )
    with pytest.raises(CheckpointError, match="not an expected session"):
        _run(
            _report(dates),
            expected=dates,
            output_root=tmp_path / "other-run",
            shared_budget_path=tmp_path / "budget.jsonl",
            targets=[{"session_date": _dates(4)[3], "route": ROUTE}],
        )


def test_report_session_count_mismatch_fails_before_network(tmp_path: Path) -> None:
    dates = _dates(2)
    transport = FakeTransport()
    with pytest.raises(CheckpointError, match="does not match"):
        _run(
            _report(dates, expected=3),
            expected=dates,
            output_root=tmp_path / "run",
            shared_budget_path=tmp_path / "budget.jsonl",
            transport=transport,
        )
    assert transport.calls == []
    assert not (tmp_path / "budget.jsonl").exists()


def test_runner_refuses_unqualified_without_touching_transport(tmp_path: Path) -> None:
    dates = _dates(3)
    run = _run(
        _report(dates, qualified=False),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=FakeTransport(),
    )
    assert run["status"] == "NOT_RUN"
    assert run["denominator_qualified"] is False
    assert not (tmp_path / "run").exists()
    assert not (tmp_path / "budget.jsonl").exists()


def test_integrated_retries_are_not_doubled_by_guard(tmp_path: Path) -> None:
    dates = _dates(1)
    clock = Clock()
    transport = FakeTransport(fail=True)
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        clock=clock,
    )
    assert len(transport.calls) == MAX_ATTEMPTS_PER_URL
    assert clock.sleeps == [5.0, 30.0]
    assert run["acquisition"]["request_count"] == 3
    assert run["acquisition"]["failures"] == 3


class HttpErrorTransport:
    def __init__(self, status: int = 500) -> None:
        self.status = status
        self.calls: list[str] = []

    def __call__(
        self,
        url: str,
        connect_timeout: float,
        read_timeout: float,
        deadline: datetime | None = None,
    ) -> HttpResponse:
        self.calls.append(url)
        return HttpResponse(
            status=self.status,
            headers={"Content-Type": "text/csv"},
            body=b"server error",
            final_url=url,
            retrieved_at="2026-12-31T00:00:00+00:00",
        )


def test_rejected_http_responses_are_counted_from_receipts(tmp_path: Path) -> None:
    dates = _dates(1)
    transport = HttpErrorTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
    )
    assert len(transport.calls) == MAX_ATTEMPTS_PER_URL
    assert run["acquisition"]["rejected_responses"] == 3
    assert run["acquisition"]["failures"] == 0


def test_transport_receives_configured_timeouts(tmp_path: Path) -> None:
    dates = _dates(1)
    transport = FakeTransport()
    _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
    )
    assert transport.timeouts == [(10.0, 30.0)]


def test_insufficient_remaining_allowance_makes_no_http(tmp_path: Path) -> None:
    dates = _dates(1)
    transport = FakeTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        max_elapsed_seconds=5,
    )
    assert transport.calls == []
    assert run["status"] == "STOPPED"
    assert run["invocation"]["targets"][-1]["status"] == "deadline_stop"
    assert run["acquisition"]["clamped_requests"] == 0


def test_configured_stricter_timeouts_pass_through_unchanged(tmp_path: Path) -> None:
    dates = _dates(1)
    transport = FakeTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        connect_timeout_seconds=1.5,
        read_timeout_seconds=2.5,
        max_elapsed_seconds=120,
    )
    assert run["status"] == "COMPLETE"
    assert transport.timeouts == [(1.5, 2.5)]
    assert run["acquisition"]["connect_timeout_seconds"] == 1.5
    assert run["acquisition"]["read_timeout_seconds"] == 2.5
    assert run["acquisition"]["clamped_requests"] == 0


def test_effective_deadline_is_forwarded_to_transport(tmp_path: Path) -> None:
    dates = _dates(1)
    transport = FakeTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        max_elapsed_seconds=120,
    )
    assert run["status"] == "COMPLETE"
    assert transport.deadlines == [datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)]


def test_config_cannot_loosen_plan_bounds(tmp_path: Path) -> None:
    dates = _dates(1)
    overrides = [
        {"max_attempts_per_url": MAX_ATTEMPTS_PER_URL + 1},
        {"max_elapsed_seconds": MAX_ELAPSED_SECONDS + 1},
        {"min_start_spacing_seconds": 0.4},
        {"connect_timeout_seconds": 11.0},
        {"read_timeout_seconds": 31.0},
    ]
    for index, override in enumerate(overrides):
        with pytest.raises(CheckpointError):
            _run(
                _report(dates),
                expected=dates,
                output_root=tmp_path / f"run-{index}",
                shared_budget_path=tmp_path / f"budget-{index}.jsonl",
                transport=ForbiddenTransport(),
                **override,
            )


def test_crossing_deadline_in_retry_wait_makes_no_extra_http(tmp_path: Path) -> None:
    dates = _dates(1)
    clock = Clock()
    transport = FakeTransport(fail=True)
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        clock=clock,
        max_elapsed_seconds=4,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
    )
    assert len(transport.calls) == 1
    assert run["status"] == "STOPPED"
    assert run["invocation"]["targets"][-1]["status"] == "deadline_stop"


def test_omitted_seed_resume_still_exhausted_same_budget(tmp_path: Path) -> None:
    dates = _dates(1)
    budget = tmp_path / "budget.jsonl"
    first_transport = FakeTransport(fail=True)
    first = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run-a",
        shared_budget_path=budget,
        transport=first_transport,
    )
    assert first["acquisition"]["request_count"] == 3
    assert len(first_transport.calls) == 3

    # A fresh output root, no seed argument, must still see the durable exhaustion.
    second_transport = ForbiddenTransport()
    second = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run-b",
        shared_budget_path=budget,
        transport=second_transport,
    )
    assert second["acquisition"]["request_count"] == 3
    assert [item["status"] for item in second["invocation"]["targets"]] == ["budget_exhausted"]


def test_two_run_roots_cannot_bypass_shared_budget(tmp_path: Path) -> None:
    dates = _dates(1)
    budget = tmp_path / "budget.jsonl"
    _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run-a",
        shared_budget_path=budget,
        transport=FakeTransport(fail=True),
    )
    url = nsearchives_sec_bhavdata_full_url(_day(dates[0]))
    assert SharedUrlBudget(
        budget, deadline=datetime(2030, 1, 1, tzinfo=timezone.utc), run_id="probe"
    ).attempts(url) == 3

    succeeding = FakeTransport()
    second = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run-b",
        shared_budget_path=budget,
        transport=succeeding,
    )
    assert succeeding.calls == []
    assert second["recovered_session_denominator"] == 0


def test_restart_retains_totals_and_rejects_changed_identity(tmp_path: Path) -> None:
    dates = _dates(2)
    root = tmp_path / "run"
    budget = tmp_path / "budget.jsonl"
    first = _run(
        _report(dates),
        expected=dates,
        output_root=root,
        shared_budget_path=budget,
        transport=FakeTransport(),
    )
    assert first["status"] == "COMPLETE"
    assert first["acquisition"]["request_count"] == 2

    resumed = _run(
        _report(dates),
        expected=dates,
        output_root=root,
        shared_budget_path=budget,
        transport=ForbiddenTransport(),
    )
    assert resumed["acquisition"]["request_count"] == 2
    assert resumed["recovered_session_denominator"] == 2
    assert resumed["status"] == "COMPLETE"

    with pytest.raises(CheckpointError, match="identity"):
        _run(
            _report(dates[:-1]),
            expected=dates[:-1],
            output_root=root,
            shared_budget_path=budget,
            transport=ForbiddenTransport(),
        )


def test_intermediate_resume_snapshot_selects_no_convention(tmp_path: Path) -> None:
    dates = _dates(2)
    state_path = tmp_path / "run" / "resume-state.json"

    class SnapshotTransport(FakeTransport):
        def __call__(self, url, connect_timeout, read_timeout, deadline=None) -> HttpResponse:
            snapshots.append(json.loads(state_path.read_text(encoding="utf-8")))
            return super().__call__(url, connect_timeout, read_timeout, deadline)

    snapshots: list[dict] = []
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=SnapshotTransport(),
    )
    assert snapshots
    assert all(snapshot["phase"] == "resume_snapshot" for snapshot in snapshots)
    assert all(snapshot["status"] == "IN_PROGRESS" for snapshot in snapshots)
    assert all(snapshot["reference_convention"]["selected"] is None for snapshot in snapshots)
    assert run["phase"] == "terminal"
    assert run["reference_convention"]["selected"] in {REFERENCE_OPENING, REFERENCE_CLOSING}


def test_fresh_root_cannot_reset_the_shared_run_deadline(tmp_path: Path) -> None:
    dates = _dates(2)
    budget = tmp_path / "budget.jsonl"
    failing = FakeTransport(fail_dates=frozenset({dates[0]}))
    first_clock = Clock()
    first = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run-a",
        shared_budget_path=budget,
        transport=failing,
        clock=first_clock,
    )
    assert sum(1 for url in failing.calls if _date_from_url(url).isoformat() == dates[0]) == 3
    assert first["acquisition"]["request_count"] == 4

    later = Clock()
    later.t = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
    second_transport = FakeTransport()
    second = run_bounded_checkpoint(
        _report(dates),
        output_root=tmp_path / "run-b",
        transport=second_transport,
        route=ROUTE,
        expected_session_dates=dates,
        shared_budget_path=budget,
        now=later.now,
        sleep=later.sleep,
    )
    # The deadline still runs from the shared run start, so only two hours remain.
    assert second["acquisition"]["elapsed_seconds"] >= 6 * 3600
    assert second["run_id"] == first["run_id"]
    assert [_date_from_url(url).isoformat() for url in second_transport.calls] == [dates[1]]


def test_runner_imports_seed_state_path_without_explicit_counts(tmp_path: Path) -> None:
    dates = _dates(1)
    url = nsearchives_sec_bhavdata_full_url(_day(dates[0]))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"urls": {url: {"attempts": MAX_ATTEMPTS_PER_URL}}}), encoding="utf-8")
    transport = FakeTransport()
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        seed_state_path=state,
    )
    assert transport.calls == []
    assert run["invocation"]["targets"][-1]["status"] == "budget_exhausted"


def test_seed_aggregation_uses_max_and_counts_retained_reservations(tmp_path: Path) -> None:
    dates = _dates(1)
    root = tmp_path / "cash-evidence-v1"
    _seed_root(root, dates)
    url = nse_sec_bhavdata_full_url(_day(dates[0]))
    audit = tmp_path / "state.json"
    audit.write_text(json.dumps({"urls": {url: {"attempts": 5}}}), encoding="utf-8")
    assert aggregate_seed_counts(audit, (root,))[url] == 5
    assert aggregate_seed_counts(None, (root,))[url] == 1
    assert aggregate_seed_counts(audit, ())[url] == 5


def test_malformed_seed_summary_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CheckpointError):
        aggregate_seed_counts(path, ())
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CheckpointError):
        aggregate_seed_counts(path, ())
    path.write_text(json.dumps({"urls": {"u": {"attempts": -1}}}), encoding="utf-8")
    with pytest.raises(CheckpointError):
        aggregate_seed_counts(path, ())


def test_collector_cli_refuses_known_exhausted_route(tmp_path: Path, monkeypatch) -> None:
    from ingest.nse_bhavcopy import nse_sec_bhavdata_full_url

    module = __import__("scripts.data.collect_daily_cash", fromlist=["main"])
    url = nse_sec_bhavdata_full_url(_day("2026-09-11"))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"urls": {url: {"attempts": MAX_ATTEMPTS_PER_URL}}}), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("collector CLI must fail closed before any transport")

    monkeypatch.setattr(module, "collect_daily_cash", forbidden)
    with pytest.raises(SystemExit, match="durably exhausted"):
        module.main(
            ["--date", "2026-09-11", "--output-root", str(tmp_path / "out"), "--seed-budget", str(state)]
        )


def test_corrupt_retained_bytes_are_not_counted(tmp_path: Path) -> None:
    dates = _dates(1)
    existing = tmp_path / "cash-evidence-v1"
    _seed_root(existing, dates)
    assert verify_retained_session(existing, dates[0]) is not None
    body = existing / "requests" / dates[0] / "attempt-001" / "response.bin"
    body.unlink()

    assert verify_retained_session(existing, dates[0]) is None
    transport = FakeTransport(fail=True)
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        existing=(existing,),
    )
    assert run["recovered_session_denominator"] == 0
    assert transport.calls  # the corrupt artifact was retried, never trusted
    assert not (tmp_path / "run" / "cash-evidence" / "current.json").exists()


def test_unreadable_receipt_json_is_not_counted(tmp_path: Path) -> None:
    dates = _dates(1)
    existing = tmp_path / "cash-evidence-v1"
    _seed_root(existing, dates)
    receipt = existing / "requests" / dates[0] / "attempt-001" / "receipt.json"
    receipt.write_text("{not valid json", encoding="utf-8")
    assert verify_retained_session(existing, dates[0]) is None
    transport = FakeTransport(fail=True)
    run = _run(
        _report(dates),
        expected=dates,
        output_root=tmp_path / "run",
        shared_budget_path=tmp_path / "budget.jsonl",
        transport=transport,
        existing=(existing,),
    )
    assert run["recovered_session_denominator"] == 0
    assert transport.calls


def test_missing_session_targets_revalidates_supplied_roots(tmp_path: Path) -> None:
    dates = _dates(2)
    existing = tmp_path / "cash-evidence-v1"
    _seed_root(existing, dates[:1])
    rows = [
        {"session_date": dates[0], "state": "expected", "cash": {"status": "admitted_price"}},
        {"session_date": dates[1], "state": "expected", "cash": {"status": "not_retained"}},
    ]
    assert missing_session_targets(rows, route=ROUTE, existing_cash_roots=(existing,)) == [
        {"session_date": dates[1], "route": ROUTE}
    ]
    assert missing_session_targets(rows, route=ROUTE) == [
        {"session_date": dates[0], "route": ROUTE},
        {"session_date": dates[1], "route": ROUTE},
    ]


