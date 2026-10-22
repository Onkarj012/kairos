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
    assert "buy costs" in body
    assert "sell costs" in body
    assert "<svg" in body


def test_account_page_keeps_cleared_halts_in_history(tmp_path):
    account = _account(tmp_path)
    account.clear_halts(session=SESSIONS[-1].isoformat(), note="checked the <split>")
    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), f"/accounts/{SPEC.account_id}")

    assert status == 200
    assert "No open halts." in body
    assert "test_halt" in body and "checked the &lt;split&gt;" in body


def test_closed_trades_leave_out_open_positions_and_skipped_intents(tmp_path):
    from ops.daily_runner import advance, place
    from ops.daily_account import DailyAccount

    market = _market()
    account = DailyAccount(tmp_path, SPEC)
    account.open(session=SESSIONS[0].isoformat(), verdict="replay")
    advance(account, account.view(), market, SESSIONS[0], plan=True)
    place(account, account.view(), SESSIONS[1], SESSIONS[0])
    advance(account, account.view(), market, SESSIONS[1], plan=False)  # A fills; B misses its entry band
    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), f"/accounts/{SPEC.account_id}")

    closed = body.split("<h2>Closed trades</h2>")[1].split("</section>")[0]
    skipped = body.split("<h2>Skipped intents</h2>")[1].split("</section>")[0]
    assert status == 200
    assert "No closed trades yet." in closed
    assert ">B<" in skipped and ">A<" not in skipped


def test_a_trade_rescaled_to_zero_shares_still_shows_as_closed():
    import pandas as pd
    from datetime import date
    from serve.dashboard import AccountPage, render_account
    from ops.daily_account import AccountView
    from ops.daily_rules import TRADE_COLUMNS, new_state

    row = dict.fromkeys(TRADE_COLUMNS, "")
    row.update(signal_session=date(2025, 8, 4), security_id="RS", qty=0, entry_session=date(2025, 8, 5), entry_price=100.0,
               exit_session=date(2025, 8, 8), exit_price=0.0, buy_costs=15.0, sell_costs=0.0, net_pnl=-1015.0, net_bps=-10000.0,
               exit_reason="exit_band_open")
    page = AccountPage(SPEC, "2025-08-04", "paper", "replay", AccountView(SPEC, new_state(50_000.0)),
                       pd.DataFrame([row], columns=TRADE_COLUMNS), pd.DataFrame(), False, [])
    body = render_account(page)
    assert ">RS<" in body.split("<h2>Closed trades</h2>")[1].split("</section>")[0]
    assert "No skipped intents." in body


def test_account_api_returns_state_and_trades(tmp_path):
    _account(tmp_path)
    status, body = _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), f"/api/accounts/{SPEC.account_id}")

    assert status == 200
    payload = json.loads(body)
    assert payload["cash"] >= 0
    assert payload["equity"] > 0
    assert payload["trades"][0]["buy_costs"] > 0


def test_unknown_account_is_404(tmp_path):
    assert _get(create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner"), "/accounts/missing")[0] == 404
SPEC = PAPER_ACCOUNTS[0]

