"""Tests for the durable append-only research event ledger."""
from __future__ import annotations

import json
import threading

import pytest

from evidence.research_ledger import (
    LedgerConflictError,
    LedgerError,
    ResearchLedgerWriter,
    from_json_line,
    read_events,
    to_json_line,
    verify_chain,
)


def _append(writer: ResearchLedgerWriter, event_id: str, *, payload=None):
    return writer.append(
        event_id=event_id,
        event_type="JOB_STARTED",
        study_id="study-1",
        job_id=f"job-{event_id}",
        attempt=1,
        payload=payload or {"worker": "local"},
    )


def test_appended_events_form_a_valid_chain(tmp_path) -> None:
    writer = ResearchLedgerWriter(tmp_path / "research.jsonl")
    events = tuple(_append(writer, f"event-{index}") for index in range(1, 4))

    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[0].previous_event_hash is None
    assert events[1].previous_event_hash == events[0].event_hash
    assert events[2].previous_event_hash == events[1].event_hash
    assert verify_chain(events) == []


def test_duplicate_event_id_with_identical_content_is_idempotent(tmp_path) -> None:
    path = tmp_path / "research.jsonl"
    writer = ResearchLedgerWriter(path)
    first = _append(writer, "event-1")
    original = path.read_bytes()

    second = _append(writer, "event-1")

    assert second == first
    assert path.read_bytes() == original
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_duplicate_event_id_with_different_payload_conflicts(tmp_path) -> None:
    writer = ResearchLedgerWriter(tmp_path / "research.jsonl")
    _append(writer, "event-1", payload={"value": 1})

    with pytest.raises(LedgerConflictError):
        _append(writer, "event-1", payload={"value": 2})


def test_unknown_event_type_is_rejected(tmp_path) -> None:
    writer = ResearchLedgerWriter(tmp_path / "research.jsonl")

    with pytest.raises(LedgerError):
        writer.append(
            event_id="event-1", event_type="UNKNOWN", study_id="study-1",
            job_id="job-1", attempt=1, payload={},
        )


def test_payload_tampering_is_detected_by_read_and_verification(tmp_path) -> None:
    path = tmp_path / "research.jsonl"
    writer = ResearchLedgerWriter(path)
    _append(writer, "event-1", payload={"state": "planned"})
    tampered = path.read_text(encoding="utf-8").replace('"planned"', '"changed"')
    path.write_text(tampered, encoding="utf-8")

    with pytest.raises(LedgerError):
        read_events(path)
    events = tuple(from_json_line(line) for line in tampered.splitlines())
    assert any("event_hash" in violation for violation in verify_chain(events))


def test_tail_removal_is_valid_but_middle_deletion_breaks_the_chain(tmp_path) -> None:
    path = tmp_path / "research.jsonl"
    writer = ResearchLedgerWriter(path)
    events = tuple(_append(writer, f"event-{index}") for index in range(1, 4))

    path.write_text("".join(to_json_line(event) for event in events[:-1]), encoding="utf-8")
    assert verify_chain(read_events(path)) == []

    path.write_text(to_json_line(events[0]) + to_json_line(events[2]), encoding="utf-8")
    remaining = (events[0], events[2])
    violations = verify_chain(remaining)
    assert any("sequence" in violation or "previous_event_hash" in violation for violation in violations)


def test_new_writer_instance_resumes_the_existing_chain(tmp_path) -> None:
    path = tmp_path / "research.jsonl"
    first = _append(ResearchLedgerWriter(path), "event-1")
    second = _append(ResearchLedgerWriter(path), "event-2")

    assert second.sequence == 2
    assert second.previous_event_hash == first.event_hash
    assert read_events(path) == (first, second)


def test_concurrent_writers_produce_one_valid_twenty_event_chain(tmp_path) -> None:
    path = tmp_path / "research.jsonl"
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def write(prefix: str) -> None:
        try:
            writer = ResearchLedgerWriter(path)
            barrier.wait()
            for index in range(10):
                _append(writer, f"{prefix}-{index}")
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=write, args=(prefix,)) for prefix in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events = read_events(path)
    assert failures == []
    assert len(events) == 20
    assert [event.sequence for event in events] == list(range(1, 21))
    assert verify_chain(events) == []
