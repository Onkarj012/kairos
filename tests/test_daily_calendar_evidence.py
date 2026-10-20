from __future__ import annotations

import fcntl
import io
import json
import zipfile
from pathlib import Path

import pytest

from ingest.daily_calendar_evidence import (
    ANNUAL_CALENDAR,
    HTML_MEDIA,
    MAX_ATTEMPTS_PER_URL,
    MAX_COLLECTION_SECONDS,
    MAX_ZIP_EXPANDED_BYTES,
    MIN_START_SPACING_SECONDS,
    PDF_MEDIA,
    RETAINED_UNQUALIFIED,
    RETRY_WAIT_SECONDS,
    SPECIAL_SESSION,
    UNKNOWN_MEDIA,
    ZIP_MEDIA,
    BoundedRetriever,
    DailyCalendarEvidenceError,
    EvidenceStore,
    PdfExtractionError,
    RetrievalState,
    TransportResponse,
    classify_media,
    collect_source,
    default_pdf_text_extractor,
    replay_retained,
    sha256_hex,
    validate_source_response,
    validate_official_url,
)
from scripts.data.collect_daily_calendar import build_report, main


ANNUAL_URL = "https://archives.nseindia.com/content/circulars/CMTR88888.pdf"
NOTICE_URL = "https://www.nseindia.com/resources/special-session-2026-10-21.pdf"
OTHER_OFFICIAL_URL = "https://nseindia.com/redirected-annual-calendar.pdf"
TIMESTAMP = "2026-09-15T00:00:00+00:00"
VALID_ANNUAL_TEXT = "NSE Capital Market trading holidays for the calendar year 2026: January 26, 2026 Republic Day"
SPECIAL_TEXT = "NSE special session notice for Muhurat trading"


class FakeClock:
    def __init__(self, now_value: float = 0.0) -> None:
        self.now_value = now_value
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_value += seconds


def _retriever(clock: FakeClock, transport, *, state=None, state_path=None) -> BoundedRetriever:
    return BoundedRetriever(
        transport=transport,
        clock=clock.now,
        sleep=clock.sleep,
        wall_clock=clock.now,
        timestamp=lambda: TIMESTAMP,
        state=state,
        state_path=state_path,
    )


def _extractor(text: str):
    def extract(body: bytes) -> str:
        return text

    return extract


def _failing_extractor(body: bytes) -> str:
    raise PdfExtractionError("fixture is not a parseable PDF")


