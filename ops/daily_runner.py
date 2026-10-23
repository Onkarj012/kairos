"""Daily paper runner: the signal, place and reconcile jobs over the four paper accounts.

Each job runs on NSE sessions only and records one run marker per (job, session).
A job that finds its predecessor's marker missing, or runs outside its window,
halts every account. A halted account is frozen: no settles and no intents until
``clear``. After a clear, the next job settles the missed sessions in order, which
gives the same result as settling them on time because a settle reads only
official prices.

Paper settles happen in ``signal``, right after it retains the session's
bhavcopy. ``reconcile`` at 18:45 runs before NSE publishes that file, so in paper
it checks the account logs and settles only if the data is already there.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from evidence.daily_backtest import run_backtest
from evidence.daily_session_ledger import load_calendar_evidence
from evidence.research_ledger import LedgerError, ResearchEvent, ResearchLedgerWriter, read_events
from evidence.sealed_test import HOLDOUT_START, load_dev_panel
from ingest.calendar import parse_holiday_rows
from ingest.daily_cash import CASH_URL_ROUTES, collect_daily_cash, request_directory_for_route, verify_retained_session
from ingest.daily_panel import EXCLUDED_SESSIONS, PANEL_FILE, START, derived_root, replace_directory
from models.daily_baselines import STRATEGIES, Candidate, cohort_by_month, rank_session, trading_sessions
from ops.alerts import send_alert
from ops.daily_account import ACCOUNTS_DIR, PAPER_ACCOUNTS, AccountSpec, AccountView, DailyAccount
from ops.daily_rules import HOLD_SESSIONS, AccountEvent, Bar, Intent, plan_session, settle_session
from ops.halts import kill_switch_path
from ops.kite_broker import KiteReader, dry_run_place
from ops.notify import APPROVALS_URL, Notifier, NotifyError
from ops.telegram import Button, Telegram, TelegramError, callback_chat, callback_data, parse_callback, read_offset, write_offset

IST = ZoneInfo("Asia/Kolkata")
JOBS = ("place", "reconcile", "signal")
WINDOWS = {
    "place": (time(8, 45), time(9, 5)),
    "reconcile": (time(18, 45), time(19, 25)),
    "signal": (time(19, 30), time(21, 45)),
}
# Answers to a signal's intents close here on the session after the signal, before the place job at 08:45.
ANSWER_CUTOFF = time(8, 40)
# Each signal slot may cost up to three requests (the collector's per-call retry cap).
FETCH_SLOTS = (
    (time(19, 30), "archives_full_bhavdata", False),
    (time(20, 15), "archives_full_bhavdata", True),
    (time(21, 0), "nsearchives_full_bhavdata", False),
    (time(21, 30), "nsearchives_udiff_zip", False),
)
CALL_ATTEMPTS = 3
NIGHT_REQUEST_BUDGET = 6
LOSS_LIMIT_TOTAL = 0.10
LOSS_LIMIT_MONTH = 0.05
MOVE_GUARD = 0.35
REPLAY_FIRST_SIGNAL = date(2025, 8, 28)
REPLAY_LAST_SESSION = date(2025, 9, 30)


class CalendarMissing(RuntimeError):
    """Raised when no retained NSE holiday calendar covers a needed day."""


@dataclass(frozen=True)
class RunnerPaths:
    """Every file the runner reads or writes, under one derived-data root."""

    root: Path

    @property
    def runner(self) -> Path:
        return self.root / "daily-runner"

    @property
    def frozen_ledger(self) -> Path:
        return self.runner / "session-ledger-v1/sessions.jsonl"

    @property
    def live_ledger(self) -> Path:
        return self.runner / "session-ledger-live-v1/sessions.jsonl"

    @property
    def frozen_panel(self) -> Path:
        return self.runner / "panel-v1" / PANEL_FILE

    @property
    def live_panel_dir(self) -> Path:
        return self.runner / "panel-live-v1"

    @property
    def frozen_cohort(self) -> Path:
        return self.runner / "cohort-v1/cohort.parquet"

    @property
    def live_cohort_dir(self) -> Path:
        return self.runner / "cohort-live-v1"

    @property
    def covered_ids(self) -> Path:
        return self.runner / "cohort-v1/ever_members.csv"

    @property
    def actions_manifest(self) -> Path:
        return self.runner / "corporate-actions-v1/manifest.json"

    @property
    def cash_root(self) -> Path:
        return self.runner / "cash-evidence-v1"

    @property
    def accounts(self) -> Path:
        return self.root / ACCOUNTS_DIR

    @property
    def runs(self) -> Path:
        return self.runner / "runner-v1/runs.jsonl"

    @property
    def telegram_offset(self) -> Path:
        return self.runner / "runner-v1/telegram-offset.json"

    @property
    def telegram_intents_posted(self) -> Path:
        return self.runner / "runner-v1/telegram-intents-posted.json"

    @property
    def telegram_halts_posted(self) -> Path:
        return self.runner / "runner-v1/telegram-halts-posted.json"

    @property
    def mac_intents_posted(self) -> Path:
        return self.runner / "runner-v1/mac-intents-posted.json"

    @property
    def mac_halts_posted(self) -> Path:
        return self.runner / "runner-v1/mac-halts-posted.json"

    @property
    def research_ledger(self) -> Path:
        return self.runner / "research-ledger-v1/events.jsonl"

    @property
    def calendar_root(self) -> Path:
        return self.root / "daily-graph/calendar-evidence-v1"

    @property
    def kite_dry_run(self) -> Path:
        return self.runner / "runner-v1/kite-dry-run"


def default_paths() -> RunnerPaths:
    """Return paths under the configured derived-data root."""
    return RunnerPaths(derived_root())


@dataclass(frozen=True)
class Calendar:
    """Session states from the frozen ledger, extended forward from retained annual holiday PDFs."""

    frozen: dict[date, str]
    holidays: dict[int, dict[date, str]]

    @property
    def frozen_end(self) -> date:
        return max(self.frozen)

    def state(self, day: date) -> str:
        """Return 'expected' or 'non_session' for one calendar day."""
        if day in self.frozen:
            return self.frozen[day]
        year = self.holidays.get(day.year)
        if year is None:
            raise CalendarMissing(f"no retained NSE holiday calendar for {day.year}")
        kind = year.get(day)
        if kind is not None:
            return "non_session" if kind == "holiday" else "expected"
        return "non_session" if day.weekday() >= 5 else "expected"

    def is_session(self, day: date) -> bool:
        """Return whether a day is a trading session the runner settles."""
        return self.state(day) == "expected" and day.isoformat() not in EXCLUDED_SESSIONS

    def sessions_after(self, day: date, count: int) -> list[date]:
        """Return the next count sessions after a day."""
        found: list[date] = []
        current = day
        while len(found) < count:
            current += timedelta(days=1)
            if self.is_session(current):
                found.append(current)
        return found

    def previous_session(self, day: date) -> date:
        """Return the last session before a day."""
        current = day - timedelta(days=1)
        while not self.is_session(current):
            current -= timedelta(days=1)
        return current


def load_calendar(paths: RunnerPaths) -> Calendar:
    """Read the frozen ledger states and the retained annual holiday rows."""
    frozen = {
        date.fromisoformat(row["session_date"]): str(row["state"])
        for row in map(json.loads, paths.frozen_ledger.read_text(encoding="utf-8").splitlines())
    }
    if shutil.which("pdftotext") is None:
        raise CalendarMissing("pdftotext is not on PATH, so the retained NSE holiday PDFs cannot be read")
    holidays: dict[int, dict[date, str]] = {}
    annuals, _notices, _sources = load_calendar_evidence(paths.calendar_root)
    for annual in annuals:
        rows = parse_holiday_rows(annual.text, year=annual.year)
        if rows:
            holidays[annual.year] = {date.fromisoformat(r["session_date"]): str(r.get("session_type", "holiday")) for r in rows}
    return Calendar(frozen, holidays)


def write_live_ledger(paths: RunnerPaths, calendar: Calendar, through: date) -> None:
    """Write the frozen ledger plus one calendar-derived row per day after it, through a date."""
    lines = paths.frozen_ledger.read_text(encoding="utf-8").splitlines()
    day = calendar.frozen_end + timedelta(days=1)
    while day <= through:
        lines.append(json.dumps({
            "session_date": day.isoformat(),
            "state": calendar.state(day),
            "source": "kairos runner: weekday rule plus retained NSE annual holiday calendar",
        }, sort_keys=True, separators=(",", ":")))
        day += timedelta(days=1)
    _write_atomic(paths.live_ledger, ("\n".join(lines) + "\n").encode("utf-8"))


def _write_atomic(path: Path, body: bytes) -> None:
    """Replace a file in one rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def ledger_sessions(path: Path) -> list[date]:
    """Return trading sessions from START in a ledger file, without excluded sessions."""
    expected = {
        date.fromisoformat(row["session_date"])
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())
        if row.get("state") == "expected"
    }
    return trading_sessions(sorted(session for session in expected if session >= START))


