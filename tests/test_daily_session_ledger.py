from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from evidence.daily_session_ledger import (
    ACCEPTED,
    CALENDAR_UNRESOLVED,
    CASH_ADMITTED,
    CASH_REJECTED,
    CASH_ROOT_ABSENT,
    EXPECTED,
    LEGACY_EXPECTED_GAP,
    LEGACY_NON_SESSION,
    NON_SESSION,
    AnnualCalendar,
    LedgerError,
    NoticeSource,
    RetainedSource,
    SPECIAL_SESSION,
    build_ledger,
    load_annual_calendars,
    load_calendar_evidence,
    parse_amendment,
)

URL = "https://archives.nseindia.com/content/circulars/CMTR00000.pdf"
RETRIEVED = "2026-09-01T00:00:00+00:00"
AS_OF_JAN = "2024-01-10T15:35:00+05:30"
AS_OF_NOV = "2024-11-04T15:35:00+05:30"

ANNUAL_TEXT = (
    "NSE Capital Market trading holidays for the calendar year 2024\n"
    "1 January 26, 2024 Friday Republic Day\n"
    "2 March 25, 2024 Monday Holi\n"
    "*Muhurat Trading will be conducted on Friday, November 01, 2024. "
    "Timings of Muhurat Trading shall be notified subsequently.\n"
)

COMPLETE_REVIEW = {
    "special_session_inventory_complete": True,
    "annual_calendar_complete": True,
    "unresolved": [],
    "reviewed_at": "2026-09-15T00:00:00+00:00",
}


def _source(
    label: str,
    seed: str,
    *,
    kind: str = "official_nse_annual_holiday_pdf",
    year: int | None = None,
    decision: str = ACCEPTED,
    publication: str | None = "2023-12-13",
) -> RetainedSource:
    return RetainedSource(
        label=label,
        source_kind=kind,
        source_url=f"https://archives.nseindia.com/content/circulars/{label}",
        sha256=hashlib.sha256(seed.encode()).hexdigest(),
        byte_count=128,
        retrieved_at=RETRIEVED,
        publication_date=publication,
        publication_date_basis="pdf_statement" if publication else "source_date_absent",
        available_at=None,
        available_at_basis="not_authoritatively_published",
        source_path=f"bodies/{label}",
        decision=decision,
        declared_year=year,
    )


def _annual(year: int, text: str = ANNUAL_TEXT) -> AnnualCalendar:
    return AnnualCalendar(year, _source(f"annual/{year}.pdf", f"annual-{year}", year=year), text)


def _notice() -> NoticeSource:
    return NoticeSource(
        _source("notice-muhurat-2024.pdf", "muhurat-2024", kind="special_session", year=2024, publication="2024-10-19"),
        "Retained special-session notice text; interpretation is caller-supplied.",
    )


def _muhurat_amendment(sha: str, **overrides) -> dict:
    payload = {
        "effective_date": "2024-11-01",
        "session_type": "muhurat",
        "label": "Diwali Muhurat",
        "source_sha256": sha,
        "publication_date": "2024-10-19",
        "open_at": "2024-11-01T18:00:00+05:30",
        "close_at": "2024-11-01T19:00:00+05:30",
    }
    payload.update(overrides)
    return payload


def _by_date(result) -> dict[str, dict]:
    return {row["session_date"]: row for row in result.rows}


def _kinds(result) -> set[str]:
    return {blocker["kind"] for blocker in result.report["blockers"]}


def _accepted_cash_receipt(root: Path, session: str) -> None:
    attempt = root / "requests" / session / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "normalized.json").write_text("{}\n")
    (attempt / "receipt.json").write_text(json.dumps({
        "validation_result": {"status": "accepted"},
        "normalized_path": f"requests/{session}/attempt-001/normalized.json",
        "normalized_sha256": hashlib.sha256(b"{}\n").hexdigest(),
        "body_sha256": hashlib.sha256(b"csv").hexdigest(),
        "final_url": "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240108_F_0000.csv",
    }))


