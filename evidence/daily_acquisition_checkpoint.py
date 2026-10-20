"""Bounded historical acquisition checkpoint (plan DG-01 / issue 03).

Two responsibilities, both offline-testable:

``assess_checkpoint``
    Consume the immutable session ledger and decide whether the bounded
    historical recovery checkpoint may start.  It fails closed on a missing or
    contradictory report: a ``READY`` decision is never emitted while a
    calendar/amendment/review blocker remains or the qualified count disagrees
    with the expected count.  It never inspects a strategy outcome.

``run_bounded_checkpoint``
    The resumable runner.  It refuses to start on an unqualified denominator,
    validates the complete expected-session set against the immutable report
    before any network call, then reuses every already-validated retained price
    artifact (across the caller-supplied evidence roots *and* its own new root)
    and attempts recovery only for expected sessions that no root can
    re-validate.  It shares one durable, file-locked per-URL attempt journal
    across resumes, route aliases and fresh output roots, enforces one request
    in flight, at least 0.5 s between starts, at most three attempts per URL,
    5/30 s retry waits (single-sourced in the collector), a cumulative
    eight-hour deadline that survives interruption, and clamped transport
    timeouts so no request begins past the deadline.  It republishes cumulative
    accepted/rejected/failure/request/elapsed state atomically after progress
    so a crash resumes honestly, and applies the 98% recovery threshold to
    select the plan reference convention without admitting any missing session.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time as time_module
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from contracts.calendar import canonical_json
from ingest.daily_cash import (
    collect_daily_cash,
    tally_receipts,
    verify_retained_session,
)

LEDGER_SCHEMA = "meridian.daily-session-ledger.report.v1"
DECISION_SCHEMA = "meridian.daily-acquisition-checkpoint.decision.v1"
RUN_SCHEMA = "meridian.daily-acquisition-checkpoint.run.v1"
BUDGET_SCHEMA = "meridian.daily-acquisition-checkpoint.budget.v1"

MAX_ELAPSED_SECONDS = 8 * 60 * 60
MAX_ATTEMPTS_PER_URL = 3
MIN_START_SPACING_SECONDS = 0.5
RETRY_WAITS_SECONDS = (5.0, 30.0)
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
RECOVERY_THRESHOLD = 0.98

REFERENCE_OPENING = "T+1_open_to_T+4_open"
REFERENCE_CLOSING = "T+1_close_to_T+4_close"

# Scopes that gate the expected-session denominator (plan §5).
BLOCKING_SCOPES = frozenset({"calendar", "amendment", "review"})
QUALIFICATION_SCOPES = frozenset({"legacy", "cash"})


class CheckpointError(ValueError):
    """A checkpoint input is missing, malformed, or self-contradictory."""


class SharedBudgetExhausted(Exception):
    """The shared per-URL request budget is durably exhausted.

    Deliberately not a ``ValueError``: the collector catches ``ValueError`` as a
    validation rejection and must never convert budget exhaustion into a fake
    retained attempt.
    """


class AcquisitionDeadlineExceeded(SharedBudgetExhausted):
    """The shared cumulative eight-hour deadline has been reached."""


class Transport(Protocol):
    """Collector-facing transport: retries/timeouts are owned by the collector."""

    def __call__(self, url: str, connect_timeout: float, read_timeout: float) -> Any: ...


class GuardedTransport(Protocol):
    """Underlying transport that also receives the absolute shared deadline."""

    def __call__(
        self, url: str, connect_timeout: float, read_timeout: float, deadline: datetime
    ) -> Any: ...


Now = Callable[[], datetime]
Sleep = Callable[[float], None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sleep(seconds: float) -> None:
    time_module.sleep(seconds)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise CheckpointError("timestamp must be timezone-aware")
    return parsed


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Readiness assessment
# ---------------------------------------------------------------------------


def _validate_blocker(entry: Any, known_scopes: frozenset[str]) -> str:
    if not isinstance(entry, Mapping):
        raise CheckpointError("each ledger blocker must be an object")
    scope = entry.get("scope")
    if not isinstance(scope, str) or scope not in known_scopes:
        raise CheckpointError(f"ledger blocker has an unknown or malformed scope: {scope!r}")
    return scope


def _blocker_scopes(report: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    known_scopes = BLOCKING_SCOPES | QUALIFICATION_SCOPES
    blockers = report.get("blockers")
    explicit_denominator = report.get("denominator_blockers")
    explicit_qualification = report.get("qualification_blockers")
    split_present = explicit_denominator is not None or explicit_qualification is not None
    if split_present:
        if not isinstance(explicit_denominator, list) or not isinstance(explicit_qualification, list):
            raise CheckpointError("split blocker lists must both be present and be lists")
        if not isinstance(blockers, list):
            raise CheckpointError("report has split blocker lists but no main blockers list")
        for entry in explicit_denominator:
            scope = _validate_blocker(entry, known_scopes)
            if scope not in BLOCKING_SCOPES:
                raise CheckpointError(f"denominator_blockers contains a non-gating scope: {scope!r}")
        for entry in explicit_qualification:
            scope = _validate_blocker(entry, known_scopes)
            if scope not in QUALIFICATION_SCOPES:
                raise CheckpointError(f"qualification_blockers contains a non-qualification scope: {scope!r}")
        for entry in blockers:
            _validate_blocker(entry, known_scopes)
        combined = explicit_denominator + explicit_qualification
        if canonical_json({"blockers": list(combined)}) != canonical_json({"blockers": list(blockers)}):
            raise CheckpointError("split blocker lists contradict the main blockers list")
        return list(explicit_denominator), list(explicit_qualification)
    if not isinstance(blockers, list):
        raise CheckpointError("ledger report is missing its blockers list")
    denominator: list[Any] = []
    qualification: list[Any] = []
    for entry in blockers:
        scope = _validate_blocker(entry, known_scopes)
        (denominator if scope in BLOCKING_SCOPES else qualification).append(entry)
    return denominator, qualification


def assess_checkpoint(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fail-closed readiness decision without performing acquisition."""
    if not isinstance(report, Mapping):
        raise CheckpointError("ledger report must be an object")
    if report.get("schema") != LEDGER_SCHEMA:
        raise CheckpointError(f"unexpected ledger report schema: {report.get('schema')!r}")
    denominator = report.get("denominator")
    if not isinstance(denominator, Mapping):
        raise CheckpointError("ledger report is missing its denominator block")

    expected = denominator.get("expected_session_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected <= 0:
        raise CheckpointError("denominator expected_session_count must be a positive integer")

    qualified_flag = denominator.get("denominator_qualified")
    if not isinstance(qualified_flag, bool):
        raise CheckpointError("denominator_qualified must be a boolean")

    denominator_blockers, qualification_blockers = _blocker_scopes(report)
    qualified_count = denominator.get("qualified_expected_denominator")
    if qualified_count is not None and (isinstance(qualified_count, bool) or not isinstance(qualified_count, int)):
        raise CheckpointError("qualified_expected_denominator must be a non-boolean integer")

    if qualified_flag:
        if denominator_blockers:
            raise CheckpointError(
                "report claims a qualified denominator while calendar/amendment/review blockers remain"
            )
        if qualified_count != expected:
            raise CheckpointError(
                "report claims a qualified denominator but the qualified count does not match the expected count"
            )
        if denominator.get("historical_checkpoint_blocked") is True:
            raise CheckpointError("report claims qualification while also marking the checkpoint blocked")
    else:
        if qualified_count is not None:
            raise CheckpointError(
                "report marks the denominator unqualified but still publishes a qualified count"
            )

    status = "READY" if qualified_flag else "NOT_RUN"
    if qualified_flag:
        reason = (
            "expected-session denominator is qualified; the bounded historical acquisition "
            "checkpoint may run under the declared budget"
        )
        basis = "denominator qualified; execution is a separate authorized run"
    else:
        reason = (
            "expected-session denominator is not qualified; the bounded historical acquisition "
            "checkpoint must not start"
        )
        basis = "not evaluated because the checkpoint did not run"

    latest_completed = report.get("latest_completed_session")
    if qualified_flag and not isinstance(latest_completed, str):
        raise CheckpointError("a qualified denominator requires a latest completed session")

    return {
        "schema": DECISION_SCHEMA,
        "status": status,
        "as_of": report.get("as_of"),
        "ledger_schema": report.get("schema"),
        "ledger_start": report.get("start"),
        "latest_completed_session": latest_completed,
        "expected_session_count": expected,
        "qualified_expected_denominator": qualified_count,
        "denominator_qualified": qualified_flag,
        "denominator_blocker_count": len(denominator_blockers),
        "qualification_blocker_count": len(qualification_blockers),
        "denominator_blockers": denominator_blockers,
        "qualification_blockers": qualification_blockers,
        "reason": reason,
        "blocker_count": len(denominator_blockers) + len(qualification_blockers),
        "blockers": list(report.get("blockers") or []),
        "acquisition": {
            "started": False,
            "request_count": 0,
            "accepted_sessions": 0,
            "rejected_responses": 0,
            "failures": 0,
            "elapsed_seconds": 0.0,
            "recovered_session_denominator": None,
            "max_elapsed_seconds": MAX_ELAPSED_SECONDS,
            "max_attempts_per_url": MAX_ATTEMPTS_PER_URL,
            "min_start_spacing_seconds": MIN_START_SPACING_SECONDS,
            "retry_waits_seconds": list(RETRY_WAITS_SECONDS),
            "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
            "read_timeout_seconds": READ_TIMEOUT_SECONDS,
        },
        "reference_convention": {
            "selected": None,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "basis": basis,
        },
        "strategy_outcomes_inspected": False,
    }


# ---------------------------------------------------------------------------
# Shared durable per-URL request budget
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


BUDGET_RECORD_KINDS = frozenset({"seed", "run", "reservation", "outcome"})


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CheckpointError(f"shared budget {label} must be a non-negative integer")
    return value


def _canonical_budget_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise CheckpointError(f"shared budget {label} must be a timestamp string")
    try:
        parsed = _parse_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise CheckpointError(f"shared budget {label} is not a valid timestamp") from exc
    if _timestamp(parsed) != value:
        raise CheckpointError(f"shared budget {label} is not a canonical UTC timestamp")
    return parsed


def _validate_budget_record(record: Mapping[str, Any], path: Path) -> None:
    if record.get("schema") != BUDGET_SCHEMA:
        raise CheckpointError(f"shared budget journal record has an unknown or missing schema: {path}")
    kind = record.get("record")
    if kind not in BUDGET_RECORD_KINDS:
        raise CheckpointError(f"shared budget journal record has an unknown kind: {kind!r}")
    if kind == "seed":
        counts = record.get("counts")
        if not isinstance(counts, Mapping):
            raise CheckpointError("shared budget seed counts must be an object")
        for url, count in counts.items():
            if not isinstance(url, str) or not url:
                raise CheckpointError("shared budget seed URLs must be non-empty strings")
            _nonnegative_int(count, label=f"seed count for {url!r}")
        _canonical_budget_timestamp(record.get("imported_at"), label="seed imported_at")
    elif kind == "run":
        if not isinstance(record.get("run_id"), str) or not record["run_id"]:
            raise CheckpointError("shared budget run record needs a non-empty run_id")
        _canonical_budget_timestamp(record.get("started_at"), label="run started_at")
    elif kind == "reservation":
        if not isinstance(record.get("url"), str) or not record["url"]:
            raise CheckpointError("shared budget reservation needs a non-empty url")
        attempt_number = record.get("attempt_number")
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number <= 0:
            raise CheckpointError("shared budget reservation attempt_number must be a positive integer")
        _canonical_budget_timestamp(record.get("started_at"), label="reservation started_at")
    elif kind == "outcome":
        if not isinstance(record.get("url"), str) or not record["url"]:
            raise CheckpointError("shared budget outcome needs a non-empty url")
        if not isinstance(record.get("received"), bool):
            raise CheckpointError("shared budget outcome received must be a boolean")
        _canonical_budget_timestamp(record.get("finished_at"), label="outcome finished_at")


class SharedUrlBudget:
    """Append-only, fsync-durable, file-locked per-URL request journal.

    The same exact URL shares one attempt budget regardless of route directory,
    route alias, diagnostic or acquisition use.  Prior consumption is imported
    as append-only ``seed`` snapshots; each snapshot is the ``max`` of every
    earlier snapshot and the newly supplied counts, so a fresh output root can
    never forget exhaustion and no count is ever reset or double counted.  The
    earliest run start for a ``run_id`` is likewise persisted as a ``run``
    record, so a fresh root cannot restart the cumulative deadline.  A
    reservation is appended under an exclusive file lock before the network call
    and held across it, so two consumers of the same journal cannot start the
    same URL concurrently.

    This journal constrains only code that routes its transport through
    :meth:`guard`.  A raw ``curl``/``wget``/ad-hoc ``urllib`` call from a shell
    cannot be seen or stopped here; the durable journal plus operator discipline
    are the only controls against that, and no claim is made otherwise.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        run_id: str,
        deadline: datetime | None = None,
        limit: int = MAX_ATTEMPTS_PER_URL,
        min_start_spacing_seconds: float = MIN_START_SPACING_SECONDS,
        seed_counts: Mapping[str, int] | None = None,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.parent / (self.path.name + ".lock")
        self.deadline = deadline
        self.run_id = run_id
        self.limit = limit
        self.min_start_spacing_seconds = min_start_spacing_seconds
        self._seed_counts = {
            str(url): _nonnegative_int(count, label=f"seed count for {url!r}")
            for url, count in (seed_counts or {}).items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _read_locked(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CheckpointError(f"shared budget journal is corrupt: {self.path}") from exc
            if not isinstance(decoded, Mapping):
                raise CheckpointError(f"shared budget journal entry is not an object: {self.path}")
            record = dict(decoded)
            _validate_budget_record(record, self.path)
            records.append(record)
        return records

    def _append_locked(self, record: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _seed_totals(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        totals: dict[str, int] = {}
        for record in records:
            if record.get("record") != "seed":
                continue
            counts = record.get("counts")
            if not isinstance(counts, Mapping):
                raise CheckpointError("shared budget seed counts must be an object")
            for url, count in counts.items():
                if not isinstance(url, str) or not url:
                    raise CheckpointError("shared budget seed URLs must be non-empty strings")
                totals[url] = max(totals.get(url, 0), _nonnegative_int(count, label=f"seed count for {url!r}"))
        return totals

    def _records_with_seed_locked(self) -> list[dict[str, Any]]:
        records = self._read_locked()
        current = self._seed_totals(records)
        merged = dict(current)
        for url, count in self._seed_counts.items():
            if count > merged.get(url, 0):
                merged[url] = count
        if merged != current:
            seed = {
                "schema": BUDGET_SCHEMA,
                "record": "seed",
                "run_id": self.run_id,
                "counts": merged,
                "imported_at": _timestamp(_utc_now()),
            }
            self._append_locked(seed)
            records.append(seed)
        return records

    @staticmethod
    def _attempts_from(records: Sequence[Mapping[str, Any]], url: str) -> int:
        total = SharedUrlBudget._seed_totals(records).get(url, 0)
        for record in records:
            if record.get("record") == "reservation" and record.get("url") == url:
                total += 1
        return total

    @staticmethod
    def _last_started_at(records: Sequence[Mapping[str, Any]]) -> datetime | None:
        starts = [
            _parse_timestamp(record["started_at"])
            for record in records
            if record.get("record") == "reservation" and isinstance(record.get("started_at"), str)
        ]
        return max(starts) if starts else None

    def resolve_run_start(self, now: Now = _utc_now) -> datetime:
        """Return the earliest durable start for this run id, recording it once.

        The value lives in the shared journal rather than a per-root manifest, so
        a fresh output root (or a resume) cannot reset the cumulative deadline.
        """
        with self._locked():
            records = self._records_with_seed_locked()
            starts = [
                _parse_timestamp(record["started_at"])
                for record in records
                if record.get("record") == "run" and record.get("run_id") == self.run_id
            ]
            if starts:
                return min(starts)
            started = now()
            self._append_locked({
                "schema": BUDGET_SCHEMA,
                "record": "run",
                "run_id": self.run_id,
                "started_at": _timestamp(started),
            })
            return started

    def set_deadline(self, deadline: datetime) -> None:
        self.deadline = deadline

    def attempts(self, url: str) -> int:
        with self._locked():
            return self._attempts_from(self._records_with_seed_locked(), url)

    def exhausted(self, url: str) -> bool:
        return self.attempts(url) >= self.limit

    def exhausted_urls(self) -> list[str]:
        with self._locked():
            records = self._records_with_seed_locked()
            urls = set(self._seed_totals(records))
            urls.update(
                str(record["url"])
                for record in records
                if record.get("record") == "reservation" and isinstance(record.get("url"), str)
            )
            return sorted(url for url in urls if self._attempts_from(records, url) >= self.limit)

    def reservations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._locked():
            return [
                record
                for record in self._records_with_seed_locked()
                if record.get("record") == "reservation" and record.get("run_id") == run_id
            ]

    def guard(
        self,
        transport: GuardedTransport,
        *,
        now: Now = _utc_now,
        sleep: Sleep = _sleep,
    ) -> Transport:
        """Wrap a transport so every call is bounded and durably accounted.

        Retry waits are intentionally *not* applied here: the collector already
        enforces the 5/30 s waits and 0.5 s spacing, and doubling them would
        violate the plan.  The guard owns the shared per-URL cap, the cumulative
        deadline, the spacing across distinct consumers, and timeout clamping.

        Before reserving, the guard requires enough remaining time for the full
        connect *and* read allowance and refuses the request otherwise.  It then
        forwards that absolute deadline to the transport, so a default transport
        subtracts the connect time from the read budget instead of treating the
        two socket inactivity timeouts as independent.
        """

        if self.deadline is None:
            raise CheckpointError("shared budget guard requires a resolved deadline")

        def bounded(url: str, connect_timeout: float, read_timeout: float) -> Any:
            with self._locked():
                records = self._records_with_seed_locked()
                attempts = self._attempts_from(records, url)
                if attempts >= self.limit:
                    raise SharedBudgetExhausted(f"shared per-URL budget durably exhausted for {url}")
                started = now()
                remaining = (self.deadline - started).total_seconds()
                if remaining <= 0:
                    raise AcquisitionDeadlineExceeded("cumulative eight-hour acquisition budget exhausted")
                last = self._last_started_at(records)
                if last is not None:
                    wait = max(0.0, self.min_start_spacing_seconds - (started - last).total_seconds())
                    if wait > 0:
                        if wait >= remaining:
                            raise AcquisitionDeadlineExceeded(
                                "cumulative eight-hour acquisition budget exhausted before request"
                            )
                        sleep(wait)
                        started = now()
                        remaining = (self.deadline - started).total_seconds()
                        if remaining <= 0:
                            raise AcquisitionDeadlineExceeded(
                                "cumulative eight-hour acquisition budget exhausted before request"
                            )
                connect = float(connect_timeout)
                read = float(read_timeout)
                if remaining < connect + read:
                    raise AcquisitionDeadlineExceeded(
                        "insufficient connect+read allowance before the cumulative acquisition deadline"
                    )
                forwarded_connect = min(connect, remaining)
                forwarded_read = min(read, remaining)
                attempt_number = attempts + 1
                self._append_locked({
                    "schema": BUDGET_SCHEMA,
                    "record": "reservation",
                    "run_id": self.run_id,
                    "url": url,
                    "attempt_number": attempt_number,
                    "started_at": _timestamp(started),
                    "deadline_at": _timestamp(self.deadline),
                    "connect_timeout_seconds": forwarded_connect,
                    "read_timeout_seconds": forwarded_read,
                })
                try:
                    response = transport(url, forwarded_connect, forwarded_read, self.deadline)
                except BaseException as exc:
                    self._append_locked({
                        "schema": BUDGET_SCHEMA,
                        "record": "outcome",
                        "run_id": self.run_id,
                        "url": url,
                        "attempt_number": attempt_number,
                        "finished_at": _timestamp(now()),
                        "received": False,
                        "error": str(exc) or exc.__class__.__name__,
                    })
                    raise
                self._append_locked({
                    "schema": BUDGET_SCHEMA,
                    "record": "outcome",
                    "run_id": self.run_id,
                    "url": url,
                    "attempt_number": attempt_number,
                    "finished_at": _timestamp(now()),
                    "received": True,
                    "error": None,
                })
                return response

        return bounded


def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


# ---------------------------------------------------------------------------
# Resumable runner
# ---------------------------------------------------------------------------


def _normalize_expected_dates(report: Mapping[str, Any], values: Sequence[str]) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise CheckpointError("expected_session_dates must be a sequence of date strings")
    try:
        start_date = date.fromisoformat(str(report.get("start")))
    except (TypeError, ValueError) as exc:
        raise CheckpointError("ledger report is missing a valid start date") from exc
    latest_value = report.get("latest_completed_session")
    latest_date: date | None = None
    if latest_value is not None:
        try:
            latest_date = date.fromisoformat(str(latest_value))
        except (TypeError, ValueError) as exc:
            raise CheckpointError("ledger latest_completed_session is not a date") from exc
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise CheckpointError("each expected session date must be a string")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise CheckpointError(f"expected session date is not a date: {value!r}") from exc
        if parsed.isoformat() != value:
            raise CheckpointError(f"expected session date is not canonical: {value!r}")
        if parsed < start_date or (latest_date is not None and parsed > latest_date):
            raise CheckpointError(f"expected session date is outside the ledger range: {value!r}")
        if value in seen:
            raise CheckpointError(f"duplicate expected session date: {value!r}")
        seen.add(value)
        normalized.append(value)
    return normalized


def _target_rows(
    targets: Sequence[Mapping[str, Any]],
    expected_dates: Sequence[str],
    *,
    default_route: str,
) -> list[dict[str, str]]:
    expected_set = set(expected_dates)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, Mapping):
            raise CheckpointError("each target must be an object")
        session_date = target.get("session_date")
        if not isinstance(session_date, str):
            raise CheckpointError("each target requires a session_date string")
        try:
            parsed = date.fromisoformat(session_date)
        except ValueError as exc:
            raise CheckpointError(f"target session_date is not a date: {session_date!r}") from exc
        if parsed.isoformat() != session_date:
            raise CheckpointError(f"target session_date is not canonical: {session_date!r}")
        if session_date not in expected_set:
            raise CheckpointError(f"target is not an expected session: {session_date!r}")
        if session_date in seen:
            raise CheckpointError(f"duplicate target session date: {session_date!r}")
        seen.add(session_date)
        route = target.get("route", default_route)
        if not isinstance(route, str) or not route:
            raise CheckpointError("target route must be a non-empty string")
        if route != default_route:
            raise CheckpointError("target route does not match the configured acquisition route")
        rows.append({"session_date": session_date, "route": route})
    return rows


def _find_recovered(roots: Sequence[Path], session_date: str) -> dict[str, Any] | None:
    for root in roots:
        evidence = verify_retained_session(root, session_date)
        if evidence is not None:
            return evidence
    return None


def _recovered_map(expected_dates: Sequence[str], roots: Sequence[Path]) -> dict[str, dict[str, Any]]:
    recovered: dict[str, dict[str, Any]] = {}
    for session_date in expected_dates:
        evidence = _find_recovered(roots, session_date)
        if evidence is not None:
            recovered[session_date] = evidence
    return recovered


def _finite_float(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CheckpointError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise CheckpointError(f"{label} must be a finite number")
    return number


def _validated_config(
    *,
    max_attempts_per_url: Any,
    min_start_spacing_seconds: Any,
    max_elapsed_seconds: Any,
    connect_timeout_seconds: Any,
    read_timeout_seconds: Any,
) -> dict[str, Any]:
    """Refuse any configuration that would loosen a fixed plan bound.

    Only stricter-than-plan settings are accepted: at most three attempts per
    URL, at most eight hours, spacing no tighter than 0.5 s, and timeouts no
    longer than the 10 s connect / 30 s read plan values.  Retry waits are owned
    by the collector and are not a tunable here.
    """
    if (
        isinstance(max_attempts_per_url, bool)
        or not isinstance(max_attempts_per_url, int)
        or not 1 <= max_attempts_per_url <= MAX_ATTEMPTS_PER_URL
    ):
        raise CheckpointError("max_attempts_per_url must be an integer between 1 and 3")
    if (
        isinstance(max_elapsed_seconds, bool)
        or not isinstance(max_elapsed_seconds, int)
        or not 0 < max_elapsed_seconds <= MAX_ELAPSED_SECONDS
    ):
        raise CheckpointError("max_elapsed_seconds must be a positive integer no greater than eight hours")
    spacing = _finite_float(min_start_spacing_seconds, label="min_start_spacing_seconds")
    if spacing < MIN_START_SPACING_SECONDS:
        raise CheckpointError("min_start_spacing_seconds cannot be below the 0.5 second plan bound")
    connect = _finite_float(connect_timeout_seconds, label="connect_timeout_seconds")
    if not 0.0 < connect <= CONNECT_TIMEOUT_SECONDS:
        raise CheckpointError("connect_timeout_seconds cannot exceed the 10 second plan bound")
    read = _finite_float(read_timeout_seconds, label="read_timeout_seconds")
    if not 0.0 < read <= READ_TIMEOUT_SECONDS:
        raise CheckpointError("read_timeout_seconds cannot exceed the 30 second plan bound")
    return {
        "max_attempts_per_url": int(max_attempts_per_url),
        "min_start_spacing_seconds": spacing,
        "retry_waits_seconds": list(RETRY_WAITS_SECONDS),
        "max_elapsed_seconds": int(max_elapsed_seconds),
        "connect_timeout_seconds": connect,
        "read_timeout_seconds": read,
    }


def _audit_seed_counts(path: Path | None) -> dict[str, int]:
    if path is None or not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"default shared-budget summary is unreadable: {path}") from exc
    if not isinstance(state, Mapping):
        raise CheckpointError(f"default shared-budget summary must be an object: {path}")
    urls = state.get("urls")
    if not isinstance(urls, Mapping):
        raise CheckpointError(f"default shared-budget summary has no urls mapping: {path}")
    counts: dict[str, int] = {}
    for url, entry in urls.items():
        if not isinstance(entry, Mapping):
            raise CheckpointError(f"shared-budget URL entry must be an object: {url!r}")
        attempts = entry.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise CheckpointError(f"shared-budget URL attempts must be a non-negative integer: {url!r}")
        counts[str(url)] = attempts
    return counts


def _retained_reservation_counts(roots: Sequence[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for root in roots:
        requests_root = Path(root) / "requests"
        if not requests_root.is_dir():
            continue
        for reservation_path in sorted(requests_root.glob("**/attempt-*/reservation.json")):
            try:
                reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CheckpointError(f"retained reservation is unreadable: {reservation_path}") from exc
            if not isinstance(reservation, Mapping):
                raise CheckpointError(f"retained reservation is not an object: {reservation_path}")
            url = reservation.get("requested_url")
            if not isinstance(url, str) or not url:
                raise CheckpointError(f"retained reservation has no requested_url: {reservation_path}")
            counts[url] = counts.get(url, 0) + 1
    return counts


def aggregate_seed_counts(
    seed_state_path: str | Path | None,
    existing_cash_roots: Sequence[str | Path],
) -> dict[str, int]:
    """Snapshot the earlier per-URL consumption to import into the shared journal.

    The durable audit summary (diagnostics plus acquisition) and the reservation
    evidence inside the supplied retained cash roots describe overlapping
    histories, so the two per-URL totals are combined with ``max`` rather than
    summed.  Every collector attempt in a retained root is counted, not only the
    accepted reuse, and no attempt is ever counted twice or reset.
    """
    seed = _audit_seed_counts(Path(seed_state_path) if seed_state_path is not None else None)
    retained = _retained_reservation_counts([Path(value) for value in existing_cash_roots])
    urls = set(seed) | set(retained)
    return {url: max(seed.get(url, 0), retained.get(url, 0)) for url in urls}


def _run_identity(
    report: Mapping[str, Any],
    expected_dates: Sequence[str],
    route: str,
    shared_budget_path: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "ledger_schema": report.get("schema"),
        "ledger_start": report.get("start"),
        "ledger_as_of": report.get("as_of"),
        "latest_completed_session": report.get("latest_completed_session"),
        "expected_session_count": (report.get("denominator") or {}).get("expected_session_count"),
        "expected_dates_sha256": hashlib.sha256(canonical_json({"dates": list(expected_dates)})).hexdigest(),
        "route": route,
        "shared_budget_path": str(shared_budget_path),
        "config": dict(config),
    }


def run_bounded_checkpoint(
    report: Mapping[str, Any],
    *,
    output_root: str | Path,
    transport: Transport,
    route: str,
    expected_session_dates: Sequence[str],
    shared_budget_path: str | Path,
    existing_cash_roots: Sequence[str | Path] = (),
    targets: Sequence[Mapping[str, Any]] | None = None,
    seed_counts: Mapping[str, int] | None = None,
    seed_state_path: str | Path | None = None,
    now: Now = _utc_now,
    sleep: Sleep = _sleep,
    max_attempts_per_url: int = MAX_ATTEMPTS_PER_URL,
    min_start_spacing_seconds: float = MIN_START_SPACING_SECONDS,
    max_elapsed_seconds: int = MAX_ELAPSED_SECONDS,
    connect_timeout_seconds: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: float = READ_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run the bounded checkpoint, or refuse in the presence of blockers."""
    decision = assess_checkpoint(report)
    if decision["status"] != "READY":
        return {
            "schema": RUN_SCHEMA,
            "status": "NOT_RUN",
            "reason": decision["reason"],
            "denominator_qualified": False,
            "expected_session_count": decision["expected_session_count"],
            "recovered_session_denominator": None,
            "recovered_dates": [],
            "decision": decision,
            "acquisition": {**decision["acquisition"], "started": False},
            "reference_convention": decision["reference_convention"],
            "strategy_outcomes_inspected": False,
        }

    config = _validated_config(
        max_attempts_per_url=max_attempts_per_url,
        min_start_spacing_seconds=min_start_spacing_seconds,
        max_elapsed_seconds=max_elapsed_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
    )

    expected_dates = _normalize_expected_dates(report, expected_session_dates)
    if len(expected_dates) != decision["expected_session_count"]:
        raise CheckpointError(
            "report expected_session_count does not match the supplied expected session dates"
        )

    if targets is not None:
        target_list = _target_rows(targets, expected_dates, default_route=route)
    else:
        target_list = [{"session_date": value, "route": route} for value in expected_dates]

    root = Path(output_root)
    budget_path = Path(shared_budget_path)
    new_cash_root = root / "cash-evidence"
    roots = [Path(value) for value in existing_cash_roots]
    if seed_counts is None:
        seed_counts = aggregate_seed_counts(seed_state_path, roots)

    identity = _run_identity(report, expected_dates, route, budget_path, config)
    run_id = hashlib.sha256(canonical_json(identity)).hexdigest()

    root.mkdir(parents=True, exist_ok=True)
    lock_handle = _lock(root / "checkpoint.lock")
    try:
        manifest_path = root / "run-manifest.json"
        if manifest_path.is_file():
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(stored, Mapping) or stored.get("run_id") != run_id or stored.get("identity") != identity:
                raise CheckpointError(
                    "resume run directory identity does not match the immutable ledger, expected sessions, "
                    "route or shared-budget configuration"
                )

        recovered = _recovered_map(expected_dates, roots + [new_cash_root])
        budget = SharedUrlBudget(
            budget_path,
            run_id=run_id,
            limit=config["max_attempts_per_url"],
            min_start_spacing_seconds=config["min_start_spacing_seconds"],
            seed_counts=seed_counts,
        )
        started_at = budget.resolve_run_start(now)
        deadline = started_at + timedelta(seconds=config["max_elapsed_seconds"])
        budget.set_deadline(deadline)

        if not manifest_path.is_file():
            _atomic_write_json(
                manifest_path,
                {"schema": RUN_SCHEMA, "run_id": run_id, "identity": identity, "started_at": _timestamp(started_at)},
            )

        guarded = budget.guard(transport, now=now, sleep=sleep)

        invocation_started = now()
        invocation_reservations = len(budget.reservations_for_run(run_id))
        results: list[dict[str, Any]] = []
        stopped_reason: str | None = None
        remaining = list(target_list)

        def publish(*, final: bool) -> dict[str, Any]:
            reservations = budget.reservations_for_run(run_id)
            tally = tally_receipts(new_cash_root) if new_cash_root.exists() else {
                "attempts": 0, "accepted": 0, "rejected_responses": 0, "failures": 0,
            }
            recovered_count = len(recovered)
            ratio = (recovered_count / decision["expected_session_count"]) if decision["expected_session_count"] else 0.0
            if final:
                status = "COMPLETE" if recovered_count == decision["expected_session_count"] else (
                    "STOPPED" if stopped_reason else "PARTIAL"
                )
                convention_block: dict[str, Any] = {
                    "selected": REFERENCE_OPENING if ratio >= RECOVERY_THRESHOLD else REFERENCE_CLOSING,
                    "basis": (
                        "recovered-session ratio meets the 98% threshold; the opening convention is retained"
                        if ratio >= RECOVERY_THRESHOLD
                        else "recovered-session ratio is below 98%; the closing convention is selected"
                    ),
                    "admits_missing_sessions": False,
                }
                phase = "terminal"
            else:
                status = "IN_PROGRESS"
                convention_block = {
                    "selected": None,
                    "basis": (
                        "checkpoint in progress; no reference convention is selected until a terminal decision"
                    ),
                    "admits_missing_sessions": False,
                    "provisional_recovery_ratio": ratio,
                }
                phase = "resume_snapshot"
            state = {
                "schema": RUN_SCHEMA,
                "run_id": run_id,
                "status": status,
                "phase": phase,
                "reason": stopped_reason or (
                    "expected-session denominator is qualified; execution is a separate authorized run"
                    if final
                    else "checkpoint in progress; this is a resumable snapshot, not a terminal decision"
                ),
                "denominator_qualified": True,
                "expected_session_count": decision["expected_session_count"],
                "recovered_session_denominator": recovered_count,
                "recovered_dates": sorted(recovered),
                "recovery_ratio": ratio,
                "unrecovered_session_count": decision["expected_session_count"] - recovered_count,
                "quarantined_url_budget_exhausted": budget.exhausted_urls(),
                "acquisition": {
                    "started": True,
                    "request_count": len(reservations),
                    "accepted_sessions": recovered_count,
                    "rejected_responses": tally["rejected_responses"],
                    "failures": tally["failures"],
                    "retained_attempts": tally["attempts"],
                    "elapsed_seconds": (now() - started_at).total_seconds(),
                    "max_elapsed_seconds": config["max_elapsed_seconds"],
                    "max_attempts_per_url": config["max_attempts_per_url"],
                    "min_start_spacing_seconds": config["min_start_spacing_seconds"],
                    "retry_waits_seconds": config["retry_waits_seconds"],
                    "connect_timeout_seconds": config["connect_timeout_seconds"],
                    "read_timeout_seconds": config["read_timeout_seconds"],
                    "clamped_requests": sum(
                        1
                        for record in reservations
                        if record.get("connect_timeout_seconds") != config["connect_timeout_seconds"]
                        or record.get("read_timeout_seconds") != config["read_timeout_seconds"]
                    ),
                },
                "invocation": {
                    "started_at": _timestamp(invocation_started),
                    "elapsed_seconds": (now() - invocation_started).total_seconds(),
                    "request_count": len(reservations) - invocation_reservations,
                    "targets": list(results),
                },
                "reference_convention": {
                    "recovery_threshold": RECOVERY_THRESHOLD,
                    **convention_block,
                },
                "strategy_outcomes_inspected": False,
            }
            _atomic_write_json(root / "resume-state.json", state)
            return state

        publish(final=False)
        while remaining:
            if now() >= deadline:
                stopped_reason = "cumulative eight-hour acquisition budget exhausted before next date"
                break
            target = remaining.pop(0)
            session_date = target["session_date"]
            if session_date in recovered:
                results.append({"session_date": session_date, "status": "reused"})
                continue
            try:
                outcome = collect_daily_cash(
                    session_date,
                    new_cash_root,
                    transport=guarded,
                    route=target["route"],
                    now=now,
                    sleep=sleep,
                    resume=True,
                    connect_timeout=config["connect_timeout_seconds"],
                    read_timeout=config["read_timeout_seconds"],
                )
            except AcquisitionDeadlineExceeded as exc:
                stopped_reason = str(exc)
                results.append({"session_date": session_date, "status": "deadline_stop", "reason": str(exc)})
                break
            except SharedBudgetExhausted as exc:
                results.append({"session_date": session_date, "status": "budget_exhausted", "reason": str(exc)})
            except Exception as exc:  # noqa: BLE001 - recorded as a resume fact, never fatal
                results.append({"session_date": session_date, "status": "failure", "reason": str(exc)})
            else:
                status = getattr(outcome, "status", "failure")
                outcome_reason = getattr(outcome, "reason", None)
                if status == "exhausted" and isinstance(outcome_reason, str) and "elapsed budget" in outcome_reason:
                    # The collector hit its own cumulative cap without ever
                    # reaching the guard, so the runner must stop the whole run
                    # rather than iterate every remaining date.
                    stopped_reason = outcome_reason
                    results.append({
                        "session_date": session_date,
                        "status": "deadline_stop",
                        "reason": outcome_reason,
                    })
                    break
                evidence = _find_recovered([new_cash_root], session_date)
                if evidence is not None:
                    recovered[session_date] = evidence
                results.append({
                    "session_date": session_date,
                    "status": status,
                    "attempts": int(getattr(outcome, "attempts", 0) or 0),
                    "reason": outcome_reason,
                })
            publish(final=False)

        for target in remaining:
            session_date = target["session_date"]
            if session_date not in recovered:
                results.append({"session_date": session_date, "status": "not_attempted"})
        return publish(final=True)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def missing_session_targets(
    rows: Sequence[Mapping[str, Any]],
    *,
    route: str,
    existing_cash_roots: Sequence[str | Path] = (),
) -> list[dict[str, str]]:
    """Expected sessions no supplied cash root can re-validate are the targets."""
    roots = [Path(value) for value in existing_cash_roots]
    targets: list[dict[str, str]] = []
    for row in rows:
        if row.get("state") != "expected":
            continue
        session_date = str(row.get("session_date"))
        if any(verify_retained_session(root, session_date) is not None for root in roots):
            continue
        targets.append({"session_date": session_date, "route": route})
    return targets


__all__ = [
    "AcquisitionDeadlineExceeded",
    "BUDGET_SCHEMA",
    "BLOCKING_SCOPES",
    "CheckpointError",
    "DECISION_SCHEMA",
    "LEDGER_SCHEMA",
    "MAX_ATTEMPTS_PER_URL",
    "MAX_ELAPSED_SECONDS",
    "MIN_START_SPACING_SECONDS",
    "QUALIFICATION_SCOPES",
    "RECOVERY_THRESHOLD",
    "REFERENCE_CLOSING",
    "REFERENCE_OPENING",
    "RUN_SCHEMA",
    "SharedBudgetExhausted",
    "SharedUrlBudget",
    "aggregate_seed_counts",
    "assess_checkpoint",
    "missing_session_targets",
    "run_bounded_checkpoint",
]