@dataclass
class Market:
    """Panel, sessions and cohorts the jobs read, with per-session price lookups."""

    panel: pd.DataFrame
    sessions: list[date]
    cohorts: dict[str, list[str]]
    guard_from: date
    covered: set[str]
    calendar: Calendar | None = None
    indexed: pd.DataFrame = field(init=False)
    present: set[date] = field(init=False)
    _days: dict[date, pd.DataFrame] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.indexed = self.panel.set_index(["security_id", "session_date"]).sort_index()
        self.present = set(self.panel["session_date"].unique())

    def day(self, session: date) -> pd.DataFrame:
        """Return one session's rows indexed by security id."""
        if session not in self._days:
            self._days[session] = self.panel.loc[self.panel["session_date"] == session].set_index("security_id")
        return self._days[session]

    def bar(self, session: date) -> Callable[[str], Bar | None]:
        """Return a price lookup for one session in the backtest's float order."""
        day = self.day(session)

        def lookup(security_id: str) -> Bar | None:
            """Return the official prices for one id, or None when it has no row."""
            if security_id not in day.index:
                return None
            row = day.loc[security_id]
            return Bar(
                float(row["open"]),
                float(row["close"]),
                float(row["prev_close"]) * float(row["prev_close_factor"]) / float(row["adj_factor"]),
                float(row["adj_factor"]),
            )

        return lookup

    def closes(self, session: date) -> dict[str, float]:
        """Return every id's close for one session."""
        return {str(sid): float(close) for sid, close in self.day(session)["close"].items()}

    def exit_due_after(self, entry: str) -> str:
        """Return the session three sessions after an entry."""
        day = date.fromisoformat(entry)
        if self.calendar is not None:
            return self.calendar.sessions_after(day, HOLD_SESSIONS)[-1].isoformat()
        index = self.sessions.index(day) + HOLD_SESSIONS
        if index >= len(self.sessions):
            raise ValueError(f"no session {HOLD_SESSIONS} after {entry}")
        return self.sessions[index].isoformat()

    def unexplained(self, ids: list[str], session: date, lookback: int) -> set[str]:
        """Ids with a raw move beyond the guard that no known corporate action explains."""
        end = self.sessions.index(session)
        window = set(self.sessions[max(0, end - lookback + 1) : end + 1])
        rows = self.panel.loc[self.panel["security_id"].isin(ids) & self.panel["session_date"].isin(window)]
        return _unexplained_ids(rows, self.guard_from, self.covered)

    def rank(self, strategy_name: str, session: date) -> tuple[list[Candidate], list[str], list[str]]:
        """Rank the session's cohort without guarded ids; return candidates, excluded ids and the cohort."""
        strategy = STRATEGIES[strategy_name]
        ids = self.cohorts.get(session.strftime("%Y-%m"), [])
        excluded = self.unexplained(ids, session, strategy.lookback_sessions) if ids else set()
        kept = [sid for sid in ids if sid not in excluded]
        return rank_session(strategy, self.indexed, self.sessions, session, kept), sorted(excluded), ids


