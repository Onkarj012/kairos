from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from evidence.gates import PromotionGateThresholds, evaluate_candidate
from evidence.research_ledger import ResearchLedgerWriter
from evidence.sealed_test import assert_dev_only, ensure_holdout_sealed, load_dev_panel
from evidence.stats import break_even_trials
from evidence.trials import kairos_trial_count


def test_dev_panel_loader_filters_holdout_rows(tmp_path) -> None:
    path = tmp_path / "panel.parquet"
    pd.DataFrame({"session_date": [date(2025, 9, 30), date(2025, 10, 1)], "close": [1, 2]}).to_parquet(path)

    frame = load_dev_panel(path)

    assert frame["session_date"].tolist() == [date(2025, 9, 30)]


def test_assert_dev_only_rejects_holdout_session() -> None:
    with pytest.raises(ValueError):
        assert_dev_only([date(2025, 10, 1)])


def test_holdout_seal_is_idempotent(tmp_path) -> None:
    writer = ResearchLedgerWriter(tmp_path / "research.jsonl")

    first = ensure_holdout_sealed(writer)
    second = ensure_holdout_sealed(writer)

    assert second == first
    assert first.event_type == "HOLDOUT_SEALED"


def test_evaluate_candidate_applies_dsr_floor() -> None:
    candidate = {
        "trades": 400,
        "trading_days": 120,
        "max_symbol_trade_share": 0.1,
        "distinct_symbols": 25,
        "folds": [{"net_expectancy_bps": 2.0}],
        "cost_scenarios": {"full_cost": {"expectancy_ci95_low_bps": 1.0}},
        "deflated_sharpe_ratio": 0.95,
    }
    thresholds = PromotionGateThresholds(require_baseline_sharpe_beaten=False, require_sealed_test=False)

    assert evaluate_candidate(candidate, thresholds)["eligible"] is True
    candidate["deflated_sharpe_ratio"] = 0.94
    verdict = evaluate_candidate(candidate, thresholds)
    assert verdict["eligible"] is False
    assert "deflated_sharpe_min" in verdict["rejection_reasons"]


def test_break_even_trials_respects_threshold_order() -> None:
    returns = [0.01, 0.04, -0.01, 0.03, 0.02] * 30

    stricter = break_even_trials(returns, threshold=0.99, max_trials=10_000)
    looser = break_even_trials(returns, threshold=0.90, max_trials=10_000)

    assert stricter <= looser


def test_trial_count_dedupes_strategy_reruns(tmp_path) -> None:
    path = tmp_path / "research.jsonl"
    writer = ResearchLedgerWriter(path)
    for event_id, strategy in (("gate-1", "reversal_5d@1"), ("gate-2", "reversal_5d@1"), ("gate-3", "breakout_3d@2")):
        writer.append(event_id=event_id, event_type="GATE_VERDICT", study_id="study", job_id=event_id, attempt=1, payload={"strategy": strategy})

    assert kairos_trial_count(path) == 2
