"""Contracts for an official, point-in-time-safe NSE trading calendar."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
REGULAR_OPEN = time(9, 15)
REGULAR_CLOSE = time(15, 30)
SOURCE_DATE_ABSENT = "source_date_absent"
AVAILABLE_AT_ABSENT = "not_authoritatively_published"
OFFICIAL_HOSTS = frozenset({
    "www.nseindia.com", "nseindia.com", "archives.nseindia.com",
    "nsearchives.nseindia.com", "www.niftyindices.com", "niftyindices.com",
})


class SessionType(StrEnum):
    REGULAR = "regular"
    HOLIDAY = "holiday"
    MUHURAT = "muhurat"
    SPECIAL = "special"
    SHORTENED = "shortened"
    CONTINGENCY = "contingency"


def _date(value: str, field: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be canonical YYYY-MM-DD")
    return value


def canonical_timestamp(value: str, field: str = "timestamp") -> str:
    """Validate an ISO timestamp with an explicit offset and stable spelling."""
    if not isinstance(value, str) or value.endswith("Z"):
        raise ValueError(f"{field} must use an explicit timezone offset")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    normalized = parsed.isoformat(timespec="seconds")
    if normalized != value:
        raise ValueError(f"{field} must be canonical ISO seconds with offset")
    return value


def _optional_date(value: str | None, field: str) -> str | None:
    return None if value is None else _date(value, field)


def _source_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("source_url must be direct HTTPS without credentials, query, or fragment")
    if parts.hostname not in OFFICIAL_HOSTS:
        raise ValueError(f"source host is not official: {parts.hostname}")
    return value


@dataclass(frozen=True)
class SourceEvidence:
    source_url: str
    source_bytes: bytes
    retrieved_at: str
    publication_date: str | None = None
    effective_date: str | None = None
    available_at: str | None = None
    content_type: str | None = None
    status: int = 200
    final_url: str | None = None
    last_modified: str | None = None
    source_kind: str = "official"
    source_path: str | None = None

    def __post_init__(self) -> None:
        _source_url(self.source_url)
        if self.final_url is not None and _source_url(self.final_url) != self.source_url:
            raise ValueError("redirected source final_url is not the exact requested URL")
        canonical_timestamp(self.retrieved_at, "retrieved_at")
        _optional_date(self.publication_date, "publication_date")
        _optional_date(self.effective_date, "effective_date")
        if self.available_at is not None:
            canonical_timestamp(self.available_at, "available_at")
        if self.status < 200 or self.status >= 300:
            raise ValueError("source evidence must be a successful response")
        if not self.source_bytes:
            raise ValueError("source bytes are required")

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.source_bytes).hexdigest()

    def metadata(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_path": self.source_path,
            "source_path_basis": "retained_source_path" if self.source_path is not None else "source_path_absent",
            "final_url": self.final_url or self.source_url,
            "source_sha256": self.source_sha256,
            "source_byte_count": len(self.source_bytes),
            "retrieved_at": self.retrieved_at,
            "available_at": self.available_at,
            "available_at_basis": AVAILABLE_AT_ABSENT if self.available_at is None else "authoritative_source_fact",
            "publication_date": self.publication_date,
            "publication_date_basis": SOURCE_DATE_ABSENT if self.publication_date is None else "source_fact",
            "effective_date": self.effective_date,
            "content_type": self.content_type,
            "status": self.status,
            "last_modified": self.last_modified,
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True)
class CalendarSession:
    session_date: str
    session_type: SessionType
    label: str
    open_at: str | None
    close_at: str | None
    source_url: str
    source_sha256: str
    retrieved_at: str
    publication_date: str | None
    effective_date: str | None
    available_at: str | None
    source_kind: str = "official"
    source_path: str | None = None

    def __post_init__(self) -> None:
        _date(self.session_date, "session_date")
        if (self.open_at is None) != (self.close_at is None):
            raise ValueError("open_at and close_at must both be present or both absent")
        if self.open_at is not None:
            canonical_timestamp(self.open_at, "open_at")
            canonical_timestamp(self.close_at or "", "close_at")
            if datetime.fromisoformat(self.open_at) >= datetime.fromisoformat(self.close_at or ""):
                raise ValueError("session interval must be half-open and positive")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256")
        _source_url(self.source_url)
        canonical_timestamp(self.retrieved_at, "retrieved_at")
        _optional_date(self.publication_date, "publication_date")
        _optional_date(self.effective_date, "effective_date")
        if self.available_at is not None:
            canonical_timestamp(self.available_at, "available_at")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["session_type"] = self.session_type.value
        return result


def ist_interval(session_date: date, start: time, end: time) -> tuple[str, str]:
    """Return canonical half-open IST timestamps for one session date."""
    if start >= end:
        raise ValueError("session interval must be positive")
    return (
        datetime.combine(session_date, start, tzinfo=IST).isoformat(timespec="seconds"),
        datetime.combine(session_date, end, tzinfo=IST).isoformat(timespec="seconds"),
    )


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
