"""Build and query point-in-time sector membership."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import hashlib
import json
from typing import Iterable, Mapping

from contracts.constituents import IntervalAudit, audit_intervals
from contracts.security_master import SecurityMasterRecord
from contracts.sectors import SectorMembershipRecord
from ingest.security_master import resolve_security_id


@dataclass(frozen=True)
class SectorMembershipSourceRecord:
    """A source classification before canonical security identity resolution."""

    taxonomy: str
    sector: str | None
    industry: str | None
    effective_from: str
    effective_to: str | None
    available_at: str
    source_confidence: float
    source: str
    source_hash: str
    security_id: str | None = None
    symbol: str | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "SectorMembershipSourceRecord":
        source_hash = _optional_text(row.get("source_hash", row.get("provenance_hash")))
        return cls(
            taxonomy=_required_text(row.get("taxonomy"), "taxonomy"),
            sector=_optional_text(row.get("sector")),
            industry=_optional_text(row.get("industry")),
            effective_from=_required_text(row.get("effective_from"), "effective_from"),
            effective_to=_optional_text(row.get("effective_to")),
            available_at=_required_text(row.get("available_at"), "available_at"),
            source_confidence=float(row.get("source_confidence", row.get("confidence", -1))),
            source=_required_text(row.get("source", row.get("source_provenance")), "source"),
            source_hash=source_hash or _hash_mapping(row),
            security_id=_optional_text(row.get("security_id")),
            symbol=_optional_text(row.get("symbol", row.get("symbol_at_observation"))),
        )


def build_sector_membership(
    source_rows: Iterable[SectorMembershipSourceRecord | Mapping[str, object]],
    security_master: Iterable[SecurityMasterRecord],
    *,
    available_cutoff: str | datetime | None = None,
) -> tuple[SectorMembershipRecord, ...]:
    """Resolve source classifications to security-master IDs without lookahead."""
    master = tuple(security_master)
    cutoff = _cutoff(available_cutoff)
    rows: list[SectorMembershipRecord] = []
    for raw in source_rows:
        source = raw if isinstance(raw, SectorMembershipSourceRecord) else SectorMembershipSourceRecord.from_mapping(raw)
        if cutoff is not None and _instant(source.available_at) > cutoff:
            continue
        security_id = _resolve_identity(source, master)
        rows.append(
            SectorMembershipRecord(
                security_id=security_id,
                taxonomy=source.taxonomy,
                sector=source.sector,
                industry=source.industry,
                effective_from=source.effective_from,
                effective_to=source.effective_to,
                available_at=source.available_at,
                source_confidence=source.source_confidence,
                source=source.source,
                source_hash=source.source_hash,
            )
        )
    result = tuple(sorted(rows, key=lambda row: (row.security_id, row.taxonomy, row.effective_from)))
    audit = audit_sector_membership(result)
    _raise_if_invalid(audit)
    return result


ingest_sector_membership = build_sector_membership


def query_sector_membership(
    rows: Iterable[SectorMembershipRecord | Mapping[str, object]],
    as_of: str,
    *,
    taxonomy: str | None = None,
    security_id: str | None = None,
    available_cutoff: str | datetime | None = None,
) -> tuple[SectorMembershipRecord, ...]:
    """Return sector rows effective on ``as_of`` and known by the cutoff."""
    records = tuple(_coerce_record(row) for row in rows)
    audit = audit_sector_membership(records)
    _raise_if_invalid(audit)
    date_key = _date(as_of, "as_of")
    cutoff = _cutoff(available_cutoff) or datetime.combine(
        datetime.strptime(date_key, "%Y-%m-%d").date(), time.max, tzinfo=timezone.utc
    )
    result = [
        row
        for row in records
        if (taxonomy is None or row.taxonomy == taxonomy)
        and (security_id is None or row.security_id == security_id)
        and row.effective_from <= date_key
        and (row.effective_to is None or date_key < row.effective_to)
        and _instant(row.available_at) <= cutoff
    ]
    return tuple(sorted(result, key=lambda row: (row.security_id, row.taxonomy)))


sector_on_date = query_sector_membership


def audit_sector_membership(rows: Iterable[SectorMembershipRecord | Mapping[str, object]]) -> IntervalAudit:
    """Return the interval audit, including gaps that callers must address."""
    records = tuple(_coerce_record(row) for row in rows)
    return audit_intervals(records, key_fields=("security_id", "taxonomy"))


def _resolve_identity(source: SectorMembershipSourceRecord, master: tuple[SecurityMasterRecord, ...]) -> str:
    if source.security_id is not None:
        matches = [
            row
            for row in master
            if row.security_id == source.security_id
            and row.effective_from <= source.effective_from
            and (row.effective_to is None or source.effective_from < row.effective_to)
            and _instant(row.available_at) <= _instant(source.available_at)
        ]
        if not matches:
            raise ValueError(f"security_id is not available for sector observation: {source.security_id}")
        if source.symbol is not None:
            resolved = resolve_security_id(
                master,
                source.symbol,
                as_of=source.effective_from,
                available_cutoff=source.available_at,
            )
            if resolved != source.security_id:
                raise ValueError(f"sector symbol resolves to a different security_id: {source.symbol}")
        return source.security_id
    if source.symbol is None:
        raise ValueError("sector observation requires security_id or symbol")
    resolved = resolve_security_id(
        master,
        source.symbol,
        as_of=source.effective_from,
        available_cutoff=source.available_at,
    )
    if resolved is None:
        raise ValueError(f"sector symbol cannot be resolved at observation: {source.symbol}")
    return resolved


def _coerce_record(row: SectorMembershipRecord | Mapping[str, object]) -> SectorMembershipRecord:
    if isinstance(row, SectorMembershipRecord):
        return row
    return SectorMembershipRecord(**{str(key): value for key, value in row.items()})  # type: ignore[arg-type]


def _raise_if_invalid(audit: object) -> None:
    if getattr(audit, "duplicate_keys"):
        raise ValueError(f"duplicate canonical sector keys: {getattr(audit, 'duplicate_keys')}")
    if getattr(audit, "overlaps"):
        raise ValueError("overlapping sector membership intervals")


def _required_text(value: object, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    return str(value).strip()


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


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _cutoff(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("available_cutoff must include a timezone")
    return parsed.astimezone(timezone.utc)


def _hash_mapping(row: Mapping[str, object]) -> str:
    stable = {str(key): str(value) for key, value in sorted(row.items(), key=lambda item: str(item[0]))}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
