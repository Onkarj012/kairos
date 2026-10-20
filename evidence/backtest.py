from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from evidence.costs import DEFAULT_DELIVERY_ROUND_TRIP_BPS, calculate_round_trip_cost
from lake.io import parse_simple_yaml, read_csv, write_json


FULL_COST_BPS = DEFAULT_DELIVERY_ROUND_TRIP_BPS


def score_row(row: dict[str, Any]) -> float:
    """Deterministic baseline confidence used when no trained model is supplied."""
    for key in ("probability", "confidence", "long_probability", "score"):
        if row.get(key) not in (None, ""):
            return float(row[key])
    momentum = sum(float(row.get(key, 0.0) or 0.0) for key in ("return_1", "return_3", "relative_strength"))
    return max(0.0, min(1.0, 0.5 + momentum))


def run_backtest(input_path: Path, report_path: Path, config_path: Path) -> dict[str, object]:
    config = parse_simple_yaml(config_path)
    top_k = int(config.get("top_k", 3))
    min_confidence = float(config.get("min_confidence", 0.52))
    rows = [row for row in read_csv(input_path) if row.get("label_excluded") != "true"]
    by_time = defaultdict(list)
    for row in rows:
        row["confidence"] = f"{score_row(row):.6f}"
        by_time[row["timestamp"]].append(row)

    trades = []
    for ts, group in sorted(by_time.items()):
        candidates = sorted(group, key=lambda row: float(row["confidence"]), reverse=True)
        for row in candidates[:top_k]:
            confidence = float(row["confidence"])
            if confidence < min_confidence:
                continue
            gross = 1.0 if row.get("long_label") == "LONG_SUCCESS" else -1.0 if row.get("long_label") == "LONG_FAIL" else 0.0
            notional = float(row.get("notional", config.get("notional", 100_000)) or 100_000)
            product = str(row.get("product", config.get("product", "delivery")) or "delivery").lower()
            liquidity_bucket = str(row.get("liquidity_bucket", config.get("liquidity_bucket", "liquid")) or "liquid")
            cost = calculate_round_trip_cost(notional, product=product, liquidity_bucket=liquidity_bucket)
            cost_bps = float(cost["total_bps"])
            net = gross - (cost_bps / 10000)
            trades.append(
                {
                    "timestamp": ts,
                    "symbol": row["symbol"],
                    "confidence": confidence,
                    "outcome": gross,
                    "net_score": net,
                    "cost_bps": cost_bps,
                    "cost_breakdown": cost,
                }
            )

    wins = sum(1 for trade in trades if trade["outcome"] > 0)
    losses = sum(1 for trade in trades if trade["outcome"] < 0)
    net_score = sum(float(trade["net_score"]) for trade in trades)
    report = {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "hit_rate": wins / len(trades) if trades else 0,
        "net_score_after_costs": net_score,
        "precision_at_k": wins / len(trades) if trades else 0,
        "recommendations": trades[:200],
        "cost_model": "indian_equity_full_cost_v1",
    }
    write_json(report_path.with_suffix(".json"), report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: dict[str, object]) -> str:
    return "\n".join(
        [
            "# Kairos v0.1 Backtest Report",
            "",
            f"- Trades: {report['trades']}",
            f"- Wins: {report['wins']}",
            f"- Losses: {report['losses']}",
            f"- Hit rate: {float(report['hit_rate']):.2%}",
            f"- Net score after costs: {float(report['net_score_after_costs']):.4f}",
            "",
            "This is a research baseline report, not financial advice.",
        ]
    )
