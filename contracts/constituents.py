"""Point-in-time index constituent contracts.

Constituent intervals use inclusive ``effective_from`` and exclusive
``effective_to`` boundaries. A missing ``effective_to`` is open-ended.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


CONSTITUENT_HISTORY_FORMAT = "meridian.pit-constituents.v1"
INDEX_MEMBERSHIP_SCHEMA: tuple[tuple[str, str], ...] = (
    ("security_id", "str"),
    ("index_name", "str"),
    ("symbol", "str"),
    ("effective_from", "str"),
    ("effective_to", "str|None"),
    ("source_publication_date", "str|None"),
    ("source_effective_date", "str|None"),
    ("available_at", "str"),
    ("source", "str"),
    ("source_hash", "str"),
    ("source_publication_date_basis", "str"),
    ("source_effective_date_basis", "str"),
)

SOURCE_DATE_FACT = "explicit_source_field"
SOURCE_DATE_ABSENT = "source_field_absent"


@dataclass(frozen=True)
class IndexMembershipRecord:
    """One point-in-time index membership observation.

    Effective intervals are inclusive at ``effective_from`` and exclusive at
    ``effective_to``.  ``symbol`` is the spelling observed in the source;
    ``security_id`` is the canonical security-master identity.
    """

    security_id: str
    index_name: str
    symbol: str
    effective_from: str
    effective_to: str | None
    source_publication_date: str | None
    source_effective_date: str | None
    available_at: str
    source: str
    source_hash: str
    source_publication_date_basis: str = SOURCE_DATE_FACT
    source_effective_date_basis: str = SOURCE_DATE_FACT

    def __post_init__(self) -> None:
        object.__setattr__(self, "security_id", _required_text(self.security_id, "security_id"))
        object.__setattr__(self, "index_name", _required_text(self.index_name, "index_name"))
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol").upper())
        effective_from = _date_key(self.effective_from)
        effective_to = _optional_date_key(self.effective_to)
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be after effective_from")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        publication = _optional_date_key(self.source_publication_date)
        effective = _optional_date_key(self.source_effective_date)
        _validate_source_date_basis(publication, self.source_publication_date_basis, "source_publication_date")
        _validate_source_date_basis(effective, self.source_effective_date_basis, "source_effective_date")
        object.__setattr__(self, "source_publication_date", publication)
        object.__setattr__(self, "source_effective_date", effective)
        object.__setattr__(self, "available_at", _timestamp(self.available_at, "available_at"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))

    @property
    def symbol_at_observation(self) -> str:
        return self.symbol

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntervalIssue:
    """A reported overlap or internal gap for one canonical key."""

    key: tuple[str, ...]
    previous_from: str
    previous_to: str | None
    current_from: str
    current_to: str | None


@dataclass(frozen=True)
class IntervalAudit:
    """Integrity report; gaps are diagnostic and are never auto-filled."""

    duplicate_keys: tuple[tuple[str, ...], ...]
    overlaps: tuple[IntervalIssue, ...]
    gaps: tuple[IntervalIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.duplicate_keys and not self.overlaps

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_constituent_intervals(rows: Iterable[IndexMembershipRecord | Mapping[str, object]]) -> IntervalAudit:
    """Audit canonical index intervals for duplicates, overlaps, and gaps."""
    records = tuple(_coerce_index_record(row) for row in rows)
    return audit_intervals(records, key_fields=("index_name", "security_id"))


def audit_intervals(rows: Iterable[object], *, key_fields: tuple[str, ...]) -> IntervalAudit:
    """Audit any interval records exposing the canonical key and boundaries."""
    grouped: dict[tuple[str, ...], list[object]] = {}
    for row in rows:
        key = tuple(str(getattr(row, field)) for field in key_fields)
        grouped.setdefault(key, []).append(row)

    duplicate_keys: list[tuple[str, ...]] = []
    overlaps: list[IntervalIssue] = []
    gaps: list[IntervalIssue] = []
    for key, values in sorted(grouped.items()):
        starts = sorted(values, key=lambda row: str(getattr(row, "effective_from")))
        seen_starts: set[str] = set()
        previous: object | None = None
        for current in starts:
            current_from = str(getattr(current, "effective_from"))
            if current_from in seen_starts:
                duplicate_keys.append(key + (current_from,))
            seen_starts.add(current_from)
            if previous is not None:
                previous_from = str(getattr(previous, "effective_from"))
                previous_to = getattr(previous, "effective_to")
                current_to = getattr(current, "effective_to")
                issue = IntervalIssue(key, previous_from, previous_to, current_from, current_to)
                if previous_to is None or current_from < str(previous_to):
                    overlaps.append(issue)
                elif current_from > str(previous_to):
                    gaps.append(issue)
            previous = current
    return IntervalAudit(tuple(sorted(set(duplicate_keys))), tuple(overlaps), tuple(gaps))


def load_constituent_history(source: Path | str | Iterable[dict[str, Any]], *, index_name: str | None = None) -> list[dict[str, Any]]:
    """Load normalized PIT constituent rows from CSV, JSON, JSONL, or mappings."""
    source_path: Path | None = None
    source_sha256: str | None = None
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        source_sha256 = _sha256_file(source_path)
        rows = _read_rows(source_path)
    else:
        rows = list(source)

    normalized = [_normalize_row(row, source_path, source_sha256, index_name) for row in rows]
    normalized.sort(key=lambda row: (row["index_name"], row["symbol"], row["effective_from"], row.get("effective_to") or "9999-12-31"))
    return normalized


def tradable_on(history: Iterable[dict[str, Any]], symbol: str, as_of: str, *, index_name: str | None = None) -> bool:
    """Return whether ``symbol`` is a member on ``as_of``."""
    symbol_key = str(symbol).upper()
    date_key = _date_key(as_of)
    for row in history:
        if str(row.get("symbol", "")).upper() != symbol_key:
            continue
        if index_name is not None and str(row.get("index_name")) != str(index_name):
            continue
        if _active_on(row, date_key):
            return True
    return False


def filter_tradable_rows(
    rows: Iterable[dict[str, Any]],
    history: Iterable[dict[str, Any]],
    *,
    date_column: str = "timestamp",
    symbol_column: str = "symbol",
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Keep trade/feature rows whose symbols are PIT index members on row date."""
    constituent_rows = list(history)
    kept: list[dict[str, Any]] = []
    for row in rows:
        symbol = row.get(symbol_column)
        as_of = row.get(date_column)
        if symbol is None or as_of is None:
            continue
        if tradable_on(constituent_rows, str(symbol), str(as_of), index_name=index_name):
            kept.append(dict(row))
    return kept


