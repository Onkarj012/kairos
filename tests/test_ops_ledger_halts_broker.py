from ops.broker import LiveExecutionGate, OrderTicket, make_broker
from ops.halts import HaltLimits, evaluate_halts
from ops.ledger import LedgerEntry, append_ledger_entry, load_ledger


def test_ledger_appends_without_rewriting_existing_rows(tmp_path):
    path = tmp_path / "ledger.csv"
    first = append_ledger_entry(
        path,
        LedgerEntry(
            sleeve="F",
            signal_ts="2026-07-01T09:15:00",
            model_version="m1",
            symbol="NIFTY",
            side="BUY",
            quantity=1,
            intended_price=100.0,
            simulated_fill_price=100.5,
            backtest_expected_fill_price=100.0,
            pnl=10.0,
            status="CLOSED",
        ),
    )
    second = append_ledger_entry(
        path,
        LedgerEntry(
            sleeve="F",
            signal_ts="2026-07-02T09:15:00",
            model_version="m1",
            symbol="NIFTY",
            side="SELL",
            quantity=1,
            intended_price=101.0,
            simulated_fill_price=100.8,
            backtest_expected_fill_price=101.0,
            pnl=-5.0,
            status="CLOSED",
        ),
    )

    df = load_ledger(path)

    assert first["ledger_id"] == "L000001"
    assert second["ledger_id"] == "L000002"
    assert len(df) == 2
    assert df[0]["symbol"] == "NIFTY"
    assert float(df[0]["fill_divergence"]) == 0.5


def test_halt_threshold_triggers_and_writes_kill_switch(tmp_path):
    ledger = [
        {"signal_ts": "2026-07-01", "pnl": -20_000.0},
        {"signal_ts": "2026-07-02", "pnl": -80_000.0},
        {"signal_ts": "2026-07-03", "pnl": -60_000.0},
    ]

    decision = evaluate_halts(
        ledger,
        sleeve="F",
        root=tmp_path,
        limits=HaltLimits(paper_capital=1_000_000.0, hard_drawdown_pct=0.10),
        write_halt=True,
    )

    assert decision.hard_halt is True
    assert (tmp_path / "halts" / "F.halt").exists()


def test_triple_key_gate_refuses_without_all_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIROS_LIVE", raising=False)
    token = tmp_path / "confirm"
    token.write_text("abc")
    (tmp_path / "LIVE_TOKEN").write_text("abc")

    gate = LiveExecutionGate.evaluate(
        root=tmp_path,
        sleeve="F",
        cli_live=True,
        confirm_token_path=token,
    )

    assert gate.is_clear() is False
    assert gate.env_live_set is False


def test_v1_live_path_refuses_even_when_gate_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_LIVE", "1")
    token = tmp_path / "confirm"
    token.write_text("abc")
    (tmp_path / "LIVE_TOKEN").write_text("abc")

    broker, gate = make_broker(
        root=tmp_path,
        sleeve="F",
        cli_live=True,
        confirm_token_path=token,
    )
    response = broker.place_order(
        OrderTicket(
            ticket_id="T1",
            sleeve="F",
            symbol="NIFTY",
            side="BUY",
            quantity=1,
            intended_for_live=gate.is_clear(),
        )
    )

    assert gate.is_clear() is True
    assert response["status"] == "LIVE_REFUSED_V1"
