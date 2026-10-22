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
    EntryPlan,
    buy_costs,
    exit_fills,
    entry_fills,
    plan_entries,
    rescaled_quantity,
    sell_costs,
)


@dataclass
class _Position:
    """Track one filled position until its exit settles."""

    trade: dict[str, object]
    qty: int
    entry_notional: float
    exit_due: date
    last_close: float
    last_factor: float
    last_seen: date
    pending_missing_exit: bool = False


@dataclass(frozen=True)
class BacktestResult:
    """Return completed trade rows and daily account marks."""

    trades: pd.DataFrame
    equity: pd.DataFrame


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
        return BacktestResult(_trades_frame([]), _equity_frame([]))
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

    trades: list[dict[str, object]] = []
    positions: list[_Position] = []
    scheduled_entries: dict[date, list[tuple[EntryPlan, date]]] = {}
    cash = float(capital)
    equity_rows: list[dict[str, object]] = []
    start_index = session_index[first_signal]

    def value(row: tuple[object, ...], name: str) -> float:
        """Read a numeric panel cell by its column name."""
        return float(row[columns[name]])

    for session in ordered_sessions[start_index : last_index + 1]:
        # Rescale held shares before exits so an ex-session sale uses the new quantity.
        for position in positions:
            sid = str(position.trade["security_id"])
            row = rows.get(sid, {}).get(session)
            if row is None:
                continue
            factor = value(row, "adj_factor")
            if factor and position.last_factor and factor != position.last_factor:
                position.qty = rescaled_quantity(position.qty, position.last_factor, factor)
                position.trade["qty"] = position.qty
                position.trade["flags"] = _add_flag(position.trade["flags"], "qty_rescaled")
            position.last_factor = factor

        # Settle due exits first; absent rows remain pending until the next available open.
        remaining: list[_Position] = []
        for position in positions:
            if position.exit_due > session and not position.pending_missing_exit:
                remaining.append(position)
                continue
            sid = str(position.trade["security_id"])
            row = rows.get(sid, {}).get(session)
            if row is None:
                position.pending_missing_exit = True
                remaining.append(position)
                continue
            open_price = value(row, "open")
            previous_close_adj = value(row, "prev_close") * value(row, "prev_close_factor") / value(row, "adj_factor")
            if position.pending_missing_exit:
                exit_price = open_price
                exit_reason = "missing_exit_row"
                position.trade["flags"] = _add_flag(position.trade["flags"], "missing_exit_row")
            elif exit_fills(open_price, previous_close_adj):
                exit_price = open_price
                exit_reason = "exit_band_open"
            else:
                exit_price = value(row, "close")
                exit_reason = "exit_band_close"
            notional = position.qty * exit_price
            costs = sell_costs(notional)
            cash += notional - costs
            _finish_trade(position, session, exit_price, costs, exit_reason)
        positions = remaining

        # Close unresolved positions on the final run date before its equity mark.
        if session == last_session:
            terminal: list[_Position] = []
            for position in positions:
                sid = str(position.trade["security_id"])
                if rows.get(sid, {}).get(session) is not None:
                    terminal.append(position)
                    continue
                notional = position.qty * position.last_close
                costs = sell_costs(notional)
                cash += notional - costs
                position.trade["flags"] = _add_flag(position.trade["flags"], "no_exit_data")
                _finish_trade(position, session, position.last_close, costs, "no_exit_data")
            positions = terminal

        # Settle entries reserved at the previous close and release each reservation.
        for plan, signal_session in scheduled_entries.pop(session, []):
            sid_rows = rows.get(plan.security_id, {})
            row = sid_rows.get(session)
            trade: dict[str, object] = {
                "signal_session": signal_session,
                "security_id": plan.security_id,
                "qty": 0,
                "entry_session": session,
                "entry_price": float("nan"),
                "exit_session": pd.NaT,
                "exit_price": float("nan"),
                "buy_costs": 0.0,
                "sell_costs": 0.0,
                "net_pnl": float("nan"),
                "net_bps": float("nan"),
                "exit_reason": "",
                "flags": "",
            }
            if row is None:
                trade["exit_reason"] = "missing_entry_row"
                trade["flags"] = "missing_entry_row"
                trades.append(trade)
                continue
            open_price = value(row, "open")
            previous_close_adj = value(row, "prev_close") * value(row, "prev_close_factor") / value(row, "adj_factor")
            if not entry_fills(open_price, previous_close_adj):
                trade["exit_reason"] = "entry_miss"
                trade["flags"] = "entry_miss"
                trades.append(trade)
                continue
            buy_notional = plan.qty * open_price
            buy_charge = buy_costs(buy_notional)
            if buy_notional + buy_charge > plan.reserved:
                trade["exit_reason"] = "entry_miss"
                trade["flags"] = "over_reservation"
                trades.append(trade)
                continue
            cash -= buy_notional + buy_charge
            entry_pos = session_index[session]
            due_index = entry_pos + HOLD_SESSIONS
            exit_due = ordered_sessions[due_index] if due_index < len(ordered_sessions) else last_session
            trade.update({"qty": plan.qty, "entry_price": open_price, "buy_costs": buy_charge})
            trades.append(trade)
            positions.append(
                _Position(
                    trade=trade,
                    qty=plan.qty,
                    entry_notional=buy_notional,
                    exit_due=exit_due,
                    last_close=value(row, "close"),
                    last_factor=value(row, "adj_factor"),
                    last_seen=session,
                )
            )

        # Update held position marks after entries and exits settle.
        for position in positions:
            sid = str(position.trade["security_id"])
            row = rows.get(sid, {}).get(session)
            if row is None:
                continue
            position.last_close = value(row, "close")
            position.last_seen = session

        # Mark the account at each official close.
        positions_value = sum(position.qty * position.last_close for position in positions)
        account_equity = cash + positions_value
        previous_equity = equity_rows[-1]["equity"] if equity_rows else float(capital)
        equity_rows.append(
            {
                "session_date": session,
                "cash": cash,
                "positions_value": positions_value,
                "equity": account_equity,
                "daily_return": account_equity / float(previous_equity) - 1 if previous_equity else 0.0,
            }
        )

        # Reserve tomorrow's entries using only cash and exposure available at this close.
        next_index = session_index[session] + 1
        exit_index = next_index + HOLD_SESSIONS
        if next_index <= last_index and exit_index <= last_index:
            closes = {
                sid: value(row, "close")
                for sid, by_session in rows.items()
                if (row := by_session.get(session)) is not None
            }
            held_ids = {str(position.trade["security_id"]) for position in positions}
            open_count = len(positions)
            gross_value = positions_value
            plans = plan_entries(
                ranker(session),
                closes,
                account_equity,
                cash,
                open_count,
                gross_value,
                held_ids,
            )
            if plans:
                scheduled_entries.setdefault(ordered_sessions[next_index], []).extend(
                    (plan, session) for plan in plans
                )

    return BacktestResult(_trades_frame(trades), _equity_frame(equity_rows))


