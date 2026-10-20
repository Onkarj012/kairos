"""Bounded, auditable retention of official calendar and special-session sources.

This module retains the exact bytes of every received response, including HTTP
error bodies, and journals an independent receipt per actual transport attempt. A
retained source is evidence only: it is not a calendar ledger and it does not
establish complete special-session coverage. The transport performs exactly one
request per call; all retry, spacing, timeout and resume behaviour lives here so
it stays bounded and testable.

Durable attempt reservations are persisted under an exclusive store lock before
each transport call, so an interrupted run cannot reset a retry budget and two
processes cannot collect against the same store at once. Persisted timing uses
wall-clock seconds so a restart still honours the required waits and spacing.
"""
from __future__ import annotations

import fcntl
import hashlib
import http.client
import io
import json
import os
import re
import stat
import subprocess
import time
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

from contracts.calendar import AVAILABLE_AT_ABSENT, OFFICIAL_HOSTS, SOURCE_DATE_ABSENT


PARSER_VERSION = "meridian.daily-calendar-evidence.v1"
ANNUAL_CALENDAR = "annual_calendar"
SPECIAL_SESSION = "special_session"
SOURCE_KINDS = frozenset({ANNUAL_CALENDAR, SPECIAL_SESSION})

PDF_MEDIA = "pdf"
HTML_MEDIA = "html"
ZIP_MEDIA = "zip"
UNKNOWN_MEDIA = "unknown"

ACCEPTED = "accepted"
REJECTED = "rejected"
RETAINED_UNQUALIFIED = "retained_unqualified"

MAX_ATTEMPTS_PER_URL = 3
RETRY_WAIT_SECONDS = (5.0, 30.0)
MIN_START_SPACING_SECONDS = 0.5
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
PDFTOTEXT_TIMEOUT_SECONDS = 30.0
MAX_COLLECTION_SECONDS = 8 * 60 * 60
MAX_ZIP_MEMBERS = 16
MAX_ZIP_MEMBER_BYTES = 4 * 1024 * 1024
MAX_ZIP_EXPANDED_BYTES = 8 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; Kairos/0.1; +https://www.nseindia.com/)"
_RETRYABLE_STATUSES = frozenset({429})
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_HTML_PREFIXES = (b"<!doctype", b"<html", b"<head")
_HTML_ERROR_MARKERS = (
    b"page not found",
    b"404 not found",
    b"error 404",
    b"the requested url",
    b"access denied",
    b"forbidden",
    b"bad request",
    b"internal server error",
    b"service unavailable",
    b"<title>error",
)
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (OSError, http.client.HTTPException)
_CALENDAR_YEAR_RE = re.compile(r"calendar\s+year\s+((?:19|20)\d{2})", re.IGNORECASE)
_HOLIDAY_ROW_RE = re.compile(
    r"\b(?:\d{1,2}\s*[-/ ]\s*(?:\d{1,2}|[A-Za-z]{3,9})\s*[-/ ]\s*\d{2,4}"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4})\b",
    re.IGNORECASE,
)

Transport = Callable[[str], "TransportResponse"]
PdfTextExtractor = Callable[[bytes], str]
AttemptSink = Callable[["AttemptRecord"], None]


@dataclass(frozen=True)
class TransportResponse:
    """One received response. ``status`` and ``final_url`` may be absent on failure."""

    status: int | None
    headers: Mapping[str, str]
    body: bytes
    final_url: str | None


class DailyCalendarEvidenceError(ValueError):
    pass


class PdfExtractionError(DailyCalendarEvidenceError):
    """Raised when a body that claims to be a PDF cannot be parsed as one."""


class ZipArchiveValidationError(DailyCalendarEvidenceError):
    """Raised when a ZIP is unsafe, malformed or has no PDF circular member."""


class ZipPdfExtractionError(DailyCalendarEvidenceError):
    """Raised when a ZIP member is a PDF but its text cannot be extracted."""


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if str(key).casefold() == name.casefold():
            return str(value)
    return None


def validate_official_url(url: str) -> str:
    """Require a direct HTTPS URL on an official exchange host."""
    if not isinstance(url, str) or not url:
        raise DailyCalendarEvidenceError("source URL is required")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DailyCalendarEvidenceError("source URL must use HTTPS")
    if parts.username or parts.password:
        raise DailyCalendarEvidenceError("source URL must not include credentials")
    if parts.query or parts.fragment:
        raise DailyCalendarEvidenceError("source URL must not include a query or fragment")
    if parts.hostname not in OFFICIAL_HOSTS:
        raise DailyCalendarEvidenceError(f"source host is not official: {parts.hostname}")
    return url