def _zip_body(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return output.getvalue()


def test_official_host_validation_is_exact() -> None:
    assert validate_official_url(ANNUAL_URL) == ANNUAL_URL
    for bad in (
        "https://evil.example/circular.pdf",
        "http://www.nseindia.com/circular.pdf",
        "https://user:pw@www.nseindia.com/circular.pdf",
        "https://www.nseindia.com/circular.pdf?x=1",
    ):
        with pytest.raises(ValueError):
            validate_official_url(bad)


def test_media_classification_requires_pdf_magic() -> None:
    assert classify_media({"Content-Type": "application/pdf"}, b"%PDF-1.7 body") == PDF_MEDIA
    assert classify_media({"Content-Type": "text/html; charset=utf-8"}, b"<!DOCTYPE html>") == HTML_MEDIA
    assert classify_media({"Content-Type": "text/html"}, b"<html><body>oops") == HTML_MEDIA
    assert classify_media({"Content-Type": "application/json"}, b'{"error": "x"}') == UNKNOWN_MEDIA
    assert classify_media({"Content-Type": "application/pdf"}, b"<!DOCTYPE html><html>") == HTML_MEDIA
    assert classify_media({"Content-Type": "application/pdf"}, b"not a document") == UNKNOWN_MEDIA


def test_special_session_zip_retains_exact_archive_and_hash(tmp_path: Path) -> None:
    body = _zip_body({
        "circulars/CMTR60338.pdf": b"%PDF-1.7 circular",
        "Annexure.xlsx": b"PK spreadsheet attachment retained, not interpreted",
    })
    clock = FakeClock()
    store = EvidenceStore(tmp_path)
    extracted_members: list[bytes] = []

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/zip"}, body, url)

    def extractor(member: bytes) -> str:
        extracted_members.append(member)
        return SPECIAL_TEXT

    receipt = collect_source(
        url="https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(clock, transport),
        store=store,
        text_extractor=extractor,
    )

    assert receipt.validation_decision == "accepted"
    assert receipt.media_kind == ZIP_MEDIA
    assert receipt.body == body
    assert receipt.requested_url.endswith("CMTR60338.zip")
    assert receipt.metadata()["sha256"] == sha256_hex(body)
    assert extracted_members == [b"%PDF-1.7 circular"]
    assert (store.root / str(receipt.body_path)).read_bytes() == body


def test_special_session_zip_parser_failure_is_retained_unqualified(tmp_path: Path) -> None:
    body = _zip_body({"CMTR60338.pdf": b"%PDF-1.7 circular"})
    clock = FakeClock()
    store = EvidenceStore(tmp_path)

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/zip"}, body, url)

    receipt = collect_source(
        url="https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(clock, transport),
        store=store,
        text_extractor=_failing_extractor,
    )

    assert receipt.validation_decision == RETAINED_UNQUALIFIED
    assert receipt.body == body
    assert (store.root / str(receipt.body_path)).read_bytes() == body


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"PK\x03\x04not a ZIP", "malformed"),
        (_zip_body({"circulars/readme.txt": b"not a PDF"}), "no PDF members"),
        (_zip_body({"circular.pdf": b"not a PDF"}), "not a PDF"),
        (_zip_body({"../CMTR60338.pdf": b"%PDF-1.7 circular"}), "unsafe path"),
        (_zip_body({"CMTR60338.pdf": b"%PDF-1.7" + b"x" * (MAX_ZIP_EXPANDED_BYTES + 1)}), "oversized"),
    ],
)
def test_special_session_zip_rejects_invalid_archives(body: bytes, reason: str) -> None:
    outcome = validate_source_response(
        requested_url="https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        status=200,
        final_url="https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        headers={"Content-Type": "application/zip"},
        body=body,
        source_kind=SPECIAL_SESSION,
        declared_year=2026,
        text_extractor=_extractor(SPECIAL_TEXT),
    )
    assert outcome.media_kind == ZIP_MEDIA
    assert outcome.decision == "rejected"
    assert reason.casefold() in str(outcome.reason).casefold()


def test_annual_calendar_zip_remains_pdf_only() -> None:
    body = _zip_body({"CMTR60338.pdf": b"%PDF-1.7 annual"})
    outcome = validate_source_response(
        requested_url="https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        status=200,
        final_url="https://nsearchives.nseindia.com/content/circulars/CMTR60338.zip",
        headers={"Content-Type": "application/zip"},
        body=body,
        source_kind=ANNUAL_CALENDAR,
        declared_year=2026,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )
    assert outcome.media_kind == ZIP_MEDIA
    assert outcome.decision == "rejected"
    assert "must be PDF" in str(outcome.reason)


def test_http_error_body_is_retained_as_rejected(tmp_path: Path) -> None:
    body = b"<html><body>503 service unavailable</body></html>"
    clock = FakeClock()
    store = EvidenceStore(tmp_path)

    def transport(url: str) -> TransportResponse:
        return TransportResponse(503, {"Content-Type": "text/html"}, body, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(clock, transport),
        store=store,
        text_extractor=_failing_extractor,
    )

    assert receipt.validation_decision == "rejected"
    assert receipt.media_kind == HTML_MEDIA
    assert receipt.body == body
    assert receipt.attempts == MAX_ATTEMPTS_PER_URL
    assert clock.sleeps == list(RETRY_WAIT_SECONDS)
    retained = store.root / "bodies" / f"{sha256_hex(body)}.bin"
    assert retained.read_bytes() == body
    assert receipt.body_path == f"bodies/{sha256_hex(body)}.bin"
    metadata = receipt.metadata()
    assert metadata["status"] == 503
    assert metadata["sha256"] == sha256_hex(body)
    assert metadata["parser_version"]
    assert metadata["is_calendar_ledger"] is False

    rows = [json.loads(line) for line in store.receipts_path.read_text().splitlines() if line.strip()]
    assert len(rows) == MAX_ATTEMPTS_PER_URL
    assert all(row["sha256"] == sha256_hex(body) for row in rows)