def _zip_body(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Grid construction
# ---------------------------------------------------------------------------


def test_one_row_per_calendar_date_and_standard_regular_interval() -> None:
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
    )
    assert len(result.rows) == 10
    rows = _by_date(result)
    assert rows["2024-01-01"]["state"] == EXPECTED
    assert rows["2024-01-01"]["open_at"] == "2024-01-01T09:15:00+05:30"
    assert rows["2024-01-01"]["close_at"] == "2024-01-01T15:30:00+05:30"
    assert rows["2024-01-06"]["state"] == NON_SESSION
    assert rows["2024-01-06"]["reason"] == "weekend"
    assert result.report["latest_completed_session"] == "2024-01-10"
    assert result.report["denominator"]["denominator_qualified"] is True


def test_holiday_is_non_session_with_reason() -> None:
    result = build_ledger(
        start=date(2024, 1, 20),
        as_of="2024-01-29T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
    )
    holiday = _by_date(result)["2024-01-26"]
    assert holiday["state"] == NON_SESSION
    assert holiday["session_type"] == "holiday"
    assert "Republic Day" in holiday["reason"]


def test_year_without_annual_is_unresolved_never_regular() -> None:
    result = build_ledger(
        start=date(2025, 12, 29),
        as_of="2026-01-05T15:35:00+05:30",
        annual_calendars=[_annual(2025, "NSE Capital Market trading holidays for the calendar year 2025\n1 January 01, 2025 Wednesday New Year\n")],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report["latest_completed_session"] == "2025-12-31"
    assert _by_date(result)["2025-12-31"]["state"] == EXPECTED


def test_unusable_annual_source_leaves_dates_unresolved() -> None:
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of="2024-01-03T15:35:00+05:30",
        annual_calendars=[_annual(2024, "")],
        calendar_review=COMPLETE_REVIEW,
    )
    assert all(row["state"] == CALENDAR_UNRESOLVED for row in result.rows)
    assert "unresolved_calendar_date" in _kinds(result)
    assert result.report["denominator"]["denominator_qualified"] is False


def test_annual_with_no_holiday_rows_is_not_weekday_fallback() -> None:
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of="2024-01-03T15:35:00+05:30",
        annual_calendars=[_annual(2024, "NSE Capital Market trading holidays for the calendar year 2024\n")],
        calendar_review=COMPLETE_REVIEW,
    )
    assert all(row["state"] == CALENDAR_UNRESOLVED for row in result.rows)
    assert result.report["denominator"]["denominator_qualified"] is False


def test_as_of_requires_explicit_offset() -> None:
    with pytest.raises(ValueError):
        build_ledger(
            start=date(2024, 1, 1),
            as_of="2024-01-10T15:35:00Z",
            annual_calendars=[_annual(2024)],
        )


# ---------------------------------------------------------------------------
# Denominator qualification
# ---------------------------------------------------------------------------


def test_muhurat_missing_interval_blocks_denominator() -> None:
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
    )
    muhurat = _by_date(result)["2024-11-01"]
    assert muhurat["state"] == EXPECTED
    assert muhurat["session_type"] == "muhurat"
    assert muhurat["closure_known"] is False
    assert result.report["denominator"]["denominator_qualified"] is False
    assert result.report["denominator"]["qualified_expected_denominator"] is None
    assert "missing_session_interval" in _kinds(result)


def test_absent_review_blocks_denominator_without_certification() -> None:
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
    )
    assert result.report["denominator"]["denominator_qualified"] is False
    assert result.report["calendar_review"]["supplied"] is False
    assert "calendar_review_absent" in _kinds(result)


def test_uninterpreted_retained_notice_blocks_denominator() -> None:
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        notices=[_notice()],
        calendar_review=COMPLETE_REVIEW,
    )
    assert "uninterpreted_retained_notice" in _kinds(result)
    assert result.report["denominator"]["denominator_qualified"] is False


def test_hash_bound_amendment_supplies_interval_and_qualifies() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[_muhurat_amendment(notice.source.sha256)],
        calendar_review=COMPLETE_REVIEW,
    )
    muhurat = _by_date(result)["2024-11-01"]
    assert muhurat["closure_known"] is True
    assert muhurat["open_at"] == "2024-11-01T18:00:00+05:30"
    assert result.report["amendments"]["applied_count"] == 1
    assert result.report["denominator"]["denominator_qualified"] is True


# ---------------------------------------------------------------------------
# Amendment boundaries
# ---------------------------------------------------------------------------