def _unexplained_ids(rows: pd.DataFrame, guard_from: date, covered: set[str]) -> set[str]:
    """Flag rows moving beyond the guard with no action on the day, after action coverage or for uncovered ids."""
    if rows.empty:
        return set()
    move = (rows["close"] / rows["prev_close"] - 1).abs()
    gap = (rows["open"] / rows["prev_close"] - 1).abs()
    no_action = rows["prev_close_factor"] == rows["adj_factor"]
    uncovered = (rows["session_date"] > guard_from) | ~rows["security_id"].isin(covered)
    flagged = rows.loc[((move > MOVE_GUARD) | (gap > MOVE_GUARD)) & no_action & uncovered]
    return set(flagged["security_id"].astype(str))


def load_market(paths: RunnerPaths, *, live: bool, calendar: Calendar | None = None) -> Market:
    """Load the live panel and cohort, or the frozen development panel for replay."""
    if live:
        panel = pd.read_parquet(paths.live_panel_dir / PANEL_FILE)
        cohort = pd.read_parquet(paths.live_cohort_dir / "cohort.parquet")
        sessions = ledger_sessions(paths.live_ledger)
    else:
        panel = load_dev_panel(paths.frozen_panel)
        cohort = pd.read_parquet(paths.frozen_cohort)
        sessions = [s for s in ledger_sessions(paths.frozen_ledger) if s < HOLDOUT_START]
    panel["security_id"] = panel["security_id"].astype(str)
    panel = panel.sort_values(["security_id", "session_date"]).reset_index(drop=True)
    manifest = json.loads(paths.actions_manifest.read_text(encoding="utf-8"))
    covered = set(pd.read_csv(paths.covered_ids, dtype={"security_id": str}, keep_default_na=False)["security_id"])
    return Market(
        panel=panel,
        sessions=sessions,
        cohorts=cohort_by_month(cohort),
        guard_from=date.fromisoformat(manifest["range"]["end"]),
        covered=covered,
        calendar=calendar if live else None,
    )


def _loss_halt(view: AccountView) -> tuple[str, str] | None:
    """Return a loss-limit halt when the latest mark crosses the total or monthly limit."""
    marks = [(event.session, float(event.data["equity"])) for event in view.engine_events if event.kind == "RECONCILED"]
    if not marks:
        return None
    capital = view.state.capital
    session, equity = marks[-1]
    previous = marks[-2][1] if len(marks) > 1 else capital
    month = session[:7]
    month_start = next((value for day, value in reversed(marks) if day[:7] < month), capital)
    previous_in_month = len(marks) > 1 and marks[-2][0][:7] == month
    total_limit = LOSS_LIMIT_TOTAL * capital
    month_limit = LOSS_LIMIT_MONTH * capital
    if capital - equity >= total_limit and capital - previous < total_limit:
        return "loss_limit_total", f"equity {equity:.2f} is {capital - equity:.2f} below capital {capital:.0f}; limit {total_limit:.0f}"
    month_before = month_start - previous if previous_in_month else 0.0
    if month_start - equity >= month_limit and month_before < month_limit:
        return "loss_limit_month", f"equity {equity:.2f} is {month_start - equity:.2f} below the {month} start {month_start:.2f}; limit {month_limit:.0f}"
    return None


def _held_moves(view: AccountView, market: Market, session: date) -> set[str]:
    """Return held ids with an unexplained move on a session after action coverage."""
    if session <= market.guard_from:
        return set()
    ids = [holding.security_id for holding in view.state.holdings]
    day = market.day(session)
    rows = day.loc[day.index.intersection(ids)].reset_index()
    return _unexplained_ids(rows, market.guard_from, market.covered)


def account_opened(account: DailyAccount) -> str:
    """Return the first session an account settles."""
    return str(read_events(account.path)[0].payload["session"])


def _check_move(account: DailyAccount, view: AccountView, market: Market, session: date) -> bool:
    """Halt on held ids with an unexplained move on a session, leaving out ids an earlier halt there already covered."""
    cleared = view.halt_history.get((session.isoformat(), "unexplained_move"), set())
    moved = sorted(_held_moves(view, market, session) - cleared)
    if not moved:
        return False
    account.halt(view, session=session.isoformat(), reason="unexplained_move",
                 detail=f"held {', '.join(moved)} moved beyond {MOVE_GUARD:.0%} with no known action", ids=moved)
    return True


def _check_loss(account: DailyAccount, view: AccountView) -> bool:
    """Halt on a loss-limit crossing at the latest mark unless that crossing was already halted once."""
    loss = _loss_halt(view)
    if not loss:
        return False
    session = next(e.session for e in reversed(view.engine_events) if e.kind == "RECONCILED")
    if (session, loss[0]) in view.halt_history:
        return False
    account.halt(view, session=session, reason=loss[0], detail=loss[1])
    return True


def _check_settled(account: DailyAccount, view: AccountView, market: Market) -> bool:
    """Run the halts due after a settle on the last settled session; return whether one fired."""
    if view.state.last_session is None:
        return False
    # Entries filled at that session's open were not held for the check before the settle.
    return _check_move(account, view, market, date.fromisoformat(view.state.last_session)) or _check_loss(account, view)


