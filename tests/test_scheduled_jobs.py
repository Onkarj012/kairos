"""Synthetic-only tests for scheduler contracts; no backend or external calls."""
from __future__ import annotations

import fcntl
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ops.scheduled_jobs import TokenRecord, heartbeat_is_fresh, main, parse_token_record, run_job, valid_token

IST = ZoneInfo("Asia/Kolkata")


class FakeHandler:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = fail

    def run(self, *, job: str, token: str | None) -> None:
        self.calls.append((job, token))
        if self.fail:
            raise RuntimeError("synthetic failure")


class FakeNotifier:
    def __init__(self) -> None:
        self.calls = 0

    def send_login_reminder(self) -> None:
        self.calls += 1


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "runtime", tmp_path / "shared.lock"


def test_missing_or_wrong_date_token_fails_closed(tmp_path: Path) -> None:
    runtime, lock = _paths(tmp_path)
    handler, notifier = FakeHandler(), FakeNotifier()
    today = date(2024, 12, 31)
    assert not valid_token(None, today)
    assert not valid_token(TokenRecord("synthetic", "synthetic-user", today - timedelta(days=1)), today)
    assert parse_token_record({"access_token": "fixture-token", "user_id": "fixture-user", "login_date": "2024-12-31"}) == TokenRecord("fixture-token", "fixture-user", today)
    assert parse_token_record({"access_token": "fixture-token", "user_id": "fixture-user", "login_date": "2024-2-01"}) is None
    assert parse_token_record({"access_token": "fixture-token", "login_date": "2024-12-31"}) is None
    assert run_job("morning-publish-reminder", runtime_dir=runtime, lock_path=lock,
                   token=None, handler=handler, notifier=notifier,
                   now=datetime(2024, 12, 31, 8, 0, tzinfo=IST)) == 2
    assert notifier.calls == 1
    assert handler.calls == []
    assert not (runtime / "heartbeats" / "morning-publish-reminder.json").exists()
    assert "missing or invalid same-day Kite token" in (runtime / "jobs.jsonl").read_text()


def test_success_only_writes_per_job_heartbeat(tmp_path: Path) -> None:
    runtime, lock = _paths(tmp_path)
    handler = FakeHandler()
    assert run_job("evening-report", runtime_dir=runtime, lock_path=lock, handler=handler) == 0
    payload = json.loads((runtime / "heartbeats/evening-report.json").read_text())
    assert payload["job"] == "evening-report"
    assert payload["timezone"] == "Asia/Kolkata"
    assert heartbeat_is_fresh(payload, job="evening-report", now=datetime.fromisoformat(payload["completed_at"]), deadline_seconds=60)


def test_unconfigured_and_failed_jobs_do_not_refresh_heartbeat(tmp_path: Path) -> None:
    runtime, lock = _paths(tmp_path)
    assert run_job("evening-build-picks", runtime_dir=runtime, lock_path=lock) == 2
    assert run_job("evening-report", runtime_dir=runtime, lock_path=lock, handler=FakeHandler(fail=True)) == 1
    assert not (runtime / "heartbeats/evening-build-picks.json").exists()
    assert not (runtime / "heartbeats/evening-report.json").exists()


def test_unconfigured_cli_uses_private_state_without_system_lock(tmp_path, monkeypatch):
    runtime = tmp_path / "jobs"
    monkeypatch.setenv("KAIROS_JOB_STATE", str(runtime))
    monkeypatch.delenv("KAIROS_JOB_LOCK", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert main(["evening-report"]) == 2
    assert (runtime / "kairos-jobs.lock").exists()
    assert "job backend is not configured" in (runtime / "jobs.jsonl").read_text()
    assert not (runtime / "heartbeats/evening-report.json").exists()


def test_second_job_exits_immediately_when_shared_lock_is_held(tmp_path: Path) -> None:
    runtime, lock_path = _paths(tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as first:
        fcntl.flock(first.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_job("evening-report", runtime_dir=runtime, lock_path=lock_path, handler=FakeHandler()) == 75
    assert "overlap-skipped" in (runtime / "jobs.jsonl").read_text()


def test_monitor_rejects_missing_wrong_job_future_and_late_heartbeats() -> None:
    now = datetime(2024, 12, 31, 10, 0, tzinfo=IST)
    base = {"job": "morning-publish-reminder", "timezone": "Asia/Kolkata", "completed_at": now.isoformat()}
    assert heartbeat_is_fresh(base, job="morning-publish-reminder", now=now, deadline_seconds=10)
    assert not heartbeat_is_fresh(base, job="evening-report", now=now, deadline_seconds=10)
    assert not heartbeat_is_fresh({**base, "completed_at": (now - timedelta(seconds=11)).isoformat()}, job="morning-publish-reminder", now=now, deadline_seconds=10)
    assert not heartbeat_is_fresh({**base, "completed_at": (now + timedelta(seconds=1)).isoformat()}, job="morning-publish-reminder", now=now, deadline_seconds=10)
    assert not heartbeat_is_fresh({**base, "completed_at": now.astimezone(ZoneInfo("UTC")).isoformat()}, job="morning-publish-reminder", now=now, deadline_seconds=10)
    assert not heartbeat_is_fresh({}, job="morning-publish-reminder", now=now, deadline_seconds=10)
