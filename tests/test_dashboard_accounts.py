from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from ops.account_core import AccountEvent, TRADE_COLUMNS, new_state
from ops.daily_account import PAPER_ACCOUNTS, DailyAccount
from ops.daily_account import AccountView
from ops.paper_calendar import Calendar
from serve.api import create_app
from serve.dashboard import AccountPage, render_account


def _call(app, method, path, headers=(), body=b""):
    async def request():
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver"), *headers],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        response_headers = {name.decode(): value.decode() for name, value in sent[0].get("headers", [])}
        return sent[0]["status"], response_headers, b"".join(item.get("body", b"") for item in sent).decode()

    return asyncio.run(request())


def _get(app, path: str) -> tuple[int, str]:
    status, _headers, body = _call(app, "GET", path)
    return status, body


def _approval_fixture(tmp_path, clock="08:30"):
    spec = PAPER_ACCOUNTS[0]
    account = DailyAccount(tmp_path, spec)
    account.open(session="2024-01-08", verdict="synthetic")
    account.append([AccountEvent("INTENT_CREATED", "2024-01-08", {
        "intent_id": "2024-01-08:ABC", "signal_session": "2024-01-08", "security_id": "ABC",
        "qty": 2, "close_t": 100.0, "reserved": 205.0, "rank_index": 0,
    }), AccountEvent("SIGNAL", "2024-01-08", {"count": 1})])
    moment = datetime.fromisoformat(f"2024-01-09T{clock}:00+05:30")
    app = create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner",
                     clock=lambda: moment, calendar=Calendar(frozen={}, holidays={2024: {}}))
    return app, account, f"/approvals/{spec.account_id}/2024-01-08:ABC"


def test_approvals_page_refuses_to_be_framed(tmp_path):
    app, _account, _path = _approval_fixture(tmp_path)
    status, headers, _body = _call(app, "GET", "/approvals")
    assert status == 200
    assert headers["x-frame-options"] == "DENY"
    assert headers["content-security-policy"] == "frame-ancestors 'none'"


def test_late_approval_is_refused_without_writes(tmp_path):
    app, account, path = _approval_fixture(tmp_path, "08:41")
    before = account.path.read_bytes()
    status, headers, _body = _call(app, "POST", path, [(b"origin", b"http://127.0.0.1:8765")], b"decision=approved")
    notice = parse_qs(urlsplit(headers["location"]).query)["notice"][0]
    assert status == 303 and "Answers closed" in notice and "08:40" in notice
    assert account.path.read_bytes() == before
    assert "<button" not in _get(app, "/approvals")[1]


def test_foreign_or_missing_origin_refuses_without_writes_and_trusted_referer_works(tmp_path):
    app, account, path = _approval_fixture(tmp_path)
    before = account.path.read_bytes()
    for headers in ([(b"origin", b"http://evil.example")], [(b"referer", b"http://evil.example/approvals")], []):
        assert _call(app, "POST", path, headers, b"decision=approved")[0] == 403
        assert account.path.read_bytes() == before
    assert _call(app, "POST", path, [(b"referer", b"http://localhost:8765/approvals")], b"decision=approved")[0] == 303
    assert account.view().decisions["2024-01-08:ABC"] == "approved"


def test_zero_share_closed_trade_still_renders_as_closed():
    spec = PAPER_ACCOUNTS[0]
    row = dict.fromkeys(TRADE_COLUMNS, "")
    row.update(signal_session=date(2024, 1, 8), security_id="RS", qty=0,
               entry_session=date(2024, 1, 9), entry_price=100.0,
               exit_session=date(2024, 1, 12), exit_price=0.0, buy_costs=15.0,
               sell_costs=0.0, net_pnl=-1015.0, net_bps=-10000.0, exit_reason="synthetic_exit")
    page = AccountPage(spec, "2024-01-08", "paper", "synthetic", AccountView(spec, new_state(50_000.0)),
                       pd.DataFrame([row], columns=TRADE_COLUMNS), pd.DataFrame(), False, [])
    body = render_account(page)
    assert ">RS<" in body.split("<h2>Closed trades</h2>")[1].split("</section>")[0]
    assert "No skipped intents." in body