def advance(account: DailyAccount, view: AccountView, market: Market, through: date, *, plan: bool, final: bool = False) -> AccountView:
    """Settle every unsettled session through a date, then plan that date's entries when asked.

    ``final`` closes positions with no exit data at ``through``, as the backtest does on its last session.
    """
    opened = account_opened(account)
    pending = [s for s in market.sessions if opened <= s.isoformat() <= through.isoformat() and s.isoformat() not in view.settled]
    # A crash between RECONCILED and its halts leaves them unwritten; rerun them before moving on.
    if not view.halted and _check_settled(account, view, market):
        return view
    for session in pending:
        if view.halted:
            return view
        if session not in market.present:
            account.halt(view, session=session.isoformat(), reason="missing_session_data", detail=f"no panel rows for {session}")
            return view
        if _check_move(account, view, market, session):
            return view
        events = settle_session(view.state, session.isoformat(), market.bar(session), market.exit_due_after,
                                final=final and session == through, require_sent=True)
        account.append(events)
        view.engine_events.extend(events)
        view.settled.add(session.isoformat())
        if _check_settled(account, view, market):
            return view
    day = through.isoformat()
    if not plan or view.halted or day in view.signalled or day not in view.settled:
        return view
    ranked, excluded, cohort = market.rank(account.spec.strategy, through)
    if not cohort:
        account.halt(view, session=day, reason="cohort_missing", detail=f"no cohort for {through:%Y-%m}")
        return view
    events: list[AccountEvent] = plan_session(view.state, day, ranked, market.closes(through))
    events.append(AccountEvent("SIGNAL", day, {
        "strategy": account.spec.strategy,
        "cohort_month": f"{through:%Y-%m}",
        "cohort_size": len(cohort),
        "excluded_unexplained": excluded,
        "top": [{"security_id": c.security_id, "score": float(c.score)} for c in ranked[:10]],
        "intents": len(events),
    }))
    account.append(events)
    view.signalled.add(day)
    return view


def _sendable(view: AccountView, intent_id: str) -> bool:
    """Return whether an intent may go out: never once rejected, and only once approved when the account requires it."""
    decision = view.decisions.get(intent_id)
    return decision == "approved" if view.spec.approval == "required" else decision != "rejected"


def place(account: DailyAccount, view: AccountView, session: date, signal_session: date) -> int:
    """Mark the previous signal's sendable intents as sent; paper sends no real order, and the rest expire at settle."""
    events = [
        AccountEvent("ORDER_SENT", session.isoformat(), {"intent_id": i.intent_id, "security_id": i.security_id, "qty": i.qty, "mode": "paper"})
        for i in view.state.intents
        if not i.sent and i.signal_session == signal_session.isoformat() and _sendable(view, i.intent_id)
    ]
    account.append(events)
    return len(events)


def stage1_verdict(paths: RunnerPaths, strategy: str) -> str:
    """Return the strategy's stage 1 dev-fold verdict from the research ledger."""
    identity = f"{strategy}@{STRATEGIES[strategy].version}"
    verdicts = [e for e in read_events(paths.research_ledger) if e.event_type == "GATE_VERDICT" and e.payload.get("strategy") == identity]
    if not verdicts:
        return "no verdict"
    return "eligible" if verdicts[-1].payload.get("eligible") else "not eligible"


class RunLog:
    """Run markers: one JOB_SUCCEEDED per (job, session), JOB_RECOVERED after a manual start or clear."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.writer = ResearchLedgerWriter(path)

    def markers(self) -> list[ResearchEvent]:
        """Return the markers in log order."""
        return [e for e in read_events(self.path) if e.event_type in ("JOB_SUCCEEDED", "JOB_RECOVERED")]

    def has(self, job_id: str) -> bool:
        """Return whether a job finished its slot; a halted run reopens after a later manual recovery."""
        status: str | None = None
        for event in self.markers():
            if event.event_type == "JOB_SUCCEEDED" and event.job_id == job_id:
                status = str(event.payload["status"])
            elif event.event_type == "JOB_RECOVERED" and status == "halted":
                status = None
        return status is not None

    def predecessor_ok(self, predecessor: str) -> tuple[bool, str]:
        """Check that the last marker is the predecessor slot or a manual recovery."""
        markers = self.markers()
        if not markers:
            return False, "no run markers; run `start` first"
        last = markers[-1]
        if last.job_id == predecessor:
            return True, ""
        if last.event_type == "JOB_RECOVERED":
            # A recovery only stands in for the slot right before it; an older one means a slot was missed since.
            job, session = predecessor.split(":", 1)
            slot_start = datetime.combine(date.fromisoformat(session), WINDOWS[job][0], tzinfo=IST)
            at = datetime.fromisoformat(str(last.payload["at"]))
            if (at if at.tzinfo else at.replace(tzinfo=IST)) >= slot_start:
                return True, ""
            return False, f"expected {predecessor} before this run; the last marker is a recovery from {last.payload['at']}"
        return False, f"expected {predecessor} before this run, last marker is {last.job_id}"

    def mark(self, job_id: str, status: str, detail: str) -> None:
        """Record a job's marker for its slot; a rerun after a recovery gets its own event id."""
        runs = sum(1 for e in self.markers() if e.event_type == "JOB_SUCCEEDED" and e.job_id == job_id)
        self.writer.append(event_id=job_id if runs == 0 else f"{job_id}:run-{runs + 1}", event_type="JOB_SUCCEEDED",
                           study_id="daily-runner", job_id=job_id, attempt=runs + 1, payload={"status": status, "detail": detail})

    def waiting(self, job_id: str, slot: int, detail: str) -> None:
        """Record a signal slot that ended without the bhavcopy."""
        self.writer.append(event_id=f"{job_id}:slot-{slot + 1}", event_type="JOB_RETRY_SCHEDULED", study_id="daily-runner",
                           job_id=job_id, attempt=slot + 1, payload={"detail": detail})

    def recovered(self, note: str, at: datetime) -> None:
        """Record a manual start or clear that resets the predecessor check."""
        count = sum(1 for e in self.markers() if e.event_type == "JOB_RECOVERED")
        self.writer.append(event_id=f"recovered:{count + 1}", event_type="JOB_RECOVERED", study_id="daily-runner",
                           job_id="manual", attempt=1, payload={"note": note, "at": at.isoformat()})


def _predecessor(job: str, session: date, calendar: Calendar) -> str:
    """Return the slot that must run right before a job's slot."""
    if job == "place":
        return f"signal:{calendar.previous_session(session).isoformat()}"
    if job == "reconcile":
        return f"place:{session.isoformat()}"
    return f"reconcile:{session.isoformat()}"


