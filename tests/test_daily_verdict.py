from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from evidence.daily_verdict import FOLDS, build_candidate
from evidence.gates import DEFAULT_PROMOTION_THRESHOLDS, evaluate_candidate
from evidence.research_ledger import ResearchLedgerWriter
from evidence.trials import kairos_trial_count


def test_folds_are_contiguous_and_end_at_2025_09_30() -> None:
    """Keep the five frozen annual folds contiguous through the final dev session."""
    assert FOLDS[0].start == date(2020, 10, 1)
    assert FOLDS[-1].end == date(2025, 9, 30)
    assert all(left.end + timedelta(days=1) == right.start for left, right in zip(FOLDS, FOLDS[1:]))


def test_build_candidate_supplies_the_shared_gate_checks() -> None:
    """Build synthetic gate input with every key the shared evaluator reads."""
    trades = pd.DataFrame({
        "qty": [1] * 400,
        "net_bps": [2.0] * 400,
        "entry_session": [date(2024, 1, 1) + timedelta(days=index % 120) for index in range(400)],
        "security_id": [f"S{index % 20}" for index in range(400)],
        "net_pnl": [1.0] * 400,
        "exit_reason": ["exit_band_open"] * 400,
        "flags": [""] * 400,
    })
    folds = [{"name": fold.name, "mean_net_bps": 2.0} for fold in FOLDS]
    candidate = build_candidate(folds, trades, [0.01, 0.02, 0.015, 0.005] * 40, 110)
    thresholds = DEFAULT_PROMOTION_THRESHOLDS
    verdict = evaluate_candidate(candidate, thresholds)

    assert set(verdict["checks"]) == {
        "ci_low_positive_full_cost",
        "validation_trade_count",
        "validation_trading_days",
        "symbol_concentration",
        "min_distinct_symbols",
        "folds_positive_share",
        "worst_fold_floor",
        "beats_rung0_baselines_net_sharpe",
        "deflated_sharpe_min",
        "sealed_test_once_passed",
        "test_accessed_false",
    }


def test_identical_verdict_append_is_a_noop_and_trials_stay_deduped(tmp_path) -> None:
    """An identical verdict event leaves the ledger bytes and distinct trial count unchanged."""
    path = tmp_path / "events.jsonl"
    writer = ResearchLedgerWriter(path)
    for strategy in ("reversal_5d@1", "momentum_12_1@1"):
        writer.append(
            event_id=f"gate-verdict-{strategy}-same",
            event_type="GATE_VERDICT",
            study_id="daily-runner-stage1",
            job_id="dev-folds",
            attempt=1,
            payload={"strategy": strategy},
        )
    original = path.read_bytes()
    writer.append(
        event_id="gate-verdict-reversal_5d@1-same",
        event_type="GATE_VERDICT",
        study_id="daily-runner-stage1",
        job_id="dev-folds",
        attempt=1,
        payload={"strategy": "reversal_5d@1"},
    )

    assert path.read_bytes() == original
    assert kairos_trial_count(path) == 2
