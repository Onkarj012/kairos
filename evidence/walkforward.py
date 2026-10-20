"""Pre-registered walk-forward strategy selection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd


@dataclass(frozen=True)
class WalkForwardFold:
    name: str
    select_start: pd.Timestamp
    select_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "select_start": self.select_start.date().isoformat(),
            "select_end": self.select_end.date().isoformat(),
            "oos_start": self.oos_start.date().isoformat(),
            "oos_end": self.oos_end.date().isoformat(),
        }


def rolling_folds(
    start: str,
    end: str,
    *,
    select_years: int = 3,
    oos_years: int = 1,
    step_years: int = 1,
) -> list[WalkForwardFold]:
    cursor = pd.Timestamp(start)
    final = pd.Timestamp(end)
    folds: list[WalkForwardFold] = []
    index = 1
    while True:
        select_start = cursor
        select_end = cursor + pd.DateOffset(years=select_years) - pd.DateOffset(days=1)
        oos_start = select_end + pd.DateOffset(days=1)
        oos_end = oos_start + pd.DateOffset(years=oos_years) - pd.DateOffset(days=1)
        if oos_end > final:
            break
        folds.append(WalkForwardFold(f"fold_{index}", select_start, select_end, oos_start, oos_end))
        cursor = cursor + pd.DateOffset(years=step_years)
        index += 1
    return folds


def select_by_worst_fold(
    grid_results: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    min_trades: int = 30,
    metric: str = "net_return",
) -> dict[str, Any]:
    """Select config with best worst-fold metric, after the trade floor."""
    eligible: list[dict[str, Any]] = []
    for config_id, folds in grid_results.items():
        fold_rows = [dict(item) for item in folds]
        if not fold_rows:
            continue
        total_trades = sum(int(row.get("trades", 0) or 0) for row in fold_rows)
        if total_trades < min_trades:
            continue
        values = [float(row.get(metric, row.get("net_return_pct", row.get("net_return_bps", 0.0))) or 0.0) for row in fold_rows]
        eligible.append(
            {
                "config_id": config_id,
                "folds": fold_rows,
                "trades": total_trades,
                "worst_fold": min(values),
                "mean_fold": sum(values) / len(values),
            }
        )
    if not eligible:
        raise ValueError("no walk-forward configuration met the trade eligibility floor")
    return max(eligible, key=lambda row: (row["worst_fold"], row["mean_fold"], row["trades"], row["config_id"]))


def run_walk_forward(
    folds: Iterable[WalkForwardFold | Mapping[str, Any]] | None = None,
    evaluator: Callable[[Any], Mapping[str, Any]] | None = None,
    *,
    grid: Sequence[Mapping[str, Any]] | None = None,
    grid_committed: bool = True,
    min_trades: int = 30,
    selection_metric: str = "net_return",
) -> dict[str, Any]:
    """Run committed grid configs through rolling select/OOS folds.

    The evaluator receives ``{"fold": fold, "config": config}`` for grid mode
    and must return at least ``net_return`` and ``trades``. Without a grid, this
    preserves the legacy simple fold-evaluation behavior.
    """
    fold_list = list(folds or [])
    if not grid:
        results = [dict(evaluator(fold) if evaluator else _fold_dict(fold)) for fold in fold_list]
        positive = sum(float(item.get("expected_value_bps", item.get("net_expectancy_bps", 0)) or 0) > 0 for item in results)
        return {"folds": results, "positive_fold_share": positive / len(results) if results else 0.0}

    if not grid_committed:
        raise ValueError("walk-forward grid must be committed before OOS execution")
    if evaluator is None:
        raise ValueError("grid walk-forward requires an evaluator")

    grid_results: dict[str, list[dict[str, Any]]] = {}
    for config in grid:
        config_id = str(config.get("id", config.get("name", len(grid_results))))
        grid_results[config_id] = []
        for fold in fold_list:
            result = dict(evaluator({"fold": fold, "config": config}))
            result.setdefault("fold", _fold_dict(fold))
            grid_results[config_id].append(result)

    selected = select_by_worst_fold(grid_results, min_trades=min_trades, metric=selection_metric)
    return {
        "selection_metric": "worst_fold_net_return",
        "min_trades": min_trades,
        "grid_committed": True,
        "grid_results": grid_results,
        "selected_config_id": selected["config_id"],
        "selected": selected,
    }


def _fold_dict(fold: WalkForwardFold | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(fold, WalkForwardFold):
        return fold.as_dict()
    return dict(fold)