def test_amendment_with_unretained_source_hash_is_rejected() -> None:
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        amendments=[_muhurat_amendment("ab" * 32)],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report["amendments"]["applied_count"] == 0
    assert result.report["amendments"]["rejected_count"] == 1
    assert result.report["denominator"]["denominator_qualified"] is False


def test_amendment_outside_preflight_range_is_rejected() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[_muhurat_amendment(notice.source.sha256, effective_date="2023-05-01")],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report["amendments"]["applied_count"] == 0


def test_amendment_published_after_preflight_is_rejected() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[_muhurat_amendment(notice.source.sha256, publication_date="2024-12-01")],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report["amendments"]["applied_count"] == 0
    assert "amendment_rejected" in _kinds(result)
    assert result.report["denominator"]["denominator_qualified"] is False


def test_amendment_not_available_at_preflight_is_rejected() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[_muhurat_amendment(notice.source.sha256, available_at="2026-01-01T00:00:00+00:00")],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report["amendments"]["applied_count"] == 0
    assert "amendment_rejected" in _kinds(result)


def test_holiday_replacement_moves_the_session() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 3, 20),
        as_of="2024-03-29T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[{
            "effective_date": "2024-03-26",
            "session_type": "holiday",
            "label": "Holi (moved)",
            "source_sha256": notice.source.sha256,
            "publication_date": "2024-03-20",
            "replacement_of": "2024-03-25",
        }],
        calendar_review=COMPLETE_REVIEW,
    )
    rows = _by_date(result)
    assert rows["2024-03-26"]["state"] == NON_SESSION
    assert rows["2024-03-25"]["state"] == EXPECTED
    assert "replaced" in rows["2024-03-25"]["reason"]


def test_special_weekend_session_becomes_expected() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[{
            "effective_date": "2024-01-06",
            "session_type": "special",
            "label": "DR switch session",
            "source_sha256": notice.source.sha256,
            "publication_date": "2023-12-13",
            "open_at": "2024-01-06T09:15:00+05:30",
            "close_at": "2024-01-06T15:30:00+05:30",
        }],
        calendar_review=COMPLETE_REVIEW,
    )
    row = _by_date(result)["2024-01-06"]
    assert row["state"] == EXPECTED
    assert row["session_type"] == "special"


def test_conflicting_amendments_reject_the_second() -> None:
    notice = _notice()
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[
            _muhurat_amendment(notice.source.sha256, effective_date="2024-11-04", session_type="holiday", open_at=None, close_at=None),
            _muhurat_amendment(notice.source.sha256, effective_date="2024-11-04", session_type="special"),
        ],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report["amendments"]["applied_count"] == 1
    assert result.report["amendments"]["rejected_count"] == 1


def test_amendment_interval_must_fall_on_the_effective_date() -> None:
    with pytest.raises(LedgerError):
        parse_amendment({
            "effective_date": "2024-11-01",
            "session_type": "muhurat",
            "source_sha256": "ab" * 32,
            "open_at": "2024-11-02T18:00:00+05:30",
            "close_at": "2024-11-02T19:00:00+05:30",
        })


# ---------------------------------------------------------------------------
# Legacy reconciliation
# ---------------------------------------------------------------------------


def _inventory() -> dict:
    return {
        "root": "/outside/repo/data/bhavcopy/cm",
        "artifact_count": 1737,
        "rows": 3138017,
        "columns": ["close", "date", "symbol"],
        "unique_payload_count": 1658,
        "repeated_artifact_count": 79,
        "repeated_payload_groups": [
            {"payload_hash": "aa" * 32, "dates": ["2024-01-26", "2024-01-29"]},
        ],
        "read_failures": [],
        "status": "inventory_only_calendar_reconciliation_required",
    }


def test_expected_duplicate_is_gap_and_holiday_duplicate_is_rejected() -> None:
    result = build_ledger(
        start=date(2024, 1, 20),
        as_of="2024-01-31T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
        legacy_inventory=_inventory(),
    )
    rows = _by_date(result)
    assert rows["2024-01-26"]["legacy_status"] == LEGACY_NON_SESSION
    assert rows["2024-01-29"]["legacy_status"] == LEGACY_EXPECTED_GAP
    assert "legacy_expected_date_duplicate" in _kinds(result)


