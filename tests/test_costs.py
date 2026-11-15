from __future__ import annotations

from math import isclose

from evidence.costs import DEFAULT_COST_MODEL, ZERODHA_DELIVERY


def test_zerodha_delivery_costs_match_charge_schedule_and_slippage():
    buy = ZERODHA_DELIVERY.one_side(10_000, side="buy", liquidity_bucket="liquid")
    sell = ZERODHA_DELIVERY.one_side(10_000, side="sell", liquidity_bucket="liquid")

    assert isclose(buy["stt"], 10)
    assert isclose(buy["stamp"], 1.5)
    assert isclose(sell["stt"], 10)
    assert isclose(buy["exchange_txn"] + sell["exchange_txn"], 0.614)
    assert buy["dp_charge"] == 0
    assert isclose(sell["dp_charge"], 15.34)
    assert isclose(buy["slippage"] + sell["slippage"], 10)
    assert isclose(buy["total"] + sell["total"], 47.58812)


def test_default_round_trip_cost_model_is_available_without_rules():
    result = DEFAULT_COST_MODEL.round_trip(100_000, liquidity_bucket="liquid")

    assert result["total"] > 0
    assert isclose(result["total_bps"], 26.99992)