class CashSource(Protocol):
    """Where the signal job gets each session's bhavcopy."""

    def retained(self, session: date) -> bool: ...
    def spent(self, session: date) -> int: ...
    def fetch(self, session: date, route: str, resume: bool) -> str: ...


@dataclass(frozen=True)
class NseCash:
    """Retained NSE Full Bhavdata through the stage 0 collector."""

    root: Path

    def retained(self, session: date) -> bool:
        """Return whether an accepted, re-verified bhavcopy exists for a session."""
        return verify_retained_session(self.root, session) is not None

    def spent(self, session: date) -> int:
        """Count the requests already spent on a session across routes."""
        return sum(len(list(request_directory_for_route(self.root, session, route).glob("attempt-*"))) for route in CASH_URL_ROUTES)

    def fetch(self, session: date, route: str, resume: bool) -> str:
        """Request one session's file on one route; return the collector's status and reason."""
        result = collect_daily_cash(session, self.root, route=route, resume=resume)
        return f"{result.status} {result.reason or ''}".strip()


def build_live_data(paths: RunnerPaths, calendar: Calendar, through: date) -> None:
    """Rewrite the live ledger, then rebuild the live panel and cohort from retained bytes."""
    from scripts.data import build_daily_panel, build_daily_universe

    write_live_ledger(paths, calendar, through)
    with redirect_stdout(sys.stderr):
        build_daily_panel.main(["--ledger", str(paths.live_ledger), "--out", str(paths.live_panel_dir)])
        build_daily_universe.main([
            "--panel", str(paths.live_panel_dir), "--ledger", str(paths.live_ledger), "--out", str(paths.live_cohort_dir),
        ])


