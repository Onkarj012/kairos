"""Hard halts, soft alerts, and kill-switch files per sleeve."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class HaltLimits:
    paper_capital: float = 1_000_000.0
    hard_drawdown_pct: float = 0.15
    soft_sharpe_30d_min: float = 0.5
    loss_streak_days: int = 5

@dataclass(frozen=True)
class HaltDecision:
    sleeve: str
    hard_halt: bool
    soft_alerts: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    kill_switch_path: str | None = None
    def to_dict(self) -> dict: return asdict(self)

def kill_switch_path(root: str | Path, sleeve: str) -> Path:
    return Path(root)/"halts"/f"{sleeve}.halt"

def evaluate_halts(ledger, *, sleeve: str, root: str | Path, limits: HaltLimits | None = None, write_halt: bool = False) -> HaltDecision:
    limits=limits or HaltLimits(); reasons=[]; soft=[]; switch=kill_switch_path(root,sleeve)
    if switch.exists(): reasons.append(f"kill-switch present: {switch}")
    rows=_records(ledger); pnls=[float(row.get("pnl") or 0.0) for row in rows]
    equity=[]; cur=limits.paper_capital
    for pnl in pnls:
        cur+=pnl; equity.append(cur)
    if equity:
        peak=equity[0]; min_dd=0.0
        for value in equity:
            peak=max(peak,value); min_dd=min(min_dd, value/peak-1.0)
        if min_dd <= -abs(limits.hard_drawdown_pct): reasons.append(f"drawdown {min_dd:.2%} <= -{limits.hard_drawdown_pct:.2%}")
    if len(pnls) >= limits.loss_streak_days and all(v<0 for v in pnls[-limits.loss_streak_days:]): soft.append(f"{limits.loss_streak_days}-day loss streak")
    hard=bool(reasons)
    if hard and write_halt:
        switch.parent.mkdir(parents=True, exist_ok=True); switch.write_text("\n".join(reasons)+"\n")
    return HaltDecision(sleeve, hard, soft, reasons, str(switch))

def assert_not_halted(root: str | Path, sleeve: str) -> None:
    switch=kill_switch_path(root,sleeve)
    if switch.exists(): raise RuntimeError(f"sleeve {sleeve} halted: {switch}")

def _records(rows):
    if rows is None: return []
    if isinstance(rows, dict):
        keys=list(rows); return [dict(zip(keys, vals)) for vals in zip(*(rows[k] for k in keys))]
    return [dict(row) for row in rows]
