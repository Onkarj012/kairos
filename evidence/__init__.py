"""Evidence engine for full-cost validation and promotion."""

__all__ = [
    "DEFAULT_PROMOTION_THRESHOLDS",
    "PromotionGateThresholds",
    "block_bootstrap_ci",
    "day_block_bootstrap_ci",
    "deflated_sharpe_ratio",
    "build_promotion_readiness_report",
    "economic_metrics",
    "run_backtest",
]


def __getattr__(name):
    if name == "run_backtest":
        from .backtest import run_backtest

        return run_backtest
    if name in {"DEFAULT_PROMOTION_THRESHOLDS", "PromotionGateThresholds", "build_promotion_readiness_report"}:
        from . import gates

        return getattr(gates, name)
    if name in {"block_bootstrap_ci", "day_block_bootstrap_ci", "deflated_sharpe_ratio", "economic_metrics"}:
        from . import stats

        return getattr(stats, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
