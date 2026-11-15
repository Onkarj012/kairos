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

from dataclasses import dataclass
from math import floor
from typing import TYPE_CHECKING, Callable, Sequence

from evidence.costs import ZERODHA_DELIVERY
from ops.account_core import (
    ENGINE_EVENTS, TRADE_COLUMNS, EQUITY_COLUMNS, Bar, AccountEvent, Intent, Holding, AccountState,
    new_state, add_flag, apply_event, trades_frame, equity_frame,
)
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


def settle_session(
    state: AccountState,
    session: str,
    bar: Callable[[str], Bar | None],
    exit_due_after: Callable[[str], str],
    *,
    final: bool = False,
    require_sent: bool = False,
    previous_factor: Callable[[str, str], float | None] | None = None,
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
        # The panel can be rebuilt with new actions, so compare with its factor for the last settled session, not the stored one.
        before = previous_factor(holding.security_id, holding.last_session) if previous_factor else None
        before = holding.last_factor if before is None else before
        if factor and before and factor != before:
            emit("POSITION_RESCALED", {
                "security_id": holding.security_id,
                "qty_before": holding.qty,
                "qty": rescaled_quantity(holding.qty, before, factor),
                "factor_before": before,
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
