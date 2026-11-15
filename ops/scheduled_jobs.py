"""Fail-closed runtime for the bounded Kairos systemd jobs.

The external monitor contract uses per-job JSON under ``heartbeats/``. Only a
fully completed, configured handler writes a per-job ``completed_at`` heartbeat. A
started job or a held/failed job never refreshes that deadline.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
JOBS = {"evening-build-picks", "morning-publish-reminder", "evening-report"}


class JobHandler(Protocol):
    def run(self, *, job: str, token: str | None) -> None: ...


class NotificationTransport(Protocol):
    def send_login_reminder(self) -> None: ...


@dataclass(frozen=True)
class TokenRecord:
    access_token: str
    user_id: str
    login_date: date


def valid_token(record: TokenRecord | None, today: date) -> bool:
    """Validate an injected token record; this function never reads a token file."""
    return bool(record and record.access_token.strip() and record.user_id.strip() and record.login_date == today)


def parse_token_record(payload: object) -> TokenRecord | None:
    """Parse the established token JSON shape from an injected synthetic payload."""
    if not isinstance(payload, dict):
        return None
    access_token, user_id, raw_date = payload.get("access_token"), payload.get("user_id"), payload.get("login_date")
    if not isinstance(access_token, str) or not isinstance(user_id, str) or not isinstance(raw_date, str):
        return None
    try:
        parsed_date = date.fromisoformat(raw_date)
    except ValueError:
        return None
    if parsed_date.isoformat() != raw_date:
        return None
    return TokenRecord(access_token, user_id, parsed_date)


def _log(path: Path, event: str, job: str, detail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now(IST).isoformat(), "event": event, "job": job}
    if detail:
        entry["detail"] = detail
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")


def run_job(
    job: str,
    *,
    runtime_dir: Path,
    lock_path: Path,
    handler: JobHandler | None = None,
    notifier: NotificationTransport | None = None,
    token: TokenRecord | None = None,
    now: datetime | None = None,
    heartbeat_writer: Callable[[Path, str, datetime], None] | None = None,
) -> int:
    """Run one injected backend under a host-wide nonblocking lock."""
    if job not in JOBS:
        raise ValueError(f"unknown scheduled job: {job}")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / "jobs.jsonl"
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log(log_path, "overlap-skipped", job, "another Kairos job holds the shared lock")
            return 75
        started = (now or datetime.now(IST)).astimezone(IST)
        _log(log_path, "started", job)
        try:
            if job == "morning-publish-reminder":
                if notifier is None:
                    _log(log_path, "held", job, "login reminder transport is not configured")
                    return 2
                notifier.send_login_reminder()
                if not valid_token(token, started.date()):
                    _log(log_path, "held", job, "missing or invalid same-day Kite token; no orders")
                    return 2
                if handler is None:
                    _log(log_path, "held", job, "publish backend is not configured; no orders")
                    return 2
                handler.run(job=job, token=token.access_token)
            else:
                if handler is None:
                    _log(log_path, "held", job, "job backend is not configured")
                    return 2
                handler.run(job=job, token=None)
            _log(log_path, "completed", job)
            writer = heartbeat_writer or _write_heartbeat
            writer(runtime_dir / "heartbeats" / f"{job}.json", job, started)
            return 0
        except Exception as exc:
            _log(log_path, "failed", job, type(exc).__name__)
            return 1
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_heartbeat(path: Path, job: str, started: datetime) -> None:
    completed = datetime.now(IST)
    value = {"job": job, "started_at": started.isoformat(), "completed_at": completed.isoformat(), "timezone": "Asia/Kolkata"}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def heartbeat_is_fresh(payload: object, *, job: str, now: datetime, deadline_seconds: int) -> bool:
    """Pure outside-monitor predicate; missing, malformed or late heartbeat fails."""
    if not isinstance(payload, Mapping):
        return False
    try:
        if payload.get("job") != job or payload.get("timezone") != "Asia/Kolkata":
            return False
        completed = datetime.fromisoformat(payload["completed_at"])
        if completed.utcoffset() != IST.utcoffset(completed):
            return False
        age = (now - completed).total_seconds()
        return 0 <= age <= deadline_seconds
    except (KeyError, TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: kairos_jobs.py JOB", file=sys.stderr)
        return 64
    # Real handlers and transports are intentionally unavailable until later
    # tickets provide explicit, audited adapters.
    private_state = Path.home() / ".local/state/kairos/jobs"
    base = Path(os.environ.get("KAIROS_JOB_STATE", private_state))
    lock = Path(os.environ.get("KAIROS_JOB_LOCK", private_state / "kairos-jobs.lock"))
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return run_job(args[0], runtime_dir=base, lock_path=lock)


if __name__ == "__main__":
    raise SystemExit(main())
