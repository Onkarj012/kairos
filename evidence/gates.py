"""Promotion readiness report for validation-only research candidates."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROMOTION_REPORT_FORMAT = "meridian.promotion-readiness.v1"


@dataclass(frozen=True)
class PromotionGateThresholds:
    ci_low_min_bps: float = 0.0
    min_trades: int = 300
    min_trading_days: int = 100
    max_single_symbol_share: float = 0.20
    min_distinct_symbols: int = 20
    min_positive_fold_share: float = 0.70
    worst_fold_floor_bps: float = 0.0
    min_dsr: float = 0.0
    require_baseline_sharpe_beaten: bool = True
    require_sealed_test: bool = True


DEFAULT_PROMOTION_THRESHOLDS = PromotionGateThresholds()


def build_promotion_readiness_report(
    report_path: Path,
    *,
    validation_report: Path | None = None,
    event_report: Path | None = None,
    ranking_report: Path | None = None,
    breadth_report: Path | None = None,
    execution_report: Path | None = None,
    thresholds: PromotionGateThresholds = DEFAULT_PROMOTION_THRESHOLDS,
) -> dict[str, Any]:
    artifacts = {
        "validation": _artifact(validation_report),
        "event": _artifact(event_report),
        "ranking": _artifact(ranking_report),
        "breadth": _artifact(breadth_report),
        "execution": _artifact(execution_report),
    }
    candidates = _candidate_pool(artifacts)
    best = max(candidates, key=_candidate_rank, default=None)
    readiness = _readiness(best, artifacts, thresholds)
    report = {
        "format": PROMOTION_REPORT_FORMAT,
        "policy": "validation_only_no_sealed_2025_until_all_acceptance_gates_pass",
        "thresholds": asdict(thresholds),
        "test_accessed": False,
        "sealed_2025_accessed": False,
        "best_candidate": best,
        "rejected_candidates": [candidate for candidate in candidates if candidate is not best],
        "event_data_contract_status": _event_status(artifacts["event"]),
        "ranking_label_results": _summary_artifact(artifacts["ranking"]),
        "execution_scenarios": _execution_scenarios(best, artifacts["execution"]),
        "breadth_feasibility": _summary_artifact(artifacts["breadth"]),
        "run_health": {name: item["status"] for name, item in artifacts.items()},
        "acceptance": readiness,
        "sealed_test_decision": "eligible_to_spend_2025_once" if readiness["eligible"] else "do_not_spend_2025",
        "tradability": "tradable_candidate" if readiness["eligible"] else "not_tradable",
    }
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {"report": str(report_path), "tradability": report["tradability"], "sealed_test_decision": report["sealed_test_decision"]}


def _artifact(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "missing", "path": None, "data": None}
    path = Path(path)
    if not path.exists():
        return {"status": "missing", "path": str(path), "data": None}
    try:
        return {"status": "complete", "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "unreadable", "path": str(path), "error": str(exc), "data": None}


def _candidate_pool(artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    validation = artifacts["validation"].get("data") or {}
    for candidate in validation.get("candidates", []):
        output.append({"source": "validation", **candidate})
    ranking = artifacts["ranking"].get("data") or {}
    if ranking.get("validation"):
        output.append({
            "source": "ranking",
            "model_family": "cross_sectional_ranking_label",
            "robust_validation_eligible": bool(ranking.get("acceptance", {}).get("eligible")),
            "rejection_reasons": ranking.get("acceptance", {}).get("rejection_reasons", []),
            **_metrics_summary(ranking["validation"]),
            "side_results": ranking["validation"].get("side_results", {}),
            "cost_scenarios": ranking["validation"].get("cost_scenarios", {}),
        })
    event = artifacts["event"].get("data") or {}
    if event.get("event_gated"):
        output.append({
            "source": "event_overlay",
            "model_family": "event_gated_selected_baseline",
            "robust_validation_eligible": event.get("tradability") == "tradable_candidate",
            "rejection_reasons": [] if event.get("tradability") == "tradable_candidate" else [event.get("reason", "event_overlay_not_promoted")],
            **event["event_gated"],
        })
    return output


def _readiness(candidate: dict[str, Any] | None, artifacts: dict[str, dict[str, Any]], thresholds: PromotionGateThresholds) -> dict[str, Any]:
    if candidate is None:
        return {"eligible": False, "rejection_reasons": ["no_validation_candidate"], "checks": {}}
    full_cost = _full_cost_metrics(candidate)
    side_results = candidate.get("side_results", {})
    folds = candidate.get("folds", candidate.get("fold_results", [])) or []
    positive_folds = sum(1 for fold in folds if float(fold.get("net_expectancy_bps", fold.get("expected_value_bps", 0.0)) or 0.0) > 0.0)
    fold_share = positive_folds / len(folds) if folds else float(candidate.get("positive_fold_share", 0.0) or 0.0)
    worst_fold = min((float(fold.get("net_expectancy_bps", fold.get("expected_value_bps", 0.0)) or 0.0) for fold in folds), default=float(candidate.get("worst_fold_bps", 0.0) or 0.0))
    baseline_sharpe = float(candidate.get("net_sharpe", candidate.get("sharpe", 0.0)) or 0.0)
    baselines = candidate.get("baselines", candidate.get("baseline_sharpes", {})) or {}
    beats_baselines = bool(candidate.get("beats_baselines", False))
    if baselines:
        beats_baselines = all(baseline_sharpe > float(value.get("net_sharpe", value) if isinstance(value, dict) else value) for value in baselines.values())
    sealed = candidate.get("sealed_test", candidate.get("sealed", {})) or {}
    sealed_passed = bool(candidate.get("sealed_test_passed", sealed.get("passed", False)))
    checks = {
        "ci_low_positive_full_cost": float(full_cost.get("expectancy_ci95_low_bps", candidate.get("ci_low_bps", 0.0)) or 0.0) > thresholds.ci_low_min_bps,
        "validation_trade_count": int(candidate.get("trades", 0)) >= thresholds.min_trades,
        "validation_trading_days": int(candidate.get("trading_days", 0)) >= thresholds.min_trading_days,
        "symbol_concentration": float(candidate.get("max_symbol_trade_share", 1.0)) <= thresholds.max_single_symbol_share,
        "min_distinct_symbols": int(candidate.get("distinct_symbols", 0)) >= thresholds.min_distinct_symbols,
        "folds_positive_share": fold_share >= thresholds.min_positive_fold_share,
        "worst_fold_floor": worst_fold >= thresholds.worst_fold_floor_bps,
        "beats_rung0_baselines_net_sharpe": beats_baselines if thresholds.require_baseline_sharpe_beaten else True,
        "deflated_sharpe_positive": float(candidate.get("deflated_sharpe_ratio", candidate.get("dsr", 0.0)) or 0.0) > thresholds.min_dsr,
        "sealed_test_once_passed": sealed_passed if thresholds.require_sealed_test else True,
        "test_accessed_false": all((item.get("data") or {}).get("test_accessed", False) is False for item in artifacts.values() if item.get("data")),
    }
    for side in ("long", "short"):
        item = side_results.get(side, {})
        if int(item.get("count", 0) or 0):
            checks[f"{side}_min_distinct_symbols"] = int(item.get("distinct_symbols", 0)) >= thresholds.min_distinct_symbols
    breadth = artifacts["breadth"].get("data") or {}
    if breadth:
        checks["breadth_structurally_feasible"] = bool(breadth.get("production_gate_feasibility", {}).get("structurally_feasible_at_observed_breadth"))
    reasons = [name for name, passed in checks.items() if not passed]
    return {"eligible": not reasons, "rejection_reasons": reasons, "checks": checks}


def _candidate_rank(candidate: dict[str, Any]) -> tuple[float, float, float, float, float]:
    flat_12 = _full_cost_metrics(candidate)
    return (
        1.0 if candidate.get("robust_validation_eligible") else 0.0,
        float(flat_12.get("expectancy_ci95_low_bps", candidate.get("ci_low_bps", 0.0))),
        float(flat_12.get("profit_factor", candidate.get("profit_factor", 0.0))),
        float(candidate.get("trades", 0)),
        -float(candidate.get("max_symbol_trade_share", 1.0)),
    )


def _full_cost_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    scenarios = candidate.get("cost_scenarios", {}) or {}
    return scenarios.get("full_cost") or scenarios.get("flat_12bps") or {}


def _event_status(artifact: dict[str, Any]) -> dict[str, Any]:
    data = artifact.get("data") or {}
    if artifact["status"] != "complete":
        return {"status": artifact["status"], "reason": "event_signal_blocked_by_source_contract"}
    if data.get("reason") == "event_signal_blocked_by_source_contract":
        return {"status": "blocked", "reason": "event_signal_blocked_by_source_contract"}
    return {"status": "complete", "tradability": data.get("tradability")}


def _summary_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    data = artifact.get("data")
    if not data:
        return {"status": artifact["status"]}
    return {"status": artifact["status"], "format": data.get("format"), "tradability": data.get("tradability"), "test_accessed": data.get("test_accessed", data.get("sealed_2025_accessed"))}


def _execution_scenarios(candidate: dict[str, Any] | None, artifact: dict[str, Any]) -> dict[str, Any]:
    if candidate and candidate.get("execution_scenarios"):
        return candidate["execution_scenarios"]
    return _summary_artifact(artifact)


def _metrics_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": int(metrics.get("long", 0)) + int(metrics.get("short", 0)),
        "long": int(metrics.get("long", 0)),
        "short": int(metrics.get("short", 0)),
        "trading_days": int(metrics.get("trading_days", 0)),
        "expected_value_bps": float(metrics.get("expected_value_bps", 0.0)),
        "profit_factor": float(metrics.get("net_profit_factor", 0.0)),
        "ci_low_bps": float(metrics.get("net_expectancy_ci95_low_bps", 0.0)),
        "max_symbol_trade_share": float(metrics.get("max_symbol_trade_share", 0.0)),
        "distinct_symbols": int(metrics.get("distinct_symbols", 0)),
        "execution_scenarios": metrics.get("execution_scenarios", {}),
    }
