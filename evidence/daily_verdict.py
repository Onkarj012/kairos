"""Fold metrics and promotion-gate inputs for the daily baseline runner."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Mapping

import pandas as pd

from evidence.gates import DEFAULT_PROMOTION_THRESHOLDS
from evidence.stats import (
    break_even_trials,
    day_block_bootstrap_ci,
    deflated_sharpe_ratio,
    sharpe,
)


@dataclass(frozen=True)
class Fold:
    """One frozen annual development fold."""

    name: str
    start: date
    end: date


FOLDS = (
    Fold("fold_2021", date(2020, 10, 1), date(2021, 9, 30)),
    Fold("fold_2022", date(2021, 10, 1), date(2022, 9, 30)),
    Fold("fold_2023", date(2022, 10, 1), date(2023, 9, 30)),
    Fold("fold_2024", date(2023, 10, 1), date(2024, 9, 30)),
    Fold("fold_2025", date(2024, 10, 1), date(2025, 9, 30)),
)
ACCOUNTS = (50_000, 100_000)
VERDICT_ACCOUNT = 50_000
THRESHOLDS = replace(
    DEFAULT_PROMOTION_THRESHOLDS,
    require_baseline_sharpe_beaten=False,
    require_sealed_test=False,
)


def trade_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    """Summarize filled trades, misses, close bands, and trade flags."""
    frame = trades.copy()
    if frame.empty:
        frame = pd.DataFrame(columns=["qty", "net_bps", "entry_session", "security_id", "net_pnl", "exit_reason", "flags"])
    filled = frame.loc[(pd.to_numeric(frame["qty"], errors="coerce").fillna(0) > 0) & pd.to_numeric(frame["net_bps"], errors="coerce").notna()].copy()
    returns = pd.to_numeric(filled["net_bps"], errors="coerce").astype(float)
    grouped: dict[Any, list[float]] = {
        session: group["net_bps"].astype(float).tolist()
        for session, group in filled.assign(net_bps=returns).groupby("entry_session", dropna=True)
    }
    ci_low, ci_high = day_block_bootstrap_ci(grouped, confidence=0.95)
    symbol_counts = filled["security_id"].astype(str).value_counts()
    reasons = frame["exit_reason"].fillna("").astype(str)
    flag_counts: dict[str, int] = {}
    for value in frame["flags"].fillna("").astype(str):
        for flag in filter(None, (part.strip() for part in value.split(","))):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    return {
        "trades": int(len(filled)),
        "trading_days": int(filled["entry_session"].nunique(dropna=True)),
        "mean_net_bps": float(returns.mean()) if len(returns) else 0.0,
        "ci95_low_bps": float(ci_low),
        "ci95_high_bps": float(ci_high),
        "win_rate": float((returns > 0).mean()) if len(returns) else 0.0,
        "distinct_symbols": int(filled["security_id"].nunique(dropna=True)),
        "max_symbol_trade_share": float(symbol_counts.max() / len(filled)) if len(filled) else 0.0,
        "total_net_pnl": float(pd.to_numeric(filled["net_pnl"], errors="coerce").fillna(0).sum()),
        "entry_misses": int(reasons.isin(("entry_miss", "missing_entry_row")).sum()),
        "exit_band_closes": int((reasons == "exit_band_close").sum()),
        "flag_counts": dict(sorted(flag_counts.items())),
    }


def fold_metrics(trades: pd.DataFrame, equity: pd.DataFrame, capital: float) -> dict[str, Any]:
    """Add fold return, drawdown, and daily Sharpe to trade metrics."""
    metrics = trade_metrics(trades)
    values = pd.to_numeric(equity.get("equity", pd.Series(dtype=float)), errors="coerce").dropna()
    returns = pd.to_numeric(equity.get("daily_return", pd.Series(dtype=float)), errors="coerce").dropna()
    peaks = values.cummax().clip(lower=float(capital))
    drawdowns = values.div(peaks).sub(1.0).fillna(0.0)
    metrics.update({
        "fold_return": float(values.iloc[-1] / capital - 1.0) if len(values) and capital else 0.0,
        "max_drawdown": float(drawdowns.min()) if len(drawdowns) else 0.0,
        "daily_sharpe": float(sharpe(returns.tolist())),
    })
    return metrics


def build_candidate(
    fold_results: list[Mapping[str, Any]],
    all_trades: pd.DataFrame,
    daily_returns: pd.Series | list[float],
    k: int,
) -> dict[str, Any]:
    """Build the candidate fields consumed by the shared promotion gate."""
    metrics = trade_metrics(all_trades)
    returns = [float(value) for value in daily_returns]
    return {
        "trades": metrics["trades"],
        "trading_days": metrics["trading_days"],
        "max_symbol_trade_share": metrics["max_symbol_trade_share"],
        "distinct_symbols": metrics["distinct_symbols"],
        "folds": [
            {"name": str(result["name"]), "net_expectancy_bps": float(result["mean_net_bps"])}
            for result in fold_results
        ],
        "cost_scenarios": {"full_cost": {"expectancy_ci95_low_bps": metrics["ci95_low_bps"]}},
        "deflated_sharpe_ratio": deflated_sharpe_ratio(returns, trials=k),
        "break_even_k": break_even_trials(returns),
        "k": int(k),
    }
