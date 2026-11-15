from __future__ import annotations

import asyncio

from ops.daily_account import PAPER_ACCOUNTS, DailyAccount
from serve.api import create_app


def _get(app, path: str) -> tuple[int, str]:
    async def request():
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        messages = [{"type": "http.request", "body": b"", "more_body": False}]
        sent = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return sent[0]["status"], b"".join(item.get("body", b"") for item in sent).decode()

    return asyncio.run(request())


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