def classify_media(headers: Mapping[str, str], body: bytes) -> str:
    """Classify a received body as PDF, HTML or unknown from bytes and media type.

    A PDF is only recognised by its ``%PDF-`` magic, never by the declared
    ``Content-Type`` alone; a mislabelled HTML body therefore stays HTML.
    """
    content_type = (header_value(headers, "Content-Type") or "").split(";")[0].strip().lower()
    prefix = body[:512].lstrip().lower()
    if body[:5] == b"%PDF-":
        return PDF_MEDIA
    if body[:4] in _ZIP_MAGIC:
        return ZIP_MEDIA
    if prefix.startswith(_HTML_PREFIXES):
        return HTML_MEDIA
    if content_type in _HTML_MEDIA_TYPES:
        return HTML_MEDIA
    return UNKNOWN_MEDIA


def _validate_zip_member_name(name: str, *, is_directory: bool) -> None:
    """Reject names that could escape a hypothetical extraction directory."""
    if not name or "\x00" in name or "\\" in name:
        raise ZipArchiveValidationError(f"ZIP member has an unsafe path: {name!r}")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ZipArchiveValidationError(f"ZIP member has an unsafe path: {name!r}")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts[:-1]) or parts[-1] == "..":
        raise ZipArchiveValidationError(f"ZIP member has an unsafe path: {name!r}")
    if parts[-1] == "." or (parts[-1] == "" and not is_directory):
        raise ZipArchiveValidationError(f"ZIP member has an unsafe path: {name!r}")


def extract_zip_pdf_text(body: bytes, text_extractor: PdfTextExtractor) -> str:
    """Extract all bounded, genuine PDF members from an official ZIP in memory.

    The returned text includes member-name boundaries so a caller cannot lose
    which circular supplied a particular piece of extracted text. The archive
    bytes themselves are never replaced by a member's bytes.
    """
    if body[:4] not in _ZIP_MAGIC:
        raise ZipArchiveValidationError("body is not a ZIP (missing ZIP magic)")

    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        raise ZipArchiveValidationError(f"ZIP archive is malformed: {exc}") from exc

    with archive:
        try:
            members = archive.infolist()
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
            raise ZipArchiveValidationError(f"ZIP archive is malformed: {exc}") from exc
        if not members:
            raise ZipArchiveValidationError("ZIP archive has no members")
        if len(members) > MAX_ZIP_MEMBERS:
            raise ZipArchiveValidationError(
                f"ZIP archive has too many members: {len(members)} > {MAX_ZIP_MEMBERS}"
            )

        expanded_bytes = 0
        extracted: list[tuple[str, str]] = []
        for member in members:
            is_directory = member.is_dir()
            _validate_zip_member_name(member.filename, is_directory=is_directory)
            if is_directory:
                continue
            if member.flag_bits & 0x1:
                raise ZipArchiveValidationError(f"ZIP member is encrypted: {member.filename!r}")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ZipArchiveValidationError(f"ZIP member is a symlink: {member.filename!r}")
            if member.file_size < 0 or member.file_size > MAX_ZIP_MEMBER_BYTES:
                raise ZipArchiveValidationError(
                    f"ZIP member is oversized: {member.filename!r} ({member.file_size} bytes)"
                )
            if expanded_bytes + member.file_size > MAX_ZIP_EXPANDED_BYTES:
                raise ZipArchiveValidationError(
                    f"ZIP archive expands beyond {MAX_ZIP_EXPANDED_BYTES} bytes"
                )
            try:
                with archive.open(member, "r") as stream:
                    member_body = stream.read(MAX_ZIP_MEMBER_BYTES + 1)
                    if len(member_body) > MAX_ZIP_MEMBER_BYTES:
                        raise ZipArchiveValidationError(
                            f"ZIP member is oversized: {member.filename!r}"
                        )
                    if len(member_body) != member.file_size:
                        raise ZipArchiveValidationError(
                            f"ZIP member size is inconsistent: {member.filename!r}"
                        )
            except ZipArchiveValidationError:
                raise
            except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError, zlib.error) as exc:
                raise ZipArchiveValidationError(
                    f"ZIP member could not be read: {member.filename!r}: {exc}"
                ) from exc
            expanded_bytes += len(member_body)
            if member_body[:5] != b"%PDF-":
                if member.filename.lower().endswith(".pdf"):
                    raise ZipArchiveValidationError(
                        f"ZIP member is not a PDF: {member.filename!r}"
                    )
                # Exchange circular bundles also contain spreadsheets. Retain
                # them in the original archive, but do not interpret as text.
                continue
            try:
                text = text_extractor(member_body)
            except Exception as exc:
                raise ZipPdfExtractionError(
                    f"ZIP PDF member could not be parsed: {member.filename!r}: {exc}"
                ) from exc
            if not isinstance(text, str) or not text.strip():
                raise ZipPdfExtractionError(
                    f"ZIP PDF member produced no text: {member.filename!r}"
                )
            extracted.append((member.filename, text))

        if not extracted:
            raise ZipArchiveValidationError("ZIP archive has no PDF members")
        return "\n\n".join(
            f"===== ZIP MEMBER: {name} =====\n{text.rstrip()}\n===== END ZIP MEMBER: {name} ====="
            for name, text in extracted
        )


