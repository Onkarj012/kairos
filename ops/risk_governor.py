"""Per-sleeve risk governor and no-new-entry rules."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time

import pandas as pd

from ops.halts import assert_not_halted


@dataclass(frozen=True)
class RiskLimits:
    max_quantity: float = 1.0
    max_open_positions: int = 1
    daily_loss_halt: float = -15_000.0
    no_new_entries_after: time = time(14, 55)


@dataclass(frozen=True)
class RiskDecision:
    ok: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def enforce_risk_limits(
    *,
    root: str,
    sleeve: str,
    quantity: float,
    timestamp: str,
    open_positions: int = 0,
    day_pnl: float = 0.0,
    limits: RiskLimits | None = None,
) -> RiskDecision:
    limits = limits or RiskLimits()
    reasons: list[str] = []
    try:
        assert_not_halted(root, sleeve)
    except RuntimeError as exc:
        reasons.append(str(exc))
    if quantity > limits.max_quantity:
        reasons.append(f"quantity {quantity} > max {limits.max_quantity}")
    if open_positions >= limits.max_open_positions:
        reasons.append(f"open positions {open_positions} >= max {limits.max_open_positions}")
    if day_pnl <= limits.daily_loss_halt:
        reasons.append(f"day PnL {day_pnl:.2f} <= halt {limits.daily_loss_halt:.2f}")
    if pd.Timestamp(timestamp).time() >= limits.no_new_entries_after:
        reasons.append(f"timestamp at/after {limits.no_new_entries_after}")
    return RiskDecision(ok=not reasons, reasons=reasons)
