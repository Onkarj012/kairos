from __future__ import annotations

import json
import subprocess
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
from ops.notify import APPROVALS_URL, Notifier, NotifyError, mac_notifier
from ops.telegram import BotApi, Button, TelegramError, read_env

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

    account.clear_halts(session=SESSIONS[4].isoformat(), note="checked: real split")
    view = advance(account, account.view(), market, SESSIONS[4], plan=False)
    assert not view.halts
    assert SESSIONS[4].isoformat() in view.settled


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


def test_clearing_one_moved_id_still_halts_on_another_filled_that_session(tmp_path: Path) -> None:
    panel = _review_panel()
    on_s2 = panel["session_date"] == SESSIONS[2]
    panel.loc[on_s2 & panel["security_id"].isin(["A", "B"]), "close"] = 55.0
    market = _market(panel, guard_from=SESSIONS[0])
    picks = dict(PICKS)
    PICKS[SESSIONS[1]] = ["B"]
    try:
        account = replay(market, tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
        assert [h.get("ids") for h in account.view().halts] == [["A"]]
        account.clear_halts(session=SESSIONS[2].isoformat(), note="checked A")
        view = advance(account, account.view(), market, SESSIONS[2], plan=False)
    finally:
        PICKS.clear()
        PICKS.update(picks)
    assert [(h["session"], h.get("ids")) for h in view.halts] == [(SESSIONS[2].isoformat(), ["B"])]


def test_fill_move_left_unhalted_by_a_crash_halts_on_the_next_run(tmp_path: Path) -> None:
    panel = _review_panel()
    panel.loc[(panel["security_id"] == "A") & (panel["session_date"] == SESSIONS[1]), "close"] = 55.0
    market = _market(panel, guard_from=SESSIONS[0])
    account = DailyAccount(tmp_path, SPEC)
    account.open(session=SESSIONS[0].isoformat(), verdict="replay")
    advance(account, account.view(), market, SESSIONS[0], plan=True)
    place(account, account.view(), SESSIONS[1], SESSIONS[0])
    view = account.view()
    account.append(settle_session(view.state, SESSIONS[1].isoformat(), market.bar(SESSIONS[1]), market.exit_due_after, require_sent=True))
    view = advance(account, account.view(), market, SESSIONS[2], plan=True)  # crash came before the move check
    assert [(h["session"], h["reason"]) for h in view.halts] == [(SESSIONS[1].isoformat(), "unexplained_move")]
    assert SESSIONS[1].isoformat() not in view.signalled


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
    assert _run(paths, "reconcile", LIVE[2], "18:50")["status"] == "halted"  # place on LIVE[2] never ran
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

    cash.available.add(LIVE[0])  # retained by hand
    clear(paths, datetime(2026, 10, 21, 21, 35, tzinfo=IST), "retained by hand")
    assert _run(paths, "signal", LIVE[0], "21:40", cash)["status"] == "ok"
    assert all(LIVE[0].isoformat() in a.view().signalled for a in _accounts(paths))


def test_signal_skips_a_request_that_could_break_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    cash = FakeCash(set(), spent=4)
    assert _run(paths, "signal", LIVE[0], "19:31", cash)["status"] == "waiting"
    assert cash.calls == []


class FakeTelegram:
    """A bot that records posts and returns scripted button presses."""

    chat_id = "42"

    def __init__(self, presses: list[tuple[str, str]] | None = None, fail: bool = False, batch: int = 100) -> None:
        self.sent: list[tuple[str, list[list[Button]] | None]] = []
        self.answers: list[str] = []
        self.offsets: list[int | None] = []
        self.presses = presses or []
        self.fail = fail
        self.batch = batch

    def send(self, text: str, rows: list[list[Button]] | None = None) -> None:
        if self.fail:
            raise TelegramError("sendMessage failed with HTTP 502")
        self.sent.append((text, rows))

    def updates(self, offset: int | None) -> list[dict[str, object]]:
        self.offsets.append(offset)
        pending = [
            {"update_id": 100 + n, "callback_query": {"id": f"cb{n}", "data": data, "message": {"chat": {"id": int(chat)}}}}
            for n, (data, chat) in enumerate(self.presses)
            if offset is None or 100 + n >= offset
        ]
        return pending[: self.batch]

    def answer(self, callback_id: str, text: str) -> None:
        self.answers.append(text)


def _telegram_run(paths: RunnerPaths, job: str, session: date, at: str, bot: FakeTelegram) -> dict[str, object]:
    """Run one job with a fake bot attached."""
    now = datetime.fromisoformat(f"{session.isoformat()}T{at}").replace(tzinfo=IST)
    return Runner(paths, now, cash=FakeCash(set(LIVE)), calendar=CALENDAR, build=_build, log=lambda _m: None, telegram=bot).run(job)


def test_signal_posts_each_accounts_intents_with_approve_and_reject_buttons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    bot = FakeTelegram()
    _telegram_run(paths, "reconcile", LIVE[0], "18:50", bot)
    _telegram_run(paths, "signal", LIVE[0], "19:35", bot)
    assert len(bot.sent) == len(PAPER_ACCOUNTS)
    text, rows = bot.sent[0]
    assert "signal 2026-10-21" in text and "Silence sends them" in text
    assert [[b.data for b in row] for row in rows] == [
        ["a:paper-reversal_5d-50k:2026-10-21:A", "r:paper-reversal_5d-50k:2026-10-21:A"],
        ["a:paper-reversal_5d-50k:2026-10-21:B", "r:paper-reversal_5d-50k:2026-10-21:B"],
    ]


def test_a_failed_intent_post_is_retried_by_a_later_signal_slot_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    assert _telegram_run(paths, "signal", LIVE[0], "19:35", FakeTelegram(fail=True))["status"] == "ok"
    bot = FakeTelegram()
    assert _telegram_run(paths, "signal", LIVE[0], "20:16", bot)["status"] == "already_done"
    _telegram_run(paths, "signal", LIVE[0], "21:01", bot)
    assert len(bot.sent) == len(PAPER_ACCOUNTS)


def test_place_records_answers_and_sends_only_unrejected_intents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    _run(paths, "signal", LIVE[0], "19:35")
    bot = FakeTelegram([
        ("r:paper-reversal_5d-50k:2026-10-21:A", "42"),
        ("a:paper-reversal_5d-50k:2026-10-21:A", "42"),  # second answer: first one wins
        ("a:paper-reversal_5d-50k:2026-10-21:B", "42"),
        ("r:paper-reversal_5d-100k:2026-10-21:A", "7"),  # another chat: ignored
        ("r:paper-reversal_5d-100k:2026-10-14:A", "42"),  # an old signal: too late
    ])
    bot.batch = 2  # Telegram pages updates; a rejection in a later page must still count
    result = _telegram_run(paths, "place", LIVE[1], "08:46", bot)
    assert result["decided"] == 2
    assert result["sent"]["paper-reversal_5d-50k"] == 1
    assert result["sent"]["paper-reversal_5d-100k"] == 2
    assert bot.answers == ["Rejected A for the 2026-10-22 open", "Already rejected: A", "Approved B for the 2026-10-22 open", "Too late for 2026-10-14:A; nothing changed"]
    assert json.loads(paths.telegram_offset.read_text())["offset"] == 105
    _run(paths, "reconcile", LIVE[1], "18:50")
    _run(paths, "signal", LIVE[1], "19:35")
    account = DailyAccount(paths.accounts, PAPER_ACCOUNTS[0])
    assert [h.security_id for h in account.view().state.holdings] == ["B"]


def test_required_approval_skips_unanswered_intents(tmp_path: Path) -> None:
    spec = AccountSpec("paper-fixed-100k", "fixed", 100_000, approval="required")
    market = _market()
    account = DailyAccount(tmp_path, spec)
    account.open(session=SESSIONS[0].isoformat(), verdict="replay")
    advance(account, account.view(), market, SESSIONS[0], plan=True)
    account.append([AccountEvent("INTENT_APPROVED", SESSIONS[1].isoformat(), {"intent_id": f"{SESSIONS[0]}:A", "security_id": "A", "via": "test", "update_id": 1})])
    assert place(account, account.view(), SESSIONS[1], SESSIONS[0]) == 1
    assert [i.security_id for i in account.view().state.intents if i.sent] == ["A"]


def test_every_halt_a_job_opens_is_posted_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    bot = FakeTelegram()
    _telegram_run(paths, "reconcile", LIVE[0], "18:50", bot)
    _telegram_run(paths, "signal", LIVE[0], "19:35", bot)
    bot.sent.clear()
    _telegram_run(paths, "place", LIVE[1], "10:05", bot)  # late: halts all four accounts
    _telegram_run(paths, "reconcile", LIVE[1], "18:50", bot)  # still halted: nothing new to post
    assert len(bot.sent) == 1
    assert bot.sent[0][0].startswith("HALT late_run on 2026-10-22")
    assert all(spec.account_id in bot.sent[0][0] for spec in PAPER_ACCOUNTS)


def test_a_halt_left_unposted_by_a_failed_post_goes_out_on_the_next_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    _run(paths, "signal", LIVE[0], "19:35")
    _telegram_run(paths, "place", LIVE[1], "10:05", FakeTelegram(fail=True))  # late halt; the post fails
    bot = FakeTelegram()
    _telegram_run(paths, "reconcile", LIVE[1], "18:50", bot)
    _telegram_run(paths, "signal", LIVE[1], "19:35", bot)
    assert [text.split("\n")[0] for text, _rows in bot.sent] == ["HALT late_run on 2026-10-22"]


def test_bot_turns_a_truncated_response_into_a_telegram_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client
    import urllib.request

    class Truncated:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"{")

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Truncated())
    with pytest.raises(TelegramError, match="IncompleteRead"):
        BotApi("123456:SECRET-TOKEN", "42").updates(None)


