from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from ops.daily_account import DailyAccount, PAPER_ACCOUNTS
from ops.daily_rules import AccountEvent
from ops.kite_broker import KiteError, KiteSession, dry_run_place, load_token, order_payload, save_token


def test_payload_shape_and_invalid_order_inputs() -> None:
    assert order_payload("buy", "INFY", 2) == {
        "variety": "amo", "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
        "quantity": 2, "product": "CNC", "order_type": "MARKET", "market_protection": 2,
        "validity": "DAY", "tag": "kairos",
    }
    assert order_payload("sell", "INFY", 1)["transaction_type"] == "SELL"
    for qty, side in ((0, "buy"), (-1, "sell"), (1, "hold")):
        with pytest.raises(ValueError):
            order_payload(side, "INFY", qty)


def test_token_is_private_and_only_available_on_login_date(tmp_path: Path) -> None:
    path = tmp_path / "config" / "kite-token.json"
    save_token(path, "token-value", "user", date(2026, 10, 22))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert load_token(path, date(2026, 10, 22)) == "token-value"
    assert load_token(path, date(2026, 10, 23)) is None


class FakeReader:
    def holdings(self) -> dict[str, int]:
        return {"HELD": 7}


def _account_with_events(root: Path, spec_index: int, session: str) -> DailyAccount:
    account = DailyAccount(root, PAPER_ACCOUNTS[spec_index])
    account.open(session="2026-10-21", verdict="pass")
    account.append([
        AccountEvent("SIGNAL", "2026-10-21", {}),
        AccountEvent("INTENT_CREATED", "2026-10-21", {
            "intent_id": "entry-1", "security_id": "HELD", "qty": 5, "signal_session": "2026-10-21",
            "close_t": 10.0, "reserved": 50.0, "rank_index": 0,
        }),
        AccountEvent("ORDER_SENT", "2026-10-22", {"intent_id": "entry-1", "security_id": "HELD", "qty": 5, "mode": "paper"}),
        AccountEvent("POSITION_OPENED", "2026-10-22", {
            "intent_id": "entry-1", "security_id": "HELD", "qty": 5, "signal_session": "2026-10-21",
            "close": 10.0, "adj_factor": 1.0, "entry_price": 10.0, "entry_notional": 50.0,
            "buy_costs": 0.0, "exit_due": session,
        }),
        AccountEvent("RECONCILED", "2026-10-22", {"marks": {"HELD": 10.0}, "positions_value": 50.0, "equity": 50_000.0}),
        AccountEvent("SIGNAL", session, {}),
        AccountEvent("INTENT_CREATED", session, {
            "intent_id": "buy-1", "security_id": "BUY", "qty": 3, "signal_session": "2026-10-22",
            "close_t": 10.0, "reserved": 30.0, "rank_index": 0,
        }),
        AccountEvent("ORDER_SENT", session, {"intent_id": "buy-1", "security_id": "BUY", "qty": 3, "mode": "paper"}),
    ])
    return account


def test_dry_run_logs_current_buys_due_sells_holdings_fallback_and_rewrite(tmp_path: Path) -> None:
    session = "2026-10-23"
    accounts_root = tmp_path / "accounts"
    account = _account_with_events(accounts_root, 0, session)
    # Add a previously sent intent, which must not be included for this session.
    account.append([
        AccountEvent("SIGNAL", "2026-10-20", {}),
        AccountEvent("INTENT_CREATED", "2026-10-21", {
            "intent_id": "old-buy", "security_id": "OLD", "qty": 4, "signal_session": "2026-10-20",
            "close_t": 10.0, "reserved": 40.0, "rank_index": 1,
        }),
        AccountEvent("ORDER_SENT", "2026-10-21", {"intent_id": "old-buy", "security_id": "OLD", "qty": 4, "mode": "paper"}),
    ])
    out = tmp_path / "dry"
    rows = dry_run_place(accounts_root, out, date.fromisoformat(session), FakeReader())
    assert len(rows) == 2
    assert {(r["payload"]["transaction_type"], r["payload"]["tradingsymbol"], r["payload"]["quantity"]) for r in rows} == {
        ("BUY", "BUY", 3), ("SELL", "HELD", 7),
    }
    sell = next(row for row in rows if row["payload"]["transaction_type"] == "SELL")
    assert sell["paper_qty"] == 5 and sell["holdings_qty"] == 7
    assert len((out / f"{session}.jsonl").read_text().splitlines()) == 2
    dry_run_place(accounts_root, out, date.fromisoformat(session), FakeReader())
    assert len((out / f"{session}.jsonl").read_text().splitlines()) == 2
    fallback = dry_run_place(accounts_root, out, date.fromisoformat(session), None)
    fallback_sell = next(row for row in fallback if row["payload"]["transaction_type"] == "SELL")
    assert fallback_sell["payload"]["quantity"] == 5 and fallback_sell["holdings_qty"] is None


def test_dry_run_sells_a_holding_still_open_past_its_exit_day(tmp_path: Path) -> None:
    accounts_root = tmp_path / "accounts"
    _account_with_events(accounts_root, 0, "2026-10-23")
    rows = dry_run_place(accounts_root, tmp_path / "dry", date(2026, 10, 26), None)
    assert [(r["payload"]["transaction_type"], r["payload"]["tradingsymbol"]) for r in rows] == [("SELL", "HELD")]


def test_dry_run_leaves_out_accounts_the_place_job_skipped(tmp_path: Path) -> None:
    session = "2026-10-23"
    accounts_root = tmp_path / "accounts"
    _account_with_events(accounts_root, 0, session)
    rows = dry_run_place(accounts_root, tmp_path / "dry", date.fromisoformat(session), None, ready=set())
    assert rows == []
    assert (tmp_path / "dry" / f"{session}.jsonl").read_text() == ""


def test_reader_failure_does_not_fail_place_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_daily_paper_runner import CALENDAR, LIVE, _build, _live_paths, _patch_strategies, _run
    from ops.daily_runner import Runner, IST, start

    class FailedReader:
        def holdings(self) -> dict[str, int]:
            raise KiteError("holdings unavailable")

    _patch_strategies(monkeypatch)
    paths = _live_paths(tmp_path)
    start(paths, datetime(2026, 10, 21, 12, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")
    _run(paths, "signal", LIVE[0], "19:35")
    now = datetime(2026, 10, 22, 8, 46, tzinfo=IST)
    runner = Runner(paths, now, calendar=CALENDAR, build=_build, log=lambda _m: None, kite=FailedReader())
    result = runner.run("place")
    assert result["status"] == "ok"
    assert result["dry_run"] == 0


def test_errors_and_repr_never_include_credentials() -> None:
    secret, token = "api-secret-sentinel", "access-token-sentinel"
    error = KiteError("holdings failed")
    representation = repr(KiteSession("api-key", token))
    assert secret not in str(error) and token not in str(error)
    assert secret not in representation and token not in representation