def test_open_positions_and_skipped_intents_are_not_closed_trades():
    spec = PAPER_ACCOUNTS[0]
    opened = dict.fromkeys(TRADE_COLUMNS, "")
    opened.update(signal_session=date(2024, 1, 8), security_id="OPEN", qty=2,
                  entry_session=date(2024, 1, 9), entry_price=100.0,
                  exit_session=pd.NaT, exit_price=float("nan"), buy_costs=5.0,
                  sell_costs=0.0, net_pnl=float("nan"), net_bps=float("nan"))
    skipped = {**opened, "security_id": "SKIPPED", "qty": 0,
               "entry_price": float("nan"), "buy_costs": 0.0, "exit_reason": "intent_expired"}
    page = AccountPage(spec, "2024-01-08", "paper", "synthetic", AccountView(spec, new_state(50_000.0)),
                       pd.DataFrame([opened, skipped], columns=TRADE_COLUMNS), pd.DataFrame(), False, [])
    body = render_account(page)
    closed = body.split("<h2>Closed trades</h2>")[1].split("</section>")[0]
    skipped_section = body.split("<h2>Skipped intents</h2>")[1].split("</section>")[0]
    assert "No closed trades yet." in closed
    assert ">SKIPPED<" in skipped_section and ">OPEN<" not in skipped_section


def test_account_api_and_rendering_keep_trade_costs(tmp_path):
    app, account, _path = _approval_fixture(tmp_path)
    account.append([
        AccountEvent("POSITION_OPENED", "2024-01-09", {
            "intent_id": "2024-01-08:ABC", "security_id": "ABC", "signal_session": "2024-01-08",
            "qty": 2, "entry_price": 100.0, "entry_notional": 200.0, "buy_costs": 5.0,
            "exit_due": "2024-01-12", "close": 100.0, "adj_factor": 1.0,
        }),
        AccountEvent("RECONCILED", "2024-01-09", {
            "marks": {"ABC": 100.0}, "cash": 49795.0, "positions_value": 200.0,
            "equity": 49995.0, "daily_return": 0.0,
        }),
        AccountEvent("POSITION_CLOSED", "2024-01-12", {
            "security_id": "ABC", "entry_session": "2024-01-09", "qty": 2, "exit_price": 105.0, "sell_costs": 3.0,
            "net_pnl": 2.0, "net_bps": 100.0, "exit_reason": "synthetic_exit", "flags": "",
        }),
        AccountEvent("RECONCILED", "2024-01-12", {
            "marks": {}, "cash": 50002.0, "positions_value": 0.0,
            "equity": 50002.0, "daily_return": 0.0,
        }),
    ])
    status, body = _get(app, f"/api/accounts/{account.spec.account_id}")
    payload = json.loads(body)
    assert status == 200 and payload["cash"] >= 0 and payload["equity"] > 0
    assert payload["trades"][0]["buy_costs"] == 5.0
    assert payload["trades"][0]["sell_costs"] == 3.0
    status, body = _get(app, f"/accounts/{account.spec.account_id}")
    assert status == 200 and "buy costs" in body and "sell costs" in body and "<svg" in body
    assert ">ABC<" in body.split("<h2>Closed trades</h2>")[1].split("</section>")[0]


def test_accounts_page_lists_open_and_unopened_accounts(tmp_path):
    spec = PAPER_ACCOUNTS[0]
    account = DailyAccount(tmp_path, spec)
    account.open(session="2024-01-02", verdict="synthetic")

    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), "/accounts")

    assert status == 200
    assert spec.account_id in body
    assert "not opened" in body


def test_dashboard_account_history_keeps_halts_and_clears(tmp_path):
    spec = PAPER_ACCOUNTS[0]
    account = DailyAccount(tmp_path, spec)
    account.open(session="2024-01-02", verdict="synthetic")
    account.halt(account.view(), session="2024-01-03", reason="synthetic_hold", detail="review")
    account.clear_halts(session="2024-01-04", note="checked")

    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), f"/accounts/{spec.account_id}")

    assert status == 200
    assert "No open halts." in body
    assert "synthetic_hold" in body and "checked" in body


def test_dashboard_unknown_account_is_404(tmp_path):
    status, _body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), "/accounts/missing")
    assert status == 404
