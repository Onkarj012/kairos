from __future__ import annotations

import asyncio
import json

from ops.daily_account import PAPER_ACCOUNTS
from ops.daily_runner import replay
from tests.test_daily_paper_runner import SESSIONS, _market
from serve.api import create_app


def _get(app, path):
    async def request():
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver")], "client": ("testclient", 50000), "server": ("testserver", 80)}
        messages = [{"type": "http.request", "body": b"", "more_body": False}]
        sent = []
        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        return sent[0]["status"], b"".join(item.get("body", b"") for item in sent).decode()
    return asyncio.run(request())


def _account(tmp_path):
    account = replay(_market(), tmp_path, (SPEC,), SESSIONS[0], SESSIONS[-1])[SPEC.account_id]
    account.halt(account.view(), session=SESSIONS[-1].isoformat(), reason="test_halt", detail="review required")
    return account


def test_accounts_lists_open_and_unopened_accounts(tmp_path):
    _account(tmp_path)
    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), "/accounts")

    assert status == 200
    assert SPEC.account_id in body
    assert "not opened" in body
    assert "reversal_5d" in body


def test_account_page_shows_halt_trade_costs_and_svg(tmp_path):
    _account(tmp_path)
    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), f"/accounts/{SPEC.account_id}")

    assert status == 200
    assert "test_halt" in body
    assert "buy_costs" in body
    assert "sell_costs" in body
    assert "<svg" in body


def test_account_api_returns_state_and_trades(tmp_path):
    _account(tmp_path)
    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), f"/api/accounts/{SPEC.account_id}")

    assert status == 200
    payload = json.loads(body)
    assert payload["state"]["cash"] >= 0
    assert "equity" in payload["state"]
    assert payload["trades"]


def test_unknown_account_is_404(tmp_path):
    assert _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), "/accounts/missing")[0] == 404
SPEC = PAPER_ACCOUNTS[0]