def test_unique_payload_is_never_admitted_as_a_session() -> None:
    baseline = build_ledger(
        start=date(2024, 1, 20),
        as_of="2024-01-31T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
    )
    with_inventory = build_ledger(
        start=date(2024, 1, 20),
        as_of="2024-01-31T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
        legacy_inventory=_inventory(),
    )
    assert with_inventory.report["legacy_reconciliation"]["admitted_session_count"] is None
    assert with_inventory.report["denominator"]["expected_session_count"] == baseline.report["denominator"]["expected_session_count"]
    assert "legacy_unique_dates_not_enumerated" in _kinds(with_inventory)


def test_complete_per_file_inventory_clears_inventory_blocker_without_admission() -> None:
    inventory = _inventory() | {"per_file_inventory_complete": True}
    result = build_ledger(
        start=date(2024, 1, 20),
        as_of="2024-01-31T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
        legacy_inventory=inventory,
    )
    assert "legacy_unique_dates_not_enumerated" not in _kinds(result)
    assert result.report["legacy_reconciliation"]["per_file_inventory_complete"] is True
    assert result.report["legacy_reconciliation"]["admitted_session_count"] is None
    assert "legacy_expected_date_duplicate" in _kinds(result)


def test_malformed_duplicate_group_fails_closed() -> None:
    with pytest.raises(LedgerError):
        build_ledger(
            start=date(2024, 1, 1),
            as_of=AS_OF_JAN,
            annual_calendars=[_annual(2024)],
            calendar_review=COMPLETE_REVIEW,
            legacy_inventory={
                "repeated_payload_groups": [{"payload_hash": "not-a-hash", "dates": ["2024-01-26"]}],
            },
        )


# ---------------------------------------------------------------------------
# Cash annotation
# ---------------------------------------------------------------------------


def test_absent_cash_root_is_explicit_and_non_blocking(tmp_path: Path) -> None:
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
        cash_root=None,
    )
    assert all(row["cash"]["status"] == CASH_ROOT_ABSENT for row in result.rows)
    assert result.report["cash_reconciliation"]["root_present"] is False
    assert result.report["denominator"]["denominator_qualified"] is True


def test_accepted_cash_receipt_marks_admitted_price(tmp_path: Path) -> None:
    cash_root = tmp_path / "cash"
    _accepted_cash_receipt(cash_root, "2024-01-08")
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
        cash_root=cash_root,
    )
    row = _by_date(result)["2024-01-08"]
    assert row["cash"]["status"] == CASH_ADMITTED
    assert result.report["cash_reconciliation"]["status_counts"][CASH_ADMITTED] == 1


def test_rejected_cash_receipt_marks_rejected(tmp_path: Path) -> None:
    cash_root = tmp_path / "cash"
    attempt = cash_root / "requests" / "2024-01-08" / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "receipt.json").write_text(json.dumps({
        "validation_result": {"status": "rejected", "reason": "HTTP status 404"},
        "body_sha256": hashlib.sha256(b"error").hexdigest(),
    }))
    result = build_ledger(
        start=date(2024, 1, 1),
        as_of=AS_OF_JAN,
        annual_calendars=[_annual(2024)],
        calendar_review=COMPLETE_REVIEW,
        cash_root=cash_root,
    )
    row = _by_date(result)["2024-01-08"]
    assert row["cash"]["status"] == CASH_REJECTED
    assert result.report["cash_reconciliation"]["status_counts"][CASH_REJECTED] == 1


# ---------------------------------------------------------------------------
# Retained-source loaders fail closed
# ---------------------------------------------------------------------------


def test_annual_loader_binds_manifest_hash(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "annual").mkdir(parents=True)
    body = b"%PDF-1.7 annual body"
    (raw / "annual" / "2024.pdf").write_bytes(body)
    (raw / "manifest.json").write_text(json.dumps({"sources": [{
        "year": 2024,
        "requested_url": URL,
        "sha256": hashlib.sha256(body).hexdigest(),
        "byte_count": len(body),
        "retrieved_at": RETRIEVED,
        "available_at": None,
    }]}))
    annuals, sources = load_annual_calendars(raw, pdf_text=lambda _: "Date : December 13, 2023\nCapital Market 2024")
    assert annuals[0].year == 2024
    assert annuals[0].source.publication_date == "2023-12-13"
    assert sources[0].decision == ACCEPTED