def test_transport_failure_is_retained_without_hidden_retry(tmp_path: Path) -> None:
    calls: list[str] = []
    clock = FakeClock()

    def transport(url: str) -> TransportResponse:
        calls.append(url)
        raise TimeoutError("dial timed out")

    receipt = collect_source(
        url=NOTICE_URL,
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(clock, transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_failing_extractor,
    )

    assert len(calls) == MAX_ATTEMPTS_PER_URL
    assert receipt.attempts == MAX_ATTEMPTS_PER_URL
    assert receipt.body == b""
    assert receipt.status is None
    assert receipt.validation_decision == "rejected"
    assert all(record.error and "TimeoutError" in record.error for record in receipt.attempts_detail)
    assert clock.sleeps == list(RETRY_WAIT_SECONDS)


def test_retryable_status_stops_at_an_accepted_response(tmp_path: Path) -> None:
    clock = FakeClock()
    valid_pdf = b"%PDF-1.7 annual calendar body"
    bodies = [b"<html>busy</html>", valid_pdf]

    def transport(url: str) -> TransportResponse:
        body = bodies.pop(0)
        accepted = body is valid_pdf
        return TransportResponse(200 if accepted else 500, {}, body, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(clock, transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )

    assert receipt.accepted
    assert receipt.attempts == 2
    assert clock.sleeps == [RETRY_WAIT_SECONDS[0]]


def test_every_attempt_body_is_retained_and_journaled(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    valid_pdf = b"%PDF-1.7 annual calendar body"
    bodies = [b"first server error", b"second server error", valid_pdf]

    def transport(url: str) -> TransportResponse:
        body = bodies.pop(0)
        accepted = body is valid_pdf
        return TransportResponse(200 if accepted else 500, {}, body, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )

    rows = [json.loads(line) for line in store.receipts_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 3
    assert receipt.attempts == 3
    stored_hashes = {row["sha256"] for row in rows}
    assert len(stored_hashes) == 3
    for row in rows:
        body_file = store.root / row["body_path"]
        assert body_file.is_file()
        assert body_file.read_bytes() == next(body for body in (b"first server error", b"second server error", valid_pdf) if sha256_hex(body) == row["sha256"])


def test_annual_calendar_rejects_html_mislabelled_as_pdf(tmp_path: Path) -> None:
    html = b"<!DOCTYPE html><html><body>not a calendar</body></html>"
    store = EvidenceStore(tmp_path)

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, html, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )

    assert not receipt.accepted
    assert receipt.media_kind == HTML_MEDIA
    assert "must be PDF" in (receipt.rejection_reason or "")
    assert (store.root / "bodies" / f"{sha256_hex(html)}.bin").read_bytes() == html


def test_annual_calendar_header_only_fake_is_not_admitted(tmp_path: Path) -> None:
    body = b"%PDF-1.7 no real document structure"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_failing_extractor,
    )

    assert not receipt.accepted
    assert receipt.validation_decision == RETAINED_UNQUALIFIED
    assert receipt.body == body


def test_default_pdf_text_extractor_rejects_header_only_fake() -> None:
    with pytest.raises(PdfExtractionError):
        default_pdf_text_extractor(b"%PDF-1.7 this is not a parseable pdf")


def test_annual_calendar_wrong_year_is_rejected(tmp_path: Path) -> None:
    body = b"%PDF-1.7 annual calendar body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_extractor("Capital Market holidays 2025: 26-Jan-2025"),
    )

    assert not receipt.accepted
    assert "declared year" in (receipt.rejection_reason or "")


def test_special_session_html_error_page_is_rejected(tmp_path: Path) -> None:
    body = b"<html><head><title>404 Not Found</title></head><body>Page not found</body></html>"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "text/html"}, body, url)

    receipt = collect_source(
        url=NOTICE_URL,
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_failing_extractor,
    )

    assert not receipt.accepted
    assert "error page" in (receipt.rejection_reason or "")


