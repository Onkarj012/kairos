from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from evidence.daily_backtest import run_backtest
from evidence.research_ledger import read_events
from models.daily_baselines import Candidate
from ops.daily_account import PAPER_ACCOUNTS, AccountSpec, DailyAccount
from ops.daily_rules import AccountEvent, settle_session
from ops.daily_runner import (
    IST,
    Calendar,
    CalendarMissing,
    Market,
    Runner,
    RunnerPaths,
    _loss_halt,
    _unexplained_ids,
    advance,
    clear,
    place,
    replay,
    start,
    write_live_ledger,
)

SESSIONS = [date(2025, 8, 4) + timedelta(days=offset) for offset in range(12)]
SPEC = AccountSpec("paper-fixed-100k", "fixed", 100_000)


def _row(session: date, sid: str, **values: float) -> dict[str, object]:
    """Build one synthetic panel row with flat prices unless overridden."""
    row = {
        "session_date": session, "security_id": sid, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.0, "prev_close": 100.0, "traded_value": 1e6, "volume": 1_000,
        "adj_factor": 1.0, "prev_close_factor": 1.0,
    }
    row.update(values)
    return row


def _review_panel() -> pd.DataFrame:
    """A panel that hits an entry-band miss, an over-reservation, a split on exit day and a missing exit row."""
    s = SESSIONS
    rows = []
    for session in s:
        for sid in ("A", "B", "C", "D"):
            if sid == "D" and session == s[6]:
                continue  # D has no row on its exit day
            values: dict[str, float] = {}
            if sid == "A" and session <= s[3]:
                values = {"adj_factor": 0.5, "prev_close_factor": 0.5}  # 2:1 split goes ex on s[4]
            if sid == "A" and session == s[4]:
                values = {"open": 50.0, "close": 50.0, "prev_close_factor": 0.5}
            if sid == "A" and session > s[4]:
                values = {"open": 50.0, "close": 50.0, "prev_close": 50.0}
            if sid == "B" and session == s[1]:
                values = {"open": 103.0}  # opens beyond the entry band
            if sid == "C" and session == s[1]:
                values = {"close": 90.0}
            if sid == "C" and session == s[2]:
                values = {"open": 101.0}  # inside the band against prev_close 100, over the 90-close reservation
            rows.append(_row(session, sid, **values))
    return pd.DataFrame(rows)


PICKS = {
    SESSIONS[0]: ["A", "B"],
    SESSIONS[1]: ["C"],
    SESSIONS[2]: ["D"],
}


class FixedMarket(Market):
    """A market whose ranking is a fixed pick list, so tests choose the trades."""

    def rank(self, strategy_name: str, session: date) -> tuple[list[Candidate], list[str], list[str]]:
        return [Candidate(sid, 1.0, "fixed") for sid in PICKS.get(session, [])], [], ["A", "B", "C", "D"]


def _market(panel: pd.DataFrame | None = None, guard_from: date = date(2026, 10, 19)) -> FixedMarket:
    """Build the fixed market over the review panel."""
    frame = (panel if panel is not None else _review_panel()).sort_values(["security_id", "session_date"]).reset_index(drop=True)
    return FixedMarket(panel=frame, sessions=SESSIONS, cohorts={}, guard_from=guard_from, covered={"A", "B", "C", "D"})


def _backtest(market: FixedMarket):
    """Run the stage 1 engine over the same market and picks."""
    return run_backtest(
        market.panel, market.sessions, lambda s: market.rank("fixed", s)[0],
        capital=SPEC.capital, first_signal=SESSIONS[0], last_session=SESSIONS[-1],
    )