def test_annual_loader_rejects_tampered_bytes(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "annual").mkdir(parents=True)
    (raw / "annual" / "2024.pdf").write_bytes(b"tampered")
    (raw / "manifest.json").write_text(json.dumps({"sources": [{
        "year": 2024,
        "requested_url": URL,
        "sha256": hashlib.sha256(b"original").hexdigest(),
        "byte_count": 8,
        "retrieved_at": RETRIEVED,
    }]}))
    with pytest.raises(LedgerError):
        load_annual_calendars(raw, pdf_text=lambda _: "text")


def test_evidence_loader_fails_closed_on_changed_body(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    (root / "bodies").mkdir(parents=True)
    body = b"%PDF-1.7 annual body"
    digest = hashlib.sha256(body).hexdigest()
    body_path = root / "bodies" / f"{digest}.bin"
    body_path.write_bytes(body)
    (root / "receipts.jsonl").write_text(json.dumps({
        "journal_kind": "attempt",
        "logical_name": "CMTR71775.pdf",
        "source_kind": "annual_calendar",
        "declared_year": 2026,
        "requested_url": URL,
        "validation_decision": "accepted",
        "sha256": digest,
        "byte_count": len(body),
        "body_path": f"bodies/{digest}.bin",
        "retrieved_at": RETRIEVED,
    }) + "\n")

    annuals, _, _ = load_calendar_evidence(root, pdf_text=lambda _: "Capital Market 2026")
    assert annuals and annuals[0].year == 2026

    body_path.write_bytes(b"tampered")
    annuals2, notices, sources = load_calendar_evidence(root, pdf_text=lambda _: "Capital Market 2026")
    assert annuals2 == [] and notices == []
    assert sources[0].decision == "rejected"


def test_evidence_loader_reads_official_pdf_zip_in_memory_and_preserves_archive_provenance(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    (root / "bodies").mkdir(parents=True)
    body = _zip_body({
        "packet/CMTR60338-a.pdf": b"%PDF-1.7 first",
        "packet/CMTR60338-b.pdf": b"%PDF-1.7 second",
    })
    digest = hashlib.sha256(body).hexdigest()
    body_path = root / "bodies" / f"{digest}.bin"
    body_path.write_bytes(body)
    url = "https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip"
    (root / "receipts.jsonl").write_text(json.dumps({
        "journal_kind": "attempt",
        "logical_name": "CMTR60338.zip",
        "source_kind": SPECIAL_SESSION,
        "declared_year": 2026,
        "requested_url": url,
        "validation_decision": ACCEPTED,
        "media_kind": "zip",
        "sha256": digest,
        "byte_count": len(body),
        "body_path": f"bodies/{digest}.bin",
        "retrieved_at": RETRIEVED,
        "headers": {"Content-Type": "application/zip"},
    }) + "\n")

    seen: list[bytes] = []

    def pdf_text(member: bytes) -> str:
        seen.append(member)
        return member.decode("ascii")

    annuals, notices, sources = load_calendar_evidence(root, pdf_text=pdf_text)

    assert annuals == []
    assert len(notices) == 1
    assert "ZIP MEMBER: packet/CMTR60338-a.pdf" in notices[0].text
    assert "END ZIP MEMBER: packet/CMTR60338-a.pdf" in notices[0].text
    assert "ZIP MEMBER: packet/CMTR60338-b.pdf" in notices[0].text
    assert notices[0].source.sha256 == digest
    assert notices[0].source.byte_count == len(body)
    assert notices[0].source.source_path == f"bodies/{digest}.bin"
    assert seen == [b"%PDF-1.7 first", b"%PDF-1.7 second"]
    assert body_path.read_bytes() == body


def test_evidence_loader_downgrades_corrupt_zip_even_if_receipt_claims_accepted(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    (root / "bodies").mkdir(parents=True)
    body = b"PK\x03\x04not a ZIP"
    digest = hashlib.sha256(body).hexdigest()
    (root / "bodies" / f"{digest}.bin").write_bytes(body)
    (root / "receipts.jsonl").write_text(json.dumps({
        "journal_kind": "attempt",
        "logical_name": "CMTR60338.zip",
        "source_kind": SPECIAL_SESSION,
        "declared_year": 2026,
        "requested_url": "https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        "validation_decision": ACCEPTED,
        "sha256": digest,
        "byte_count": len(body),
        "body_path": f"bodies/{digest}.bin",
        "retrieved_at": RETRIEVED,
    }) + "\n")

    annuals, notices, sources = load_calendar_evidence(root, pdf_text=lambda _: "unused")

    assert annuals == [] and notices == []
    assert sources[0].decision == "rejected"
    assert "malformed" in str(sources[0].rejection_reason)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_requires_as_of() -> None:
    from scripts.data.build_daily_session_ledger import main

    with pytest.raises(SystemExit):
        main(["--output-root", "unused"])


def test_cli_refuses_existing_output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("scripts.data.build_daily_session_ledger", fromlist=["main"])
    annual = _annual(2024)
    monkeypatch.setattr(module, "load_annual_calendars", lambda root: ([annual], [annual.source]))
    monkeypatch.setattr(module, "load_calendar_evidence", lambda root: ([], [], []))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(_inventory()))
    output = tmp_path / "ledger"
    output.mkdir()

    with pytest.raises(SystemExit, match="existing immutable output root"):
        module.main([
            "--start", "2024-01-01",
            "--as-of", AS_OF_JAN,
            "--inventory", str(inventory),
            "--cash-root", str(tmp_path / "no-cash"),
            "--output-root", str(output),
        ])


def test_cli_emits_immutable_report_and_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("scripts.data.build_daily_session_ledger", fromlist=["main"])
    annual = _annual(2024)
    monkeypatch.setattr(module, "load_annual_calendars", lambda root: ([annual], [annual.source]))
    monkeypatch.setattr(module, "load_calendar_evidence", lambda root: ([], [], []))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(_inventory()))
    output = tmp_path / "ledger"

    exit_code = module.main([
        "--start", "2024-01-01",
        "--as-of", AS_OF_JAN,
        "--inventory", str(inventory),
        "--cash-root", str(tmp_path / "no-cash"),
        "--output-root", str(output),
    ])

    assert exit_code == 0
    report = json.loads((output / "report.json").read_text())
    lines = [json.loads(line) for line in (output / "sessions.jsonl").read_text().splitlines() if line.strip()]
    assert report["schema"] == "meridian.daily-session-ledger.report.v1"
    assert len(lines) == report["date_grid"]["calendar_date_count"]
    assert report["denominator"]["denominator_qualified"] is False  # review absent without certification


# ---------------------------------------------------------------------------
# Multi-interval sessions and blocker separation
# ---------------------------------------------------------------------------


def _dr_notice() -> NoticeSource:
    return NoticeSource(
        _source("MSD60677.pdf", "dr-2024", kind="special_session", year=2024, publication="2024-02-14"),
        "Two Capital Market windows retained.",
    )


def _dr_amendment(sha: str, **overrides) -> dict:
    payload = {
        "effective_date": "2024-03-02",
        "session_type": "special",
        "label": "DR switchover",
        "source_sha256": sha,
        "publication_date": "2024-02-14",
        "intervals": [
            {"open_at": "2024-03-02T09:15:00+05:30", "close_at": "2024-03-02T10:00:00+05:30"},
            {"open_at": "2024-03-02T11:30:00+05:30", "close_at": "2024-03-02T12:30:00+05:30"},
        ],
    }
    payload.update(overrides)
    return payload


def test_multi_interval_amendment_is_ordered_and_closure_is_last_close() -> None:
    notice = _dr_notice()
    result = build_ledger(
        start=date(2024, 3, 1),
        as_of="2024-03-10T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        notices=[notice],
        amendments=[_dr_amendment(notice.source.sha256)],
        calendar_review=COMPLETE_REVIEW,
    )
    row = _by_date(result)["2024-03-02"]
    assert row["state"] == EXPECTED
    assert row["session_type"] == "special"
    assert row["interval_count"] == 2
    assert row["intervals"][0] == {"open_at": "2024-03-02T09:15:00+05:30", "close_at": "2024-03-02T10:00:00+05:30"}
    assert row["open_at"] == "2024-03-02T09:15:00+05:30"
    assert row["close_at"] == "2024-03-02T12:30:00+05:30"  # closure is the last close
    assert row["closure_known"] is True
    assert "missing_session_interval" not in _kinds(result)
    assert result.report["denominator"]["denominator_qualified"] is True


def test_reversed_multi_interval_is_rejected() -> None:
    with pytest.raises(LedgerError, match="ordered and non-overlapping"):
        parse_amendment(_dr_amendment("ab" * 32, intervals=[
            {"open_at": "2024-03-02T11:30:00+05:30", "close_at": "2024-03-02T12:30:00+05:30"},
            {"open_at": "2024-03-02T09:15:00+05:30", "close_at": "2024-03-02T10:00:00+05:30"},
        ]))


def test_intervals_mixed_with_single_fields_are_rejected() -> None:
    with pytest.raises(LedgerError, match="must not mix intervals"):
        parse_amendment(_dr_amendment(
            "ab" * 32,
            open_at="2024-03-02T09:15:00+05:30",
            close_at="2024-03-02T12:30:00+05:30",
        ))


def test_contested_dates_are_unresolved_not_defaulted() -> None:
    review = COMPLETE_REVIEW | {
        "contested_dates": [{"session_date": "2024-03-25", "reason": "indirect clearing notice only"}]
    }
    result = build_ledger(
        start=date(2024, 3, 1),
        as_of="2024-04-01T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=review,
    )
    row = _by_date(result)["2024-03-25"]
    assert row["state"] == CALENDAR_UNRESOLVED
    assert row["reason"].startswith("contested:")
    assert "contested_calendar_date" in _kinds(result)
    assert result.report["denominator"]["denominator_qualified"] is False


def test_report_separates_denominator_from_qualification_blockers() -> None:
    review = COMPLETE_REVIEW | {
        "contested_dates": [{"session_date": "2024-03-25", "reason": "indirect clearing notice only"}]
    }
    result = build_ledger(
        start=date(2024, 3, 1),
        as_of="2024-04-01T15:35:00+05:30",
        annual_calendars=[_annual(2024)],
        calendar_review=review,
    )
    report = result.report
    assert report["denominator_blockers"]
    assert all(entry["scope"] in {"calendar", "amendment", "review"} for entry in report["denominator_blockers"])
    assert all(entry["scope"] in {"legacy", "cash"} for entry in report["qualification_blockers"])
    assert report["blocker_summary"]["denominator_blocking"] == len(report["denominator_blockers"])
    assert report["blocker_summary"]["qualification_only"] == len(report["qualification_blockers"])
    assert report["denominator"]["denominator_blocker_count"] == len(report["denominator_blockers"])


def test_indirect_notice_is_labelled_and_not_uninterpreted() -> None:
    notice = _notice()
    review = COMPLETE_REVIEW | {"indirect_notice_shas": [notice.source.sha256]}
    result = build_ledger(
        start=date(2024, 10, 28),
        as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)],
        notices=[notice],
        calendar_review=review,
    )
    kinds = _kinds(result)
    assert "indirect_notice_retained" in kinds
    assert "uninterpreted_retained_notice" not in kinds


