from __future__ import annotations

import json
import asyncio
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ops.account_core import AccountEvent, kill_switch_path
from ops.daily_account import AccountSpec, DailyAccount
from ops.event_log import EventLogWriter, read_events, verify_chain
from ops.paper_calendar import Calendar
from serve.api import create_app
from serve.dashboard import load_account, render_account


def test_event_log_keeps_hash_chain_format_and_idempotence(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventLogWriter(path)
    first = writer.append(
        event_id="signal:2024-01-02",
        event_type="SIGNAL",
        study_id="paper-test",
        job_id="signal:2024-01-02",
        attempt=1,
        payload={"session": "2024-01-02", "count": 1},
        recorded_at="2024-01-02T12:00:00+00:00",
    )
    original = path.read_bytes()
    duplicate = EventLogWriter(path).append(
        event_id="signal:2024-01-02",
        event_type="SIGNAL",
        study_id="paper-test",
        job_id="signal:2024-01-02",
        attempt=1,
        payload={"session": "2024-01-02", "count": 1},
        recorded_at="2024-01-02T13:00:00+00:00",
    )

    stored = read_events(path)
    record = json.loads(original)
    assert duplicate == first
    assert path.read_bytes() == original
    assert record["format"] == "meridian.research-ledger.v1"
    assert record["previous_event_hash"] is None
    assert verify_chain(stored) == []


def test_account_partial_plan_is_hidden_until_signal_marker_and_dashboard_reads_history(tmp_path):
    spec = AccountSpec("paper-test", "synthetic", 10_000)
    account = DailyAccount(tmp_path / "accounts", spec)
    account.open(session="2024-01-02", verdict="synthetic")
    account.append([
        AccountEvent("INTENT_CREATED", "2024-01-03", {
            "intent_id": "2024-01-03:ABC", "signal_session": "2024-01-03", "security_id": "ABC",
            "qty": 2, "close_t": 100.0, "reserved": 205.0, "rank_index": 0,
        })
    ])

    assert account.view().state.intents == []
    account.append([AccountEvent("SIGNAL", "2024-01-03", {"count": 1})])
    account_view = account.view()
    assert [intent.security_id for intent in account_view.state.intents] == ["ABC"]

    page = load_account(tmp_path / "accounts", tmp_path / "runner", spec)
    assert page is not None
    html = render_account(page)
    assert "Event history" in html
    assert "INTENT_CREATED" in html
    assert "SIGNAL" in html
    assert kill_switch_path(tmp_path / "runner", spec.account_id).name == "paper-test.halt"


def test_account_approval_events_remain_readable(tmp_path):
    spec = AccountSpec("paper-test", "synthetic", 10_000)
    account = DailyAccount(tmp_path, spec)
    account.open(session="2024-01-02", verdict="synthetic")
    account.append([
        AccountEvent("SIGNAL", "2024-01-03", {"count": 1}),
        AccountEvent("INTENT_CREATED", "2024-01-03", {
            "intent_id": "2024-01-03:ABC", "signal_session": "2024-01-03", "security_id": "ABC",
            "qty": 2, "close_t": 100.0, "reserved": 205.0, "rank_index": 0,
        }),
        AccountEvent("INTENT_APPROVED", "2024-01-04", {
            "intent_id": "2024-01-03:ABC", "security_id": "ABC", "via": "dashboard",
        }),
    ])

    view = account.view()
    assert view.decisions["2024-01-03:ABC"] == "approved"
    assert read_events(account.path)[-1].payload["via"] == "dashboard"


def test_dashboard_approval_works_without_the_daily_runner(tmp_path):
    spec = AccountSpec("paper-reversal_5d-50k", "reversal_5d", 50_000)
    account = DailyAccount(tmp_path / "accounts", spec)
    account.open(session="2024-01-08", verdict="synthetic")
    account.append([
        AccountEvent("SIGNAL", "2024-01-08", {"count": 1}),
        AccountEvent("INTENT_CREATED", "2024-01-08", {
            "intent_id": "2024-01-08:ABC", "signal_session": "2024-01-08", "security_id": "ABC",
            "qty": 2, "close_t": 100.0, "reserved": 205.0, "rank_index": 0,
        }),
    ])
    calendar = Calendar(frozen={}, holidays={2024: {}})
    now = lambda: datetime.fromisoformat("2024-01-09T08:30:00+05:30")
    app = create_app(accounts_root=tmp_path / "accounts", runner_root=tmp_path / "runner", calendar=calendar, clock=now)

    async def request():
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/approvals/paper-reversal_5d-50k/2024-01-08:ABC",
            "raw_path": b"/approvals/paper-reversal_5d-50k/2024-01-08:ABC", "query_string": b"", "root_path": "",
            "headers": [(b"host", b"localhost"), (b"origin", b"http://127.0.0.1:8765")],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        messages = [{"type": "http.request", "body": b"decision=approved", "more_body": False}]
        sent = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return sent[0]["status"], {key.decode(): value.decode() for key, value in sent[0].get("headers", [])}

    status, headers = asyncio.run(request())
    notice = parse_qs(urlsplit(headers["location"]).query)["notice"][0]
    assert status == 303
    assert "Approved ABC" in notice
    assert account.view().decisions["2024-01-08:ABC"] == "approved"
