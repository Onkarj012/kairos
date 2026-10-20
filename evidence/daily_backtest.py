"""Event-loop backtest engine for shared daily trading rules."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd

from models.daily_baselines import Candidate
from evidence.sealed_test import assert_dev_only
from ops.daily_rules import (
    HOLD_SESSIONS,
    AccountEvent,
    Bar,
    equity_frame,
    new_state,
    plan_session,
    settle_session,
    trades_frame,
)


@dataclass(frozen=True)
class BacktestResult:
    """Return completed trade rows, daily account marks and the engine events behind them."""

    trades: pd.DataFrame
    equity: pd.DataFrame
    events: tuple[AccountEvent, ...] = ()


def run_backtest(
    panel: pd.DataFrame,
    sessions: list[date],
    ranker: Callable[[date], list[Candidate]],
    *,
    capital: float,
    first_signal: date,
    last_session: date,
) -> BacktestResult:
    """Run ranked daily entries and scheduled exits over official panel prices."""
    assert_dev_only(sessions)
    assert_dev_only(panel["session_date"])
    assert_dev_only([last_session])
    if not sessions:
        return BacktestResult(trades_frame([]), equity_frame([]))
    ordered_sessions = list(sessions)
    session_index = {session: index for index, session in enumerate(ordered_sessions)}
    if first_signal not in session_index:
        raise ValueError("first_signal must be in sessions")
    last_index = session_index.get(last_session)
    if last_index is None:
        raise ValueError("last_session must be in sessions")
    if ordered_sessions != sorted(ordered_sessions):
        raise ValueError("sessions must be sorted")
    columns = {name: index for index, name in enumerate(panel.columns)}
    required = {
        "session_date", "security_id", "open", "high", "low", "close", "prev_close",
        "adj_factor", "prev_close_factor",
    }
    missing = required - columns.keys()
    if missing:
        raise ValueError(f"panel missing columns: {', '.join(sorted(missing))}")
    rows: dict[str, dict[date, tuple[object, ...]]] = {}
    for row in panel.itertuples(index=False, name=None):
        sid = str(row[columns["security_id"]])
        session = row[columns["session_date"]]
        if hasattr(session, "date") and not isinstance(session, date):
            session = session.date()
        rows.setdefault(sid, {})[session] = row

    state = new_state(capital)
    events: list[AccountEvent] = []
    start_index = session_index[first_signal]

    def value(row: tuple[object, ...], name: str) -> float:
        """Read a numeric panel cell by its column name."""
        return float(row[columns[name]])

    def exit_due_after(entry: str) -> str:
        """Return the scheduled exit session for an entry session."""
        due_index = session_index[date.fromisoformat(entry)] + HOLD_SESSIONS
        return (ordered_sessions[due_index] if due_index < len(ordered_sessions) else last_session).isoformat()

    for session in ordered_sessions[start_index : last_index + 1]:
        def bar(security_id: str, session: date = session) -> Bar | None:
            """Return the official prices for one id on this session."""
            row = rows.get(security_id, {}).get(session)
            if row is None:
                return None
            return Bar(
                value(row, "open"),
                value(row, "close"),
                value(row, "prev_close") * value(row, "prev_close_factor") / value(row, "adj_factor"),
                value(row, "adj_factor"),
            )

        events.extend(settle_session(state, session.isoformat(), bar, exit_due_after, final=session == last_session))

        # Reserve tomorrow's entries only when their scheduled exit fits in the run.
        next_index = session_index[session] + 1
        exit_index = next_index + HOLD_SESSIONS
        if next_index <= last_index and exit_index <= last_index:
            closes = {
                sid: value(row, "close")
                for sid, by_session in rows.items()
                if (row := by_session.get(session)) is not None
            }
            events.extend(plan_session(state, session.isoformat(), ranker(session), closes))

    return BacktestResult(trades_frame(events), equity_frame(events), tuple(events))