@dataclass
class Runner:
    """Run one scheduled job at a given IST time."""

    paths: RunnerPaths
    now: datetime
    cash: CashSource | None = None
    calendar: Calendar | None = None
    build: Callable[[RunnerPaths, Calendar, date], None] = build_live_data
    log: Callable[[str], None] = print
    telegram: Telegram | None = None
    mac: Notifier | None = None
    kite: KiteReader | None = None

    def notify(self, text: str, rows: list[list[Button]] | None = None) -> bool:
        """Send to every configured channel; a failing channel never stops the others. Return whether any went out."""
        mac = self.notify_mac(text)
        telegram = self.notify_telegram(text, rows)
        return mac or telegram

    def notify_telegram(self, text: str, rows: list[list[Button]] | None = None) -> bool:
        """Post to Telegram when a bot is configured; return whether it went out."""
        if self.telegram is None:
            return False
        try:
            self.telegram.send(text, rows)
        except TelegramError as exc:
            send_alert("warning", "telegram post failed", root=self.paths.runner, error=str(exc))
            self.log(f"telegram post failed: {exc}")
            return False
        return True

    def notify_mac(self, text: str) -> bool:
        """Show a macOS banner when the channel is on; return whether it went out."""
        if self.mac is None:
            return False
        try:
            self.mac.send(text)
        except NotifyError as exc:
            send_alert("warning", "macOS notification failed", root=self.paths.runner, error=str(exc))
            self.log(f"macOS notification failed: {exc}")
            return False
        return True

    def open_halts(self) -> set[tuple[str, str, str, str, int]]:
        """Return every open halt as (account, session, reason, detail, clears before it)."""
        found: set[tuple[str, str, str, str, int]] = set()
        for account in self.accounts():
            if not account.exists():
                continue
            try:
                view = account.view()
            except (LedgerError, ValueError):
                continue
            found |= {(account.spec.account_id, str(h["session"]), str(h["reason"]), str(h["detail"]), view.clears) for h in view.halts}
        return found

    def post_halts(self) -> None:
        """Post each open halt once on each channel; a halt a channel failed to post retries on the next job."""
        halts = sorted(self.open_halts())
        if self.telegram is not None:
            self._post_halts_once(halts, self.paths.telegram_halts_posted, self.notify_telegram)
        if self.mac is not None:
            self._post_halts_once(halts, self.paths.mac_halts_posted, self.notify_mac)

    def _post_halts_once(self, halts: list[tuple[str, str, str, str, int]], path: Path, send: Callable[[str], bool]) -> None:
        """Send each halt group that one channel's posted file does not record yet, one message per reason and session."""
        posted = set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
        grouped: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
        for account_id, session, reason, detail, clears in halts:
            key = f"{account_id}|{session}|{reason}|{clears}"
            if key not in posted:
                grouped.setdefault((session, reason, detail), []).append((account_id, key))
        for (session, reason, detail), members in grouped.items():
            sent = send(
                f"HALT {reason} on {session}\n{detail}\nAccounts: {', '.join(a for a, _k in members)}\n"
                "New intents stop until you check and run: uv run python scripts/run_daily.py clear --note \"...\""
            )
            if sent:
                posted |= {k for _a, k in members}
                _write_atomic(path, (json.dumps(sorted(posted), indent=1) + "\n").encode("utf-8"))

    def accounts(self) -> list[DailyAccount]:
        """Return the four paper accounts."""
        return [DailyAccount(self.paths.accounts, spec) for spec in PAPER_ACCOUNTS]

    def halt_all(self, session: date, reason: str, detail: str) -> None:
        """Halt every opened account and write an alert."""
        for account in self.accounts():
            if account.exists():
                account.halt(account.view(), session=session.isoformat(), reason=reason, detail=detail)
        send_alert("error", f"daily runner halted: {reason}", root=self.paths.runner, session=session.isoformat(), detail=detail)
        self.log(f"HALT {reason}: {detail}")

    def ready_accounts(self) -> list[tuple[DailyAccount, AccountView]]:
        """Return opened accounts that are neither kill-switched nor halted, with their views."""
        ready: list[tuple[DailyAccount, AccountView]] = []
        for account in self.accounts():
            if not account.exists():
                continue
            switch = kill_switch_path(self.paths.runner, account.spec.account_id)
            if switch.exists():
                self.log(f"{account.spec.account_id}: kill-switch present at {switch}, skipped")
                continue
            try:
                view = account.view()
            except (LedgerError, ValueError) as exc:
                switch.parent.mkdir(parents=True, exist_ok=True)
                switch.write_text(f"account log unreadable: {exc}\n", encoding="utf-8")
                send_alert("error", "account log unreadable; kill-switch written", root=self.paths.runner, account=account.spec.account_id, error=str(exc))
                self.log(f"{account.spec.account_id}: log unreadable, kill-switch written")
                continue
            if view.halted:
                self.log(f"{account.spec.account_id}: halted ({', '.join(str(h['reason']) for h in view.halts)}), skipped")
                continue
            ready.append((account, view))
        return ready

    def run(self, job: str) -> dict[str, object]:
        """Run one job for today's session, then post any halt it opened; return a short status."""
        if job not in JOBS:
            raise ValueError(f"unknown job {job}")
        result = self._run(job)
        self.post_halts()
        if job == "signal":
            # Later signal slots end as already_done; this still retries an intent post that failed.
            self.post_intents()
            self.announce_intents(self.now.date())
        return result

    def _run(self, job: str) -> dict[str, object]:
        """Run one job for today's session; return a short status."""
        today = self.now.date()
        runs = RunLog(self.paths.runs)
        try:
            calendar = self.calendar or load_calendar(self.paths)
            session_today = calendar.is_session(today)
        except CalendarMissing as exc:
            self.halt_all(today, "calendar_missing", str(exc))
            return {"job": job, "status": "halted", "detail": str(exc)}
        if not session_today:
            return {"job": job, "status": "no_session", "session": today.isoformat()}
        job_id = f"{job}:{today.isoformat()}"
        if runs.has(job_id):
            return {"job": job, "status": "already_done", "session": today.isoformat()}
        ok, detail = runs.predecessor_ok(_predecessor(job, today, calendar))
        if not ok:
            self.halt_all(today, "missed_run", detail)
            runs.mark(job_id, "halted", detail)
            return {"job": job, "session": today.isoformat(), "status": "halted", "detail": detail}
        start_at, end_at = WINDOWS[job]
        if not start_at <= self.now.time() <= end_at:
            late = f"{job} ran at {self.now:%H:%M} IST, outside {start_at:%H:%M}-{end_at:%H:%M}"
            self.halt_all(today, "late_run", late)
            runs.mark(job_id, "halted", late)
            return {"job": job, "session": today.isoformat(), "status": "halted", "detail": late}
        handler = {"place": self._place, "reconcile": self._reconcile, "signal": self._signal}[job]
        return {"job": job, "session": today.isoformat(), **handler(today, calendar, runs, job_id)}

    def _place(self, today: date, calendar: Calendar, runs: RunLog, job_id: str) -> dict[str, object]:
        """Record Telegram answers, then mark the last signal's sendable intents as sent for each ready account."""
        signal_session = calendar.previous_session(today)
        decided = self.read_answers(today, signal_session)
        sent = {account.spec.account_id: place(account, account.view(), today, signal_session) for account, _view in self.ready_accounts()}
        ready = set(sent)
        dry_run = 0
        try:
            dry_run = len(dry_run_place(self.paths.accounts, self.paths.kite_dry_run, today, self.kite, ready))
        except Exception as exc:
            send_alert("warning", "Kite dry-run failed", root=self.paths.runner, error=type(exc).__name__)
            self.log(f"Kite dry-run failed: {type(exc).__name__}")
        runs.mark(job_id, "ok", json.dumps({"decided": decided, "sent": sent, "dry_run": dry_run}, sort_keys=True))
        return {"status": "ok", "decided": decided, "sent": sent, "dry_run": dry_run}

    def read_answers(self, today: date, signal_session: date) -> int:
        """Record each Approve or Reject press on the last signal's open intents; return how many were recorded."""
        if self.telegram is None:
            self.log("telegram not configured; intents follow each account's approval setting")
            return 0
        ready = {account.spec.account_id: account for account, _view in self.ready_accounts()}
        recorded = 0
        # Telegram returns at most 100 updates a call; read every page so no rejection is left behind before sending.
        while True:
            try:
                updates = self.telegram.updates(read_offset(self.paths.telegram_offset))
            except TelegramError as exc:
                send_alert("warning", "telegram read failed", root=self.paths.runner, error=str(exc))
                self.log(f"telegram read failed: {exc}")
                break
            if not updates:
                break
            for update in updates:
                press = parse_callback(update)
                if press is None or callback_chat(update) != str(self.telegram.chat_id):
                    continue
                reply = record_answer(ready.get(press.account_id), press.account_id, press.intent_id, press.decision, today, signal_session,
                                      via="telegram", update_id=press.update_id)
                recorded += reply.startswith(("Approved", "Rejected"))
                try:
                    self.telegram.answer(press.callback_id, reply)
                except TelegramError as exc:
                    self.log(f"telegram answer failed: {exc}")
            offset = max(int(str(u["update_id"])) for u in updates) + 1
            if offset <= (read_offset(self.paths.telegram_offset) or 0):
                break  # the offset stopped moving; never loop on a misbehaving API
            write_offset(self.paths.telegram_offset, offset)
        return recorded

    def _reconcile(self, today: date, calendar: Calendar, runs: RunLog, job_id: str) -> dict[str, object]:
        """Verify account logs and settle today when the live panel already has it."""
        ready = self.ready_accounts()
        settled: list[str] = []
        if ready and (self.paths.live_panel_dir / PANEL_FILE).exists() and self.paths.live_ledger.exists():
            market = load_market(self.paths, live=True, calendar=calendar)
            if today in market.present:
                for account, view in ready:
                    advance(account, view, market, today, plan=False)
                    settled.append(account.spec.account_id)
        runs.mark(job_id, "ok", json.dumps({"checked": len(ready), "settled": settled}, sort_keys=True))
        return {"status": "ok", "checked": len(ready), "settled": settled}

    def _signal(self, today: date, calendar: Calendar, runs: RunLog, job_id: str) -> dict[str, object]:
        """Retain missing bhavcopies, rebuild live data, settle and plan each ready account."""
        slot = max(i for i, (start_at, _route, _resume) in enumerate(FETCH_SLOTS) if start_at <= self.now.time())
        final = slot == len(FETCH_SLOTS) - 1
        _start_at, route, resume = FETCH_SLOTS[slot]
        cash = self.cash or NseCash(self.paths.cash_root)
        missing: list[date] = []
        day = calendar.frozen_end + timedelta(days=1)
        while day <= today:
            if calendar.is_session(day) and not cash.retained(day):
                missing.append(day)
            day += timedelta(days=1)
        for session in missing:
            used = cash.spent(session)
            if used + CALL_ATTEMPTS > NIGHT_REQUEST_BUDGET:
                self.log(f"{session}: {used} requests spent; budget {NIGHT_REQUEST_BUDGET} leaves no room for another call")
                continue
            self.log(f"{session}: {route} -> {cash.fetch(session, route, resume)}")
        still = [s for s in missing if not cash.retained(s)]
        if still:
            detail = f"no accepted bhavcopy for {', '.join(s.isoformat() for s in still)} after slot {slot + 1}/{len(FETCH_SLOTS)}"
            # The collector's request budget per session is durable, so a spent session never refetches on its own.
            spent = [s.isoformat() for s in still if cash.spent(s) + CALL_ATTEMPTS > NIGHT_REQUEST_BUDGET]
            if spent:
                detail += f"; request budget spent for {', '.join(spent)}, retain those files by hand before clearing"
            if final:
                self.halt_all(today, "stale_bhavcopy", detail)
                runs.mark(job_id, "halted", detail)
                return {"status": "halted", "detail": detail}
            runs.waiting(job_id, slot, detail)
            return {"status": "waiting", "detail": detail}
        self.build(self.paths, calendar, today)
        market = load_market(self.paths, live=True, calendar=calendar)
        planned: dict[str, int] = {}
        for account, view in self.ready_accounts():
            try:
                view = advance(account, view, market, today, plan=True)
            except CalendarMissing as exc:
                account.halt(view, session=today.isoformat(), reason="calendar_missing", detail=str(exc))
                continue
            planned[account.spec.account_id] = sum(1 for i in view.state.intents if i.signal_session == today.isoformat())
        fetched = [s.isoformat() for s in missing]
        runs.mark(job_id, "ok", json.dumps({"fetched": fetched, "planned": planned}, sort_keys=True))
        return {"status": "ok", "fetched": fetched, "planned": planned}

    def post_intents(self) -> None:
        """Post each ready account's latest unsent intents with Approve and Reject buttons, once; failed posts retry next time."""
        if self.telegram is None:
            return
        path = self.paths.telegram_intents_posted
        posted = set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
        for account, view in self.ready_accounts():
            unsent = [i for i in view.state.intents if not i.sent]
            if not unsent:
                continue
            signal = max(i.signal_session for i in unsent)
            key = f"{account.spec.account_id}|{signal}"
            if key not in posted and self.notify_telegram(*intent_message(account.spec, signal, [i for i in unsent if i.signal_session == signal])):
                posted.add(key)
                _write_atomic(path, (json.dumps(sorted(posted), indent=1) + "\n").encode("utf-8"))

    def announce_intents(self, today: date) -> None:
        """Show one macOS banner for today's signal when a ready account has open intents; a failed banner retries on the next job."""
        if self.mac is None:
            return
        path = self.paths.mac_intents_posted
        posted = set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
        key = today.isoformat()
        if key in posted:
            return
        count = sum(1 for _account, view in self.ready_accounts() for i in view.state.intents if not i.sent and i.signal_session == key)
        if not count:
            return
        try:
            cutoff = answer_cutoff(self.calendar or load_calendar(self.paths), today)
            text = f"{count} intents for {cutoff:%a %Y-%m-%d}. Answer by {cutoff:%H:%M}: {APPROVALS_URL}"
        except CalendarMissing as exc:
            self.log(f"banner sent without the answer date: {exc}")
            text = f"{count} intents for the next session. Answer on the dashboard: {APPROVALS_URL}"
        if self.notify_mac(text):
            _write_atomic(path, (json.dumps(sorted(posted | {key}), indent=1) + "\n").encode("utf-8"))