def default_pdf_text_extractor(body: bytes) -> str:
    """Extract text from a genuine PDF with the installed ``pdftotext`` binary."""
    if body[:5] != b"%PDF-":
        raise PdfExtractionError("body is not a PDF (missing %PDF- magic)")
    try:
        completed = subprocess.run(
            ["pdftotext", "-q", "-", "-"],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=PDFTOTEXT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PdfExtractionError(f"pdftotext could not run: {exc}") from exc
    if completed.returncode != 0:
        raise PdfExtractionError(f"pdftotext exited with status {completed.returncode}")
    text = completed.stdout.decode("utf-8", "replace")
    if not text.strip():
        raise PdfExtractionError("pdftotext produced no textual content")
    return text


def _extract_pdf_text(body: bytes, extractor: PdfTextExtractor) -> str | None:
    try:
        return extractor(body)
    except PdfExtractionError:
        return None


def _looks_like_html_error(body: bytes) -> bool:
    lowered = body[:4096].lower()
    return any(marker in lowered for marker in _HTML_ERROR_MARKERS)


def _validate_annual_calendar_text(text: str, declared_year: int) -> tuple[str, str | None]:
    lowered = text.casefold()
    years = {int(match.group(1)) for match in _CALENDAR_YEAR_RE.finditer(text)}
    if declared_year not in years:
        return REJECTED, f"annual calendar does not state the declared year {declared_year}"
    normalized = lowered.replace("-", " ").replace("_", " ")
    if "capital market" not in normalized:
        return REJECTED, "annual calendar does not identify the capital-market segment"
    if "holiday" not in lowered or not _HOLIDAY_ROW_RE.search(text):
        return REJECTED, "annual calendar has no holiday rows"
    return ACCEPTED, None


@dataclass(frozen=True)
class ValidationOutcome:
    media_kind: str
    decision: str
    reason: str | None


def validate_source_response(
    *,
    requested_url: str,
    status: int | None,
    final_url: str | None,
    headers: Mapping[str, str],
    body: bytes,
    source_kind: str,
    declared_year: int,
    text_extractor: PdfTextExtractor,
) -> ValidationOutcome:
    """Decide whether a received response is usable, without discarding bytes.

    Bytes are always retained regardless of the outcome. Acceptance requires a
    successful response whose final URL is the exact requested URL and, for
    PDFs, content that parses and carries the expected semantics.
    """
    if source_kind not in SOURCE_KINDS:
        raise DailyCalendarEvidenceError(f"unknown source kind: {source_kind!r}")
    media_kind = classify_media(headers, body)
    if final_url is None:
        return ValidationOutcome(media_kind, REJECTED, "no response final URL was retained")
    try:
        validate_official_url(final_url)
    except DailyCalendarEvidenceError as exc:
        return ValidationOutcome(media_kind, REJECTED, str(exc))
    if final_url != requested_url:
        return ValidationOutcome(media_kind, REJECTED, "redirected final URL differs from the requested URL")
    if status != 200:
        return ValidationOutcome(media_kind, REJECTED, f"HTTP status {status}")
    if not body:
        return ValidationOutcome(media_kind, REJECTED, "empty response body")
    if source_kind == ANNUAL_CALENDAR:
        if media_kind != PDF_MEDIA:
            return ValidationOutcome(media_kind, REJECTED, f"annual calendar must be PDF, received {media_kind}")
        text = _extract_pdf_text(body, text_extractor)
        if text is None or not text.strip():
            return ValidationOutcome(media_kind, RETAINED_UNQUALIFIED, "PDF bytes were retained but not parseable")
        decision, reason = _validate_annual_calendar_text(text, declared_year)
        return ValidationOutcome(media_kind, decision, reason)
    if media_kind == PDF_MEDIA:
        text = _extract_pdf_text(body, text_extractor)
        if text is None or not text.strip():
            return ValidationOutcome(media_kind, RETAINED_UNQUALIFIED, "PDF bytes were retained but not parseable")
        return ValidationOutcome(media_kind, ACCEPTED, None)
    if media_kind == ZIP_MEDIA:
        try:
            extract_zip_pdf_text(body, text_extractor)
        except ZipPdfExtractionError as exc:
            return ValidationOutcome(media_kind, RETAINED_UNQUALIFIED, str(exc))
        except ZipArchiveValidationError as exc:
            return ValidationOutcome(media_kind, REJECTED, str(exc))
        return ValidationOutcome(media_kind, ACCEPTED, None)
    if media_kind == HTML_MEDIA:
        if _looks_like_html_error(body):
            return ValidationOutcome(media_kind, REJECTED, "HTML body is an error page")
        return ValidationOutcome(media_kind, ACCEPTED, None)
    return ValidationOutcome(media_kind, REJECTED, f"special-session notice must be PDF or HTML, received {media_kind}")


@dataclass(frozen=True)
class AttemptRecord:
    """A single transport call, including the exact bytes an HTTP error returned."""

    attempt: int
    status: int | None
    final_url: str | None
    headers: Mapping[str, str]
    body: bytes
    retrieved_at: str
    error: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "final_url": self.final_url,
            "headers": dict(self.headers),
            "content_type": header_value(self.headers, "Content-Type"),
            "byte_count": len(self.body),
            "sha256": sha256_hex(self.body),
            "retrieved_at": self.retrieved_at,
            "error": self.error,
        }