def test_a_failing_bot_never_stops_a_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    bot = FakeTelegram(fail=True)
    _telegram_run(paths, "reconcile", LIVE[0], "18:50", bot)
    assert _telegram_run(paths, "signal", LIVE[0], "19:35", bot)["status"] == "ok"


def test_bot_errors_and_repr_never_carry_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    secret = "123456:SECRET-TOKEN"

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    bot = BotApi(secret, "42")
    with pytest.raises(TelegramError) as caught:
        bot.send("hi")
    assert secret not in str(caught.value) and caught.value.__cause__ is None
    assert secret not in repr(bot)


class FakeMac:
    """A macOS notifier that records banners, or fails every send."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, text: str) -> None:
        if self.fail:
            raise NotifyError("osascript failed: CalledProcessError")
        self.sent.append(text)


def _channels_run(paths: RunnerPaths, job: str, session: date, at: str, bot: FakeTelegram | None = None, mac: Notifier | None = None) -> dict[str, object]:
    """Run one job with the given notification channels attached."""
    now = datetime.fromisoformat(f"{session.isoformat()}T{at}").replace(tzinfo=IST)
    return Runner(paths, now, cash=FakeCash(set(LIVE)), calendar=CALENDAR, build=_build, log=lambda _m: None, telegram=bot, mac=mac).run(job)


def test_signal_sends_one_macos_notification_when_intents_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    mac = FakeMac()
    _channels_run(paths, "reconcile", LIVE[0], "18:50", mac=mac)
    _channels_run(paths, "signal", LIVE[0], "19:35", mac=mac)
    _channels_run(paths, "signal", LIVE[0], "20:16", mac=mac)  # a later slot re-runs the job and must not announce again
    assert mac.sent == [f"{2 * len(PAPER_ACCOUNTS)} intents for Thu 2026-10-22. Answer by 08:40: {APPROVALS_URL}"]


def test_a_friday_signal_banner_names_the_monday_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 23, 12, 0, tzinfo=IST), "test start", CALENDAR)  # opens the accounts on this Friday
    _run(paths, "reconcile", LIVE[2], "18:50")
    mac = FakeMac()
    _channels_run(paths, "signal", LIVE[2], "19:35", mac=mac)
    assert mac.sent == [f"{2 * len(PAPER_ACCOUNTS)} intents for Mon 2026-10-26. Answer by 08:40: {APPROVALS_URL}"]


def test_an_open_halt_sends_one_macos_notification_without_telegram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    _run(paths, "signal", LIVE[0], "19:35")
    mac = FakeMac()
    _channels_run(paths, "place", LIVE[1], "10:05", mac=mac)  # late: halts all four accounts
    _channels_run(paths, "reconcile", LIVE[1], "18:50", mac=mac)  # still halted: nothing new to post
    assert len(mac.sent) == 1
    assert mac.sent[0].startswith("HALT late_run on 2026-10-22")


def test_kairos_mac_notify_0_sends_no_macos_notification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    banners: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **_kwargs: banners.append(args))
    monkeypatch.delenv("KAIROS_MAC_NOTIFY", raising=False)
    _patch_strategies(monkeypatch)
    for name, line in (("on", ""), ("off", "KAIROS_MAC_NOTIFY=0\n")):
        env_file = tmp_path / f"{name}.env"
        env_file.write_text(line, encoding="utf-8")
        paths = _live_paths(tmp_path / name)
        start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
        _run(paths, "reconcile", LIVE[0], "18:50")
        _channels_run(paths, "signal", LIVE[0], "19:35", mac=mac_notifier(read_env(env_file)))
    assert len(banners) == 1  # the run without the switch shows its banner; the other shows none


def test_a_failing_macos_notifier_never_stops_the_job_or_telegram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _channels_run(paths, "reconcile", LIVE[0], "18:50")
    bot = FakeTelegram()
    result = _channels_run(paths, "signal", LIVE[0], "19:35", bot=bot, mac=FakeMac(fail=True))
    assert result["status"] == "ok"
    assert len(bot.sent) == len(PAPER_ACCOUNTS)