def test_special_session_accepts_genuine_html_without_authoritative_dates(tmp_path: Path) -> None:
    body = b"<html><body>Special session notice: Muhurat trading on 2026-11-08</body></html>"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "text/html"}, body, url)

    receipt = collect_source(
        url=NOTICE_URL,
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_failing_extractor,
    )

    metadata = receipt.metadata()
    assert receipt.accepted
    assert receipt.media_kind == HTML_MEDIA
    assert metadata["authoritative_session_dates"] == []
    assert metadata["publication_date"] is None
    assert metadata["publication_date_basis"] == "source_date_absent"
    assert metadata["available_at_basis"] == "not_authoritatively_published"
    assert metadata["declared_year_basis"] == "cli_supplied_year"


def test_special_session_unqualified_pdf_is_retained_not_admitted(tmp_path: Path) -> None:
    body = b"%PDF-1.7 special notice body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    receipt = collect_source(
        url=NOTICE_URL,
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_failing_extractor,
    )

    assert not receipt.accepted
    assert receipt.validation_decision == RETAINED_UNQUALIFIED


def test_special_session_accepts_genuine_pdf(tmp_path: Path) -> None:
    body = b"%PDF-1.7 special notice body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    receipt = collect_source(
        url=NOTICE_URL,
        source_kind=SPECIAL_SESSION,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_extractor(SPECIAL_TEXT),
    )

    assert receipt.accepted


def test_redirected_final_url_is_rejected_but_bytes_are_retained(tmp_path: Path) -> None:
    body = b"%PDF-1.7 annual calendar body"
    store = EvidenceStore(tmp_path)

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, OTHER_OFFICIAL_URL)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )

    assert not receipt.accepted
    assert "redirected final URL" in (receipt.rejection_reason or "")
    assert (store.root / "bodies" / f"{sha256_hex(body)}.bin").read_bytes() == body


def test_attempt_reservation_is_persisted_before_transport(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    body = b"%PDF-1.7 annual calendar body"
    observed: list[int] = []

    def transport(url: str) -> TransportResponse:
        state = json.loads(store.state_path.read_text(encoding="utf-8"))
        observed.append(state["attempts"][url])
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )

    assert observed == [1]


def test_attempt_budget_does_not_reset_across_resume(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    clock = FakeClock()
    calls: list[str] = []

    def failing(url: str) -> TransportResponse:
        calls.append(url)
        return TransportResponse(500, {}, b"server error", url)

    first = _retriever(clock, failing, state_path=state_path)
    first.state = RetrievalState.load(state_path)
    first.retrieve(ANNUAL_URL)
    assert len(calls) == MAX_ATTEMPTS_PER_URL

    def unexpected(url: str) -> TransportResponse:
        raise AssertionError("transport must not be called when the budget is exhausted")

    resumed = _retriever(FakeClock(clock.now_value), unexpected, state_path=state_path)
    resumed.state = RetrievalState.load(state_path)
    assert resumed.retrieve(ANNUAL_URL) == []
    assert resumed.state.attempts[ANNUAL_URL] == MAX_ATTEMPTS_PER_URL


def test_resume_waits_out_the_retry_delay(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "attempts": {ANNUAL_URL: 1},
                "last_start_wall": {ANNUAL_URL: 100.0},
                "next_eligible_wall": {ANNUAL_URL: 105.0},
                "collection_started_wall": 100.0,
            }
        )
    )
    clock = FakeClock(100.0)

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {}, b"%PDF-1.7", url)

    retriever = _retriever(clock, transport, state_path=state_path)
    retriever.state = RetrievalState.load(state_path)
    retriever.retrieve(ANNUAL_URL)

    assert clock.sleeps == [5.0]
    assert retriever.state.attempts[ANNUAL_URL] == 2


