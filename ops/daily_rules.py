"""Shared daily trading rules.

Approved defaults:
- Quantity is fixed from the T close.
- T+1 exits do not free cash or slots for T+1 entries; proceeds arrive after fills.
- An entry-band miss leaves the slot empty with no substitute.
- If one share exceeds budget, skip to the next ranked id.
- No sector cap applies because sector data is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

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
    free_slots = min(MAX_NEW_PER_DAY, MAX_OPEN - open_count)
    if free_slots <= 0 or equity <= 0 or cash_available <= 0:
        return []
    plans: list[EntryPlan] = []
    cash_left = cash_available
    gross_left = max(0.0, MAX_GROSS_SHARE * equity - gross_value)
    for rank_index, candidate in enumerate(ranked):
        if len(plans) >= free_slots:
            break
        close_t = closes.get(candidate.security_id, 0.0)
        if candidate.security_id in held_or_pending or close_t <= 0:
            continue
        budget = min(MAX_POSITION_SHARE * equity, cash_left / free_slots, gross_left)
        qty = entry_quantity(close_t, budget)
        if qty <= 0:
            continue
        reserved = reserved_cash(qty, close_t)
        plans.append(EntryPlan(candidate.security_id, qty, close_t, reserved, rank_index))
        cash_left -= reserved
        gross_left -= reserved
    return plans
