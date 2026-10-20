"""Build point-in-time security identity, aliases, and lineage tables."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Real
from typing import Any, Iterable, Mapping

from contracts.security_master import (
    SecurityAliasRecord,
    SecurityLineageEdge,
    SecurityMasterRecord,
    canonical_date,
    canonical_row_hash,
    canonical_timestamp,
)


@dataclass(frozen=True)
class SecuritySourceRecord:
    """Normalized source observation used to build the three canonical tables."""

    source_security_id: str | None
    identity_key: str | None
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

    def __post_init__(self) -> None:
        source_security_id = _optional_text(self.source_security_id)
        identity_key = _optional_text(self.identity_key)
        if source_security_id is None and identity_key is None:
            raise ValueError("security source row requires source_security_id or identity_key")
        object.__setattr__(self, "source_security_id", source_security_id)
        object.__setattr__(self, "identity_key", identity_key)
        object.__setattr__(self, "isin", _optional_text(self.isin))
        object.__setattr__(self, "external_identifiers", _identifiers(self.external_identifiers))
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        object.__setattr__(self, "series", _required_text(self.series, "series"))
        object.__setattr__(self, "exchange", _required_text(self.exchange, "exchange"))
        effective_from = _date(self.effective_from, "effective_from")
        effective_to = _optional_date(self.effective_to)
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be after effective_from")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        object.__setattr__(self, "listed_at", _optional_date(self.listed_at))
        object.__setattr__(self, "delisted_at", _optional_date(self.delisted_at))
        object.__setattr__(self, "tick_size", _optional_float(self.tick_size))
        object.__setattr__(self, "lot_size", _optional_float(self.lot_size))
        object.__setattr__(self, "circuit_band", _optional_text(self.circuit_band))
        object.__setattr__(self, "available_at", _timestamp(self.available_at, "available_at"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "SecuritySourceRecord":
        _reject_unknown_mapping(
            row,
            {field.name for field in fields(cls)} | {"source_id", "instrument_id", "identifiers"},
            cls.__name__,
        )
        source_security_id = _optional_text(
            row.get("source_security_id", row.get("source_id", row.get("instrument_id")))
        )
        identity_key = _optional_text(row.get("identity_key"))
        if source_security_id is None and identity_key is None:
            raise ValueError("security source row requires source_security_id or identity_key")
        external = _identifiers(row.get("external_identifiers", row.get("identifiers", ())))
        return cls(
            source_security_id=source_security_id,
            identity_key=identity_key,
            isin=_optional_text(row.get("isin")),
            external_identifiers=external,
            symbol=_required_text(row.get("symbol"), "symbol"),
            series=_required_text(row.get("series", "EQ"), "series"),
            exchange=_required_text(row.get("exchange"), "exchange"),
            effective_from=_date(row.get("effective_from"), "effective_from"),
            effective_to=_optional_date(row.get("effective_to")),
            listed_at=_optional_date(row.get("listed_at")),
            delisted_at=_optional_date(row.get("delisted_at")),
            tick_size=_optional_float(row.get("tick_size")),
            lot_size=_optional_float(row.get("lot_size")),
            circuit_band=_optional_text(row.get("circuit_band")),
            available_at=_timestamp(row.get("available_at"), "available_at"),
            source=_required_text(row.get("source"), "source"),
            source_hash=_optional_text(row.get("source_hash")) or _hash_mapping(row),
        )

    def identity_tokens(self) -> tuple[str, ...]:
        tokens: list[str] = []
        if self.identity_key:
            tokens.append(f"identity:{self.identity_key}")
        if self.source_security_id:
            tokens.append(f"source:{self.exchange.upper()}:{self.source_security_id}")
        if self.isin:
            tokens.append(f"isin:{self.exchange.upper()}:{self.isin.upper()}")
        if not tokens:
            raise ValueError("security source row has no non-ticker identity evidence")
        return tuple(tokens)


@dataclass(frozen=True)
class LineageSourceRecord:
    predecessor_source_security_id: str | None
    successor_source_security_id: str | None
    predecessor_identity_key: str | None
    successor_identity_key: str | None
    relation: str
    effective_date: str
    source_publication_date: str
    available_at: str
    allocation_ratio: float | None
    source: str
    provenance_hash: str

    def __post_init__(self) -> None:
        predecessor_source_id = _optional_text(self.predecessor_source_security_id)
        successor_source_id = _optional_text(self.successor_source_security_id)
        predecessor_identity = _optional_text(self.predecessor_identity_key)
        successor_identity = _optional_text(self.successor_identity_key)
        if predecessor_source_id is None and predecessor_identity is None:
            raise ValueError("lineage row requires a predecessor source identity")
        if successor_source_id is None and successor_identity is None:
            raise ValueError("lineage row requires a successor source identity")
        effective_date = _date(self.effective_date, "effective_date")
        publication_date = _publication_date(self.source_publication_date, "source_publication_date")
        available_at = _timestamp(self.available_at, "available_at")
        if publication_date > effective_date:
            raise ValueError("source_publication_date must not be after effective_date")
        if publication_date > _calendar_date(available_at):
            raise ValueError("source_publication_date must not be after available_at calendar date")
        object.__setattr__(self, "predecessor_source_security_id", predecessor_source_id)
        object.__setattr__(self, "successor_source_security_id", successor_source_id)
        object.__setattr__(self, "predecessor_identity_key", predecessor_identity)
        object.__setattr__(self, "successor_identity_key", successor_identity)
        object.__setattr__(self, "relation", _required_text(self.relation, "relation"))
        object.__setattr__(self, "effective_date", effective_date)
        object.__setattr__(self, "source_publication_date", publication_date)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "allocation_ratio", _optional_allocation_ratio(self.allocation_ratio))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "provenance_hash", _required_text(self.provenance_hash, "provenance_hash"))

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "LineageSourceRecord":
        _reject_unknown_mapping(
            row,
            {field.name for field in fields(cls)}
            | {"predecessor_source_id", "successor_source_id", "publication_date"},
            cls.__name__,
        )
        predecessor_source_id = _optional_text(
            row.get("predecessor_source_security_id", row.get("predecessor_source_id"))
        )
        successor_source_id = _optional_text(
            row.get("successor_source_security_id", row.get("successor_source_id"))
        )
        predecessor_identity = _optional_text(row.get("predecessor_identity_key"))
        successor_identity = _optional_text(row.get("successor_identity_key"))
        if predecessor_source_id is None and predecessor_identity is None:
            raise ValueError("lineage row requires a predecessor source identity")
        if successor_source_id is None and successor_identity is None:
            raise ValueError("lineage row requires a successor source identity")
        effective_date = _date(row.get("effective_date"), "effective_date")
        publication_date = _publication_date(
            row.get("source_publication_date", row.get("publication_date")),
            "source_publication_date",
        )
        available_at = _timestamp(row.get("available_at"), "available_at")
        if publication_date > effective_date:
            raise ValueError("source_publication_date must not be after effective_date")
        if publication_date > _calendar_date(available_at):
            raise ValueError("source_publication_date must not be after available_at calendar date")
        return cls(
            predecessor_source_security_id=predecessor_source_id,
            successor_source_security_id=successor_source_id,
            predecessor_identity_key=predecessor_identity,
            successor_identity_key=successor_identity,
            relation=_required_text(row.get("relation"), "relation"),
            effective_date=effective_date,
            source_publication_date=publication_date,
            available_at=available_at,
            allocation_ratio=_optional_allocation_ratio(row.get("allocation_ratio")),
            source=_required_text(row.get("source"), "source"),
            provenance_hash=_optional_text(row.get("provenance_hash")) or _hash_mapping(row),
        )


@dataclass(frozen=True)
class SecurityMasterBundle:
    security_master: tuple[SecurityMasterRecord, ...]
    security_alias: tuple[SecurityAliasRecord, ...]
    security_lineage_edge: tuple[SecurityLineageEdge, ...]


def ingest_security_master(
    source_rows: Iterable[SecuritySourceRecord | Mapping[str, object]],
    lineage_rows: Iterable[LineageSourceRecord | Mapping[str, object]] = (),
    *,
    available_cutoff: str | datetime | None = None,
) -> SecurityMasterBundle:
    """Build canonical rows using only source facts available by the cutoff.

    A ticker is never an identity key.  Source instrument IDs or explicit
    identity keys are required; ISIN is used only as an additional bridge
    between source observations.  Lineage joins endpoints but deliberately
    does not merge their ``security_id`` values.
    """
    records = tuple(_coerce_source(row) for row in source_rows)
    cutoff = _cutoff(available_cutoff)
    visible = tuple(record for record in records if cutoff is None or _instant(record.available_at) <= cutoff)
    if not visible:
        return SecurityMasterBundle((), (), ())

    lineage_records = tuple(_coerce_lineage(row) for row in lineage_rows)
    visible_lineage = tuple(
        record for record in lineage_records
        if cutoff is None or _instant(record.available_at) <= cutoff
    )
    _reject_ambiguous_isins(visible, visible_lineage)
    _validate_lineage_temporal_alignment(visible, visible_lineage)
    _reject_same_date_symbol_conflicts(visible)

    component_by_index, component_key = _identity_components(visible)
    security_id_by_component = {
        component: _security_id(component_key[component]) for component in set(component_by_index.values())
    }
    selected = _select_versions(visible, component_by_index)
    master_rows: list[SecurityMasterRecord] = []
    aliases: list[SecurityAliasRecord] = []
    endpoint_lookup: dict[tuple[str, str], str] = {}
    for index, record in selected.items():
        _index_endpoint(endpoint_lookup, record, security_id_by_component[component_by_index[index]])
    edges = _build_lineage_edges(lineage_records, cutoff, endpoint_lookup)
    lineage_close_dates = _lineage_close_dates(
        edges,
        (
            (security_id_by_component[component_by_index[index]], record.effective_from)
            for index, record in selected.items()
        ),
    )

    for component in sorted({component_by_index[index] for index in selected}):
        component_records = [selected[index] for index in selected if component_by_index[index] == component]
        component_records.sort(key=lambda record: (record.effective_from, _instant(record.available_at)))
        security_id = security_id_by_component[component]
        for index, record in enumerate(component_records):
            next_effective = component_records[index + 1].effective_from if index + 1 < len(component_records) else None
            effective_to = record.effective_to or next_effective
            lineage_close = lineage_close_dates.get((security_id, record.effective_from))
            if lineage_close is not None and record.effective_from < lineage_close:
                if effective_to is None or lineage_close < effective_to:
                    effective_to = lineage_close
            if next_effective is not None and record.effective_to is not None:
                if record.effective_to > next_effective:
                    raise ValueError(f"overlapping security intervals for component {component}")
                if record.effective_to < next_effective:
                    raise ValueError(f"gap between security intervals for component {component}")
            master = SecurityMasterRecord(
                security_id=security_id,
                isin=record.isin,
                external_identifiers=record.external_identifiers,
                symbol=record.symbol,
                series=record.series,
                exchange=record.exchange,
                effective_from=record.effective_from,
                effective_to=effective_to,
                listed_at=record.listed_at,
                delisted_at=record.delisted_at,
                tick_size=record.tick_size,
                lot_size=record.lot_size,
                circuit_band=record.circuit_band,
                available_at=record.available_at,
                source=record.source,
                source_hash=record.source_hash,
                row_hash="",
            )
            master_rows.append(master)
            if index:
                previous = component_records[index - 1]
                if previous.symbol != record.symbol or previous.series != record.series:
                    aliases.append(
                        SecurityAliasRecord(
                            security_id=security_id,
                            old_symbol=previous.symbol,
                            new_symbol=record.symbol,
                            old_series=previous.series,
                            new_series=record.series,
                            effective_from=record.effective_from,
                            effective_to=None,
                            reason="rename" if previous.symbol != record.symbol else "series_change",
                            source=record.source,
                            available_at=record.available_at,
                        )
                    )

    master_rows.sort(key=lambda row: (row.security_id, row.effective_from, row.available_at))
    aliases.sort(key=lambda row: (row.security_id, row.effective_from, row.new_symbol or "", row.new_series or ""))
    return SecurityMasterBundle(tuple(master_rows), tuple(aliases), edges)


def build_security_master(
    source_rows: Iterable[SecuritySourceRecord | Mapping[str, object]],
    lineage_rows: Iterable[LineageSourceRecord | Mapping[str, object]] = (),
    *,
    available_cutoff: str | datetime | None = None,
) -> SecurityMasterBundle:
    """Compatibility name for the canonical security-master build."""
    return ingest_security_master(source_rows, lineage_rows, available_cutoff=available_cutoff)


def resolve_security_id(
    records: Iterable[SecurityMasterRecord],
    symbol: str,
    *,
    as_of: str,
    available_cutoff: str | datetime | None = None,
) -> str | None:
    """Resolve a symbol using both its effective date and information cutoff."""
    date_key = _date(as_of, "as_of")
    cutoff = _cutoff(available_cutoff) or datetime.fromisoformat(f"{date_key}T23:59:59.999999+00:00")
    matches = [
        record.security_id
        for record in records
        if record.symbol == symbol.upper()
        and record.effective_from <= date_key
        and (record.effective_to is None or date_key < record.effective_to)
        and (cutoff is None or _instant(record.available_at) <= cutoff)
    ]
    unique = sorted(set(matches))
    if len(unique) > 1:
        raise ValueError(f"symbol resolves to multiple security IDs at {as_of}: {symbol}")
    return unique[0] if unique else None


def _coerce_source(row: SecuritySourceRecord | Mapping[str, object]) -> SecuritySourceRecord:
    if isinstance(row, SecuritySourceRecord):
        return SecuritySourceRecord(**{field.name: getattr(row, field.name) for field in fields(SecuritySourceRecord)})
    return SecuritySourceRecord.from_mapping(row)


def _coerce_lineage(row: LineageSourceRecord | Mapping[str, object]) -> LineageSourceRecord:
    if isinstance(row, LineageSourceRecord):
        return LineageSourceRecord(**{field.name: getattr(row, field.name) for field in fields(LineageSourceRecord)})
    return LineageSourceRecord.from_mapping(row)


def _identity_components(records: tuple[SecuritySourceRecord, ...]) -> tuple[dict[int, str], dict[str, str]]:
    parent: dict[str, str] = {}

    def find(token: str) -> str:
        parent.setdefault(token, token)
        while parent[token] != token:
            parent[token] = parent[parent[token]]
            token = parent[token]
        return token

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    source_tokens_by_identity: dict[str, set[str]] = {}
    for record in records:
        tokens = record.identity_tokens()
        source_tokens = {token for token in tokens if token.startswith("source:")}
        for token in tokens:
            if not token.startswith("source:"):
                source_tokens_by_identity.setdefault(token, set()).update(source_tokens)

    for record in records:
        tokens = record.identity_tokens()
        source_tokens = [token for token in tokens if token.startswith("source:")]
        if source_tokens:
            source_token = source_tokens[0]
            for token in tokens:
                if token.startswith("source:"):
                    union(source_token, token)
                elif source_tokens_by_identity.get(token, set()) <= {source_token}:
                    union(source_token, token)
        else:
            # An explicit identity key is authoritative when no source ID is
            # available; corroborating identifiers such as ISIN do not bridge
            # distinct identity-only histories.
            find(tokens[0])
    component_by_index: dict[int, str] = {}
    for index, record in enumerate(records):
        tokens = record.identity_tokens()
        source_token = next((token for token in tokens if token.startswith("source:")), tokens[0])
        component_by_index[index] = find(source_token)
    component_key: dict[str, str] = {}
    components_by_identity: dict[str, set[str]] = {}
    for index, component in component_by_index.items():
        identity_key = records[index].identity_key
        if identity_key:
            components_by_identity.setdefault(identity_key, set()).add(component)
    for component in set(component_by_index.values()):
        indices = [index for index, value in component_by_index.items() if value == component]
        earliest = min(indices, key=lambda index: (records[index].effective_from, _instant(records[index].available_at), index))
        record = records[earliest]
        if record.identity_key and len(components_by_identity[record.identity_key]) == 1:
            key = f"identity:{record.identity_key}"
        elif record.source_security_id:
            key = f"source:{record.exchange.upper()}:{record.source_security_id}"
        else:
            raise ValueError("identity component lacks a stable non-ticker key")
        component_key[component] = key
    return component_by_index, component_key


def _select_versions(records: tuple[SecuritySourceRecord, ...], components: dict[int, str]) -> dict[int, SecuritySourceRecord]:
    selected: dict[int, SecuritySourceRecord] = {}
    groups: dict[tuple[str, str], list[int]] = {}
    for index, record in enumerate(records):
        groups.setdefault((components[index], record.effective_from), []).append(index)
    for indices in groups.values():
        claims = {
            (
                records[index].source_security_id,
                records[index].identity_key,
                records[index].isin,
                records[index].symbol,
                records[index].series,
                records[index].exchange,
            )
            for index in indices
        }
        if len({records[index].series for index in indices}) > 1:
            raise ValueError("conflicting source observations have the same effective date and series")
        if len(claims) > 1:
            raise ValueError("conflicting source observations have incompatible same-date identity claims")
        if len(indices) > 1:
            raise ValueError("duplicate canonical observation date")
        selected[indices[0]] = records[indices[0]]
    return selected


def _reject_same_date_symbol_conflicts(records: tuple[SecuritySourceRecord, ...]) -> None:
    groups: dict[tuple[str, str, str], list[SecuritySourceRecord]] = {}
    for record in records:
        groups.setdefault(
            (record.exchange.upper(), record.symbol.upper(), record.effective_from),
            [],
        ).append(record)
    for group in groups.values():
        if len({record.series for record in group}) > 1:
            raise ValueError("conflicting source observations have the same effective date and series")
        claims = {
            (
                record.source_security_id,
                record.identity_key,
                record.isin,
                record.series,
            )
            for record in group
        }
        if len(claims) > 1:
            raise ValueError("conflicting source observations have incompatible same-date identity claims")


def _index_endpoint(lookup: dict[tuple[str, str], str], record: SecuritySourceRecord, security_id: str) -> None:
    if record.source_security_id:
        _index_endpoint_value(lookup, ("source", f"{record.exchange.upper()}:{record.source_security_id}"), security_id)
    if record.identity_key:
        _index_endpoint_value(lookup, ("identity", record.identity_key), security_id)


def _index_endpoint_value(lookup: dict[tuple[str, str], str], key: tuple[str, str], security_id: str) -> None:
    existing = lookup.get(key)
    if existing is None:
        lookup[key] = security_id
    elif existing != security_id:
        # Keep the key visibly unresolved.  An identity-only lineage endpoint
        # must not silently choose one of several source-backed securities.
        lookup[key] = ""


def _reject_ambiguous_isins(
    records: tuple[SecuritySourceRecord, ...],
    lineage: tuple[LineageSourceRecord, ...],
) -> None:
    by_isin: dict[tuple[str, str], set[str]] = {}
    for record in records:
        if record.isin and record.source_security_id:
            by_isin.setdefault((record.exchange.upper(), record.isin.upper()), set()).add(record.source_security_id)
    linked: set[frozenset[str]] = set()
    for record in lineage:
        left_ids = _endpoint_source_ids(records, record.predecessor_source_security_id, record.predecessor_identity_key)
        right_ids = _endpoint_source_ids(records, record.successor_source_security_id, record.successor_identity_key)
        if len(left_ids) == 1 and len(right_ids) == 1:
            left, right = next(iter(left_ids)), next(iter(right_ids))
            linked.add(frozenset((left, right)))
    for (exchange, isin), source_ids in by_isin.items():
        if len(source_ids) < 2:
            continue
        remaining = set(source_ids)
        components: list[set[str]] = []
        while remaining:
            component = {remaining.pop()}
            changed = True
            while changed:
                changed = False
                for edge in linked:
                    if component.intersection(edge) and not edge <= component:
                        component.update(edge)
                        remaining.difference_update(edge)
                        changed = True
            components.append(component)
        if len(components) > 1:
            raise ValueError(f"ISIN {exchange}:{isin} has multiple source IDs without exact lineage")


def _validate_lineage_temporal_alignment(
    records: tuple[SecuritySourceRecord, ...],
    lineage: tuple[LineageSourceRecord, ...],
) -> None:
    for edge in lineage:
        predecessor_rows = _lineage_endpoint_candidates(
            records,
            edge.predecessor_source_security_id,
            edge.predecessor_identity_key,
            "predecessor",
        )
        successor_rows = _lineage_endpoint_candidates(
            records,
            edge.successor_source_security_id,
            edge.successor_identity_key,
            "successor",
        )
        if not any(
            previous.effective_from < edge.effective_date
            and current.effective_from == edge.effective_date
            for previous in predecessor_rows
            for current in successor_rows
        ):
            raise ValueError("lineage effective date does not match source history")


def _endpoint_source_ids(
    records: tuple[SecuritySourceRecord, ...],
    source_id: str | None,
    identity_key: str | None,
) -> set[str]:
    candidates = _endpoint_record_intersection(records, source_id, identity_key)
    return {record.source_security_id for record in candidates if record.source_security_id}


def _lineage_endpoint_candidates(
    records: tuple[SecuritySourceRecord, ...],
    source_id: str | None,
    identity_key: str | None,
    endpoint_name: str,
) -> tuple[SecuritySourceRecord, ...]:
    candidates = _endpoint_record_intersection(records, source_id, identity_key)
    if not candidates:
        raise ValueError(f"lineage {endpoint_name} endpoint is not available in source history")
    anchors = {
        ("source", record.exchange.upper(), record.source_security_id)
        if record.source_security_id
        else ("identity", record.identity_key)
        for record in candidates
    }
    if len(anchors) > 1:
        raise ValueError(f"lineage {endpoint_name} identity is ambiguous")
    return tuple(candidates)


def _endpoint_record_intersection(
    records: tuple[SecuritySourceRecord, ...],
    source_id: str | None,
    identity_key: str | None,
) -> tuple[SecuritySourceRecord, ...]:
    source_matches = {record for record in records if source_id and record.source_security_id == source_id}
    identity_matches = {record for record in records if identity_key and record.identity_key == identity_key}
    if source_id and identity_key:
        return tuple(source_matches.intersection(identity_matches))
    return tuple(source_matches or identity_matches)


def _build_lineage_edges(
    rows: Iterable[LineageSourceRecord | Mapping[str, object]],
    cutoff: datetime | None,
    endpoint_lookup: Mapping[tuple[str, str], str],
) -> tuple[SecurityLineageEdge, ...]:
    edges: list[SecurityLineageEdge] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in rows:
        record = _coerce_lineage(raw)
        if cutoff is not None and _instant(record.available_at) > cutoff:
            continue
        predecessor = _resolve_endpoint(endpoint_lookup, record.predecessor_source_security_id, record.predecessor_identity_key)
        successor = _resolve_endpoint(endpoint_lookup, record.successor_source_security_id, record.successor_identity_key)
        edge = SecurityLineageEdge(
            predecessor_security_id=predecessor,
            successor_security_id=successor,
            relation=record.relation,
            effective_date=record.effective_date,
            source_publication_date=record.source_publication_date,
            available_at=record.available_at,
            allocation_ratio=record.allocation_ratio,
            source=record.source,
            provenance_hash=record.provenance_hash,
        )
        key = (edge.predecessor_security_id, edge.successor_security_id, edge.relation, edge.effective_date)
        if key in seen:
            raise ValueError("duplicate exact lineage edge")
        seen.add(key)
        edges.append(edge)
    edges.sort(key=lambda edge: (edge.effective_date, edge.predecessor_security_id, edge.successor_security_id))
    return tuple(edges)


def _lineage_close_dates(
    rows: Iterable[SecurityLineageEdge],
    era_starts: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    boundaries: dict[str, set[str]] = {}
    for record in rows:
        boundaries.setdefault(record.predecessor_security_id, set()).add(record.effective_date)
    close_dates: dict[tuple[str, str], str] = {}
    for security_id, effective_from in era_starts:
        next_boundaries = [
            boundary
            for boundary in boundaries.get(security_id, ())
            if boundary > effective_from
        ]
        if next_boundaries:
            close_dates[(security_id, effective_from)] = min(next_boundaries)
    return close_dates


def _resolve_endpoint(lookup: Mapping[tuple[str, str], str], source_id: str | None, identity_key: str | None) -> str:
    source_candidates = {
        value
        for (kind, qualified_id), value in lookup.items()
        if source_id and kind == "source" and qualified_id.endswith(f":{source_id}")
    }
    identity_candidates = {lookup.get(("identity", identity_key))} if identity_key else set()
    source_candidates.discard(None)
    identity_candidates.discard(None)
    if source_id and identity_key:
        resolved_sources = source_candidates - {""}
        if len(resolved_sources) != 1 or len(identity_candidates) != 1:
            raise ValueError("lineage endpoint identity is ambiguous or unavailable")
        identity = next(iter(identity_candidates))
        if identity not in resolved_sources:
            raise ValueError("lineage endpoint source and identity keys do not match")
        resolved = identity
    else:
        if "" in source_candidates or "" in identity_candidates or len(source_candidates) > 1 or len(identity_candidates) > 1:
            raise ValueError("lineage endpoint source identity is ambiguous across exchanges")
        resolved = next(iter(source_candidates), None) or next(iter(identity_candidates), None)
    if resolved is None:
        raise ValueError("lineage endpoint is not available by the requested PIT cutoff")
    return resolved


def _with_row_hash(record: SecurityMasterRecord) -> SecurityMasterRecord:
    return SecurityMasterRecord(**{**record.as_dict(), "row_hash": canonical_row_hash(record.as_dict())})


def _security_id(identity_key: str) -> str:
    return "sec_" + hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:32]


def _identifiers(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        pairs = value.items()
    else:
        pairs = value
    try:
        return tuple(sorted((_required_text(name, "identifier name"), _required_text(identifier, "identifier value")) for name, identifier in pairs))  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise ValueError("external_identifiers must be a mapping or iterable of pairs") from exc


def _hash_mapping(row: Mapping[str, object]) -> str:
    stable = {str(key): str(value) for key, value in sorted(row.items(), key=lambda item: str(item[0]))}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _reject_unknown_mapping(row: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(str(key) for key in row if key not in allowed)
    if unknown:
        raise ValueError(f"{name} mapping contains unknown fields: {unknown}")


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


def _optional_float(value: object) -> float | None:
    if value is None or str(value).strip().lower() in {"", "none", "null", "nan"}:
        return None
    return float(value)


def _optional_allocation_ratio(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("allocation_ratio must be None or a finite real between 0 and 1")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("allocation_ratio must be None or a finite real between 0 and 1") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("allocation_ratio must be None or a finite real between 0 and 1")
    return result


def _date(value: object, field_name: str) -> str:
    return canonical_date(value, field_name)


def _publication_date(value: object, field_name: str) -> str:
    return canonical_date(value, field_name)


def _optional_date(value: object) -> str | None:
    return _date(value, "date") if value is not None else None


def _timestamp(value: object, field_name: str) -> str:
    result, _ = canonical_timestamp(value, field_name)
    return result


def _calendar_date(value: str) -> str:
    _, parsed = canonical_timestamp(value, "available_at")
    return parsed.date().isoformat()


def _instant(value: str) -> datetime:
    _, parsed = canonical_timestamp(value, "timestamp")
    return parsed.astimezone(timezone.utc)


def _cutoff(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        _, parsed = canonical_timestamp(value, "available_cutoff")
    else:
        raise ValueError("available_cutoff must be a datetime or canonical timestamp string")
    if parsed.tzinfo is None:
        raise ValueError("available_cutoff must include a timezone")
    return parsed.astimezone(timezone.utc)
