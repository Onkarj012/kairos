"""Durable append-only, hash-chained research and job event ledger."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from contracts.immutable_json import canonical_json_text


RESEARCH_LEDGER_FORMAT = "meridian.research-ledger.v1"
EVENT_TYPES = (
    "JOB_PLANNED", "JOB_STARTED", "JOB_RECOVERED", "ARTIFACT_WRITTEN",
    "JOB_RETRY_SCHEDULED", "JOB_SUCCEEDED", "JOB_FAILED", "STUDY_SUPERSEDED",
    "HOLDOUT_SEALED", "GATE_VERDICT",
    # Daily account events (ops/daily_account.py).
    "ACCOUNT_OPENED", "SIGNAL", "INTENT_CREATED", "INTENT_APPROVED", "INTENT_REJECTED", "ORDER_SENT", "INTENT_EXPIRED", "UNFILLED",
    "POSITION_OPENED", "POSITION_RESCALED", "EXIT_PENDING", "POSITION_CLOSED", "RECONCILED",
    "HALTED", "HALT_CLEARED",
)


class LedgerError(RuntimeError):
    """Raised when a research ledger operation violates its contract."""


class LedgerConflictError(LedgerError):
    """Raised when an event identifier is reused for different content."""


@dataclass(frozen=True)
class ResearchEvent:
    format: str
    sequence: int
    event_id: str
    event_type: str
    study_id: str
    job_id: str
    attempt: int
    recorded_at: str
    payload: Mapping[str, Any]
    previous_event_hash: str | None
    event_hash: str


def _hash_payload(event: ResearchEvent) -> dict[str, object]:
    values = asdict(event)
    values.pop("recorded_at")
    values.pop("event_hash")
    return values


def compute_event_hash(event: ResearchEvent) -> str:
    encoded = canonical_json_text(_hash_payload(event)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def to_json_line(event: ResearchEvent) -> str:
    return canonical_json_text(asdict(event)) + "\n"


def _validated_recorded_at(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise LedgerError(f"recorded_at must be ISO-8601 UTC: {value!r}") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise LedgerError(f"recorded_at must be ISO-8601 UTC: {value!r}")
    return value


def from_json_line(line: str) -> ResearchEvent:
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LedgerError(f"invalid research ledger JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LedgerError("research ledger line must contain a JSON object")
    expected = {
        "format", "sequence", "event_id", "event_type", "study_id", "job_id",
        "attempt", "recorded_at", "payload", "previous_event_hash", "event_hash",
    }
    if set(data) != expected:
        raise LedgerError("research ledger JSON keys do not match the event schema")
    try:
        event = ResearchEvent(**data)
    except TypeError as exc:
        raise LedgerError(f"invalid research ledger event: {exc}") from exc
    if event.format != RESEARCH_LEDGER_FORMAT:
        raise LedgerError(f"unexpected research ledger format: {event.format}")
    if event.event_type not in EVENT_TYPES:
        raise LedgerError(f"unknown research event_type: {event.event_type}")
    if not isinstance(event.payload, Mapping):
        raise LedgerError("research event payload must be a mapping")
    _validated_recorded_at(event.recorded_at)
    return event


def verify_chain(events: list[ResearchEvent] | tuple[ResearchEvent, ...]) -> list[str]:
    """Return content, linkage, sequence, and duplicate violations in input order."""
    violations: list[str] = []
    previous_hash: str | None = None
    seen_event_ids: set[str] = set()
    for index, event in enumerate(events):
        label = f"event[{index}] event_id={event.event_id}"
        try:
            expected_hash = compute_event_hash(event)
        except (TypeError, ValueError) as exc:
            violations.append(f"{label}: event contents are not canonical JSON: {exc}")
        else:
            if event.event_hash != expected_hash:
                violations.append(f"{label}: event_hash does not match contents")
        if event.previous_event_hash != previous_hash:
            violations.append(f"{label}: previous_event_hash does not match prior event")
        expected_sequence = index + 1
        if event.sequence != expected_sequence:
            violations.append(
                f"{label}: sequence discontinuity, expected {expected_sequence}, found {event.sequence}"
            )
        if event.event_id in seen_event_ids:
            violations.append(f"{label}: duplicate event_id")
        seen_event_ids.add(event.event_id)
        previous_hash = event.event_hash
    return violations


def read_events(path: str | Path) -> tuple[ResearchEvent, ...]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return ()
    events: list[ResearchEvent] = []
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise LedgerError(f"blank line in research ledger at {line_number}")
            try:
                events.append(from_json_line(line))
            except LedgerError as exc:
                raise LedgerError(f"invalid research ledger line {line_number}: {exc}") from exc
    violations = verify_chain(events)
    if violations:
        raise LedgerError("invalid research ledger: " + "; ".join(violations))
    return tuple(events)


class ResearchLedgerWriter:
    """Lock-protected writer that verifies the full chain before each append."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        study_id: str,
        job_id: str,
        attempt: int,
        payload: Mapping[str, Any],
        recorded_at: str | None = None,
    ) -> ResearchEvent:
        if event_type not in EVENT_TYPES:
            raise LedgerError(f"unknown research event_type: {event_type}")
        if not isinstance(payload, Mapping):
            raise LedgerError("research event payload must be a mapping")
        received = _validated_recorded_at(recorded_at) if recorded_at is not None else datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                events = read_events(self.path)
                for stored in events:
                    if stored.event_id != event_id:
                        continue
                    candidate = ResearchEvent(
                        format=RESEARCH_LEDGER_FORMAT,
                        sequence=stored.sequence,
                        event_id=event_id,
                        event_type=event_type,
                        study_id=study_id,
                        job_id=job_id,
                        attempt=attempt,
                        recorded_at=stored.recorded_at,
                        payload=dict(payload),
                        previous_event_hash=stored.previous_event_hash,
                        event_hash="",
                    )
                    try:
                        candidate_hash = compute_event_hash(candidate)
                    except (TypeError, ValueError) as exc:
                        raise LedgerError(f"research event is not canonical JSON: {exc}") from exc
                    if candidate_hash == stored.event_hash:
                        return stored
                    raise LedgerConflictError(f"event_id already exists with different content: {event_id}")
                event = ResearchEvent(
                    format=RESEARCH_LEDGER_FORMAT,
                    sequence=len(events) + 1,
                    event_id=event_id,
                    event_type=event_type,
                    study_id=study_id,
                    job_id=job_id,
                    attempt=attempt,
                    recorded_at=received,
                    payload=dict(payload),
                    previous_event_hash=events[-1].event_hash if events else None,
                    event_hash="",
                )
                try:
                    event = replace(event, event_hash=compute_event_hash(event))
                    line = to_json_line(event)
                except (TypeError, ValueError) as exc:
                    raise LedgerError(f"research event is not canonical JSON: {exc}") from exc
                with self.path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return event