@dataclass(frozen=True)
class SourceReceipt:
    """Independent audit receipt for one supplied calendar or notice URL."""

    logical_name: str
    source_kind: str
    declared_year: int
    requested_url: str
    final_url: str | None
    status: int | None
    headers: Mapping[str, str]
    body: bytes
    retrieved_at: str
    parser_version: str
    media_kind: str
    validation_decision: str
    rejection_reason: str | None
    attempts: int
    attempts_detail: tuple[AttemptRecord, ...]
    publication_date: str | None
    publication_date_basis: str
    available_at: str | None
    available_at_basis: str
    last_modified: str | None
    body_path: str | None = None

    @property
    def accepted(self) -> bool:
        return self.validation_decision == ACCEPTED

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": "meridian.daily-calendar-evidence.receipt.v1",
            "logical_name": self.logical_name,
            "source_kind": self.source_kind,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "status": self.status,
            "headers": dict(self.headers),
            "content_type": header_value(self.headers, "Content-Type"),
            "sha256": sha256_hex(self.body),
            "byte_count": len(self.body),
            "retrieved_at": self.retrieved_at,
            "parser_version": self.parser_version,
            "media_kind": self.media_kind,
            "validation_decision": self.validation_decision,
            "rejection_reason": self.rejection_reason,
            "attempts": self.attempts,
            "attempts_detail": [record.metadata() for record in self.attempts_detail],
            "declared_year": self.declared_year,
            "declared_year_basis": "cli_supplied_year",
            "publication_date": self.publication_date,
            "publication_date_basis": self.publication_date_basis,
            "available_at": self.available_at,
            "available_at_basis": self.available_at_basis,
            "last_modified": self.last_modified,
            "authoritative_session_dates": [],
            "authoritative_date_basis": "filenames_and_retrieval_time_are_not_source_dates",
            "body_path": self.body_path,
            "is_calendar_ledger": False,
            "establishes_complete_special_session_coverage": False,
        }


