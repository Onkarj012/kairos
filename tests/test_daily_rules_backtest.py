from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from evidence.costs import DEFAULT_COST_MODEL, ZERODHA_DELIVERY
from evidence.daily_backtest import run_backtest
from models.daily_baselines import Candidate
from ops.daily_rules import entry_fills, exit_fills, plan_entries


def test_zerodha_delivery_costs_match_hand_calculation() -> None:
    buy = ZERODHA_DELIVERY.one_side(10_000, side="buy", liquidity_bucket="liquid")
    sell = ZERODHA_DELIVERY.one_side(10_000, side="sell", liquidity_bucket="liquid")
    assert buy["stt"] == pytest.approx(10)
    assert buy["stamp"] == pytest.approx(1.5)
    assert sell["stt"] == pytest.approx(10)
    assert buy["exchange_txn"] + sell["exchange_txn"] == pytest.approx(0.614)
    assert buy["sebi"] + sell["sebi"] == pytest.approx(0.02)
    assert buy["gst"] + sell["gst"] == pytest.approx((0.614 + 0.02) * 0.18)
    assert sell["dp_charge"] == pytest.approx(15.34)
    assert buy["slippage"] + sell["slippage"] == pytest.approx(10)
    assert buy["total"] + sell["total"] == pytest.approx(47.58812)


def test_default_cost_model_round_trip_is_unchanged() -> None:
    assert DEFAULT_COST_MODEL.round_trip(100_000, liquidity_bucket="liquid")["total_bps"] == pytest.approx(26.99992)


def test_entry_and_exit_bands() -> None:
    assert entry_fills(102, 100)
    assert not entry_fills(102.01, 100)
    assert exit_fills(98, 100)
    assert not exit_fills(97.99, 100)


def test_plan_entries_caps_skips_and_trims_gross() -> None:
    ranked = [Candidate("too-large", 3, ""), Candidate("small-a", 2, ""), Candidate("small-b", 1, ""), Candidate("small-c", 0, "")]
    plans = plan_entries(
        ranked,
        {"too-large": 100, "small-a": 25, "small-b": 10, "small-c": 10},
        equity=1_000,
        cash_available=100,
        open_count=3,
        gross_value=750,
        held_or_pending=set(),
    )
    assert [plan.security_id for plan in plans] == ["small-a", "small-b"]
    assert all(plan.reserved <= 50 for plan in plans)
    assert sum(plan.reserved for plan in plans) <= 50
    ungated = plan_entries(ranked[1:3], {"small-a": 25, "small-b": 10}, 1_000, 100, 3, 0, set())
    assert [plan.qty for plan in ungated] == [1, 7]


def _sessions(count: int = 6) -> list[date]:
    """Return consecutive synthetic trading sessions."""
    return [date(2026, 1, 1) + timedelta(days=offset) for offset in range(count)]


def _panel(
    sessions: list[date],
    *,
    omit: set[date] | None = None,
    opens: dict[date, float] | None = None,
    closes: dict[date, float] | None = None,
    factors: dict[date, float] | None = None,
    prev_closes: dict[date, float] | None = None,
    prev_close_factors: dict[date, float] | None = None,
) -> pd.DataFrame:
    """Build a synthetic one-security raw-price panel."""
    omit = omit or set()
    opens = opens or {}
    closes = closes or {}
    factors = factors or {}
    prev_closes = prev_closes or {}
    prev_close_factors = prev_close_factors or {}
    rows = []
    for session in sessions:
        if session in omit:
            continue
        rows.append(
            {
                "session_date": session,
                "security_id": "A",
                "open": opens.get(session, 100.0),
                "high": 101.0,
                "low": 99.0,
                "close": closes.get(session, 100.0),
                "prev_close": prev_closes.get(session, 100.0),
                "traded_value": 100_000.0,
                "volume": 1_000,
                "adj_factor": factors.get(session, 1.0),
                "prev_close_factor": prev_close_factors.get(session, 1.0),
            }
        )
    return pd.DataFrame(rows)


def _candidate(as_of: date) -> list[Candidate]:
    """Return one synthetic candidate on the first signal session."""
    return [Candidate("A", 1.0, "test")] if as_of == _sessions()[0] else []


def _run(panel: pd.DataFrame, sessions: list[date]):
    """Run the synthetic panel from its first through last session."""
    return run_backtest(
        panel,
        sessions,
        _candidate,
        capital=10_000,
        first_signal=sessions[0],
        last_session=sessions[-1],
    )


def test_engine_clean_round_trip_has_exact_cash() -> None:
    sessions = _sessions(5)
    result = _run(_panel(sessions, opens={sessions[4]: 101}), sessions)
    trade = result.trades.iloc[0]
    qty = int(trade["qty"])
    buy_notional = qty * 100
    sell_notional = qty * 101
    expected_cash = 10_000 - buy_notional - trade["buy_costs"] + sell_notional - trade["sell_costs"]
    assert trade["entry_session"] == sessions[1]
    assert trade["exit_session"] == sessions[4]
    assert trade["exit_reason"] == "exit_band_open"
    assert result.equity.iloc[-1]["cash"] == pytest.approx(expected_cash)
    assert result.equity.iloc[-1]["positions_value"] == 0


def test_engine_keeps_entry_misses_and_uses_close_exit() -> None:
    sessions = _sessions(5)
    miss = _run(_panel(sessions, opens={sessions[1]: 103}), sessions).trades.iloc[0]
    assert miss["qty"] == 0
    assert miss["exit_reason"] == "entry_miss"
    close_result = _run(_panel(sessions, opens={sessions[4]: 97}, closes={sessions[4]: 96}), sessions)
    close_trade = close_result.trades.iloc[0]
    assert close_trade["exit_session"] == sessions[4]
    assert close_trade["exit_price"] == 96
    assert close_trade["exit_reason"] == "exit_band_close"


def test_engine_waits_for_missing_exit_row_and_rescales_split_qty() -> None:
    sessions = _sessions(6)
    missing = _run(_panel(sessions, omit={sessions[4]}), sessions).trades.iloc[0]
    assert missing["exit_session"] == sessions[5]
    assert missing["exit_reason"] == "missing_exit_row"
    assert "missing_exit_row" in missing["flags"]
    no_data_panel = _panel(sessions, omit={sessions[4], sessions[5]})
    no_data = _run(no_data_panel, sessions).trades.iloc[0]
    assert no_data["exit_session"] == sessions[3]
    assert no_data["exit_price"] == 100
    assert no_data["exit_reason"] == "no_exit_data"
    assert "no_exit_data" in no_data["flags"]
    split_panel = _panel(
        sessions[:5],
        opens={sessions[4]: 50},
        closes={sessions[1]: 100, sessions[2]: 50, sessions[3]: 50, sessions[4]: 50},
        factors={sessions[0]: 0.5, sessions[1]: 0.5},
        prev_closes={sessions[3]: 50, sessions[4]: 50},
        prev_close_factors={sessions[0]: 0.5, sessions[1]: 0.5, sessions[2]: 0.5},
    )
    split = _run(split_panel, sessions[:5]).trades.iloc[0]
    assert split["qty"] == 2 * 19
    assert "qty_rescaled" in split["flags"]
    assert split["net_pnl"] == pytest.approx(38 * 50 - 19 * 100 - split["buy_costs"] - split["sell_costs"])