def constituent_manifest(history: Iterable[dict[str, Any]], *, source: Path | str | None = None) -> dict[str, Any]:
    """Build a JSON-serializable provenance manifest for constituent history."""
    rows = list(history)
    identity = {
        "format": CONSTITUENT_HISTORY_FORMAT,
        "source_path": str(source) if source is not None else _common_value(rows, "source_path"),
        "source_sha256": _sha256_file(Path(source)) if source is not None and Path(source).is_file() else _common_value(rows, "source_sha256"),
        "row_count": len(rows),
        "indices": sorted({str(row["index_name"]) for row in rows}),
        "symbols": sorted({str(row["symbol"]) for row in rows}),
        "interval_boundary": {"effective_from": "inclusive", "effective_to": "exclusive", "null_effective_to": "open_ended"},
        "rows_sha256": _rows_sha256(rows),
    }
    return {**identity, "manifest_id": _sha256_json(identity)}


def write_constituent_manifest(path: Path | str, history: Iterable[dict[str, Any]], *, source: Path | str | None = None) -> dict[str, Any]:
    manifest = constituent_manifest(history, source=source)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return list(payload["rows"])
        if isinstance(payload, list):
            return payload
    raise ValueError(f"unsupported constituent history format: {path}")


def _normalize_row(row: dict[str, Any], source_path: Path | None, source_sha256: str | None, index_name: str | None) -> dict[str, Any]:
    idx = row.get("index_name", index_name)
    symbol = row.get("symbol")
    effective_from = row.get("effective_from")
    if not idx:
        raise ValueError(f"constituent row missing index_name: {row}")
    if not symbol:
        raise ValueError(f"constituent row missing symbol: {row}")
    if not effective_from:
        raise ValueError(f"constituent row missing effective_from: {row}")

    effective_to = _optional_date_key(row.get("effective_to"))
    effective_from_key = _date_key(str(effective_from))
    if effective_to is not None and effective_to <= effective_from_key:
        raise ValueError("effective_to must be after effective_from")
    normalized = {
        "index_name": str(idx),
        "symbol": str(symbol).upper(),
        "effective_from": effective_from_key,
        "effective_to": effective_to,
        "source_effective_date": _optional_date_key(row.get("source_effective_date")),
        "source_path": str(row.get("source_path") or source_path or ""),
        "source_sha256": str(row.get("source_sha256") or row.get("hash") or source_sha256 or ""),
    }
    normalized["row_hash"] = _sha256_json(normalized)
    return normalized


def _active_on(row: dict[str, Any], date_key: str) -> bool:
    if date_key < str(row["effective_from"]):
        return False
    effective_to = row.get("effective_to")
    return not effective_to or date_key < str(effective_to)


def _date_key(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("date value cannot be empty")
    return text[:10]


def _optional_date_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return _date_key(text)


def _validate_source_date_basis(value: str | None, basis: str, field_name: str) -> None:
    if basis not in {SOURCE_DATE_FACT, SOURCE_DATE_ABSENT}:
        raise ValueError(f"{field_name} basis is unsupported")
    if value is None and basis != SOURCE_DATE_ABSENT:
        raise ValueError(f"{field_name} is absent but its basis is not source_field_absent")
    if value is not None and basis != SOURCE_DATE_FACT:
        raise ValueError(f"{field_name} has a source date but its basis is not explicit_source_field")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _rows_sha256(rows: list[dict[str, Any]]) -> str:
    stable = [{key: row.get(key) for key in sorted(row) if key != "row_hash"} for row in rows]
    return _sha256_json(stable)


def _common_value(rows: list[dict[str, Any]], key: str) -> str | None:
    values = {str(row.get(key, "")) for row in rows if row.get(key)}
    return next(iter(values)) if len(values) == 1 else None


def _coerce_index_record(row: IndexMembershipRecord | Mapping[str, object]) -> IndexMembershipRecord:
    if isinstance(row, IndexMembershipRecord):
        return row
    return IndexMembershipRecord(**{str(key): value for key, value in row.items()})  # type: ignore[arg-type]


def _required_text(value: object, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field_name} cannot be empty")
    return result


def _date_key(value: object) -> str:
    result = _required_text(value, "date")[:10]
    try:
        datetime.strptime(result, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"date must start with an ISO date: {value!r}") from exc
    return result


def canonical_timestamp(value: object, field_name: str) -> tuple[str, datetime]:
    """Validate and preserve the canonical ASCII timestamp spelling."""
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        value,
    ) is None:
        raise ValueError(f"{field_name} must be a canonical timezone-aware timestamp")
    iso_value = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a canonical timezone-aware timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value, parsed


def _timestamp(value: object, field_name: str) -> str:
    result, _ = canonical_timestamp(value, field_name)
    return result


IndexMembership = IndexMembershipRecord
