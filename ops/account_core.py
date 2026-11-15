"""Strategy-neutral paper account events and readback helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

TRADE_COLUMNS = [
    "signal_session", "security_id", "qty", "entry_session", "entry_price", "exit_session",
    "exit_price", "buy_costs", "sell_costs", "net_pnl", "net_bps", "exit_reason", "flags",
]


EQUITY_COLUMNS = ["session_date", "cash", "positions_value", "equity", "daily_return"]


ENGINE_EVENTS = (
    "INTENT_CREATED", "ORDER_SENT", "INTENT_EXPIRED", "UNFILLED", "POSITION_OPENED",
    "POSITION_RESCALED", "EXIT_PENDING", "POSITION_CLOSED", "RECONCILED",
)


def kill_switch_path(root: str | Path, account_id: str) -> Path:
    return Path(root) / "halts" / f"{account_id}.halt"


@dataclass(frozen=True)
class Bar:
    """One security's official prices for one session, prev_close in the session's basis."""

    open: float
    close: float
    prev_close_adj: float
    adj_factor: float


@dataclass(frozen=True)
class AccountEvent:
    """One account state change; session is ISO and data holds JSON-ready values."""

    kind: str
    session: str
    data: dict[str, object]


@dataclass
class Intent:
    """A reserved entry waiting for the next settled session."""

    intent_id: str
    signal_session: str
    security_id: str
    qty: int
    close_t: float
    reserved: float
    rank_index: int
    sent: bool = False


@dataclass
class Holding:
    """One filled position until its exit settles."""

    security_id: str
    signal_session: str
    entry_session: str
    qty: int
    entry_price: float
    entry_notional: float
    buy_costs: float
    exit_due: str
    last_close: float
    last_factor: float
    last_session: str
    flags: str = ""
    pending_missing_exit: bool = False


@dataclass
class AccountState:
    """Cash, holdings and open intents folded from account events."""

    capital: float
    cash: float
    equity: float
    positions_value: float = 0.0
    holdings: list[Holding] = field(default_factory=list)
    intents: list[Intent] = field(default_factory=list)
    last_session: str | None = None


def new_state(capital: float) -> AccountState:
    """Return a flat account holding only its starting cash."""
    return AccountState(capital=float(capital), cash=float(capital), equity=float(capital))


def add_flag(flags: object, flag: str) -> str:
    """Append a flag to a comma-separated trade flag field."""
    current = str(flags) if flags else ""
    return ",".join([*current.split(","), flag]) if current else flag


def _holding(state: AccountState, security_id: object) -> Holding:
    """Find the single holding for a security id."""
    for holding in state.holdings:
        if holding.security_id == security_id:
            return holding
    raise ValueError(f"no holding for {security_id}")


def _intent(state: AccountState, intent_id: object) -> Intent:
    """Find an open intent by id."""
    for intent in state.intents:
        if intent.intent_id == intent_id:
            return intent
    raise ValueError(f"no open intent {intent_id}")


def apply_event(state: AccountState, event: AccountEvent) -> None:
    """Fold one engine event into the account state."""
    data = event.data
    kind = event.kind
    if kind == "INTENT_CREATED":
        state.intents.append(Intent(
            str(data["intent_id"]), str(data["signal_session"]), str(data["security_id"]),
            int(data["qty"]), float(data["close_t"]), float(data["reserved"]), int(data["rank_index"]),
        ))
    elif kind == "ORDER_SENT":
        _intent(state, data["intent_id"]).sent = True
    elif kind in ("INTENT_EXPIRED", "UNFILLED"):
        state.intents.remove(_intent(state, data["intent_id"]))
    elif kind == "POSITION_OPENED":
        state.intents.remove(_intent(state, data["intent_id"]))
        state.cash -= float(data["entry_notional"]) + float(data["buy_costs"])
        state.holdings.append(Holding(
            security_id=str(data["security_id"]),
            signal_session=str(data["signal_session"]),
            entry_session=event.session,
            qty=int(data["qty"]),
            entry_price=float(data["entry_price"]),
            entry_notional=float(data["entry_notional"]),
            buy_costs=float(data["buy_costs"]),
            exit_due=str(data["exit_due"]),
            last_close=float(data["close"]),
            last_factor=float(data["adj_factor"]),
            last_session=event.session,
        ))
    elif kind == "POSITION_RESCALED":
        holding = _holding(state, data["security_id"])
        holding.qty = int(data["qty"])
        holding.last_factor = float(data["factor"])
        holding.flags = add_flag(holding.flags, "qty_rescaled")
    elif kind == "EXIT_PENDING":
        _holding(state, data["security_id"]).pending_missing_exit = True
    elif kind == "POSITION_CLOSED":
        holding = _holding(state, data["security_id"])
        notional = holding.qty * float(data["exit_price"])
        state.cash += notional - float(data["sell_costs"])
        state.holdings.remove(holding)
    elif kind == "RECONCILED":
        marks = data["marks"]
        if not isinstance(marks, dict):
            raise ValueError("RECONCILED marks must be a mapping")
        for security_id, close in marks.items():
            holding = _holding(state, security_id)
            holding.last_close = float(close)
            holding.last_session = event.session
        state.positions_value = float(data["positions_value"])
        state.equity = float(data["equity"])
        state.last_session = event.session
    else:
        raise ValueError(f"not an engine event: {kind}")


def trades_frame(events: Iterable[AccountEvent]) -> pd.DataFrame:
    """Build the trades table, one row per settled intent, from account events."""
    rows: list[dict[str, object]] = []
    open_rows: dict[str, dict[str, object]] = {}
    for event in events:
        data = event.data
        if event.kind in ("UNFILLED", "INTENT_EXPIRED"):
            rows.append({
                "signal_session": date.fromisoformat(str(data["signal_session"])),
                "security_id": data["security_id"],
                "qty": 0,
                "entry_session": date.fromisoformat(event.session),
                "entry_price": float("nan"),
                "exit_session": pd.NaT,
                "exit_price": float("nan"),
                "buy_costs": 0.0,
                "sell_costs": 0.0,
                "net_pnl": float("nan"),
                "net_bps": float("nan"),
                "exit_reason": data.get("exit_reason", "intent_expired"),
                "flags": data.get("flags", "intent_expired"),
            })
        elif event.kind == "POSITION_OPENED":
            row: dict[str, object] = {
                "signal_session": date.fromisoformat(str(data["signal_session"])),
                "security_id": data["security_id"],
                "qty": data["qty"],
                "entry_session": date.fromisoformat(event.session),
                "entry_price": data["entry_price"],
                "exit_session": pd.NaT,
                "exit_price": float("nan"),
                "buy_costs": data["buy_costs"],
                "sell_costs": 0.0,
                "net_pnl": float("nan"),
                "net_bps": float("nan"),
                "exit_reason": "",
                "flags": "",
            }
            rows.append(row)
            open_rows[str(data["security_id"])] = row
        elif event.kind == "POSITION_RESCALED":
            row = open_rows[str(data["security_id"])]
            row["qty"] = data["qty"]
            row["flags"] = add_flag(row["flags"], "qty_rescaled")
        elif event.kind == "POSITION_CLOSED":
            row = open_rows.pop(str(data["security_id"]))
            row.update({
                "qty": data["qty"],
                "exit_session": date.fromisoformat(event.session),
                "exit_price": data["exit_price"],
                "sell_costs": data["sell_costs"],
                "net_pnl": data["net_pnl"],
                "net_bps": data["net_bps"],
                "exit_reason": data["exit_reason"],
                "flags": data["flags"],
            })
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def equity_frame(events: Iterable[AccountEvent]) -> pd.DataFrame:
    """Build the daily account marks table from RECONCILED events."""
    rows = [
        {
            "session_date": date.fromisoformat(event.session),
            "cash": event.data["cash"],
            "positions_value": event.data["positions_value"],
            "equity": event.data["equity"],
            "daily_return": event.data["daily_return"],
        }
        for event in events
        if event.kind == "RECONCILED"
    ]
    return pd.DataFrame(rows, columns=EQUITY_COLUMNS)
