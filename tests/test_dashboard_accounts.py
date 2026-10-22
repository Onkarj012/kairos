from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from evidence.research_ledger import read_events
from ops.daily_account import PAPER_ACCOUNTS, DailyAccount
from ops.daily_runner import IST, Calendar, advance, replay
from tests.test_daily_paper_runner import SESSIONS, _market
from serve.api import create_app

LOCAL = b"http://127.0.0.1:8765"
# Weekdays only, so SESSIONS[1] is the session after the signal on SESSIONS[0].
CALENDAR = Calendar(frozen={}, holidays={2025: {}})


def _call(app, method, path, headers=(), body=b""):
    async def request():
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver"), *headers], "client": ("testclient", 50000), "server": ("testserver", 80)}
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []
        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        response_headers = {name.decode(): value.decode() for name, value in sent[0].get("headers", [])}
        return sent[0]["status"], response_headers, b"".join(item.get("body", b"") for item in sent)
    return asyncio.run(request())


def _get(app, path):
    status, _headers, body = _call(app, "GET", path)
    return status, body.decode()


def _at(clock: str) -> Callable[[], datetime]:
    """Return a clock fixed at an IST time on the session after the signal."""
    moment = datetime.fromisoformat(f"{SESSIONS[1].isoformat()}T{clock}").replace(tzinfo=IST)
    return lambda: moment


def _approvals_app(tmp_path, clock: str):
    """Build the dashboard over one test root, with the clock fixed at an IST time."""
    return create_app(accounts_root=tmp_path, runner_root=tmp_path / "runner", clock=_at(clock), calendar=CALENDAR)


def _open_with_intents(tmp_path) -> DailyAccount:
    """Open the account and plan its first session, which leaves two intents waiting for answers."""
    account = DailyAccount(tmp_path, SPEC)
    account.open(session=SESSIONS[0].isoformat(), verdict="replay")
    advance(account, account.view(), _market(), SESSIONS[0], plan=True)
    return account


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


def test_an_approval_from_the_dashboard_records_an_answer_via_dashboard(tmp_path):
    account = _open_with_intents(tmp_path)
    intent_id = f"{SESSIONS[0]}:A"
    status, headers, _body = _call(_approvals_app(tmp_path, "08:30"), "POST", f"/approvals/{SPEC.account_id}/{intent_id}",
                                   [(b"origin", LOCAL)], b"decision=approved")

    assert status == 303 and headers["location"].startswith("/approvals")
    approvals = [e for e in read_events(account.path) if e.event_type == "INTENT_APPROVED"]
    assert [e.payload["via"] for e in approvals] == ["dashboard"]
    assert account.view().decisions[intent_id] == "approved"


def test_an_answer_after_0840_is_refused_and_records_nothing(tmp_path):
    account = _open_with_intents(tmp_path)
    events_before = len(read_events(account.path))
    late = _approvals_app(tmp_path, "08:41")
    status, headers, _body = _call(late, "POST", f"/approvals/{SPEC.account_id}/{SESSIONS[0]}:A", [(b"origin", LOCAL)], b"decision=approved")

    notice = parse_qs(urlsplit(headers["location"]).query)["notice"][0]
    assert status == 303 and "Answers closed" in notice and "08:40" in notice
    assert len(read_events(account.path)) == events_before
    assert "<button" not in _get(late, "/approvals")[1]


def test_a_post_from_a_foreign_or_missing_origin_is_refused(tmp_path):
    account = _open_with_intents(tmp_path)
    app = _approvals_app(tmp_path, "08:30")
    path = f"/approvals/{SPEC.account_id}/{SESSIONS[0]}:A"
    for headers in ([(b"origin", b"http://evil.example")], [(b"referer", b"http://evil.example/approvals")], []):
        assert _call(app, "POST", path, headers, b"decision=approved")[0] == 403
    assert not [e for e in read_events(account.path) if e.event_type == "INTENT_APPROVED"]
    assert _call(app, "POST", path, [(b"referer", b"http://localhost:8765/approvals")], b"decision=approved")[0] == 303
SPEC = PAPER_ACCOUNTS[0]

