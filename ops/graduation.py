"""Paper-to-live graduation checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import sqrt
from typing import Any


@dataclass(frozen=True)
class GraduationThresholds:
    min_months: float = 6.0
    min_trades: int = 200
    min_ev_ci_low: float = 0.0
    max_abs_fill_divergence: float = 0.5
    max_ece: float = 0.10
    max_unexplained_missed_signals: int = 0
    max_unexplained_duplicate_signals: int = 0


def evaluate_paper_graduation(
    ledger,
    probabilities: list[float] | None = None,
    outcomes: list[int] | None = None,
    incidents: list[dict[str, Any]] | None = None,
    thresholds: GraduationThresholds | dict[str, Any] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds if isinstance(thresholds, GraduationThresholds) else GraduationThresholds(**(thresholds or {}))
    rows = _records(ledger)
    pnls = [float(row.get("pnl") or 0.0) for row in rows if row.get("pnl") not in {None, ""}]
    duration_months = _duration_months(rows)
    ev_ci_low = _ci_low(pnls)
    max_divergence = max([abs(float(row.get("fill_divergence") or 0.0)) for row in rows] or [0.0])
    ece = _ece(probabilities, outcomes)
    incidents = list(incidents or [])
    missed = sum(1 for item in incidents if item.get("type") == "missed_signal" and not item.get("explained", False))
    duplicates = sum(1 for item in incidents if item.get("type") == "duplicate_signal" and not item.get("explained", False))

    checks = {
        "duration": _check(duration_months >= thresholds.min_months, duration_months, f">= {thresholds.min_months} months"),
        "trades": _check(len(rows) >= thresholds.min_trades, len(rows), f">= {thresholds.min_trades}"),
        "ev_ci_low": _check(ev_ci_low > thresholds.min_ev_ci_low, ev_ci_low, f"> {thresholds.min_ev_ci_low}"),
        "fill_divergence": _check(max_divergence <= thresholds.max_abs_fill_divergence, max_divergence, f"<= {thresholds.max_abs_fill_divergence}"),
        "ece": _check(ece is not None and ece <= thresholds.max_ece, ece, f"<= {thresholds.max_ece}"),
        "missed_signals": _check(missed <= thresholds.max_unexplained_missed_signals, missed, f"<= {thresholds.max_unexplained_missed_signals} unexplained"),
        "duplicate_signals": _check(duplicates <= thresholds.max_unexplained_duplicate_signals, duplicates, f"<= {thresholds.max_unexplained_duplicate_signals} unexplained"),
    }
    reasons = [f"{name}: observed {check['observed']} required {check['required']}" for name, check in checks.items() if not check["pass"]]
    live_eligible = all(check["pass"] for check in checks.values())
    return {"live_eligible": live_eligible, "checks": checks, "reasons": reasons, "thresholds": asdict(thresholds)}


def _records(rows) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if isinstance(rows, dict):
        keys = list(rows)
        return [dict(zip(keys, vals)) for vals in zip(*(rows[k] for k in keys))]
    return [dict(row) for row in rows]


def _duration_months(rows: list[dict[str, Any]]) -> float:
    dates = []
    for row in rows:
        value = row.get("signal_ts") or row.get("created_at")
        if value:
            try:
                dates.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
            except ValueError:
                pass
    if len(dates) < 2:
        return 0.0
    return (max(dates) - min(dates)).days / 30.4375


def _ci_low(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean - 1.96 * sqrt(variance) / sqrt(len(values))


def _ece(probabilities: list[float] | None, outcomes: list[int] | None, bins: int = 10) -> float | None:
    if probabilities is None or outcomes is None or len(probabilities) == 0 or len(probabilities) != len(outcomes):
        return None
    total = len(probabilities)
    error = 0.0
    for idx in range(bins):
        low = idx / bins
        high = (idx + 1) / bins
        bucket = [(p, o) for p, o in zip(probabilities, outcomes) if low <= p < high or (idx == bins - 1 and p == 1.0)]
        if bucket:
            conf = sum(p for p, _ in bucket) / len(bucket)
            acc = sum(o for _, o in bucket) / len(bucket)
            error += len(bucket) / total * abs(acc - conf)
    return error


def _check(passed: bool, observed: Any, required: str) -> dict[str, Any]:
    return {"pass": bool(passed), "observed": observed, "required": required}