def record_answer(account: DailyAccount | None, account_id: str, intent_id: str, decision: str, today: date,
                  signal_session: date, *, via: str, update_id: int | None = None) -> str:
    """Write one Approve or Reject while it is still in time; return the reply for whoever answered."""
    if account is None:
        return f"{account_id} is halted, kill-switched or unknown; nothing changed"
    view = account.view()
    intent = next((i for i in view.state.intents if i.intent_id == intent_id), None)
    if intent is None or intent.sent or intent.signal_session != signal_session.isoformat():
        return f"Too late for {intent_id}; nothing changed"
    if intent_id in view.decisions:
        return f"Already {view.decisions[intent_id]}: {intent.security_id}"
    kind = "INTENT_APPROVED" if decision == "approved" else "INTENT_REJECTED"
    data: dict[str, object] = {"intent_id": intent_id, "security_id": intent.security_id, "via": via}
    if update_id is not None:
        data["update_id"] = update_id
    account.append([AccountEvent(kind, today.isoformat(), data)])
    return f"{decision.capitalize()} {intent.security_id} for the {today.isoformat()} open"


def answer_cutoff(calendar: Calendar, signal_session: date) -> datetime:
    """Return when answers to a signal's intents close: 08:40 IST on the session after it."""
    return datetime.combine(calendar.sessions_after(signal_session, 1)[0], ANSWER_CUTOFF, tzinfo=IST)


