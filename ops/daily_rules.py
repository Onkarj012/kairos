"""Shared daily trading rules.

Approved defaults:
- Quantity is fixed from the T close.
- T+1 exits do not free cash or slots for T+1 entries; proceeds arrive after fills.
- An entry-band miss leaves the slot empty with no substitute.
- If one share exceeds budget, skip to the next ranked id.
- No sector cap applies because sector data is unavailable.

The session engine below is shared by the backtest and the paper account. Every
state change is an ``AccountEvent``; ``apply_event`` is the only code that folds
events into ``AccountState``, so a replayed event log rebuilds the same state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

import pandas as pd

from evidence.costs import ZERODHA_DELIVERY
if TYPE_CHECKING:
    from models.daily_baselines import Candidate


ENTRY_BAND = 0.02
EXIT_BAND = 0.02
MAX_OPEN = 5
MAX_NEW_PER_DAY = 2
MAX_POSITION_SHARE = 0.20
MAX_GROSS_SHARE = 0.80
HOLD_SESSIONS = 3
COST_MODEL = ZERODHA_DELIVERY
LIQUIDITY_BUCKET = "liquid"


def rescaled_quantity(qty: int, old_factor: float, new_factor: float) -> int:
    """Rescale whole shares while correcting floating-point near-integers."""
    raw = qty * new_factor / old_factor
    nearest = round(raw)
    return nearest if abs(raw - nearest) < 1e-9 else floor(raw)


@dataclass(frozen=True)
class EntryPlan:
    """One reserved entry selected at the signal close."""

    security_id: str
    qty: int
    close_t: float
    reserved: float
    rank_index: int


def entry_fills(open_: float, prev_close_adj: float) -> bool:
    """Return whether the open is within the allowed entry band."""
    return open_ <= prev_close_adj * (1 + ENTRY_BAND)


def exit_fills(open_: float, prev_close_adj: float) -> bool:
    """Return whether the open is within the allowed exit band."""
    return open_ >= prev_close_adj * (1 - EXIT_BAND)


def buy_costs(notional: float) -> float:
    """Return delivery buy charges including liquid-bucket slippage."""
    return COST_MODEL.one_side(notional, side="buy", liquidity_bucket=LIQUIDITY_BUCKET)["total"]


def sell_costs(notional: float) -> float:
    """Return delivery sell charges including liquid-bucket slippage."""
    return COST_MODEL.one_side(notional, side="sell", liquidity_bucket=LIQUIDITY_BUCKET)["total"]


def entry_quantity(close_t: float, budget: float) -> int:
    """Return the largest whole share count affordable at the entry limit."""
    if close_t <= 0 or budget <= 0:
        return 0
    limit_price = close_t * (1 + ENTRY_BAND)
    low, high = 0, int(budget // limit_price) + 1
    while low < high:
        mid = (low + high + 1) // 2
        notional = mid * limit_price
        if notional + buy_costs(notional) <= budget:
            low = mid
        else:
            high = mid - 1
    return low


def reserved_cash(qty: int, close_t: float) -> float:
    """Return the cash reserved for a whole-share entry."""
    notional = qty * close_t * (1 + ENTRY_BAND)
    return notional + buy_costs(notional)


def plan_entries(
    ranked: Sequence[Candidate],
    closes: dict[str, float],
    equity: float,
    cash_available: float,
    open_count: int,
    gross_value: float,
    held_or_pending: set[str],
) -> list[EntryPlan]:
    """Allocate ranked candidates within the daily cash and exposure limits."""
    free_slots = MAX_OPEN - open_count
    max_new = min(MAX_NEW_PER_DAY, free_slots)
    if max_new <= 0 or equity <= 0 or cash_available <= 0:
        return []
    plans: list[EntryPlan] = []
    cash_left = cash_available
    gross_left = max(0.0, MAX_GROSS_SHARE * equity - gross_value)
    for rank_index, candidate in enumerate(ranked):
        if len(plans) >= max_new:
            break
        close_t = closes.get(candidate.security_id, 0.0)
        if candidate.security_id in held_or_pending or close_t <= 0:
            continue
        budget = min(MAX_POSITION_SHARE * equity, cash_left / (free_slots - len(plans)), gross_left)
        qty = entry_quantity(close_t, budget)
        if qty <= 0:
            continue
        reserved = reserved_cash(qty, close_t)
        plans.append(EntryPlan(candidate.security_id, qty, close_t, reserved, rank_index))
        cash_left -= reserved
        gross_left -= reserved
    return plans


ENGINE_EVENTS = (
    "INTENT_CREATED", "ORDER_SENT", "INTENT_EXPIRED", "UNFILLED", "POSITION_OPENED",
    "POSITION_RESCALED", "EXIT_PENDING", "POSITION_CLOSED", "RECONCILED",
)
TRADE_COLUMNS = [
    "signal_session", "security_id", "qty", "entry_session", "entry_price", "exit_session",
    "exit_price", "buy_costs", "sell_costs", "net_pnl", "net_bps", "exit_reason", "flags",
]
EQUITY_COLUMNS = ["session_date", "cash", "positions_value", "equity", "daily_return"]


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
            _holding(state, security_id).last_close = float(close)
        state.positions_value = float(data["positions_value"])
        state.equity = float(data["equity"])
        state.last_session = event.session
    else:
        raise ValueError(f"not an engine event: {kind}")


def settle_session(
    state: AccountState,
    session: str,
    bar: Callable[[str], Bar | None],
    exit_due_after: Callable[[str], str],
    *,
    final: bool = False,
    require_sent: bool = False,
) -> list[AccountEvent]:
    """Settle one session's rescales, exits, entries and close marks from official prices."""
    events: list[AccountEvent] = []

    def emit(kind: str, data: dict[str, object]) -> None:
        """Apply an event to the state and keep it."""
        event = AccountEvent(kind, session, data)
        apply_event(state, event)
        events.append(event)

    def close(holding: Holding, price: float, reason: str, flags: str) -> None:
        """Sell a holding at a price and record its net result."""
        notional = holding.qty * price
        costs = sell_costs(notional)
        net_pnl = holding.qty * price - holding.entry_notional - holding.buy_costs - costs
        emit("POSITION_CLOSED", {
            "security_id": holding.security_id,
            "entry_session": holding.entry_session,
            "qty": holding.qty,
            "exit_price": price,
            "sell_costs": costs,
            "net_pnl": net_pnl,
            "net_bps": net_pnl / holding.entry_notional * 10000 if holding.entry_notional else 0.0,
            "exit_reason": reason,
            "flags": flags,
        })

    # Rescale held shares before exits so an ex-session sale uses the new quantity.
    for holding in list(state.holdings):
        row = bar(holding.security_id)
        if row is None:
            continue
        factor = row.adj_factor
        if factor and holding.last_factor and factor != holding.last_factor:
            emit("POSITION_RESCALED", {
                "security_id": holding.security_id,
                "qty_before": holding.qty,
                "qty": rescaled_quantity(holding.qty, holding.last_factor, factor),
                "factor_before": holding.last_factor,
                "factor": factor,
            })

    # Settle due exits first; absent rows remain pending until the next available open.
    for holding in list(state.holdings):
        if holding.exit_due > session and not holding.pending_missing_exit:
            continue
        row = bar(holding.security_id)
        if row is None:
            if not holding.pending_missing_exit:
                emit("EXIT_PENDING", {"security_id": holding.security_id, "entry_session": holding.entry_session})
            continue
        if holding.pending_missing_exit:
            close(holding, row.open, "missing_exit_row", add_flag(holding.flags, "missing_exit_row"))
        elif exit_fills(row.open, row.prev_close_adj):
            close(holding, row.open, "exit_band_open", holding.flags)
        else:
            close(holding, row.close, "exit_band_close", holding.flags)

    # Close unresolved positions on the final run date before its equity mark.
    if final:
        for holding in list(state.holdings):
            if bar(holding.security_id) is None:
                close(holding, holding.last_close, "no_exit_data", add_flag(holding.flags, "no_exit_data"))

    # Settle entries reserved at the previous close and release each reservation.
    for intent in list(state.intents):
        base: dict[str, object] = {
            "intent_id": intent.intent_id,
            "signal_session": intent.signal_session,
            "security_id": intent.security_id,
            "qty": intent.qty,
            "reserved": intent.reserved,
        }
        if require_sent and not intent.sent:
            emit("INTENT_EXPIRED", {**base, "reason": "not_sent"})
            continue
        row = bar(intent.security_id)
        if row is None:
            emit("UNFILLED", {**base, "exit_reason": "missing_entry_row", "flags": "missing_entry_row"})
            continue
        if not entry_fills(row.open, row.prev_close_adj):
            emit("UNFILLED", {**base, "open": row.open, "exit_reason": "entry_miss", "flags": "entry_miss"})
            continue
        buy_notional = intent.qty * row.open
        buy_charge = buy_costs(buy_notional)
        if buy_notional + buy_charge > intent.reserved:
            emit("UNFILLED", {**base, "open": row.open, "exit_reason": "entry_miss", "flags": "over_reservation"})
            continue
        emit("POSITION_OPENED", {
            **base,
            "entry_price": row.open,
            "entry_notional": buy_notional,
            "buy_costs": buy_charge,
            "exit_due": exit_due_after(session),
            "close": row.close,
            "adj_factor": row.adj_factor,
        })

    # Mark the account at each official close.
    marks: dict[str, object] = {}
    for holding in state.holdings:
        row = bar(holding.security_id)
        if row is not None:
            marks[holding.security_id] = row.close
    positions_value = sum(holding.qty * float(marks.get(holding.security_id, holding.last_close)) for holding in state.holdings)
    account_equity = state.cash + positions_value
    previous_equity = state.equity
    emit("RECONCILED", {
        "cash": state.cash,
        "positions_value": positions_value,
        "equity": account_equity,
        "daily_return": account_equity / float(previous_equity) - 1 if previous_equity else 0.0,
        "marks": marks,
    })
    return events


def plan_session(
    state: AccountState,
    session: str,
    ranked: Sequence[Candidate],
    closes: dict[str, float],
) -> list[AccountEvent]:
    """Reserve the next session's entries using only cash and exposure at this close."""
    held_ids = {holding.security_id for holding in state.holdings}
    plans = plan_entries(ranked, closes, state.equity, state.cash, len(state.holdings), state.positions_value, held_ids)
    events: list[AccountEvent] = []
    for plan in plans:
        candidate = ranked[plan.rank_index]
        event = AccountEvent("INTENT_CREATED", session, {
            "intent_id": f"{session}:{plan.security_id}",
            "signal_session": session,
            "security_id": plan.security_id,
            "qty": plan.qty,
            "close_t": plan.close_t,
            "reserved": plan.reserved,
            "rank_index": plan.rank_index,
            "score": candidate.score,
            "reason": candidate.reason,
        })
        apply_event(state, event)
        events.append(event)
    return events


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