def test_resume_honours_start_spacing(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "attempts": {ANNUAL_URL: 1},
                "last_start_wall": {ANNUAL_URL: 10.0},
                "next_eligible_wall": {},
                "collection_started_wall": 10.0,
            }
        )
    )
    clock = FakeClock(10.0)

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {}, b"%PDF-1.7", url)

    retriever = _retriever(clock, transport, state_path=state_path)
    retriever.state = RetrievalState.load(state_path)
    retriever.retrieve(NOTICE_URL)

    assert clock.sleeps == [MIN_START_SPACING_SECONDS]


def test_request_starts_are_spaced_for_distinct_urls() -> None:
    clock = FakeClock()

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {}, b"%PDF-1.7", url)

    retriever = _retriever(clock, transport)
    retriever.retrieve(ANNUAL_URL)
    retriever.retrieve(NOTICE_URL)

    assert clock.sleeps == [MIN_START_SPACING_SECONDS]


def test_elapsed_budget_stops_collection(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(url: str) -> TransportResponse:
        calls.append(url)
        return TransportResponse(200, {}, b"%PDF-1.7", url)

    state = RetrievalState(collection_started_wall=0.0)
    retriever = _retriever(FakeClock(MAX_COLLECTION_SECONDS + 1.0), transport, state=state)

    assert retriever.retrieve(ANNUAL_URL) == []
    assert calls == []


def test_collect_source_raises_when_budget_exhausted(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store.state_path.write_text(json.dumps({"attempts": {ANNUAL_URL: MAX_ATTEMPTS_PER_URL}}))

    def transport(url: str) -> TransportResponse:
        raise AssertionError("transport must not be called when the budget is exhausted")

    with pytest.raises(DailyCalendarEvidenceError, match="budget"):
        collect_source(
            url=ANNUAL_URL,
            source_kind=ANNUAL_CALENDAR,
            year=2026,
            retriever=_retriever(FakeClock(), transport),
            store=store,
            text_extractor=_extractor(VALID_ANNUAL_TEXT),
        )


def test_accepted_prior_source_is_reused_without_a_request(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    body = b"%PDF-1.7 annual calendar body"
    calls: list[str] = []

    def transport(url: str) -> TransportResponse:
        calls.append(url)
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    first = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )
    assert first.accepted
    assert len(calls) == 1

    def unexpected(url: str) -> TransportResponse:
        raise AssertionError("a verified retained source must be reused without a request")

    second = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), unexpected),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )

    assert second.accepted
    assert second.body == body
    assert len(calls) == 1


def test_programming_errors_propagate_from_transport(tmp_path: Path) -> None:
    def transport(url: str) -> TransportResponse:
        raise ValueError("bug in the transport implementation")

    with pytest.raises(ValueError):
        collect_source(
            url=ANNUAL_URL,
            source_kind=ANNUAL_CALENDAR,
            year=2026,
            retriever=_retriever(FakeClock(), transport),
            store=EvidenceStore(tmp_path),
            text_extractor=_extractor(VALID_ANNUAL_TEXT),
        )


