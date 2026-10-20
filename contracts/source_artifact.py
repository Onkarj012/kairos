"""Contract for retained official source bytes and their transformations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import re
from typing import Any, Mapping

from .revision import RevisionRelationship


SOURCE_ARTIFACT_FORMAT = "meridian.source-artifact.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, field_name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field_name} is required")
    return result


def _timestamp(value: object, field_name: str) -> str:
    result = _text(value, field_name).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(result)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class SourceArtifactRecord:
    """One retained response body or normalized derivative."""

    logical_path: str
    route_family: str
    requested_url: str
    final_url: str
    status: int
    redirects: int
    response_headers: Mapping[str, str]
    content_type: str | None
    byte_count: int
    sha256: str | None
    retrieved_at: str
    source_date: str | None
    parser_result: str
    schema_result: str
    normalized_hash: str | None
    revision: RevisionRelationship
    quarantine_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_path", _text(self.logical_path, "logical_path"))
        object.__setattr__(self, "route_family", _text(self.route_family, "route_family"))
        object.__setattr__(self, "requested_url", _text(self.requested_url, "requested_url"))
        object.__setattr__(self, "final_url", _text(self.final_url, "final_url"))
        if self.status < 0:
            raise ValueError("status cannot be negative")
        if self.redirects < 0:
            raise ValueError("redirects cannot be negative")
        if self.byte_count < 0:
            raise ValueError("byte_count cannot be negative")
        headers = {str(key): str(value) for key, value in self.response_headers.items()}
        object.__setattr__(self, "response_headers", headers)
        if self.sha256 is not None and _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be lowercase SHA-256")
        if self.normalized_hash is not None and _SHA256.fullmatch(self.normalized_hash) is None:
            raise ValueError("normalized_hash must be lowercase SHA-256")
        object.__setattr__(self, "retrieved_at", _timestamp(self.retrieved_at, "retrieved_at"))
        if self.source_date is not None:
            try:
                date.fromisoformat(self.source_date)
            except ValueError as exc:
                raise ValueError("source_date must be an ISO date") from exc
        if self.redirects or self.final_url != self.requested_url:
            raise ValueError("redirected source artifacts cannot be retained")
        object.__setattr__(self, "parser_result", _text(self.parser_result, "parser_result"))
        object.__setattr__(self, "schema_result", _text(self.schema_result, "schema_result"))
        if not isinstance(self.revision, RevisionRelationship):
            raise TypeError("revision must be a RevisionRelationship")
        if self.quarantine_reason is not None:
            object.__setattr__(self, "quarantine_reason", _text(self.quarantine_reason, "quarantine_reason"))

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["format"] = SOURCE_ARTIFACT_FORMAT
        return value
