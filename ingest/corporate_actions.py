"""Normalize point-in-time corporate actions against the security master."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Iterable, Mapping

from contracts.corporate_actions import CorporateActionRecord, normalize_action_type
from contracts.security_master import SecurityMasterRecord
from ingest.security_master import resolve_security_id


@dataclass(frozen=True)
class CorporateActionSourceRecord:
    """A source observation before it is joined to canonical security identity."""

    security_id: str | None
    source_security_id: str | None
    identity_key: str | None
    symbol: str | None
    series: str | None
    exchange: str | None
    action_type: str
    announcement_date: str | None
    ex_date: str | None
    record_date: str | None
    payable_date: str | None
    adjustment_factor: float
    factor_provenance: str
    available_at: str
    source: str
    source_hash: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "CorporateActionSourceRecord":
        security_id = _optional_text(row.get("security_id"))
        source_security_id = _optional_text(row.get("source_security_id", row.get("source_id", row.get("instrument_id"))))
        identity_key = _optional_text(row.get("identity_key"))
        if security_id is None and source_security_id is None and identity_key is None:
            raise ValueError("corporate action row requires canonical or explicit source identity")
        return cls(
            security_id=security_id,
            source_security_id=source_security_id,
            identity_key=identity_key,
            symbol=_optional_text(row.get("symbol")),
            series=_optional_text(row.get("series")),
            exchange=_optional_text(row.get("exchange")),
            action_type=_required_text(row.get("action_type"), "action_type"),
            announcement_date=_optional_date(row.get("announcement_date")),
            ex_date=_optional_date(row.get("ex_date")),
            record_date=_optional_date(row.get("record_date")),
            payable_date=_optional_date(row.get("payable_date")),
            adjustment_factor=float(row.get("adjustment_factor", row.get("factor"))),
            factor_provenance=_required_text(
                row.get("factor_provenance", row.get("provenance")), "factor_provenance"
            ),
            available_at=_timestamp(row.get("available_at"), "available_at"),
            source=_required_text(row.get("source"), "source"),
            source_hash=_optional_text(row.get("source_hash")) or _hash_mapping(row),
        )


@dataclass(frozen=True)
class CorporateActionBundle:
    corporate_action: tuple[CorporateActionRecord, ...]

    @property
    def actions(self) -> tuple[CorporateActionRecord, ...]:
        return self.corporate_action


def ingest_corporate_actions(
    source_rows: Iterable[CorporateActionSourceRecord | Mapping[str, object]],
    security_master: Iterable[SecurityMasterRecord],
    *,
    available_cutoff: str | datetime | None = None,
) -> CorporateActionBundle:
    """Build canonical actions from rows whose identity joins explicitly.

    ``symbol`` is accepted for source provenance and consistency checking only.
    It is never used to choose a ``security_id``.
    """
    master = tuple(security_master)
    master_security_ids = [record.security_id for record in master]
    by_security_id = set(master_security_ids)
    if len(by_security_id) != len(master_security_ids):
        raise ValueError("security master contains duplicate security IDs")
    cutoff = _cutoff(available_cutoff)
    actions: dict[str, CorporateActionRecord] = {}
    for raw in source_rows:
        source = raw if isinstance(raw, CorporateActionSourceRecord) else CorporateActionSourceRecord.from_mapping(raw)
        if cutoff is not None and _instant(source.available_at) > cutoff:
            continue
        security_id = _resolve_identity(source, master, by_security_id)
        action = CorporateActionRecord(
            security_id=security_id,
            action_type=normalize_action_type(source.action_type),
            announcement_date=source.announcement_date,
            ex_date=source.ex_date,
            record_date=source.record_date,
            payable_date=source.payable_date,
            adjustment_factor=source.adjustment_factor,
            factor_provenance=source.factor_provenance,
            available_at=source.available_at,
            source=source.source,
            source_hash=source.source_hash,
            row_identity=_row_identity(
                security_id,
                source.action_type,
                source.announcement_date,
                source.ex_date,
                source.record_date,
                source.payable_date,
            ),
            row_hash="pending",
        )
        action = _with_row_hash(action)
        previous = actions.get(action.row_identity)
        if previous is not None:
            if previous.as_dict() != action.as_dict():
                raise ValueError("conflicting corporate action observations share a canonical row identity")
            continue
        actions[action.row_identity] = action
    ordered = sorted(actions.values(), key=lambda row: (row.security_id, _effective_sort_key(row), row.row_identity))
    return CorporateActionBundle(tuple(ordered))


normalize_corporate_actions = ingest_corporate_actions


def _resolve_identity(
    source: CorporateActionSourceRecord,
    master: tuple[SecurityMasterRecord, ...],
    security_ids: set[str],
) -> str:
    candidates: set[str] = set()
    if source.security_id is not None:
        if source.security_id not in security_ids:
            raise ValueError(f"unknown canonical security_id: {source.security_id}")
        candidates.add(source.security_id)
    for kind, value in (("source_security_id", source.source_security_id), ("identity_key", source.identity_key)):
        if value is None:
            continue
        candidates.update(
            record.security_id
            for record in master
            if _master_identity_matches(record, kind, value, source.exchange)
            and _instant(record.available_at) <= _instant(source.available_at)
        )
    if len(candidates) != 1:
        if not candidates:
            raise ValueError("corporate action identity is not present in the visible security master")
        raise ValueError("corporate action source identity maps to multiple security IDs")
    security_id = next(iter(candidates))
    _verify_symbol(source, security_id, master)
    return security_id


def _master_identity_matches(
    record: SecurityMasterRecord,
    kind: str,
    value: str,
    exchange: str | None,
) -> bool:
    if exchange is not None and record.exchange != exchange.upper():
        return False
    accepted_names = {
        "source_security_id": {"source_security_id", "source_id", "instrument_id"},
        "identity_key": {"identity_key"},
    }[kind]
    return any(name.lower() in accepted_names and identifier == value for name, identifier in record.external_identifiers)


def _verify_symbol(source: CorporateActionSourceRecord, security_id: str, master: tuple[SecurityMasterRecord, ...]) -> None:
    if source.symbol is None:
        return
    symbol = source.symbol.upper()
    date_key = source.ex_date or source.record_date or source.announcement_date or source.payable_date
    records = [record for record in master if record.security_id == security_id]
    if date_key is not None:
        resolved = resolve_security_id(records, symbol, as_of=date_key, available_cutoff=source.available_at)
        if resolved != security_id:
            raise ValueError("corporate action symbol does not match its canonical security_id")
        return
    if not any(record.symbol == symbol for record in records):
        raise ValueError("corporate action symbol does not match its canonical security_id")


def _with_row_hash(action: CorporateActionRecord) -> CorporateActionRecord:
    payload = action.as_dict()
    payload["row_hash"] = None
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return CorporateActionRecord(**{**action.as_dict(), "row_hash": digest})


def _row_identity(
    security_id: str,
    action_type: str,
    announcement_date: str | None,
    ex_date: str | None,
    record_date: str | None,
    payable_date: str | None,
) -> str:
    payload = {
        "security_id": security_id,
        "action_type": action_type.strip().lower(),
        "announcement_date": announcement_date,
        "ex_date": ex_date,
        "record_date": record_date,
        "payable_date": payable_date,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return "ca_" + digest[:32]


def _effective_sort_key(action: CorporateActionRecord) -> str:
    return action.ex_date or action.record_date or action.payable_date or action.announcement_date or "9999-12-31"


def _hash_mapping(row: Mapping[str, object]) -> str:
    stable = {str(key): str(value) for key, value in sorted(row.items(), key=lambda item: str(item[0]))}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


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


def _optional_date(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    result = str(value).strip()[:10]
    date.fromisoformat(result)
    return result


def _timestamp(value: object, field_name: str) -> str:
    result = _required_text(value, field_name)
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return result


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _cutoff(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    return _instant(value.isoformat() if isinstance(value, datetime) else value)