def test_split_session_binds_both_halt_and_reopening_notices() -> None:
    halt = _source('halt.pdf', 'halt', kind=SPECIAL_SESSION, publication='2024-01-08')
    reopening = _source('reopen.pdf', 'reopen', kind=SPECIAL_SESSION, publication='2024-01-08')
    amendment = {
        'effective_date': '2024-01-08', 'session_type': 'special',
        'source_sha256': reopening.sha256,
        'supporting_source_sha256s': [halt.sha256],
        'intervals': [
            {'open_at': '2024-01-08T09:15:00+05:30', 'close_at': '2024-01-08T11:40:00+05:30'},
            {'open_at': '2024-01-08T15:45:00+05:30', 'close_at': '2024-01-08T17:00:00+05:30'},
        ],
    }
    result = build_ledger(
        start=date(2024, 1, 8), as_of='2024-01-08T17:01:00+05:30',
        annual_calendars=[_annual(2024)],
        notices=[NoticeSource(halt, 'halt'), NoticeSource(reopening, 'reopening')],
        retained_sources=[halt, reopening], amendments=[amendment], calendar_review=COMPLETE_REVIEW,
    )
    assert result.report['denominator']['denominator_qualified'] is True
    assert _by_date(result)['2024-01-08']['interval_count'] == 2
    assert result.report['amendments']['applied'][0]['supporting_source_sha256s'] == [halt.sha256]


