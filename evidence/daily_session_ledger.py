"""Authoritative, evidence-bound NSE exchange-session ledger (plan DG-01).

Builds one row for *every* calendar date from :data:`LEDGER_START` through the
latest completed authoritative exchange session at a frozen preflight
timestamp.  Each row is explicitly one of:

``expected``
    An official trading session.  Carries the session interval when the retained
    evidence states it.
``non_session``
    A weekend or an official holiday.
``calendar_unresolved``
    The date's year has no usable retained annual calendar.  A weekday is never
    silently assumed to be a session, and a missing year is never filled from
    another year's evidence.

The ledger deliberately separates the *complete date grid* from the *qualified
expected-session denominator*.  A denominator is qualified only when every
expected session has a known closure, every retained special-session notice has
a hash-bound interpretation, and the caller-supplied calendar review records no
unresolved completeness facts.  Unresolved closure/timing, missing amendments
or unreviewed notices block the denominator and therefore the §5 historical
checkpoint; the ledger reports the blockers instead of inventing a
certification.

Legacy price-only artifacts and optional daily cash receipts are reconciled
against the calendar as supporting evidence only.  A unique payload is never
admitted as a session, a duplicate on an expected date is reported as a gap,
and a payload on a holiday remains rejected/non-session.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from contracts.calendar import (
    AVAILABLE_AT_ABSENT,
    IST,
    REGULAR_CLOSE,
    REGULAR_OPEN,
    SOURCE_DATE_ABSENT,
    SessionType,
    canonical_json,
    canonical_timestamp,
    ist_interval,
)
from ingest.calendar import parse_holiday_rows
from ingest.daily_calendar_evidence import (
    ZIP_MEDIA,
    ZipArchiveValidationError,
    ZipPdfExtractionError,
    classify_media,
    extract_zip_pdf_text,
)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

LEDGER_START = date(2019, 8, 23)

EXPECTED = "expected"
NON_SESSION = "non_session"
CALENDAR_UNRESOLVED = "calendar_unresolved"

CASH_ADMITTED = "admitted_price"
CASH_REJECTED = "rejected"
CASH_RETAINED = "retained_unqualified"
CASH_NOT_RETAINED = "not_retained"
CASH_ROOT_ABSENT = "root_absent"

LEGACY_RETAINED = "retained_unqualified"
LEGACY_EXPECTED_GAP = "expected_date_duplicate_gap"
LEGACY_NON_SESSION = "non_session_payload_rejected"
LEGACY_ABSENT = "no_legacy_inventory_row"

ANNUAL_CALENDAR = "annual_calendar"
SPECIAL_SESSION = "special_session"
ACCEPTED = "accepted"
REJECTED = "rejected"

_PDFTOTEXT_TIMEOUT_SECONDS = 30.0
_PUBLICATION_RE = re.compile(
    r"\bDate\s*:\s*(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s*20\d{2}",
    re.IGNORECASE,
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_SCOPE_CALENDAR = "calendar"
_SCOPE_AMENDMENT = "amendment"
_SCOPE_REVIEW = "review"
_SCOPE_LEGACY = "legacy"
_SCOPE_CASH = "cash"

_BLOCKING_SCOPES = frozenset({_SCOPE_CALENDAR, _SCOPE_AMENDMENT, _SCOPE_REVIEW})

_LEGACY_SEVERITY = {
    LEGACY_ABSENT: 0,
    LEGACY_RETAINED: 1,
    LEGACY_EXPECTED_GAP: 2,
    LEGACY_NON_SESSION: 3,
}


class LedgerError(ValueError):
    """A ledger input is malformed or cannot be evidence-bound."""


# ---------------------------------------------------------------------------
# Retained evidence
# ---------------------------------------------------------------------------


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _require_hex64(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise LedgerError(f"{field_name} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class RetainedSource:
    """Provenance for one retained source body, accepted or rejected."""

    label: str
    source_kind: str
    source_url: str | None
    sha256: str
    byte_count: int
    retrieved_at: str | None
    publication_date: str | None
    publication_date_basis: str
    available_at: str | None
    available_at_basis: str
    source_path: str | None
    decision: str = ACCEPTED
    declared_year: int | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        _require_hex64(self.sha256, "sha256")
        if self.publication_date is not None:
            date.fromisoformat(self.publication_date)
        if self.available_at is not None:
            canonical_timestamp(self.available_at, "available_at")

    def as_report_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source_kind": self.source_kind,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "retrieved_at": self.retrieved_at,
            "publication_date": self.publication_date,
            "publication_date_basis": self.publication_date_basis,
            "available_at": self.available_at,
            "available_at_basis": self.available_at_basis,
            "source_path": self.source_path,
            "decision": self.decision,
            "declared_year": self.declared_year,
            "rejection_reason": self.rejection_reason,
        }


@dataclass(frozen=True)
class AnnualCalendar:
    year: int
    source: RetainedSource
    text: str

    @property
    def usable(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True)
class NoticeSource:
    source: RetainedSource
    text: str


def default_pdf_text(body: bytes) -> str:
    """Extract laid-out text from a genuine PDF with ``pdftotext -layout``."""
    if body[:5] != b"%PDF-":
        raise LedgerError("body is not a PDF (missing %PDF- magic)")
    try:
        completed = subprocess.run(
            ["pdftotext", "-q", "-layout", "-", "-"],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_PDFTOTEXT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LedgerError(f"pdftotext could not run: {exc}") from exc
    if completed.returncode != 0:
        raise LedgerError(f"pdftotext exited with status {completed.returncode}")
    return completed.stdout.decode("utf-8", "replace")


def _publication_date(text: str) -> str | None:
    match = _PUBLICATION_RE.search(text)
    if match is None:
        return None
    raw = match.group(0)
    raw = raw.split(":", 1)[1].strip() if ":" in raw else raw
    raw = re.sub(r"\s+", " ", raw)
    return datetime.strptime(raw, "%B %d, %Y").date().isoformat()


def load_annual_calendars(
    raw_root: str | Path,
    *,
    pdf_text: Callable[[bytes], str] = default_pdf_text,
) -> tuple[list[AnnualCalendar], list[RetainedSource]]:
    """Read retained annual holiday PDFs, hash-bound to the raw manifest."""
    raw_root = Path(raw_root)
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.is_file():
        raise LedgerError(f"retained raw manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerError(f"retained raw manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(manifest, Mapping):
        raise LedgerError(f"retained raw manifest is not an object: {manifest_path}")

    by_year: dict[int, AnnualCalendar] = {}
    sources: list[RetainedSource] = []
    for record in manifest.get("sources", []):
        if not isinstance(record, Mapping):
            continue
        year = record.get("year")
        if not isinstance(year, int) or isinstance(year, bool):
            continue
        path = raw_root / "annual" / f"{year}.pdf"
        if not path.is_file():
            raise LedgerError(f"retained annual calendar is missing for {year}: {path}")
        body = path.read_bytes()
        expected_hash = record.get("sha256")
        if sha256_hex(body) != expected_hash or len(body) != record.get("byte_count"):
            raise LedgerError(f"retained annual bytes do not match manifest: {path}")
        text = ""
        try:
            text = pdf_text(body)
        except LedgerError:
            text = ""
        publication = _publication_date(text)
        source = RetainedSource(
            label=f"annual/{year}.pdf",
            source_kind="official_nse_annual_holiday_pdf",
            source_url=record.get("requested_url"),
            sha256=str(expected_hash),
            byte_count=int(record.get("byte_count") or len(body)),
            retrieved_at=record.get("retrieved_at"),
            publication_date=publication,
            publication_date_basis="pdf_statement" if publication else SOURCE_DATE_ABSENT,
            available_at=record.get("available_at"),
            available_at_basis=str(record.get("available_at_basis") or AVAILABLE_AT_ABSENT),
            source_path=path.as_posix(),
            decision=ACCEPTED,
            declared_year=year,
        )
        sources.append(source)
        by_year.setdefault(year, AnnualCalendar(year, source, text))
    return [by_year[year] for year in sorted(by_year)], sources


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LedgerError(f"retained receipt journal is missing: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"retained receipt journal is corrupt: {path}") from exc
        if isinstance(decoded, Mapping):
            rows.append(dict(decoded))
    return rows


def load_calendar_evidence(
    evidence_root: str | Path,
    *,
    pdf_text: Callable[[bytes], str] = default_pdf_text,
) -> tuple[list[AnnualCalendar], list[NoticeSource], list[RetainedSource]]:
    """Read accepted annual calendars and special-session notices from a store.

    Every receipt is verified against its recorded hash before its body is used.
    A missing or changed body fails closed and cannot contribute calendar text.
    """
    root = Path(evidence_root)
    if not root.is_dir():
        raise LedgerError(f"calendar evidence root does not exist: {root}")
    rows = _read_json_lines(root / "receipts.jsonl")

    by_url: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        url = row.get("requested_url")
        if isinstance(url, str) and url:
            by_url.setdefault(url, []).append(row)

    annuals: list[AnnualCalendar] = []
    notices: list[NoticeSource] = []
    sources: list[RetainedSource] = []
    for url in sorted(by_url):
        attempts = by_url[url]
        accepted_rows = [row for row in attempts if row.get("validation_decision") == ACCEPTED]
        chosen = accepted_rows[-1] if accepted_rows else attempts[-1]
        source_kind = str(chosen.get("source_kind") or "")
        declared_year = chosen.get("declared_year")
        body = b""
        body_path = chosen.get("body_path")
        integrity_ok = False
        if isinstance(body_path, str) and body_path:
            path = root / body_path
            if path.is_file():
                body = path.read_bytes()
                integrity_ok = (
                    sha256_hex(body) == chosen.get("sha256")
                    and len(body) == chosen.get("byte_count")
                )
        decision = str(chosen.get("validation_decision") or REJECTED)
        rejection_reason = chosen.get("rejection_reason")
        if decision == ACCEPTED and not integrity_ok:
            decision = REJECTED
            rejection_reason = "retained body is missing or does not match the recorded hash"
        media_kind = classify_media(chosen.get("headers") or {}, body)
        extracted_zip_text: str | None = None
        if decision == ACCEPTED and integrity_ok and source_kind == ANNUAL_CALENDAR and media_kind == ZIP_MEDIA:
            decision = REJECTED
            rejection_reason = "annual calendar must be PDF, received zip"
        elif decision == ACCEPTED and integrity_ok and source_kind == SPECIAL_SESSION and media_kind == ZIP_MEDIA:
            try:
                extracted_zip_text = extract_zip_pdf_text(body, pdf_text)
            except ZipPdfExtractionError as exc:
                decision = LEGACY_RETAINED
                rejection_reason = str(exc)
            except ZipArchiveValidationError as exc:
                decision = REJECTED
                rejection_reason = str(exc)
        source = RetainedSource(
            label=str(chosen.get("logical_name") or Path(url).name),
            source_kind=source_kind,
            source_url=url,
            sha256=str(chosen.get("sha256") or sha256_hex(body)),
            byte_count=int(chosen.get("byte_count") or len(body)),
            retrieved_at=chosen.get("retrieved_at"),
            publication_date=chosen.get("publication_date"),
            publication_date_basis=str(chosen.get("publication_date_basis") or SOURCE_DATE_ABSENT),
            available_at=chosen.get("available_at"),
            available_at_basis=str(chosen.get("available_at_basis") or AVAILABLE_AT_ABSENT),
            source_path=body_path if isinstance(body_path, str) else None,
            decision=decision,
            declared_year=declared_year if isinstance(declared_year, int) else None,
            rejection_reason=rejection_reason,
        )
        sources.append(source)
        if decision != ACCEPTED:
            continue
        if source_kind == ANNUAL_CALENDAR:
            text = ""
            try:
                text = pdf_text(body)
            except LedgerError:
                text = ""
            if isinstance(declared_year, int) and not isinstance(declared_year, bool):
                annuals.append(AnnualCalendar(declared_year, source, text))
        elif source_kind == SPECIAL_SESSION:
            text = ""
            if extracted_zip_text is not None:
                text = extracted_zip_text
            else:
                try:
                    text = pdf_text(body)
                except LedgerError:
                    text = ""
            notices.append(NoticeSource(source, text))
    return annuals, notices, sources


# ---------------------------------------------------------------------------
# Amendments and review
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionPhase:
    phase: str
    security_scope: str
    open_at: str
    close_at: str

    def as_dict(self) -> dict[str, str]:
        return {"phase": self.phase, "security_scope": self.security_scope,
                "open_at": self.open_at, "close_at": self.close_at}


@dataclass(frozen=True)
class Amendment:
    effective_date: date
    session_type: SessionType
    label: str
    source_sha256: str
    source_url: str | None
    publication_date: date | None
    available_at: str | None
    open_at: str | None
    close_at: str | None
    replacement_of: date | None
    intervals: tuple[tuple[str, str], ...] = ()
    supporting_source_sha256s: tuple[str, ...] = ()
    phases: tuple[SessionPhase, ...] = ()

    def as_report_dict(self) -> dict[str, Any]:
        return {
            "effective_date": self.effective_date.isoformat(),
            "session_type": self.session_type.value,
            "label": self.label,
            "source_sha256": self.source_sha256,
            "source_url": self.source_url,
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
            "available_at": self.available_at,
            "open_at": self.open_at,
            "close_at": self.close_at,
            "replacement_of": self.replacement_of.isoformat() if self.replacement_of else None,
            "intervals": [{"open_at": open_at, "close_at": close_at} for open_at, close_at in self.intervals],
            "interval_count": len(self.intervals),
            "supporting_source_sha256s": list(self.supporting_source_sha256s),
            "phases": [phase.as_dict() for phase in self.phases],
        }


def _optional_iso_date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LedgerError(f"amendment {field_name} must be a canonical YYYY-MM-DD string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise LedgerError(f"amendment {field_name} must be canonical YYYY-MM-DD")
    return parsed


def parse_amendment(raw: Mapping[str, Any]) -> Amendment:
    """Validate one parent-supplied, hash-bound source interpretation."""
    if not isinstance(raw, Mapping):
        raise LedgerError("each amendment must be an object")
    effective = _optional_iso_date(raw.get("effective_date"), "effective_date")
    if effective is None:
        raise LedgerError("amendment effective_date is required")
    type_value = raw.get("session_type")
    try:
        session_type = SessionType(str(type_value))
    except ValueError as exc:
        raise LedgerError(f"amendment session_type is not supported: {type_value!r}") from exc
    source_sha = _require_hex64(raw.get("source_sha256"), "amendment source_sha256")
    supporting = raw.get("supporting_source_sha256s", [])
    if not isinstance(supporting, list):
        raise LedgerError("amendment supporting_source_sha256s must be a list")
    supporting_shas = tuple(
        _require_hex64(value, "amendment supporting_source_sha256s") for value in supporting
    )
    if len(set(supporting_shas)) != len(supporting_shas) or source_sha in supporting_shas:
        raise LedgerError("amendment source hashes must be distinct")
    open_at = raw.get("open_at")
    close_at = raw.get("close_at")
    intervals = _parse_amendment_intervals(raw.get("intervals"), effective, open_at, close_at)
    phases = _parse_session_phases(raw.get("phases"), effective, intervals)
    if intervals:
        # Backward-compatible single interval fields become the outer envelope of
        # the ordered, non-overlapping windows; closure is the last close.
        open_at = intervals[0][0]
        close_at = intervals[-1][1]
    available_at = raw.get("available_at")
    if available_at is not None:
        canonical_timestamp(available_at, "amendment available_at")
    publication = _optional_iso_date(raw.get("publication_date"), "publication_date")
    replacement = _optional_iso_date(raw.get("replacement_of"), "replacement_of")
    label = raw.get("label")
    if label is not None and not isinstance(label, str):
        raise LedgerError("amendment label must be a string")
    return Amendment(
        effective_date=effective,
        session_type=session_type,
        label=str(label) if label else session_type.value,
        source_sha256=source_sha,
        source_url=raw.get("source_url") if isinstance(raw.get("source_url"), str) else None,
        publication_date=publication,
        available_at=available_at,
        open_at=open_at,
        close_at=close_at,
        replacement_of=replacement,
        intervals=intervals,
        supporting_source_sha256s=supporting_shas,
        phases=phases,
    )


def _parse_session_phases(
    raw: Any, effective: date, intervals: tuple[tuple[str, str], ...],
) -> tuple[SessionPhase, ...]:
    """Security-scoped phases may overlap; price-forming union must match the venue windows.

    Pre-open and post-close are documented but are not the normal/closing-price
    session used by this daily date ledger. They do not establish file availability.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw or not intervals:
        raise LedgerError("phases require a non-empty list and explicit venue intervals")
    phases: list[SessionPhase] = []
    price_windows: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise LedgerError("session phase must be an object")
        phase, scope = item.get("phase"), item.get("security_scope")
        if not isinstance(phase, str) or phase not in {"continuous", "closing_auction", "pre_open", "post_close"}:
            raise LedgerError("unsupported session phase")
        if not isinstance(scope, str) or not scope.strip():
            raise LedgerError("session phase requires security_scope")
        opening, closing = _parse_one_interval(item.get("open_at"), item.get("close_at"), effective)
        phases.append(SessionPhase(phase, scope, opening, closing))
        if phase in {"continuous", "closing_auction"}:
            price_windows.append((opening, closing))

    def union(windows: Sequence[tuple[str, str]]) -> list[tuple[datetime, datetime]]:
        merged: list[tuple[datetime, datetime]] = []
        for start, end in sorted((datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in windows):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return merged

    if union(price_windows) != union(intervals):
        raise LedgerError("price-forming phase union must equal venue intervals")
    return tuple(phases)


def _parse_one_interval(open_at: Any, close_at: Any, effective: date) -> tuple[str, str]:
    if not isinstance(open_at, str) or not isinstance(close_at, str):
        raise LedgerError("amendment interval open_at and close_at must be strings")
    canonical_timestamp(open_at, "open_at")
    canonical_timestamp(close_at, "close_at")
    start = datetime.fromisoformat(open_at)
    end = datetime.fromisoformat(close_at)
    if start >= end:
        raise LedgerError("amendment interval must be half-open and positive")
    if start.astimezone(IST).date() != effective or end.astimezone(IST).date() != effective:
        raise LedgerError("amendment interval must fall on the effective date in IST")
    return open_at, close_at


def _parse_amendment_intervals(
    raw_intervals: Any, effective: date, open_at: Any, close_at: Any
) -> tuple[tuple[str, str], ...]:
    """Ordered, non-overlapping, date-bound windows; closure is the last close."""
    if raw_intervals is not None:
        if not isinstance(raw_intervals, list) or not raw_intervals:
            raise LedgerError("amendment intervals must be a non-empty list")
        if open_at is not None or close_at is not None:
            raise LedgerError("amendment must not mix intervals with single open_at/close_at")
        parsed: list[tuple[str, str]] = []
        previous_close: datetime | None = None
        for item in raw_intervals:
            if not isinstance(item, Mapping):
                raise LedgerError("each amendment interval must be an object")
            window = _parse_one_interval(item.get("open_at"), item.get("close_at"), effective)
            start = datetime.fromisoformat(window[0])
            if previous_close is not None and start < previous_close:
                raise LedgerError("amendment intervals must be ordered and non-overlapping")
            previous_close = datetime.fromisoformat(window[1])
            parsed.append(window)
        return tuple(parsed)
    if (open_at is None) != (close_at is None):
        raise LedgerError("amendment open_at and close_at must both be present or both absent")
    if open_at is None:
        return ()
    return (_parse_one_interval(open_at, close_at, effective),)


def parse_calendar_review(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise LedgerError("calendar review must be an object")
    unresolved = raw.get("unresolved", [])
    if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
        raise LedgerError("calendar review 'unresolved' must be a list of strings")
    for key in ("special_session_inventory_complete", "annual_calendar_complete"):
        value = raw.get(key)
        if value is not None and not isinstance(value, bool):
            raise LedgerError(f"calendar review '{key}' must be a boolean or null")
    reviewed_at = raw.get("reviewed_at")
    if reviewed_at is not None:
        canonical_timestamp(reviewed_at, "reviewed_at")

    contested_raw = raw.get("contested_dates", [])
    if not isinstance(contested_raw, list):
        raise LedgerError("calendar review 'contested_dates' must be a list")
    contested_dates: list[dict[str, str]] = []
    for item in contested_raw:
        if not isinstance(item, Mapping):
            raise LedgerError("each contested date must be an object")
        value = item.get("session_date")
        if not isinstance(value, str):
            raise LedgerError("contested date session_date must be a canonical YYYY-MM-DD string")
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise LedgerError("contested date session_date must be canonical YYYY-MM-DD")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason:
            raise LedgerError("contested date reason is required")
        contested_dates.append({"session_date": value, "reason": reason})

    indirect_raw = raw.get("indirect_notice_shas", [])
    if not isinstance(indirect_raw, list):
        raise LedgerError("calendar review 'indirect_notice_shas' must be a list")
    indirect_notice_shas = [_require_hex64(value, "indirect notice sha256") for value in indirect_raw]

    transport_raw = raw.get("transport_history", [])
    if not isinstance(transport_raw, list) or any(not isinstance(item, str) for item in transport_raw):
        raise LedgerError("calendar review 'transport_history' must be a list of strings")

    return {
        "special_session_inventory_complete": raw.get("special_session_inventory_complete"),
        "annual_calendar_complete": raw.get("annual_calendar_complete"),
        "unresolved": list(unresolved),
        "contested_dates": contested_dates,
        "indirect_notice_shas": indirect_notice_shas,
        "transport_history": list(transport_raw),
        "reviewed_at": reviewed_at,
        "notes": raw.get("notes") if isinstance(raw.get("notes"), str) else None,
    }


# ---------------------------------------------------------------------------
# Internal resolution records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Override:
    state: str
    session_type: SessionType | None
    reason: str
    open_at: str | None
    close_at: str | None
    source: RetainedSource | None
    amendment: bool
    intervals: tuple[tuple[str, str], ...] = ()
    phases: tuple[SessionPhase, ...] = ()


@dataclass(frozen=True)
class _Resolved:
    state: str
    session_type: SessionType | None
    reason: str
    open_at: str | None
    close_at: str | None
    source: RetainedSource | None
    amendment: bool
    intervals: tuple[tuple[str, str], ...] = ()
    phases: tuple[SessionPhase, ...] = ()

    @property
    def closure_known(self) -> bool:
        if self.state != EXPECTED:
            return True
        return bool(self.intervals)


@dataclass(frozen=True)
class _LegacyGroup:
    payload_hash: str
    dates: tuple[date, ...]

    @property
    def canonical_date(self) -> date:
        return min(self.dates)


@dataclass(frozen=True)
class LedgerResult:
    report: dict[str, Any]
    rows: tuple[dict[str, Any], ...]

    @property
    def report_bytes(self) -> bytes:
        return canonical_json(self.report)

    @property
    def sessions_bytes(self) -> bytes:
        return b"".join(canonical_json(row) for row in self.rows)


def _iter_dates(start: date, end: date) -> Iterator[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def _parse_as_of(as_of: str) -> tuple[str, datetime, date]:
    canonical_timestamp(as_of, "as_of")
    parsed = datetime.fromisoformat(as_of)
    return as_of, parsed, parsed.astimezone(IST).date()


def _parse_legacy_groups(inventory: Mapping[str, Any]) -> list[_LegacyGroup]:
    groups: list[_LegacyGroup] = []
    for raw in inventory.get("repeated_payload_groups", []) or []:
        if not isinstance(raw, Mapping):
            raise LedgerError("legacy inventory duplicate group must be an object")
        payload_hash = _require_hex64(raw.get("payload_hash"), "legacy payload_hash")
        dates = raw.get("dates")
        if not isinstance(dates, list) or len(dates) < 2:
            raise LedgerError(f"legacy duplicate group {payload_hash} needs at least two dates")
        parsed = tuple(sorted(date.fromisoformat(str(value)) for value in dates))
        groups.append(_LegacyGroup(payload_hash, parsed))
    return groups


def _cash_status(cash_root: str | Path | None, session_date: date) -> dict[str, Any]:
    if cash_root is None:
        return {"status": CASH_ROOT_ABSENT, "attempts": 0, "reason": "no optional cash root was supplied"}
    root = Path(cash_root)
    requests = root / "requests"
    if not requests.is_dir():
        return {"status": CASH_ROOT_ABSENT, "attempts": 0, "root": str(root), "reason": "cash evidence root/requests absent"}
    request_dir = requests / session_date.isoformat()
    if not request_dir.is_dir():
        return {"status": CASH_NOT_RETAINED, "attempts": 0}
    # Attempts may live directly under the date (default route) or under a
    # named route subdirectory; every route carries its own budget and receipts.
    attempt_dirs = sorted(path for path in request_dir.glob("**/attempt-*") if path.is_dir())
    receipts: list[tuple[dict[str, Any], str | None]] = []
    for attempt in attempt_dirs:
        receipt_path = attempt / "receipt.json"
        if not receipt_path.is_file():
            continue
        try:
            decoded = json.loads(receipt_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, Mapping):
            relative = attempt.relative_to(request_dir)
            route = relative.parts[0] if len(relative.parts) > 1 else None
            receipts.append((dict(decoded), route))
    accepted = [
        (receipt, route) for receipt, route in receipts
        if isinstance(receipt.get("validation_result"), Mapping)
        and receipt["validation_result"].get("status") == ACCEPTED
    ]
    if accepted:
        receipt, route = accepted[0]
        return {
            "status": CASH_ADMITTED,
            "attempts": len(attempt_dirs),
            "route": route,
            "normalized_path": receipt.get("normalized_path"),
            "normalized_sha256": receipt.get("normalized_sha256"),
            "source_sha256": receipt.get("body_sha256"),
            "source_url": receipt.get("final_url") or receipt.get("requested_url"),
        }
    if receipts:
        latest, route = receipts[-1]
        result = latest.get("validation_result") if isinstance(latest.get("validation_result"), Mapping) else {}
        if result.get("status") == REJECTED:
            return {
                "status": CASH_REJECTED,
                "attempts": len(attempt_dirs),
                "route": route,
                "reason": result.get("reason"),
                "source_sha256": latest.get("body_sha256"),
            }
    return {"status": CASH_RETAINED, "attempts": len(attempt_dirs)}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_ledger(
    *,
    start: date,
    as_of: str,
    annual_calendars: Sequence[AnnualCalendar],
    notices: Sequence[NoticeSource] = (),
    retained_sources: Sequence[RetainedSource] = (),
    amendments: Sequence[Mapping[str, Any]] = (),
    calendar_review: Mapping[str, Any] | None = None,
    legacy_inventory: Mapping[str, Any] | None = None,
    legacy_inventory_path: str | None = None,
    cash_root: str | Path | None = None,
) -> LedgerResult:
    """Build the immutable ledger; never publishes a denominator it cannot support."""
    as_of, as_of_dt, preflight_date = _parse_as_of(as_of)
    if start > preflight_date:
        raise LedgerError("start date is after the preflight date")

    annual_by_year: dict[int, AnnualCalendar] = {}
    duplicate_year_sources: list[dict[str, Any]] = []
    for annual in annual_calendars:
        existing = annual_by_year.get(annual.year)
        if existing is not None and existing.source.sha256 != annual.source.sha256:
            duplicate_year_sources.append({
                "year": annual.year,
                "retained_sha256": [existing.source.sha256, annual.source.sha256],
            })
            continue
        annual_by_year.setdefault(annual.year, annual)

    retained_by_sha: dict[str, RetainedSource] = {}
    for source in list(retained_sources):
        retained_by_sha.setdefault(source.sha256, source)
    for annual in annual_calendars:
        retained_by_sha.setdefault(annual.source.sha256, annual.source)
    for notice in notices:
        retained_by_sha.setdefault(notice.source.sha256, notice.source)
    accepted_by_sha = {
        sha: source for sha, source in retained_by_sha.items() if source.decision == ACCEPTED
    }

    # Base calendar derived only from retained annual evidence.  An annual
    # source with no parsable holiday rows is treated as unusable rather than
    # allowing an implicit weekday fallback.
    holidays_by_year: dict[int, dict[str, dict[str, str]]] = {}
    unusable_years: set[int] = set()
    for year, annual in annual_by_year.items():
        if not annual.usable:
            unusable_years.add(year)
            continue
        parsed_holidays = {
            row["session_date"]: row for row in parse_holiday_rows(annual.text, year=year)
        }
        if not parsed_holidays:
            unusable_years.add(year)
            continue
        holidays_by_year[year] = parsed_holidays

    def base_resolution(day: date) -> _Resolved:
        annual = annual_by_year.get(day.year)
        if annual is None or not annual.usable or day.year in unusable_years:
            return _Resolved(CALENDAR_UNRESOLVED, None, "missing_annual_source", None, None, annual.source if annual else None, False)
        holiday = holidays_by_year.get(day.year, {}).get(day.isoformat())
        if holiday is not None:
            session_type = SessionType(str(holiday.get("session_type", SessionType.HOLIDAY.value)))
            if session_type is SessionType.HOLIDAY:
                return _Resolved(NON_SESSION, session_type, holiday.get("label", "holiday"), None, None, annual.source, False)
            return _Resolved(EXPECTED, session_type, holiday.get("label", session_type.value), None, None, annual.source, False)
        if day.weekday() >= 5:
            return _Resolved(NON_SESSION, None, "weekend", None, None, annual.source, False)
        opening, closing = ist_interval(day, REGULAR_OPEN, REGULAR_CLOSE)
        return _Resolved(
            EXPECTED, SessionType.REGULAR, "regular trading day", opening, closing,
            annual.source, False, ((opening, closing),),
        )

    # Apply hash-bound amendments to dates inside the frozen preflight window.
    overrides: dict[date, _Override] = {}
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    amendment_blockers: list[dict[str, Any]] = []
    applied_shas: set[str] = set()

    def reject_amendment(raw: Mapping[str, Any], reason: str) -> None:
        rejected.append({"amendment": dict(raw), "reason": reason})
        amendment_blockers.append({
            "kind": "amendment_rejected",
            "scope": _SCOPE_AMENDMENT,
            "detail": reason,
            "effective_date": raw.get("effective_date") if isinstance(raw, Mapping) else None,
        })

    for raw in amendments:
        if not isinstance(raw, Mapping):
            amendment_blockers.append({"kind": "amendment_malformed", "scope": _SCOPE_AMENDMENT, "detail": "amendment is not an object"})
            continue
        try:
            amendment = parse_amendment(raw)
        except (LedgerError, ValueError) as exc:
            reject_amendment(raw, f"invalid amendment: {exc}")
            continue
        source = accepted_by_sha.get(amendment.source_sha256)
        if source is None:
            reject_amendment(raw, "amendment source hash is not a retained accepted source")
            continue
        supporting_sources = [accepted_by_sha.get(sha) for sha in amendment.supporting_source_sha256s]
        if any(item is None for item in supporting_sources):
            reject_amendment(raw, "amendment supporting source hash is not a retained accepted source")
            continue
        if any(
            item is not None and (
                (item.publication_date is not None and date.fromisoformat(item.publication_date) > preflight_date)
                or (item.available_at is not None and datetime.fromisoformat(item.available_at) > as_of_dt)
            )
            for item in supporting_sources
        ):
            reject_amendment(raw, "amendment supporting source is after the frozen preflight")
            continue
        if not (start <= amendment.effective_date <= preflight_date):
            reject_amendment(raw, "amendment effective date is outside the frozen preflight range")
            continue
        publication = amendment.publication_date or (
            date.fromisoformat(source.publication_date) if source.publication_date else None
        )
        if publication is not None and publication > preflight_date:
            reject_amendment(raw, "amendment source publication date is after the preflight date")
            continue
        if amendment.available_at is not None and datetime.fromisoformat(amendment.available_at) > as_of_dt:
            reject_amendment(raw, "amendment source was not available at the preflight timestamp")
            continue
        if amendment.effective_date in overrides:
            reject_amendment(raw, "a conflicting amendment already targets this date")
            continue
        if amendment.session_type is SessionType.REGULAR and amendment.open_at is not None and not amendment.phases:
            reject_amendment(raw, "a regular session amendment must not declare a special interval")
            continue
        if amendment.session_type is SessionType.REGULAR and amendment.phases:
            baseline = base_resolution(amendment.effective_date)
            if baseline.state != EXPECTED or baseline.session_type is not SessionType.REGULAR:
                reject_amendment(raw, "a regular phase amendment requires an authoritative regular date")
                continue
        if amendment.session_type is SessionType.HOLIDAY and amendment.open_at is not None:
            reject_amendment(raw, "a holiday amendment must not declare a session interval")
            continue
        if amendment.session_type is SessionType.HOLIDAY:
            overrides[amendment.effective_date] = _Override(NON_SESSION, SessionType.HOLIDAY, amendment.label, None, None, source, True)
        elif amendment.open_at is None:
            overrides[amendment.effective_date] = _Override(EXPECTED, amendment.session_type, amendment.label, None, None, source, True)
        else:
            overrides[amendment.effective_date] = _Override(
                EXPECTED, amendment.session_type, amendment.label,
                amendment.open_at, amendment.close_at, source, True, amendment.intervals,
                amendment.phases,
            )
        applied.append(amendment.as_report_dict() | {"source_label": source.label, "publication_basis": "amendment_or_source_fact"})
        applied_shas.add(amendment.source_sha256)
        applied_shas.update(amendment.supporting_source_sha256s)
        if amendment.replacement_of is not None:
            original = amendment.replacement_of
            if start <= original <= preflight_date and original not in overrides:
                weekend = original.weekday() >= 5
                if weekend:
                    overrides[original] = _Override(
                        NON_SESSION, None,
                        f"holiday replaced: moved to {amendment.effective_date.isoformat()}",
                        None, None, source, True,
                    )
                else:
                    opening, closing = ist_interval(original, REGULAR_OPEN, REGULAR_CLOSE)
                    overrides[original] = _Override(
                        EXPECTED, SessionType.REGULAR,
                        f"holiday replaced: moved to {amendment.effective_date.isoformat()}",
                        opening, closing, source, True, ((opening, closing),),
                    )

    # The caller-supplied review must be parsed before resolution so contested
    # dates are never silently defaulted to a session or a non-session.
    review_report: dict[str, Any] | None = None
    review_blockers: list[dict[str, Any]] = []
    if calendar_review is None:
        review_blockers.append({
            "kind": "calendar_review_absent",
            "scope": _SCOPE_REVIEW,
            "detail": "no calendar review was supplied; completeness facts are unverified",
        })
    else:
        review_report = parse_calendar_review(calendar_review)
        if review_report["special_session_inventory_complete"] is not True:
            review_blockers.append({
                "kind": "special_session_inventory_incomplete",
                "scope": _SCOPE_REVIEW,
                "detail": "special-session inventory is not certified complete",
            })
        if review_report["annual_calendar_complete"] is not True:
            review_blockers.append({
                "kind": "annual_calendar_completeness_unverified",
                "scope": _SCOPE_REVIEW,
                "detail": "annual calendar completeness is not certified",
            })
        for item in review_report["unresolved"]:
            review_blockers.append({
                "kind": "calendar_review_unresolved",
                "scope": _SCOPE_REVIEW,
                "detail": item,
            })

    # Dates the review explicitly flags as contested: their status cannot be
    # resolved from original exchange evidence, so they must not be silently
    # counted as a regular session (or weekend/holiday) by annual default.
    contested: dict[date, str] = {}
    if review_report is not None:
        for item in review_report["contested_dates"]:
            contested[date.fromisoformat(item["session_date"])] = item["reason"]
    contested_blockers: list[dict[str, Any]] = []

    def resolve(day: date) -> _Resolved:
        override = overrides.get(day)
        if override is not None:
            return _Resolved(
                override.state,
                override.session_type,
                override.reason,
                override.open_at,
                override.close_at,
                override.source,
                override.amendment,
                override.intervals,
                override.phases,
            )
        if day in contested:
            if start <= day <= preflight_date:
                contested_blockers.append({
                    "kind": "contested_calendar_date",
                    "scope": _SCOPE_CALENDAR,
                    "session_date": day.isoformat(),
                    "detail": contested[day],
                })
            return _Resolved(CALENDAR_UNRESOLVED, None, f"contested: {contested[day]}", None, None, None, False)
        return base_resolution(day)

    resolved = {day: resolve(day) for day in _iter_dates(start, preflight_date)}

    # Latest completed authoritative session at the frozen preflight timestamp.
    completed: list[date] = []
    for day, record in resolved.items():
        if record.state != EXPECTED:
            continue
        if day < preflight_date:
            completed.append(day)
        elif record.open_at is not None and record.close_at is not None and datetime.fromisoformat(record.close_at) <= as_of_dt:
            completed.append(day)
    latest = max(completed) if completed else None

    calendar_blockers: list[dict[str, Any]] = []
    if latest is None:
        calendar_blockers.append({
            "kind": "no_completed_session_before_as_of",
            "scope": _SCOPE_CALENDAR,
            "detail": "no authoritative expected session completes on or before the preflight timestamp",
        })

    end = latest if latest is not None else start
    row_dates = list(_iter_dates(start, end))

    legacy_groups: list[_LegacyGroup] = []
    if legacy_inventory is not None:
        legacy_groups = _parse_legacy_groups(legacy_inventory)
    groups_by_date: dict[date, list[_LegacyGroup]] = {}
    for group in legacy_groups:
        for group_date in group.dates:
            groups_by_date.setdefault(group_date, []).append(group)

    legacy_expected_gaps: list[dict[str, Any]] = []
    legacy_non_session: list[dict[str, Any]] = []
    retained_unqualified: list[dict[str, Any]] = []

    rows: list[dict[str, Any]] = []
    state_counts = {EXPECTED: 0, NON_SESSION: 0, CALENDAR_UNRESOLVED: 0}
    session_type_counts: dict[str, int] = {key.value: 0 for key in SessionType}
    closure_unknown: list[dict[str, Any]] = []
    expected_dates: list[date] = []

    for day in row_dates:
        record = resolved[day]
        state_counts[record.state] = state_counts.get(record.state, 0) + 1
        if record.session_type is not None:
            session_type_counts[record.session_type.value] = session_type_counts.get(record.session_type.value, 0) + 1
        if record.state == EXPECTED:
            expected_dates.append(day)
            if not record.closure_known:
                closure_unknown.append({
                    "kind": "missing_session_interval",
                    "scope": _SCOPE_CALENDAR,
                    "session_date": day.isoformat(),
                    "session_type": record.session_type.value if record.session_type else None,
                    "reason": record.reason,
                    "detail": "expected session has no retained closure interval",
                })
        artifacts: list[dict[str, Any]] = []
        severity = _LEGACY_SEVERITY[LEGACY_ABSENT]
        for group in groups_by_date.get(day, []):
            is_canonical = day == group.canonical_date
            if record.state == NON_SESSION:
                artifact_status = LEGACY_NON_SESSION
                legacy_non_session.append({"session_date": day.isoformat(), "payload_hash": group.payload_hash})
            elif record.state == EXPECTED and not is_canonical:
                artifact_status = LEGACY_EXPECTED_GAP
                legacy_expected_gaps.append({"session_date": day.isoformat(), "payload_hash": group.payload_hash, "duplicate_dates": [value.isoformat() for value in group.dates]})
            else:
                artifact_status = LEGACY_RETAINED
                retained_unqualified.append({"session_date": day.isoformat(), "payload_hash": group.payload_hash, "is_canonical": is_canonical})
            severity = max(severity, _LEGACY_SEVERITY[artifact_status])
            artifacts.append({
                "payload_hash": group.payload_hash,
                "declared_date": day.isoformat(),
                "duplicate_dates": [value.isoformat() for value in group.dates],
                "duplicate_count": len(group.dates),
                "is_canonical": is_canonical,
                "status": artifact_status,
            })
        legacy_status = next(key for key, value in _LEGACY_SEVERITY.items() if value == severity)
        rows.append({
            "session_date": day.isoformat(),
            "state": record.state,
            "session_type": record.session_type.value if record.session_type else None,
            "reason": record.reason,
            "open_at": record.open_at,
            "close_at": record.close_at,
            "first_open_at": record.intervals[0][0] if record.intervals else None,
            "last_close_at": record.intervals[-1][1] if record.intervals else None,
            "intervals": [{"open_at": open_at, "close_at": close_at} for open_at, close_at in record.intervals],
            "interval_count": len(record.intervals),
            "interval_semantics": "venue_price_forming_phase_union" if record.phases else "normal_market_windows",
            "phases": [phase.as_dict() for phase in record.phases],
            "closure_known": record.closure_known,
            "amendment_applied": record.amendment,
            "calendar_source_sha256": record.source.sha256 if record.source else None,
            "calendar_source_url": record.source.source_url if record.source else None,
            "calendar_source_path": record.source.source_path if record.source else None,
            "calendar_source_kind": record.source.source_kind if record.source else None,
            "legacy_status": legacy_status,
            "legacy_artifacts": artifacts,
            "cash": _cash_status(cash_root, day),
        })

    # Calendar-level unresolved dates inside the emitted range.
    for day in row_dates:
        record = resolved[day]
        if record.state == CALENDAR_UNRESOLVED and day not in contested:
            calendar_blockers.append({
                "kind": "unresolved_calendar_date",
                "scope": _SCOPE_CALENDAR,
                "session_date": day.isoformat(),
                "detail": "no usable retained annual calendar covers this year",
            })
    calendar_blockers.extend(contested_blockers)

    for entry in closure_unknown:
        calendar_blockers.append(entry)

    for duplicate in duplicate_year_sources:
        calendar_blockers.append({
            "kind": "conflicting_annual_sources",
            "scope": _SCOPE_CALENDAR,
            "detail": "two retained annual calendars disagree for one year",
            **duplicate,
        })

    # Retained notices that no applied amendment interpreted remain open facts.
    # Indirect official clearing notices are labelled as such and are never
    # promoted to original exchange trading evidence.
    indirect_notice_shas = set(review_report["indirect_notice_shas"]) if review_report else set()
    for notice in notices:
        if notice.source.sha256 in applied_shas:
            continue
        if notice.source.sha256 in indirect_notice_shas:
            review_blockers.append({
                "kind": "indirect_notice_retained",
                "scope": _SCOPE_REVIEW,
                "detail": "indirect official clearing notice retained; it is not original exchange trading evidence and is not promoted to one",
                "source_label": notice.source.label,
                "source_url": notice.source.source_url,
                "sha256": notice.source.sha256,
            })
        else:
            review_blockers.append({
                "kind": "uninterpreted_retained_notice",
                "scope": _SCOPE_REVIEW,
                "detail": "a retained accepted special-session notice has no hash-bound amendment",
                "source_label": notice.source.label,
                "source_url": notice.source.source_url,
                "sha256": notice.source.sha256,
            })

    legacy_blockers: list[dict[str, Any]] = []
    if legacy_inventory is not None:
        for gap in legacy_expected_gaps:
            legacy_blockers.append({
                "kind": "legacy_expected_date_duplicate",
                "scope": _SCOPE_LEGACY,
                "session_date": gap["session_date"],
                "detail": "a legacy payload on an expected session duplicates another declared date",
                "payload_hash": gap["payload_hash"],
            })
        if (
            legacy_inventory.get("unique_payload_count") is not None
            and legacy_inventory.get("per_file_inventory_complete") is not True
        ):
            legacy_blockers.append({
                "kind": "legacy_unique_dates_not_enumerated",
                "scope": _SCOPE_LEGACY,
                "detail": "per-file inventory is unavailable for the unique payloads; caller-supplied rows or a full inventory are required",
            })

    cash_blockers: list[dict[str, Any]] = []
    if cash_root is None or not (Path(cash_root) / "requests").is_dir():
        cash_blockers.append({
            "kind": "cash_root_absent",
            "scope": _SCOPE_CASH,
            "detail": "optional daily cash evidence root is absent; price admission was not evaluated",
        })

    blockers = amendment_blockers + calendar_blockers + review_blockers + legacy_blockers + cash_blockers
    denominator_blockers = [entry for entry in blockers if entry.get("scope") in _BLOCKING_SCOPES]
    qualification_blockers = [
        entry for entry in blockers if entry.get("scope") in {_SCOPE_LEGACY, _SCOPE_CASH}
    ]
    denominator_qualified = latest is not None and not denominator_blockers

    report = _build_report(
        start=start,
        as_of=as_of,
        preflight_date=preflight_date,
        latest=latest,
        rows=rows,
        state_counts=state_counts,
        session_type_counts=session_type_counts,
        expected_dates=expected_dates,
        denominator_qualified=denominator_qualified,
        blockers=blockers,
        denominator_blockers=denominator_blockers,
        qualification_blockers=qualification_blockers,
        applied=applied,
        rejected=rejected,
        review_report=review_report,
        annual_by_year=annual_by_year,
        holidays_by_year=holidays_by_year,
        notices=notices,
        retained_by_sha=retained_by_sha,
        legacy_inventory=legacy_inventory,
        legacy_groups=legacy_groups,
        legacy_expected_gaps=legacy_expected_gaps,
        legacy_non_session=legacy_non_session,
        retained_unqualified=retained_unqualified,
        cash_root=cash_root,
        rows_for_cash=rows,
        closure_unknown=closure_unknown,
        legacy_inventory_path=legacy_inventory_path,
    )
    return LedgerResult(report=report, rows=tuple(rows))


def _source_family(
    *,
    family: str,
    root: str | None,
    producer: str,
    file_format: str,
    observed_range: Any,
    last_observation: Any,
    stock_count: Any,
    raw_adjusted: str,
    volume_convention: str,
    identifiers: str,
    action_coverage: str,
    availability_evidence: str,
    checksums: str,
    duplicate_revisions: str,
    permitted_use: str,
    proposed_use: str,
    next_action: str,
    status: str | None = None,
) -> dict[str, Any]:
    """One plan §4 inventory row: provenance through next action."""
    return {
        "family": family,
        "root": root,
        "producer": producer,
        "file_format": file_format,
        "observed_range": observed_range,
        "last_observation": last_observation,
        "stock_count": stock_count,
        "raw_adjusted": raw_adjusted,
        "volume_convention": volume_convention,
        "identifiers": identifiers,
        "action_coverage": action_coverage,
        "availability_evidence": availability_evidence,
        "checksums": checksums,
        "duplicate_revisions": duplicate_revisions,
        "permitted_use": permitted_use,
        "proposed_use": proposed_use,
        "next_action": next_action,
        "status": status,
    }


def _source_family_inventory(
    *,
    inventory: Mapping[str, Any] | None,
    legacy_groups: Sequence[_LegacyGroup],
    annual_by_year: Mapping[int, AnnualCalendar],
    notices: Sequence[NoticeSource],
    retained_by_sha: Mapping[str, RetainedSource],
    cash_root: str | Path | None,
    applied_count: int,
) -> list[dict[str, Any]]:
    families: list[dict[str, Any]] = []

    if inventory is not None:
        group_dates = [value for group in legacy_groups for value in group.dates]
        observed_range = {
            "start": min(group_dates).isoformat() if group_dates else None,
            "end": max(group_dates).isoformat() if group_dates else None,
            "basis": "duplicate-group dates only; unique-file dates are not enumerated by this inventory",
        }
        families.append(_source_family(
            family="legacy_price_only_parquet",
            root=str(inventory.get("root")) if inventory.get("root") else None,
            producer="unrecorded in retained inventory",
            file_format="parquet",
            observed_range=observed_range,
            last_observation=None,
            stock_count=None,
            raw_adjusted="unknown; close/prev_close only, no source bytes",
            volume_convention="unknown",
            identifiers="symbol only (no ISIN or instrument id)",
            action_coverage="none retained",
            availability_evidence="absent; no retrieval timestamps or identifiers",
            checksums=f"per-artifact payload_hash for {len(legacy_groups)} duplicate groups",
            duplicate_revisions=f"{inventory.get('repeated_artifact_count')} artifacts in {len(legacy_groups)} duplicate payload groups",
            permitted_use="E0/E1 reconciliation evidence only; never session admission",
            proposed_use="reconcile missing expected sessions and duplicate payloads",
            next_action="retain original source bytes plus identity, action and availability evidence before admission",
            status=str(inventory.get("status")) if inventory.get("status") else None,
        ))

    families.append(_source_family(
        family="retained_annual_calendars_phase02",
        root="derived_data/phase02/raw/nse_calendar/public-v1/annual",
        producer="official NSE annual holiday circulars (CMTR series)",
        file_format="pdf",
        observed_range={
            "years": sorted(annual_by_year),
            "effective_date_policy": "retained annual source year as canonical YYYY-01-01",
        },
        last_observation=max((annual.source.publication_date or "" for annual in annual_by_year.values()), default=None) or None,
        stock_count=None,
        raw_adjusted="official holiday dates only; no prices",
        volume_convention="not applicable",
        identifiers="annual circular reference and URL",
        action_coverage="holidays and Muhurat dates; timings only where stated",
        availability_evidence="retrieved_at plus circular date; available_at absent",
        checksums="sha256 per retained PDF, verified against manifest",
        duplicate_revisions="one retained source per year; conflicts are reported",
        permitted_use="authoritative holiday and non-session evidence for the declared year",
        proposed_use="base expected/non-session calendar; not a special-session completeness proof",
        next_action="interpret retained special-session notices into hash-bound amendments",
        status="accepted",
    ))

    annual_2026 = [annual for annual in annual_by_year.values() if annual.year >= 2026]
    families.append(_source_family(
        family="daily_calendar_evidence_v1",
        root="derived_data/daily-graph/calendar-evidence-v1",
        producer="bounded official-source retriever (ingest/daily_calendar_evidence.py)",
        file_format="pdf/html retained bodies with receipts.jsonl",
        observed_range={
            "annual_years": sorted(annual.year for annual in annual_2026),
            "special_session_notices": len(notices),
        },
        last_observation=max((notice.source.retrieved_at or "" for notice in notices), default=None) or None,
        stock_count=None,
        raw_adjusted="official calendar and notice text; no prices",
        volume_convention="not applicable",
        identifiers="requested URL, logical name, declared year",
        action_coverage="2026 annual calendar plus retained special-session notices",
        availability_evidence="retrieved_at retained; available_at authoritatively absent",
        checksums="sha256 per retained body, verified on read",
        duplicate_revisions=f"{len(retained_by_sha)} retained source bodies including rejected attempts",
        permitted_use="evidence for calendar construction and hash-bound amendments",
        proposed_use="2026 annual calendar base plus special-session notice interpretation",
        next_action="obtain substitute bytes for 404 notices and publish a complete review",
        status="accepted_with_rejections",
    ))

    families.append(_source_family(
        family="amendment_interpretations",
        root=None,
        producer="parent-supplied, hash-bound to a retained source",
        file_format="json",
        observed_range={"applied_amendments": applied_count},
        last_observation=None,
        stock_count=None,
        raw_adjusted="interpretation, not raw evidence",
        volume_convention="not applicable",
        identifiers="source_sha256 binding each amendment to a retained body",
        action_coverage="holiday replacement, Muhurat/special intervals, weekend sessions",
        availability_evidence="publication_date and available_at checked against the preflight timestamp",
        checksums="source_sha256 must match a retained accepted source",
        duplicate_revisions="conflicting amendments for one date are rejected",
        permitted_use="only when hash-bound and inside the frozen preflight window",
        proposed_use="supply the required session semantics the annual calendars omit",
        next_action="supply amendments for every retained special-session notice",
        status="applied" if applied_count else "none_supplied",
    ))

    families.append(_source_family(
        family="daily_cash_evidence_optional",
        root=str(cash_root) if cash_root is not None else None,
        producer="ingest/daily_cash.py current-session collector",
        file_format="csv responses plus normalized json and receipts",
        observed_range={"evaluated": cash_root is not None},
        last_observation=None,
        stock_count=None,
        raw_adjusted="raw Full Bhavdata price fields retained per attempt",
        volume_convention="as published on the source response",
        identifiers="symbol only; identity admission is separate",
        action_coverage="none; price-only",
        availability_evidence="retrieved_at and headers per accepted attempt",
        checksums="body_sha256 and normalized_sha256 per attempt receipt",
        duplicate_revisions="accepted receipt is reused; rejected attempts retained",
        permitted_use="admitted-price evidence for the named daily study only",
        proposed_use="price-status annotation for the session ledger",
        next_action="finish the cash-interface correction, then retain the missing sessions",
        status="root_present" if (cash_root is not None and (Path(cash_root) / "requests").is_dir()) else "root_absent",
    ))
    return families


def _build_report(
    *,
    start: date,
    as_of: str,
    preflight_date: date,
    latest: date | None,
    rows: Sequence[Mapping[str, Any]],
    state_counts: Mapping[str, int],
    session_type_counts: Mapping[str, int],
    expected_dates: Sequence[date],
    denominator_qualified: bool,
    blockers: Sequence[Mapping[str, Any]],
    denominator_blockers: Sequence[Mapping[str, Any]],
    qualification_blockers: Sequence[Mapping[str, Any]],
    applied: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    review_report: Mapping[str, Any] | None,
    annual_by_year: Mapping[int, AnnualCalendar],
    holidays_by_year: Mapping[int, Mapping[str, Mapping[str, str]]],
    notices: Sequence[NoticeSource],
    retained_by_sha: Mapping[str, RetainedSource],
    legacy_inventory: Mapping[str, Any] | None,
    legacy_groups: Sequence[_LegacyGroup],
    legacy_expected_gaps: Sequence[Mapping[str, Any]],
    legacy_non_session: Sequence[Mapping[str, Any]],
    retained_unqualified: Sequence[Mapping[str, Any]],
    cash_root: str | Path | None,
    rows_for_cash: Sequence[Mapping[str, Any]],
    closure_unknown: Sequence[Mapping[str, Any]],
    legacy_inventory_path: str | None,
) -> dict[str, Any]:
    cash_counts = {
        CASH_ADMITTED: 0,
        CASH_REJECTED: 0,
        CASH_RETAINED: 0,
        CASH_NOT_RETAINED: 0,
        CASH_ROOT_ABSENT: 0,
    }
    for row in rows_for_cash:
        status = str((row.get("cash") or {}).get("status"))
        cash_counts[status] = cash_counts.get(status, 0) + 1

    legacy_reconciliation: dict[str, Any] | None = None
    if legacy_inventory is not None:
        legacy_reconciliation = {
            "inventory_path": legacy_inventory_path,
            "root": legacy_inventory.get("root"),
            "artifact_count": legacy_inventory.get("artifact_count"),
            "rows": legacy_inventory.get("rows"),
            "columns": legacy_inventory.get("columns"),
            "unique_payload_count": legacy_inventory.get("unique_payload_count"),
            "repeated_artifact_count": legacy_inventory.get("repeated_artifact_count"),
            "duplicate_group_count": len(legacy_groups),
            "admitted_session_count": None,
            "admission_policy": "a unique payload is inventory only; uniqueness is never session admission",
            "expected_date_duplicate_gaps": list(legacy_expected_gaps),
            "non_session_payload_rejections": list(legacy_non_session),
            "retained_unqualified": list(retained_unqualified),
            "unique_dates_not_enumerated": None if legacy_inventory.get("per_file_inventory_complete") is True else "the retained inventory enumerates duplicate groups only; unique artifact dates require a full per-file inventory or caller-supplied rows",
            "per_file_inventory_complete": legacy_inventory.get("per_file_inventory_complete") is True,
            "read_failures": legacy_inventory.get("read_failures"),
            "status": legacy_inventory.get("status"),
        }

    sources_report = [source.as_report_dict() for source in sorted(retained_by_sha.values(), key=lambda item: (item.source_kind, item.label, item.sha256))]
    rejected_sources = [source for source in retained_by_sha.values() if source.decision != ACCEPTED]

    unverified_states = [
        "special-session inventory completeness",
        "closure/timing for expected sessions without a retained interval",
        "legacy unique-artifact dates (duplicate groups only are enumerated)",
        "daily cash price admission (optional evidence root)",
        "amendment availability at the preflight timestamp",
    ]

    report: dict[str, Any] = {
        "schema": "meridian.daily-session-ledger.report.v1",
        "start": start.isoformat(),
        "as_of": as_of,
        "preflight_date": preflight_date.isoformat(),
        "range": {
            "start": start.isoformat(),
            "requested_end": preflight_date.isoformat(),
            "emitted_end": latest.isoformat() if latest else None,
        },
        "latest_completed_session": latest.isoformat() if latest else None,
        "date_grid": {
            "calendar_date_count": len(rows),
            "state_counts": dict(state_counts),
            "session_type_counts": dict(session_type_counts),
            "note": "the complete date grid is not the qualified denominator",
        },
        "denominator": {
            "expected_session_count": len(expected_dates),
            "qualified_expected_denominator": len(expected_dates) if denominator_qualified else None,
            "denominator_qualified": denominator_qualified,
            "historical_checkpoint_blocked": not denominator_qualified,
            "denominator_blocker_count": len(denominator_blockers),
            "qualification_blocker_count": len(qualification_blockers),
            "qualification_basis": "qualified only when no calendar/amendment/review blocker remains and a completed session precedes the preflight timestamp; legacy and cash blockers are qualification/reconciliation facts and do not gate the denominator",
        },
        "blockers": list(blockers),
        "denominator_blockers": list(denominator_blockers),
        "qualification_blockers": list(qualification_blockers),
        "blocker_summary": {
            "total": len(blockers),
            "denominator_blocking": len(denominator_blockers),
            "qualification_only": len(qualification_blockers),
            "by_scope": {
                scope: sum(1 for entry in blockers if entry.get("scope") == scope)
                for scope in (_SCOPE_AMENDMENT, _SCOPE_CALENDAR, _SCOPE_REVIEW, _SCOPE_LEGACY, _SCOPE_CASH)
            },
        },
        "legacy_reconciliation": legacy_reconciliation,
        "cash_reconciliation": {
            "root": str(cash_root) if cash_root is not None else None,
            "root_present": bool(cash_root is not None and (Path(cash_root) / "requests").is_dir()),
            "status_counts": cash_counts,
            "note": "cash evidence is optional and annotated per date; absence is explicit, never inferred",
        },
        "amendments": {
            "applied": list(applied),
            "rejected": list(rejected),
            "applied_count": len(applied),
            "rejected_count": len(rejected),
        },
        "calendar_review": {
            "supplied": review_report is not None,
            "review": dict(review_report) if review_report is not None else None,
        },
        "sources": {
            "retained_source_count": len(retained_by_sha),
            "accepted_source_count": len(retained_by_sha) - len(rejected_sources),
            "rejected_source_count": len(rejected_sources),
            "annual_years": sorted(annual_by_year),
            "holiday_row_counts": {str(year): len(rows) for year, rows in sorted(holidays_by_year.items())},
            "notice_count": len(notices),
            "retained_sources": sources_report,
        },
        "source_family_inventory": _source_family_inventory(
            inventory=legacy_inventory,
            legacy_groups=legacy_groups,
            annual_by_year=annual_by_year,
            notices=notices,
            retained_by_sha=retained_by_sha,
            cash_root=cash_root,
            applied_count=len(applied),
        ),
        "unverified_states": unverified_states,
        "outputs": {"report_json": "report.json", "sessions_jsonl": "sessions.jsonl"},
        "claims": {
            "complete_calendar": False,
            "complete_special_session_coverage": False,
            "note": "retaining every annual source is not a claim of a complete calendar",
        },
    }
    return report


__all__ = [
    "ACCEPTED",
    "AnnualCalendar",
    "CASH_ADMITTED",
    "CASH_NOT_RETAINED",
    "CASH_REJECTED",
    "CASH_RETAINED",
    "CASH_ROOT_ABSENT",
    "CALENDAR_UNRESOLVED",
    "EXPECTED",
    "LEDGER_START",
    "LedgerError",
    "LedgerResult",
    "NON_SESSION",
    "NoticeSource",
    "REJECTED",
    "RetainedSource",
    "build_ledger",
    "default_pdf_text",
    "load_annual_calendars",
    "load_calendar_evidence",
    "parse_amendment",
    "parse_calendar_review",
    "sha256_hex",
]
