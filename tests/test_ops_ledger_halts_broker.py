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