@pytest.mark.parametrize('supporting', ['missing', 'rejected', 'future'])
def test_unqualified_supporting_notice_blocks_amendment(supporting: str) -> None:
    main = _notice()
    support = _source(
        'support.pdf', 'support', kind=SPECIAL_SESSION,
        decision='rejected' if supporting == 'rejected' else ACCEPTED,
        publication='2025-01-01' if supporting == 'future' else '2024-10-19',
    )
    result = build_ledger(
        start=date(2024, 11, 1), as_of=AS_OF_NOV,
        annual_calendars=[_annual(2024)], notices=[main],
        retained_sources=[] if supporting == 'missing' else [support],
        amendments=[_muhurat_amendment(main.source.sha256, supporting_source_sha256s=[support.sha256])],
        calendar_review=COMPLETE_REVIEW,
    )
    assert result.report['denominator']['denominator_qualified'] is False
    assert result.report['amendments']['rejected_count'] == 1


def _cas_amendment(sha: str, day: str = '2024-01-08') -> dict:
    def window(start: str, end: str) -> dict:
        return {'open_at': f'{day}T{start}:00+05:30', 'close_at': f'{day}T{end}:00+05:30'}
    return {
        'effective_date': day, 'session_type': 'regular', 'source_sha256': sha,
        'intervals': [window('09:15', '15:35')],
        'phases': [
            {'phase': 'continuous', 'security_scope': 'CAS eligible', **window('09:15', '15:15')},
            {'phase': 'continuous', 'security_scope': 'non-CAS', **window('09:15', '15:30')},
            {'phase': 'closing_auction', 'security_scope': 'CAS eligible', **window('15:15', '15:35')},
            {'phase': 'post_close', 'security_scope': 'eligible post-close securities', **window('15:50', '16:00')},
        ],
    }


