"""Point-in-time sector and industry membership contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


SECTOR_MEMBERSHIP_FORMAT = "meridian.pit-sector-membership.v1"
SECTOR_MEMBERSHIP_SCHEMA: tuple[tuple[str, str], ...] = (
    ("security_id", "str"),
    ("taxonomy", "str"),
    ("sector", "str|None"),
    ("industry", "str|None"),
    ("effective_from", "str"),
    ("effective_to", "str|None"),
    ("available_at", "str"),
    ("source_confidence", "float"),
    ("source", "str"),
    ("source_hash", "str"),
)


@dataclass(frozen=True)
class SectorMembershipRecord:
    """One effective-dated, source-backed sector classification."""

    security_id: str
    taxonomy: str
    sector: str | None
    industry: str | None
    effective_from: str
    effective_to: str | None
    available_at: str
    source_confidence: float
    source: str
    source_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "security_id", _required_text(self.security_id, "security_id"))
        object.__setattr__(self, "taxonomy", _required_text(self.taxonomy, "taxonomy"))
        sector = _optional_text(self.sector)
        industry = _optional_text(self.industry)
        if sector is None and industry is None:
            raise ValueError("sector or industry is required")
        object.__setattr__(self, "sector", sector)
        object.__setattr__(self, "industry", industry)
        effective_from = _date(self.effective_from, "effective_from")
        effective_to = _optional_date(self.effective_to)
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be after effective_from")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        object.__setattr__(self, "available_at", _timestamp(self.available_at, "available_at"))
        confidence = float(self.source_confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("source_confidence must be between 0 and 1")
        object.__setattr__(self, "source_confidence", confidence)
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def source_provenance(self) -> str:
        return self.source

    @property
    def provenance_hash(self) -> str:
        return self.source_hash


def _required_text(value: object, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field_name} cannot be empty")
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _date(value: object, field_name: str) -> str:
    result = _required_text(value, field_name)[:10]
    try:
        datetime.strptime(result, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must start with an ISO date: {value!r}") from exc
    return result


def _optional_date(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _date(value, "effective_to")


def _timestamp(value: object, field_name: str) -> str:
    result = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return result


SectorMembership = SectorMembershipRecord