def intent_message(spec: AccountSpec, signal: str, intents: list[Intent]) -> tuple[str, list[list[Button]]]:
    """Return the intent message text and its Approve and Reject buttons."""
    silence = "Silence sends them (paper, auto)." if spec.approval == "auto" else "Silence skips them."
    lines = [f"{spec.account_id}: {len(intents)} buy intent(s) for the next session's open", f"{spec.strategy}, signal {signal}"]
    rows: list[list[Button]] = []
    for number, intent in enumerate(intents, 1):
        lines.append(f"{number}. {intent.security_id} x{intent.qty}, about ₹{intent.reserved:,.0f} at the {intent.close_t:,.2f} close")
        approve, reject = callback_data("a", spec.account_id, intent.intent_id), callback_data("r", spec.account_id, intent.intent_id)
        if approve and reject:
            rows.append([Button(f"Approve {intent.security_id}", approve), Button(f"Reject {intent.security_id}", reject)])
        else:
            lines.append(f"   (id too long for buttons; {intent.security_id} follows the {spec.approval} setting)")
    lines.append(f"Answer before {ANSWER_CUTOFF:%H:%M}. {silence}")
    return "\n".join(lines), rows


def start(paths: RunnerPaths, now: datetime, note: str, calendar: Calendar | None = None) -> dict[str, str]:
    """Open any missing paper account at the next session and record a manual recovery marker."""
    calendar = calendar or load_calendar(paths)
    today = now.date()
    first = today if calendar.is_session(today) else calendar.sessions_after(today, 1)[0]
    opened: dict[str, str] = {}
    for spec in PAPER_ACCOUNTS:
        account = DailyAccount(paths.accounts, spec)
        if not account.exists():
            account.open(session=first.isoformat(), verdict=stage1_verdict(paths, spec.strategy))
            opened[spec.account_id] = first.isoformat()
    RunLog(paths.runs).recovered(note, now)
    return opened


def clear(paths: RunnerPaths, now: datetime, note: str, account_id: str | None = None) -> dict[str, list[str]]:
    """Clear open halts on one or all accounts and record a manual recovery marker."""
    cleared: dict[str, list[str]] = {}
    for spec in PAPER_ACCOUNTS:
        if account_id and spec.account_id != account_id:
            continue
        account = DailyAccount(paths.accounts, spec)
        if account.exists():
            reasons = account.clear_halts(session=now.date().isoformat(), note=note)
            if reasons:
                cleared[spec.account_id] = reasons
    RunLog(paths.runs).recovered(note, now)
    return cleared


def status(paths: RunnerPaths) -> list[dict[str, object]]:
    """Summarize each paper account's cash, holdings, halts and last settled session."""
    rows: list[dict[str, object]] = []
    for spec in PAPER_ACCOUNTS:
        account = DailyAccount(paths.accounts, spec)
        if not account.exists():
            rows.append({"account": spec.account_id, "opened": False})
            continue
        view = account.view()
        rows.append({
            "account": spec.account_id,
            "opened": account_opened(account),
            "last_settled": view.state.last_session,
            "cash": round(view.state.cash, 2),
            "equity": round(view.state.equity, 2),
            "holdings": [{"security_id": h.security_id, "qty": h.qty, "exit_due": h.exit_due} for h in view.state.holdings],
            "open_intents": {i.intent_id: view.decisions.get(i.intent_id, "no answer") for i in view.state.intents},
            "approval": account.spec.approval,
            "halts": view.halts,
            "kill_switch": kill_switch_path(paths.runner, spec.account_id).exists(),
        })
    return rows


def replay(market: Market, root: Path, specs: tuple[AccountSpec, ...], first_signal: date, last_session: date) -> dict[str, DailyAccount]:
    """Drive fresh accounts through place, settle and plan for each past session, as the jobs do."""
    first_index = market.sessions.index(first_signal)
    last_index = market.sessions.index(last_session)
    plan_until = last_index - HOLD_SESSIONS - 1
    accounts = {spec.account_id: DailyAccount(root, spec) for spec in specs}
    for account in accounts.values():
        account.open(session=first_signal.isoformat(), verdict="replay")
    for index in range(first_index, last_index + 1):
        session = market.sessions[index]
        for account in accounts.values():
            if index > first_index:
                place(account, account.view(), session, market.sessions[index - 1])
            advance(account, account.view(), market, session, plan=index <= plan_until, final=index == last_index)
    return accounts


def replay_check(paths: RunnerPaths, out_dir: Path, first_signal: date = REPLAY_FIRST_SIGNAL, last_session: date = REPLAY_LAST_SESSION) -> dict[str, object]:
    """Replay dev sessions through the paper accounts and compare trades and marks with run_backtest."""
    market = load_market(paths, live=False)
    window = market.panel.loc[market.panel["session_date"].between(first_signal, last_session)]
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        accounts = replay(market, staging / "accounts", PAPER_ACCOUNTS, first_signal, last_session)
        results: dict[str, dict[str, object]] = {}
        for spec in PAPER_ACCOUNTS:
            account = accounts[spec.account_id]
            expected = run_backtest(
                window,
                market.sessions,
                lambda session, name=spec.strategy: market.rank(name, session)[0],
                capital=spec.capital,
                first_signal=first_signal,
                last_session=last_session,
            )
            trades, equity = account.trades(), account.equity()
            folder = staging / spec.account_id
            folder.mkdir(parents=True, exist_ok=True)
            for name, frame in (("trades", trades), ("equity", equity), ("backtest_trades", expected.trades), ("backtest_equity", expected.equity)):
                frame.to_csv(folder / f"{name}.csv", index=False)
            results[spec.account_id] = {
                "filled": int((trades["qty"] > 0).sum()),
                "rows": len(trades),
                "trades_match": trades.to_csv(index=False) == expected.trades.to_csv(index=False),
                "equity_match": equity.to_csv(index=False) == expected.equity.to_csv(index=False),
                "halts": account.view().halts,
            }
        report = {
            "first_signal": first_signal.isoformat(),
            "last_session": last_session.isoformat(),
            "sessions": len([s for s in market.sessions if first_signal <= s <= last_session]),
            "accounts": results,
            "match": all(r["trades_match"] and r["equity_match"] for r in results.values()),
        }
        (staging / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def now_ist() -> datetime:
    """Return the current time in IST."""
    return datetime.now(IST)