def test_replayed_account_matches_backtest_through_review_rules(tmp_path: Path) -> None:
    market = _market()
    account = replay(market, tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
    expected = _backtest(market)
    trades = account.trades()
    assert trades.to_csv(index=False) == expected.trades.to_csv(index=False)
    assert account.equity().to_csv(index=False) == expected.equity.to_csv(index=False)
    by_id = trades.set_index("security_id")
    assert by_id.loc["B", "flags"] == "entry_miss"
    assert by_id.loc["C", "flags"] == "over_reservation"
    assert by_id.loc["A", "flags"] == "qty_rescaled"
    assert by_id.loc["A", "exit_session"] == SESSIONS[4]
    assert by_id.loc["D", "exit_reason"] == "missing_exit_row"
    assert by_id.loc["D", "exit_session"] == SESSIONS[7]


def test_next_day_exit_does_not_fund_next_day_entry(tmp_path: Path) -> None:
    """Cash and slots for the s[4] entries come from the s[3] close, before A's s[4] sale settles."""
    market = _market()
    picks = dict(PICKS)
    PICKS[SESSIONS[3]] = ["B"]
    try:
        account = replay(market, tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
        expected = _backtest(market)
    finally:
        PICKS.clear()
        PICKS.update(picks)
    events = account.view().engine_events
    intent = next(e for e in events if e.kind == "INTENT_CREATED" and e.session == SESSIONS[3].isoformat())
    close_before = next(e for e in events if e.kind == "RECONCILED" and e.session == SESSIONS[3].isoformat())
    assert float(intent.data["reserved"]) <= float(close_before.data["cash"])
    assert account.trades().to_csv(index=False) == expected.trades.to_csv(index=False)


def test_interrupted_settle_reruns_to_the_same_log(tmp_path: Path) -> None:
    market = _market()
    clean = replay(market, tmp_path / "clean", (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]

    broken = DailyAccount(tmp_path / "broken", SPEC)
    broken.open(session=SESSIONS[0].isoformat(), verdict="replay")
    advance(broken, broken.view(), market, SESSIONS[0], plan=True)
    place(broken, broken.view(), SESSIONS[1], SESSIONS[0])
    view = broken.view()
    partial = settle_session(view.state, SESSIONS[1].isoformat(), market.bar(SESSIONS[1]), market.exit_due_after, require_sent=True)
    broken.append(partial[:1])  # crash after the first settle event
    for index in range(1, len(SESSIONS)):
        if index > 1:
            place(broken, broken.view(), SESSIONS[index], SESSIONS[index - 1])
        advance(broken, broken.view(), market, SESSIONS[index], plan=index <= len(SESSIONS) - 5)

    def body(account: DailyAccount) -> list[tuple[str, str, object]]:
        """Return the log without timestamps and hashes."""
        return [(e.event_id, e.event_type, e.payload) for e in read_events(account.path)[1:]]

    assert body(broken) == body(clean)


def test_account_refuses_sealed_holdout_sessions(tmp_path: Path) -> None:
    account = DailyAccount(tmp_path, SPEC)
    with pytest.raises(ValueError, match="sealed holdout"):
        account.open(session="2026-01-05", verdict="replay")


def test_calendar_extends_with_holidays_and_muhurat() -> None:
    calendar = Calendar(
        frozen={date(2026, 10, 19): "expected"},
        holidays={2026: {date(2026, 10, 20): "holiday", date(2026, 11, 8): "muhurat"}},
    )
    assert not calendar.is_session(date(2026, 10, 20))
    assert calendar.is_session(date(2026, 10, 21))
    assert calendar.is_session(date(2026, 11, 8))
    assert calendar.sessions_after(date(2026, 10, 19), 3) == [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
    assert calendar.previous_session(date(2026, 10, 21)) == date(2026, 10, 19)
    with pytest.raises(CalendarMissing):
        calendar.is_session(date(2027, 1, 4))


def test_guard_flags_unexplained_moves_only_after_coverage_or_for_uncovered_ids() -> None:
    rows = pd.DataFrame([
        _row(date(2026, 10, 21), "NEW", open=50.0, close=50.0),
        _row(date(2026, 10, 21), "SPLIT", open=50.0, close=50.0, prev_close_factor=0.5),
        _row(date(2026, 5, 4), "OLD", open=50.0, close=50.0),
        _row(date(2026, 5, 4), "UNCOVERED", open=50.0, close=50.0),
        _row(date(2026, 10, 21), "FLAT"),
    ])
    flagged = _unexplained_ids(rows, date(2026, 10, 19), {"NEW", "SPLIT", "OLD", "FLAT"})
    assert flagged == {"NEW", "UNCOVERED"}


def test_held_unexplained_move_halts_before_settling(tmp_path: Path) -> None:
    panel = _review_panel()
    panel.loc[(panel["security_id"] == "A") & (panel["session_date"] == SESSIONS[4]), "prev_close_factor"] = 1.0
    market = _market(panel, guard_from=SESSIONS[0])
    account = replay(market, tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
    view = account.view()
    assert [h["reason"] for h in view.halts] == ["unexplained_move"]
    assert view.state.last_session == SESSIONS[3].isoformat()


def test_replay_closes_positions_with_no_exit_data_at_the_terminal_session(tmp_path: Path) -> None:
    panel = _review_panel()
    panel = panel.loc[~((panel["security_id"] == "D") & (panel["session_date"] >= SESSIONS[6]))]
    market = _market(panel)
    account = replay(market, tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
    expected = _backtest(market)
    assert account.trades().to_csv(index=False) == expected.trades.to_csv(index=False)
    assert not account.view().state.holdings


def test_fill_with_an_unexplained_move_halts_after_settling(tmp_path: Path) -> None:
    panel = _review_panel()
    panel.loc[(panel["security_id"] == "A") & (panel["session_date"] == SESSIONS[1]), "close"] = 55.0
    market = _market(panel, guard_from=SESSIONS[0])
    account = replay(market, tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
    view = account.view()
    assert [(h["session"], h["reason"]) for h in view.halts] == [(SESSIONS[1].isoformat(), "unexplained_move")]
    assert [h.security_id for h in view.state.holdings] == ["A"]


def test_loss_crossing_left_unhalted_by_a_crash_halts_on_the_next_run(tmp_path: Path) -> None:
    market = _market()
    account = DailyAccount(tmp_path, SPEC)
    account.open(session=SESSIONS[0].isoformat(), verdict="replay")
    account.append([AccountEvent("RECONCILED", SESSIONS[0].isoformat(), {
        "cash": 94_000.0, "positions_value": 0.0, "equity": 94_000.0, "daily_return": -0.06, "marks": {},
    })])  # crash before the loss halt was written
    view = advance(account, account.view(), market, SESSIONS[1], plan=True)
    assert [(h["session"], h["reason"]) for h in view.halts] == [(SESSIONS[0].isoformat(), "loss_limit_month")]
    assert SESSIONS[1].isoformat() not in account.view().settled

    account.clear_halts(session=SESSIONS[1].isoformat(), note="checked")
    view = advance(account, account.view(), market, SESSIONS[1], plan=True)
    assert not view.halts
    assert SESSIONS[1].isoformat() in view.signalled


def test_loss_limits_halt_on_crossing_only(tmp_path: Path) -> None:
    account = DailyAccount(tmp_path, SPEC)
    account.open(session=SESSIONS[0].isoformat(), verdict="replay")
    view = account.view()

    def mark(session: date, equity: float) -> None:
        """Add a synthetic close mark to the view."""
        view.engine_events.append(AccountEvent("RECONCILED", session.isoformat(), {"equity": equity}))

    mark(date(2025, 8, 4), 97_000)
    assert _loss_halt(view) is None
    mark(date(2025, 8, 5), 95_000)
    assert _loss_halt(view)[0] == "loss_limit_month"
    mark(date(2025, 8, 6), 94_000)
    assert _loss_halt(view) is None
    mark(date(2025, 9, 1), 90_000)
    assert _loss_halt(view)[0] == "loss_limit_total"


class FakeCash:
    """Cash source with scripted availability and a request counter."""

    def __init__(self, available: set[date], spent: int = 0) -> None:
        self.available = set(available)
        self.calls: list[tuple[date, str, bool]] = []
        self.already = spent

    def retained(self, session: date) -> bool:
        return session in self.available

    def spent(self, session: date) -> int:
        return self.already + sum(1 for call in self.calls if call[0] == session)

    def fetch(self, session: date, route: str, resume: bool) -> str:
        self.calls.append((session, route, resume))
        return "rejected HTTP status 404"


LIVE = [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]


def _live_paths(tmp_path: Path) -> RunnerPaths:
    """Write the minimum live data files the jobs read."""
    paths = RunnerPaths(tmp_path)
    paths.frozen_ledger.parent.mkdir(parents=True)
    paths.frozen_ledger.write_text(json.dumps({"session_date": "2026-10-19", "state": "expected"}) + "\n")
    rows = []
    for session in [date(2026, 10, 19), *LIVE]:
        for sid in ("A", "B", "C"):
            rows.append(_row(session, sid))
    paths.live_panel_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(paths.live_panel_dir / "panel.parquet", index=False)
    paths.live_cohort_dir.mkdir(parents=True)
    pd.DataFrame({"month": ["2026-10"] * 3, "security_id": ["A", "B", "C"]}).to_parquet(paths.live_cohort_dir / "cohort.parquet", index=False)
    paths.actions_manifest.parent.mkdir(parents=True)
    paths.actions_manifest.write_text(json.dumps({"range": {"end": "2026-10-19"}}))
    paths.covered_ids.parent.mkdir(parents=True)
    pd.DataFrame({"security_id": ["A", "B", "C"]}).to_csv(paths.covered_ids, index=False)
    paths.research_ledger.parent.mkdir(parents=True)
    paths.research_ledger.write_text("")
    return paths


CALENDAR = Calendar(frozen={date(2026, 10, 19): "expected"}, holidays={2026: {date(2026, 10, 20): "holiday"}})


def _build(paths: RunnerPaths, calendar: Calendar, through: date) -> None:
    """Write only the live ledger; the test panel and cohort are fixed."""
    write_live_ledger(paths, calendar, through)


def _run(paths: RunnerPaths, job: str, session: date, at: str, cash: FakeCash | None = None) -> dict[str, object]:
    """Run one job at an IST clock time on a session."""
    now = datetime.fromisoformat(f"{session.isoformat()}T{at}").replace(tzinfo=IST)
    runner = Runner(paths, now, cash=cash or FakeCash(set(LIVE)), calendar=CALENDAR, build=_build, log=lambda _m: None)
    return runner.run(job)


def _accounts(paths: RunnerPaths) -> list[DailyAccount]:
    """Return the four paper accounts under a test root."""
    return [DailyAccount(paths.accounts, spec) for spec in PAPER_ACCOUNTS]


def _patch_strategies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rank the live test cohort with a fixed pick of A then B."""
    monkeypatch.setattr(Market, "rank", lambda self, name, session: ([Candidate("A", 1.0, "t"), Candidate("B", 0.5, "t")], [], ["A", "B", "C"]))


def test_scheduled_jobs_settle_plan_and_send_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    assert _run(paths, "reconcile", LIVE[0], "18:50")["status"] == "ok"
    assert _run(paths, "signal", LIVE[0], "19:35")["planned"] == {a.spec.account_id: 2 for a in _accounts(paths)}
    assert _run(paths, "signal", LIVE[0], "20:20")["status"] == "already_done"
    assert _run(paths, "place", LIVE[1], "08:46")["sent"] == {a.spec.account_id: 2 for a in _accounts(paths)}
    _run(paths, "reconcile", LIVE[1], "18:50")
    _run(paths, "signal", LIVE[1], "19:35")
    for account in _accounts(paths):
        view = account.view()
        assert not view.halts
        assert [h.security_id for h in view.state.holdings] == ["A", "B"]
        assert view.state.holdings[0].exit_due == LIVE[4].isoformat()


def test_missed_and_late_runs_halt_every_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    _run(paths, "signal", LIVE[0], "19:35")
    assert _run(paths, "place", LIVE[1], "10:05")["status"] == "halted"
    assert {h["reason"] for a in _accounts(paths) for h in a.view().halts} == {"late_run"}

    clear(paths, datetime(2026, 10, 22, 12, 0, tzinfo=IST), "checked")
    _run(paths, "reconcile", LIVE[1], "18:50")
    _run(paths, "signal", LIVE[1], "19:35")
    _run(paths, "reconcile", LIVE[2], "18:50")  # place on LIVE[2] never ran
    assert {h["reason"] for a in _accounts(paths) for h in a.view().halts} == {"missed_run"}


def test_recovery_marker_does_not_cover_a_later_missed_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 20, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "place", LIVE[1], "08:46")  # every LIVE[0] slot was missed
    assert {h["reason"] for a in _accounts(paths) for h in a.view().halts} == {"missed_run"}


def test_cleared_halt_catches_up_missed_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    _run(paths, "signal", LIVE[0], "19:35")
    _run(paths, "place", LIVE[1], "10:05")  # late: halts and freezes every account
    clear(paths, datetime(2026, 10, 23, 12, 0, tzinfo=IST), "checked")
    _run(paths, "reconcile", LIVE[2], "18:50")
    _run(paths, "signal", LIVE[2], "19:35")
    for account in _accounts(paths):
        view = account.view()
        assert {LIVE[1].isoformat(), LIVE[2].isoformat()} <= view.settled
        assert account.trades()["exit_reason"].tolist() == ["intent_expired", "intent_expired"]


def test_signal_waits_then_halts_on_stale_bhavcopy_within_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    cash = FakeCash(set())
    assert _run(paths, "signal", LIVE[0], "19:31", cash)["status"] == "waiting"
    assert _run(paths, "signal", LIVE[0], "20:16", cash)["status"] == "waiting"
    assert _run(paths, "signal", LIVE[0], "21:01", cash)["status"] == "waiting"
    result = _run(paths, "signal", LIVE[0], "21:31", cash)
    assert result["status"] == "halted"
    assert [call[1:] for call in cash.calls] == [
        ("archives_full_bhavdata", False),
        ("archives_full_bhavdata", True),
        ("nsearchives_full_bhavdata", False),
        ("nsearchives_udiff_zip", False),
    ]
    assert "request budget spent for 2026-10-21" in result["detail"]
    assert {h["reason"] for a in _accounts(paths) for h in a.view().halts} == {"stale_bhavcopy"}


def test_signal_skips_a_request_that_could_break_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    cash = FakeCash(set(), spent=4)
    assert _run(paths, "signal", LIVE[0], "19:31", cash)["status"] == "waiting"
    assert cash.calls == []