def test_cas_keeps_regular_date_and_waits_for_price_forming_close() -> None:
    notice = NoticeSource(_source('cas.pdf', 'cas', kind=SPECIAL_SESSION, publication='2024-01-01'), 'CAS')
    kwargs = dict(start=date(2024, 1, 5), annual_calendars=[_annual(2024)], notices=[notice],
                  amendments=[_cas_amendment(notice.source.sha256)], calendar_review=COMPLETE_REVIEW)
    before = build_ledger(as_of='2024-01-08T15:32:00+05:30', **kwargs)
    assert before.report['latest_completed_session'] == '2024-01-05'
    after = build_ledger(as_of='2024-01-08T15:35:00+05:30', **kwargs)
    row = _by_date(after)['2024-01-08']
    assert row['session_type'] == 'regular'
    assert row['close_at'] == '2024-01-08T15:35:00+05:30'
    assert row['interval_semantics'] == 'venue_price_forming_phase_union'
    assert len(row['phases']) == 4
    assert after.report['denominator']['denominator_qualified'] is True


def test_cas_phase_metadata_cannot_hide_early_close_or_create_weekend() -> None:
    notice = NoticeSource(_source('cas.pdf', 'cas', kind=SPECIAL_SESSION, publication='2024-01-01'), 'CAS')
    amendment = _cas_amendment(notice.source.sha256)
    amendment['intervals'][0]['close_at'] = '2024-01-08T15:30:00+05:30'
    with pytest.raises(LedgerError, match='phase union'):
        parse_amendment(amendment)
    result = build_ledger(
        start=date(2024, 1, 5), as_of=AS_OF_JAN, annual_calendars=[_annual(2024)], notices=[notice],
        amendments=[_cas_amendment(notice.source.sha256, '2024-01-06')], calendar_review=COMPLETE_REVIEW,
    )
    assert _by_date(result)['2024-01-06']['state'] == NON_SESSION
    assert result.report['denominator']['denominator_qualified'] is False