def test_corrupt_retrieval_state_fails_closed(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store.state_path.write_text("{not valid json")

    def transport(url: str) -> TransportResponse:
        raise AssertionError("transport must not be called with corrupt state")

    with pytest.raises(DailyCalendarEvidenceError, match="not valid JSON"):
        collect_source(
            url=ANNUAL_URL,
            source_kind=ANNUAL_CALENDAR,
            year=2026,
            retriever=_retriever(FakeClock(), transport),
            store=store,
            text_extractor=_extractor(VALID_ANNUAL_TEXT),
        )


def test_store_lock_is_exclusive(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    with store.locked():
        with store.lock_path.open("a+", encoding="utf-8") as handle:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_replay_verifies_recorded_bytes(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    body = b"%PDF-1.7 annual calendar body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )
    replayed = replay_retained(store.root, text_extractor=_extractor(VALID_ANNUAL_TEXT))
    assert len(replayed) == 1
    assert replayed[0].integrity_ok is True
    assert replayed[0].replay_decision == "accepted"
    assert replayed[0].matches_stored is True


def test_replay_fails_closed_on_missing_body(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    body = b"%PDF-1.7 annual calendar body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )
    (store.root / "bodies" / f"{sha256_hex(body)}.bin").unlink()

    replayed = replay_retained(store.root, text_extractor=_extractor(VALID_ANNUAL_TEXT))
    assert replayed[0].integrity_ok is False
    assert replayed[0].replay_decision == "rejected"
    assert replayed[0].matches_stored is False
    assert "missing" in (replayed[0].integrity_reason or "")


def test_replay_fails_closed_on_changed_bytes(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    body = b"%PDF-1.7 annual calendar body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=store,
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )
    (store.root / "bodies" / f"{sha256_hex(body)}.bin").write_bytes(b"<!DOCTYPE html><html>tampered</html>")

    replayed = replay_retained(store.root, text_extractor=_extractor(VALID_ANNUAL_TEXT))
    assert replayed[0].integrity_ok is False
    assert replayed[0].replay_decision == "rejected"
    assert replayed[0].matches_stored is False


def test_report_never_claims_a_ledger_or_complete_coverage(tmp_path: Path) -> None:
    body = b"%PDF-1.7 annual calendar body"

    def transport(url: str) -> TransportResponse:
        return TransportResponse(200, {"Content-Type": "application/pdf"}, body, url)

    receipt = collect_source(
        url=ANNUAL_URL,
        source_kind=ANNUAL_CALENDAR,
        year=2026,
        retriever=_retriever(FakeClock(), transport),
        store=EvidenceStore(tmp_path),
        text_extractor=_extractor(VALID_ANNUAL_TEXT),
    )
    report = build_report(receipt)
    assert report["coverage"]["is_calendar_ledger"] is False
    assert report["coverage"]["establishes_complete_special_session_coverage"] is False
    assert "publication_date" in report["date_separation"]["absent"]
    assert report["date_separation"]["explicit_known"]["declared_year"] == 2026
    json.dumps(report)


def test_cli_replay_missing_root_fails_without_creating_it(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises(SystemExit) as excinfo:
        main(["--replay", "--output-root", str(missing)])

    assert "does not exist" in str(excinfo.value)
    assert not missing.exists()


def test_annual_year_is_not_taken_from_publication_date() -> None:
    from ingest.daily_calendar_evidence import _validate_annual_calendar_text

    text = 'Capital Market. Published December 12, 2026. Trading holidays for the calendar year 2027. January 26, 2027 Republic Day'
    assert _validate_annual_calendar_text(text, 2026)[0] == 'rejected'
    assert _validate_annual_calendar_text(text, 2027)[0] == 'accepted'


def test_crash_after_reservation_preserves_retry_wait(tmp_path: Path) -> None:
    clock = FakeClock()
    store = EvidenceStore(tmp_path)

    def crash(url: str) -> TransportResponse:
        raise RuntimeError('crash before response')

    with pytest.raises(RuntimeError):
        collect_source(url=NOTICE_URL, source_kind=SPECIAL_SESSION, year=2026,
                       retriever=_retriever(clock, crash), store=store)
    resumed = FakeClock(1.0)
    starts: list[float] = []

    def response(url: str) -> TransportResponse:
        starts.append(resumed.now())
        return TransportResponse(200, {}, b'<html>Official special-session notice</html>', url)

    collect_source(url=NOTICE_URL, source_kind=SPECIAL_SESSION, year=2026,
                   retriever=_retriever(resumed, response), store=store)
    assert starts == [5.0]
