"""Point-in-time contracts for instrument identity and security lineage.

Effective intervals are inclusive at ``effective_from`` and exclusive at
``effective_to``.  ``available_at`` is the information-set timestamp: a row
must not be used by a historical build whose cutoff precedes that timestamp.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any


SECURITY_MASTER_FORMAT = "meridian.security-master.v1"
SECURITY_ALIAS_FORMAT = "meridian.security-alias.v1"
SECURITY_LINEAGE_EDGE_FORMAT = "meridian.security-lineage-edge.v1"

SECURITY_MASTER_SCHEMA: tuple[tuple[str, str], ...] = (
    ("security_id", "str"),
    ("isin", "str|None"),
    ("external_identifiers", "tuple[tuple[str,str],...]"),
    ("symbol", "str"),
    ("series", "str"),
    ("exchange", "str"),
    ("effective_from", "str"),
    ("effective_to", "str|None"),
    ("listed_at", "str|None"),
    ("delisted_at", "str|None"),
    ("tick_size", "float|None"),
    ("lot_size", "float|None"),
    ("circuit_band", "str|None"),
    ("available_at", "str"),
    ("source", "str"),
    ("source_hash", "str"),
    ("row_hash", "str"),
)

SECURITY_ALIAS_SCHEMA: tuple[tuple[str, str], ...] = (
    ("security_id", "str"),
    ("old_symbol", "str|None"),
    ("new_symbol", "str|None"),
    ("old_series", "str|None"),
    ("new_series", "str|None"),
    ("effective_from", "str"),
    ("effective_to", "str|None"),
    ("reason", "str"),
    ("source", "str"),
    ("available_at", "str"),
    ("row_hash", "str"),
)

SECURITY_LINEAGE_EDGE_SCHEMA: tuple[tuple[str, str], ...] = (
    ("predecessor_security_id", "str"),
    ("successor_security_id", "str"),
    ("relation", "str"),
    ("effective_date", "str"),
    ("source_publication_date", "str"),
    ("available_at", "str"),
    ("allocation_ratio", "float|None"),
    ("source", "str"),
    ("provenance_hash", "str"),
    ("row_hash", "str"),
)

_LINEAGE_RELATIONS = {"merger", "demerger", "relisting", "acquisition", "spin-off", "other"}
_ALIAS_REASONS = {"rename", "series_change"}
_ALLOCATION_RATIO_MIN = 0.0
_ALLOCATION_RATIO_MAX = 1.0
_CANONICAL_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def _require_text(value: str, field_name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field_name} cannot be empty")
    return result


def canonical_date(value: object, field_name: str) -> str:
    """Validate the one canonical ASCII date representation used at both boundaries."""
    if (
        not isinstance(value, str)
        or len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
        or any(character not in "0123456789" for index, character in enumerate(value) if index not in {4, 7})
    ):
        raise ValueError(f"{field_name} must be an ISO date string")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date string: {value!r}") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError(f"{field_name} must be an ISO date string: {value!r}")
    return value


def _validate_publication_date(value: str, field_name: str) -> str:
    return canonical_date(value, field_name)


def canonical_timestamp(value: object, field_name: str) -> tuple[str, datetime]:
    """Validate the one exact ASCII timestamp grammar used at identity boundaries.

    Accepted spellings are ``YYYY-MM-DDTHH:MM:SS`` with an optional six-digit
    fractional second, followed by ``Z``, ``+HH:MM``, or ``-HH:MM``.  The
    original spelling is returned so offsets are never normalized implicitly.
    """
    if isinstance(value, str) and "T" in value and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?",
        value,
    ):
        raise ValueError(f"{field_name} must include a timezone")
    if not isinstance(value, str) or _CANONICAL_TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical timezone-aware timestamp")
    iso_value = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a canonical timezone-aware timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value, parsed


def _validate_lineage_timestamp(value: object, field_name: str) -> tuple[str, datetime]:
    return canonical_timestamp(value, field_name)


def _validate_timestamp(value: str, field_name: str) -> str:
    result, _ = canonical_timestamp(value, field_name)
    return result


def _validate_interval(effective_from: str, effective_to: str | None) -> None:
    if effective_to is not None and effective_to <= effective_from:
        raise ValueError("effective_to must be after effective_from")


def canonical_row_hash(payload: dict[str, Any]) -> str:
    """Hash a canonical record payload using the frozen ingest convention."""
    stable_payload = {**payload, "row_hash": None}
    return hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _set_or_validate_row_hash(payload: dict[str, Any], supplied: object, field_name: str) -> str:
    expected = canonical_row_hash(payload)
    if supplied is None or supplied == "":
        return expected
    if not isinstance(supplied, str) or supplied != expected:
        raise ValueError(f"{field_name} does not match canonical semantic fields")
    return supplied


@dataclass(frozen=True)
class SecurityMasterRecord:
    """One effective-dated representation of a continuous instrument."""

    security_id: str
    isin: str | None
    external_identifiers: tuple[tuple[str, str], ...]
    symbol: str
    series: str
    exchange: str
    effective_from: str
    effective_to: str | None
    listed_at: str | None
    delisted_at: str | None
    tick_size: float | None
    lot_size: float | None
    circuit_band: str | None
    available_at: str
    source: str
    source_hash: str
    row_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "security_id", _require_text(self.security_id, "security_id"))
        object.__setattr__(self, "symbol", _require_text(self.symbol, "symbol").upper())
        object.__setattr__(self, "series", _require_text(self.series, "series").upper())
        object.__setattr__(self, "exchange", _require_text(self.exchange, "exchange").upper())
        effective_from = canonical_date(self.effective_from, "effective_from")
        effective_to = canonical_date(self.effective_to, "effective_to") if self.effective_to is not None else None
        _validate_interval(effective_from, effective_to)
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        object.__setattr__(self, "listed_at", canonical_date(self.listed_at, "listed_at") if self.listed_at is not None else None)
        object.__setattr__(self, "delisted_at", canonical_date(self.delisted_at, "delisted_at") if self.delisted_at is not None else None)
        object.__setattr__(self, "available_at", _validate_timestamp(self.available_at, "available_at"))
        object.__setattr__(self, "source", _require_text(self.source, "source"))
        object.__setattr__(self, "source_hash", _require_text(self.source_hash, "source_hash"))
        identifiers = tuple(sorted((str(name), str(value)) for name, value in self.external_identifiers))
        if any(not name or not value for name, value in identifiers):
            raise ValueError("external_identifiers cannot contain empty names or values")
        object.__setattr__(self, "external_identifiers", identifiers)
        object.__setattr__(self, "row_hash", _set_or_validate_row_hash(self.as_dict(), self.row_hash, "row_hash"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityAliasRecord:
    """An old symbol/series spelling for the same continuous security."""

    security_id: str
    old_symbol: str | None
    new_symbol: str | None
    old_series: str | None
    new_series: str | None
    effective_from: str
    effective_to: str | None
    reason: str
    source: str
    available_at: str
    row_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "security_id", _require_text(self.security_id, "security_id"))
        effective_from = canonical_date(self.effective_from, "effective_from")
        effective_to = canonical_date(self.effective_to, "effective_to") if self.effective_to is not None else None
        _validate_interval(effective_from, effective_to)
        reason = _require_text(self.reason, "reason")
        if reason not in _ALIAS_REASONS:
            raise ValueError(f"unsupported alias reason: {reason}")
        if self.old_symbol is None and self.old_series is None:
            raise ValueError("alias must have an old symbol or series")
        if self.new_symbol is None and self.new_series is None:
            raise ValueError("alias must have a new symbol or series")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "available_at", _validate_timestamp(self.available_at, "available_at"))
        object.__setattr__(self, "source", _require_text(self.source, "source"))
        object.__setattr__(self, "row_hash", _set_or_validate_row_hash(self.as_dict(), self.row_hash, "row_hash"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityLineageEdge:
    """A succession relationship; endpoints remain separate return histories."""

    predecessor_security_id: str
    successor_security_id: str
    relation: str
    effective_date: str
    source_publication_date: str
    available_at: str
    allocation_ratio: float | None
    source: str
    provenance_hash: str
    row_hash: str = ""

    def __post_init__(self) -> None:
        predecessor = _require_text(self.predecessor_security_id, "predecessor_security_id")
        successor = _require_text(self.successor_security_id, "successor_security_id")
        if predecessor == successor:
            raise ValueError("lineage edge endpoints must differ")
        relation = _require_text(self.relation, "relation")
        if relation not in _LINEAGE_RELATIONS:
            raise ValueError(f"unsupported lineage relation: {relation}")
        object.__setattr__(self, "predecessor_security_id", predecessor)
        object.__setattr__(self, "successor_security_id", successor)
        object.__setattr__(self, "relation", relation)
        effective_date = _validate_publication_date(self.effective_date, "effective_date")
        source_publication_date = _validate_publication_date(self.source_publication_date, "source_publication_date")
        available_at, available_at_parsed = _validate_lineage_timestamp(self.available_at, "available_at")
        if source_publication_date > effective_date:
            raise ValueError("source_publication_date must not be after effective_date")
        if source_publication_date > available_at_parsed.date().isoformat():
            raise ValueError("source_publication_date must not be after available_at calendar date")
        allocation_ratio = self.allocation_ratio
        if allocation_ratio is not None:
            if isinstance(allocation_ratio, bool) or not isinstance(allocation_ratio, (int, float)):
                raise ValueError("allocation_ratio must be None or a finite number between 0 and 1")
            if not math.isfinite(allocation_ratio) or not _ALLOCATION_RATIO_MIN <= allocation_ratio <= _ALLOCATION_RATIO_MAX:
                raise ValueError("allocation_ratio must be None or a finite number between 0 and 1")
            object.__setattr__(self, "allocation_ratio", float(allocation_ratio))
        object.__setattr__(self, "effective_date", effective_date)
        object.__setattr__(self, "source_publication_date", source_publication_date)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "source", _require_text(self.source, "source"))
        object.__setattr__(self, "provenance_hash", _require_text(self.provenance_hash, "provenance_hash"))
        object.__setattr__(self, "row_hash", _set_or_validate_row_hash(self.as_dict(), self.row_hash, "row_hash"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def schema_field_names(contract: type[object]) -> tuple[str, ...]:
    """Return dataclass field order for contract/schema parity checks."""
    return tuple(field.name for field in fields(contract))


assert schema_field_names(SecurityMasterRecord) == tuple(name for name, _ in SECURITY_MASTER_SCHEMA)
assert schema_field_names(SecurityAliasRecord) == tuple(name for name, _ in SECURITY_ALIAS_SCHEMA)
assert schema_field_names(SecurityLineageEdge) == tuple(name for name, _ in SECURITY_LINEAGE_EDGE_SCHEMA)

# Short names are convenient at call sites while the explicit names document
# which table each row belongs to.
SecurityMaster = SecurityMasterRecord
SecurityAlias = SecurityAliasRecord