def _add_flag(flags: object, flag: str) -> str:
    """Append a flag once to a comma-separated trade flag field."""
    current = str(flags) if flags else ""
    return ",".join([*current.split(","), flag]) if current else flag


def _finish_trade(position: _Position, session: date, price: float, costs: float, reason: str) -> None:
    """Fill the exit fields and net return on a completed trade."""
    trade = position.trade
    buy_charge = float(trade["buy_costs"])
    entry_notional = position.entry_notional
    net_pnl = position.qty * price - entry_notional - buy_charge - costs
    trade.update(
        {
            "qty": position.qty,
            "exit_session": session,
            "exit_price": price,
            "sell_costs": costs,
            "net_pnl": net_pnl,
            "net_bps": net_pnl / entry_notional * 10000 if entry_notional else 0.0,
            "exit_reason": reason,
        }
    )


def _trades_frame(trades: list[dict[str, object]]) -> pd.DataFrame:
    """Build the stable strategy-independent trades table."""
    columns = [
        "signal_session", "security_id", "qty", "entry_session", "entry_price", "exit_session",
        "exit_price", "buy_costs", "sell_costs", "net_pnl", "net_bps", "exit_reason", "flags",
    ]
    return pd.DataFrame(trades, columns=columns)


def _equity_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build the daily account equity table."""
    return pd.DataFrame(rows, columns=["session_date", "cash", "positions_value", "equity", "daily_return"])