def _int_map(value: Any, field_name: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DailyCalendarEvidenceError(f"retrieval state '{field_name}' must be a mapping")
    result: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise DailyCalendarEvidenceError(f"retrieval state '{field_name}.{key}' must be a non-negative integer")
        result[str(key)] = item
    return result


def _float_map(value: Any, field_name: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DailyCalendarEvidenceError(f"retrieval state '{field_name}' must be a mapping")
    result: dict[str, float] = {}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise DailyCalendarEvidenceError(f"retrieval state '{field_name}.{key}' must be a number")
        result[str(key)] = float(item)
    return result


@dataclass
class RetrievalState:
    """Durable per-URL attempt budget plus wall-clock timing for resume."""

    attempts: dict[str, int] = field(default_factory=dict)
    last_start_wall: dict[str, float] = field(default_factory=dict)
    next_eligible_wall: dict[str, float] = field(default_factory=dict)
    collection_started_wall: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": dict(self.attempts),
            "last_start_wall": dict(self.last_start_wall),
            "next_eligible_wall": dict(self.next_eligible_wall),
            "collection_started_wall": self.collection_started_wall,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RetrievalState":
        if not isinstance(value, Mapping):
            raise DailyCalendarEvidenceError("retrieval state must be a mapping")
        started = value.get("collection_started_wall")
        if started is not None and (isinstance(started, bool) or not isinstance(started, (int, float))):
            raise DailyCalendarEvidenceError("retrieval state 'collection_started_wall' must be a number or null")
        return cls(
            attempts=_int_map(value.get("attempts"), "attempts"),
            last_start_wall=_float_map(value.get("last_start_wall"), "last_start_wall"),
            next_eligible_wall=_float_map(value.get("next_eligible_wall"), "next_eligible_wall"),
            collection_started_wall=None if started is None else float(started),
        )

    @classmethod
    def load(cls, path: str | Path) -> "RetrievalState":
        state_path = Path(path)
        if not state_path.is_file():
            return cls()
        try:
            decoded = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DailyCalendarEvidenceError(f"retrieval state is not valid JSON: {state_path}") from exc
        if not isinstance(decoded, Mapping):
            raise DailyCalendarEvidenceError(f"retrieval state is not an object: {state_path}")
        return cls.from_dict(decoded)

    def save(self, path: str | Path) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(self.as_dict(), sort_keys=True, indent=2) + "\n").encode("utf-8")
        temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_path)

    def reserve(self, url: str, wall_now: float) -> int:
        """Reserve the next attempt durably before its transport call."""
        if self.collection_started_wall is None:
            self.collection_started_wall = wall_now
        count = self.attempts.get(url, 0) + 1
        self.attempts[url] = count
        self.last_start_wall[url] = wall_now
        if count < MAX_ATTEMPTS_PER_URL:
            self.next_eligible_wall[url] = wall_now + RETRY_WAIT_SECONDS[count - 1]
        else:
            self.next_eligible_wall.pop(url, None)
        return count

    def schedule_retry(self, url: str, wall_now: float, delay: float) -> None:
        self.next_eligible_wall[url] = wall_now + delay

    def retry_delay_remaining(self, url: str, wall_now: float) -> float:
        target = self.next_eligible_wall.get(url)
        if target is None:
            return 0.0
        return max(0.0, target - wall_now)


def default_transport(url: str) -> TransportResponse:
    """One HTTPS GET with a 10-second connect and 30-second read timeout, no retry."""
    validate_official_url(url)
    parts = urlsplit(url)
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(READ_TIMEOUT_SECONDS)
        connection.request(
            "GET",
            parts.path or "/",
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html,*/*"},
        )
        response = connection.getresponse()
        body = response.read()
        headers = {str(key): str(value) for key, value in response.getheaders()}
        return TransportResponse(int(response.status), headers, body, url)
    finally:
        connection.close()


def _is_retryable(record: AttemptRecord) -> bool:
    if record.error is not None or record.status is None:
        return True
    return record.status >= 500 or record.status in _RETRYABLE_STATUSES


class BoundedRetriever:
    """Retrieve a URL with bounded attempts, spacing, waits and injectable time.

    ``clock`` is a monotonic source used only for in-process sleeps. ``wall_clock``
    supplies the durable timestamps that survive a restart, so a resumed run still
    waits out the retry delay and observes the start-spacing gap.
    """

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        state: RetrievalState | None = None,
        timestamp: Callable[[], str] | None = None,
        wall_clock: Callable[[], float] | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        self.transport: Transport = transport or default_transport
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.wall_clock = wall_clock or time.time
        self.state = state if state is not None else RetrievalState()
        self.timestamp = timestamp or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.state_path = None if state_path is None else Path(state_path)
        self._last_start_monotonic: float | None = None

    def _await_spacing(self) -> None:
        if self._last_start_monotonic is not None:
            remaining = MIN_START_SPACING_SECONDS - (self.clock() - self._last_start_monotonic)
            if remaining > 0:
                self.sleep(remaining)
        self._last_start_monotonic = self.clock()
        if self.state.last_start_wall:
            remaining = MIN_START_SPACING_SECONDS - (self.wall_clock() - max(self.state.last_start_wall.values()))
            if remaining > 0:
                self.sleep(remaining)

    def _await_retry_eligibility(self, url: str) -> None:
        remaining = self.state.retry_delay_remaining(url, self.wall_clock())
        if remaining > 0:
            self.sleep(remaining)

    def _elapsed_budget_available(self) -> bool:
        if self.state.collection_started_wall is None:
            return True
        return (self.wall_clock() - self.state.collection_started_wall) < MAX_COLLECTION_SECONDS

    def _persist_state(self) -> None:
        if self.state_path is not None:
            self.state.save(self.state_path)

    def retrieve(self, url: str, *, sink: AttemptSink | None = None) -> list[AttemptRecord]:
        """Return this call's attempts; empty when a durable budget is exhausted.

        The attempt count and timing are persisted before each transport call and
        each received body is handed to ``sink`` immediately after the response.
        """
        attempts: list[AttemptRecord] = []
        while self.state.attempts.get(url, 0) < MAX_ATTEMPTS_PER_URL:
            self._await_retry_eligibility(url)
            self._await_spacing()
            if not self._elapsed_budget_available():
                break
            used = self.state.reserve(url, self.wall_clock())
            self._persist_state()
            try:
                response = self.transport(url)
                record = AttemptRecord(
                    used,
                    response.status,
                    response.final_url,
                    dict(response.headers),
                    response.body,
                    self.timestamp(),
                    None,
                )
            except _TRANSPORT_ERRORS as exc:  # transport failures are retained as empty-body attempts
                record = AttemptRecord(used, None, None, {}, b"", self.timestamp(), f"{type(exc).__name__}: {exc}")
            attempts.append(record)
            if sink is not None:
                sink(record)
            if _is_retryable(record) and self.state.attempts[url] < MAX_ATTEMPTS_PER_URL:
                if not self._elapsed_budget_available():
                    break
                delay = RETRY_WAIT_SECONDS[used - 1]
                self.state.schedule_retry(url, self.wall_clock(), delay)
                self._persist_state()
                self.sleep(delay)
                continue
            break
        return attempts


def _receipt_from_journal(row: Mapping[str, Any], body: bytes) -> SourceReceipt:
    status = row.get("status")
    record = AttemptRecord(
        attempt=int(row.get("attempt") or 1),
        status=None if status is None else int(status),
        final_url=None if row.get("final_url") is None else str(row["final_url"]),
        headers=dict(row.get("headers") or {}),
        body=body,
        retrieved_at=str(row.get("retrieved_at") or ""),
        error=None if row.get("error") is None else str(row["error"]),
    )
    requested_url = str(row.get("requested_url"))
    return SourceReceipt(
        logical_name=str(row.get("logical_name") or logical_name(requested_url)),
        source_kind=str(row.get("source_kind")),
        declared_year=int(row.get("declared_year")),
        requested_url=requested_url,
        final_url=record.final_url,
        status=record.status,
        headers=dict(record.headers),
        body=body,
        retrieved_at=record.retrieved_at,
        parser_version=str(row.get("parser_version") or PARSER_VERSION),
        media_kind=str(row.get("media_kind") or UNKNOWN_MEDIA),
        validation_decision=str(row.get("validation_decision") or REJECTED),
        rejection_reason=None if row.get("rejection_reason") is None else str(row["rejection_reason"]),
        attempts=1,
        attempts_detail=(record,),
        publication_date=None,
        publication_date_basis=SOURCE_DATE_ABSENT,
        available_at=None,
        available_at_basis=AVAILABLE_AT_ABSENT,
        last_modified=header_value(record.headers, "Last-Modified"),
        body_path=str(row.get("body_path")),
    )


class EvidenceStore:
    """Content-addressed body retention plus an append-only attempt journal.

    The store is the unit of exclusion: :meth:`locked` serialises reload, reserve,
    transport and retention so exactly one process collects against a store.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.receipts_path = self.root / "receipts.jsonl"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _retain_body(self, body: bytes) -> str | None:
        if not body:
            return None
        digest = sha256_hex(body)
        target = self.root / "bodies" / f"{digest}.bin"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_bytes(body)
            os.replace(temporary, target)
        return target.relative_to(self.root).as_posix()

    def _append_journal(self, row: Mapping[str, Any]) -> None:
        self.receipts_path.parent.mkdir(parents=True, exist_ok=True)
        with self.receipts_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def retain_attempt(
        self,
        *,
        requested_url: str,
        source_kind: str,
        declared_year: int,
        record: AttemptRecord,
        text_extractor: PdfTextExtractor,
    ) -> tuple[ValidationOutcome, str | None]:
        """Retain one received body and journal its receipt immediately."""
        body_path = self._retain_body(record.body)
        outcome = validate_source_response(
            requested_url=requested_url,
            status=record.status,
            final_url=record.final_url,
            headers=record.headers,
            body=record.body,
            source_kind=source_kind,
            declared_year=declared_year,
            text_extractor=text_extractor,
        )
        row = {
            "schema": "meridian.daily-calendar-evidence.attempt.v1",
            "journal_kind": "attempt",
            "logical_name": logical_name(requested_url),
            "source_kind": source_kind,
            "declared_year": declared_year,
            "requested_url": requested_url,
            "attempt": record.attempt,
            "status": record.status,
            "final_url": record.final_url,
            "headers": dict(record.headers),
            "content_type": header_value(record.headers, "Content-Type"),
            "byte_count": len(record.body),
            "sha256": sha256_hex(record.body),
            "retrieved_at": record.retrieved_at,
            "error": record.error,
            "parser_version": PARSER_VERSION,
            "media_kind": outcome.media_kind,
            "validation_decision": outcome.decision,
            "rejection_reason": outcome.reason,
            "body_path": body_path,
        }
        self._append_journal(row)
        return outcome, body_path

    def _read_journal(self) -> list[dict[str, Any]]:
        if not self.receipts_path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.receipts_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DailyCalendarEvidenceError(f"retained receipt journal is corrupt: {self.receipts_path}") from exc
        return rows

    def find_reusable(self, *, requested_url: str, source_kind: str, declared_year: int) -> SourceReceipt | None:
        """Reuse a verified prior accepted retention without another request."""
        for row in self._read_journal():
            if row.get("journal_kind") != "attempt":
                continue
            if row.get("validation_decision") != ACCEPTED:
                continue
            if row.get("source_kind") != source_kind or row.get("declared_year") != declared_year:
                continue
            if row.get("requested_url") != requested_url:
                continue
            body_path = row.get("body_path")
            if not body_path:
                continue
            path = self.root / str(body_path)
            if not path.is_file():
                continue
            body = path.read_bytes()
            if sha256_hex(body) != row.get("sha256") or len(body) != row.get("byte_count"):
                continue
            return _receipt_from_journal(row, body)
        return None


def logical_name(url: str) -> str:
    return Path(urlsplit(url).path).name or "source"


def build_receipt(
    *,
    url: str,
    source_kind: str,
    year: int,
    attempts: list[AttemptRecord],
    outcome: ValidationOutcome,
    body_path: str | None,
) -> SourceReceipt:
    if not attempts:
        raise DailyCalendarEvidenceError("cannot build a receipt without an attempt")
    final = attempts[-1]
    return SourceReceipt(
        logical_name=logical_name(url),
        source_kind=source_kind,
        declared_year=year,
        requested_url=url,
        final_url=final.final_url,
        status=final.status,
        headers=dict(final.headers),
        body=final.body,
        retrieved_at=final.retrieved_at,
        parser_version=PARSER_VERSION,
        media_kind=outcome.media_kind,
        validation_decision=outcome.decision,
        rejection_reason=outcome.reason,
        attempts=len(attempts),
        attempts_detail=tuple(attempts),
        publication_date=None,
        publication_date_basis=SOURCE_DATE_ABSENT,
        available_at=None,
        available_at_basis=AVAILABLE_AT_ABSENT,
        last_modified=header_value(final.headers, "Last-Modified"),
        body_path=body_path,
    )


def collect_source(
    *,
    url: str,
    source_kind: str,
    year: int,
    retriever: BoundedRetriever,
    store: EvidenceStore,
    text_extractor: PdfTextExtractor | None = None,
) -> SourceReceipt:
    """Retrieve, validate and retain one supplied official source URL.

    The store lock is held across reload, reservation, transport and retention so
    the durable state is authoritative and only one request is in flight per store.
    """
    validate_official_url(url)
    if source_kind not in SOURCE_KINDS:
        raise DailyCalendarEvidenceError(f"unknown source kind: {source_kind!r}")
    if isinstance(year, bool) or not isinstance(year, int):
        raise DailyCalendarEvidenceError("declared calendar year must be an integer")
    extractor = text_extractor or default_pdf_text_extractor

    with store.locked():
        retriever.state = RetrievalState.load(store.state_path)
        retriever.state_path = store.state_path
        reusable = store.find_reusable(requested_url=url, source_kind=source_kind, declared_year=year)
        if reusable is not None:
            return reusable

        outcomes: dict[int, tuple[ValidationOutcome, str | None]] = {}

        def sink(record: AttemptRecord) -> None:
            outcomes[record.attempt] = store.retain_attempt(
                requested_url=url,
                source_kind=source_kind,
                declared_year=year,
                record=record,
                text_extractor=extractor,
            )

        attempts = retriever.retrieve(url, sink=sink)
        if not attempts:
            if retriever.state.attempts.get(url, 0) >= MAX_ATTEMPTS_PER_URL:
                raise DailyCalendarEvidenceError(f"attempt budget is already exhausted for {url}")
            raise DailyCalendarEvidenceError(f"collection time budget is exhausted before retrieving {url}")

        final = attempts[-1]
        outcome, body_path = outcomes[final.attempt]
        return build_receipt(url=url, source_kind=source_kind, year=year, attempts=attempts, outcome=outcome, body_path=body_path)


@dataclass(frozen=True)
class ReplayResult:
    logical_name: str
    requested_url: str
    stored_decision: str
    replay_decision: str
    media_kind: str
    sha256: str
    integrity_ok: bool
    integrity_reason: str | None
    rejection_reason: str | None

    @property
    def matches_stored(self) -> bool:
        return self.integrity_ok and self.stored_decision == self.replay_decision


def read_receipts(root: str | Path) -> list[dict[str, Any]]:
    path = Path(root) / "receipts.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def replay_retained(root: str | Path, *, text_extractor: PdfTextExtractor | None = None) -> list[ReplayResult]:
    """Re-validate retained supplied responses from disk without any network call.

    Recorded byte counts and hashes are verified first; a missing or changed body
    fails closed rather than silently re-evaluating different bytes.
    """
    root = Path(root)
    extractor = text_extractor or default_pdf_text_extractor
    results: list[ReplayResult] = []
    for metadata in read_receipts(root):
        body_path = metadata.get("body_path")
        expected_sha = str(metadata.get("sha256") or "")
        expected_count = metadata.get("byte_count")
        body = b""
        integrity_ok = False
        integrity_reason: str | None = None
        if body_path:
            path = root / str(body_path)
            if not path.is_file():
                integrity_reason = "retained body is missing"
            else:
                body = path.read_bytes()
                if sha256_hex(body) != expected_sha or len(body) != expected_count:
                    integrity_reason = "retained bytes do not match the recorded hash/byte_count"
                else:
                    integrity_ok = True
        elif expected_count in (0, None) and expected_sha == sha256_hex(b""):
            integrity_ok = True
        else:
            integrity_reason = "no retained body for a non-empty attempt"

        if integrity_ok:
            outcome = validate_source_response(
                requested_url=str(metadata.get("requested_url")),
                status=metadata.get("status"),
                final_url=metadata.get("final_url"),
                headers=metadata.get("headers") or {},
                body=body,
                source_kind=str(metadata.get("source_kind")),
                declared_year=int(metadata.get("declared_year") or 0),
                text_extractor=extractor,
            )
            replay_decision = outcome.decision
            rejection_reason = outcome.reason
        else:
            replay_decision = REJECTED
            rejection_reason = integrity_reason

        results.append(
            ReplayResult(
                logical_name=str(metadata.get("logical_name")),
                requested_url=str(metadata.get("requested_url")),
                stored_decision=str(metadata.get("validation_decision")),
                replay_decision=replay_decision,
                media_kind=str(metadata.get("media_kind")),
                sha256=sha256_hex(body),
                integrity_ok=integrity_ok,
                integrity_reason=integrity_reason,
                rejection_reason=rejection_reason,
            )
        )
    return results
