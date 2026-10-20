"""Focused tests for Phase 02a identity, aliases, and lineage."""
from __future__ import annotations

import dataclasses
import math

import pytest

from contracts.security_master import (
    SECURITY_ALIAS_SCHEMA,
    SECURITY_LINEAGE_EDGE_SCHEMA,
    SECURITY_MASTER_SCHEMA,
    SecurityAliasRecord,
    SecurityLineageEdge,
    SecurityMasterRecord,
    canonical_row_hash,
)
from ingest.security_master import SecuritySourceRecord, LineageSourceRecord, ingest_security_master, resolve_security_id


# Real fixture provenance: Adani Ports' FY2011-12 annual report states that
# Mundra Port and Special Economic Zone Limited changed its name to Adani
# Ports and Special Economic Zone Limited effective 2012-01-06. The older
# MPSEZ and current ADANIPORTS market symbols are retained as the historical
# alias spelling used by this deterministic fixture; no price data is inferred.
ADANI_RENAME_PROVENANCE = {
    "source_name": "Adani Ports FY2011-12 annual report",
    "source_url": "https://www.adaniports.com/-/media/project/ports/investor/investor-downloads/annual-report/ar-2012.pdf",
    "evidence": "Change of Name section, effective January 6, 2012",
    "secondary_symbol_source_url": "https://www.sebi.gov.in/sebi_data/attachdocs/1291869138497.pdf",
}


def _rows() -> list[dict[str, object]]:
    return [
        {
            "source_security_id": "APSEZ-EQ",
            "isin": "INE742F01042",
            "symbol": "MPSEZ",
            "series": "EQ",
            "exchange": "NSE",
            "effective_from": "2007-11-27",
            "available_at": "2007-11-27T18:00:00+05:30",
            "source": ADANI_RENAME_PROVENANCE["source_name"],
            "source_hash": "annual-report-2012",
        },
        {
            "source_security_id": "APSEZ-EQ",
            "isin": "INE742F01042",
            "symbol": "ADANIPORTS",
            "series": "EQ",
            "exchange": "NSE",
            "effective_from": "2012-01-06",
            "available_at": "2012-01-10T18:00:00+05:30",
            "source": ADANI_RENAME_PROVENANCE["source_name"],
            "source_hash": "annual-report-2012",
        },
    ]


def _identity_rows() -> list[dict[str, object]]:
    common = {
        "symbol": "A",
        "series": "EQ",
        "exchange": "NSE",
        "source": "identity-fixture",
    }
    return [
        {
            **common,
            "identity_key": "K1",
            "effective_from": "2020-01-01",
            "available_at": "2020-01-01T18:00:00+05:30",
        },
        {
            **common,
            "identity_key": "K2",
            "effective_from": "2020-01-02",
            "available_at": "2020-01-02T18:00:00+05:30",
        },
    ]


def _identity_lineage(predecessor: str = "K1", successor: str = "K2", effective_date: str = "2020-01-02") -> dict[str, object]:
    return {
        "predecessor_identity_key": predecessor,
        "successor_identity_key": successor,
        "relation": "relisting",
        "effective_date": effective_date,
        "source_publication_date": effective_date,
        "available_at": "2020-01-03T18:00:00+05:30",
        "source": "identity-lineage-fixture",
        "provenance_hash": "identity-lineage-1",
    }


def _source_row(
    source_id: str,
    effective_from: str,
    *,
    symbol: str | None = None,
    effective_to: str | None = None,
    identity_key: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "source_security_id": source_id,
        "symbol": symbol or source_id,
        "series": "EQ",
        "exchange": "NSE",
        "effective_from": effective_from,
        "available_at": f"{effective_from}T18:00:00+05:30",
        "source": "lineage-regression-fixture",
    }
    if effective_to is not None:
        row["effective_to"] = effective_to
    if identity_key is not None:
        row["identity_key"] = identity_key
    return row


def _source_lineage(predecessor: str, successor: str, effective_date: str) -> dict[str, object]:
    return {
        "predecessor_source_id": predecessor,
        "successor_source_id": successor,
        "relation": "relisting",
        "effective_date": effective_date,
        "source_publication_date": effective_date,
        "available_at": f"{effective_date}T18:00:00+05:30",
        "source": "lineage-regression-fixture",
    }


def test_contracts_match_canonical_phase02a_fields_and_are_immutable() -> None:
    assert tuple(field.name for field in dataclasses.fields(SecurityMasterRecord)) == tuple(name for name, _ in SECURITY_MASTER_SCHEMA)
    assert tuple(field.name for field in dataclasses.fields(SecurityAliasRecord)) == tuple(name for name, _ in SECURITY_ALIAS_SCHEMA)
    assert tuple(field.name for field in dataclasses.fields(SecurityLineageEdge)) == tuple(name for name, _ in SECURITY_LINEAGE_EDGE_SCHEMA)
    record = SecurityMasterRecord(
        "security-1", None, (), "AAA", "EQ", "NSE", "2024-01-01", None, None, None,
        None, None, None, "2024-01-01T18:00:00+05:30", "fixture", "source", "",
    )
    assert record.row_hash == canonical_row_hash(record.as_dict())
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.symbol = "MUTATED"  # type: ignore[misc]


def _direct_master_record(**overrides: object) -> SecurityMasterRecord:
    values: dict[str, object] = {
        "security_id": "security-master",
        "isin": "INE000X01010",
        "external_identifiers": (("vendor", "master-1"),),
        "symbol": "MASTER",
        "series": "EQ",
        "exchange": "NSE",
        "effective_from": "2020-01-01",
        "effective_to": None,
        "listed_at": None,
        "delisted_at": None,
        "tick_size": 0.05,
        "lot_size": 1.0,
        "circuit_band": None,
        "available_at": "2020-01-01T18:00:00+05:30",
        "source": "direct-master-fixture",
        "source_hash": "source-master-1",
        "row_hash": "",
    }
    values.update(overrides)
    return SecurityMasterRecord(**values)  # type: ignore[arg-type]


def _direct_alias_record(**overrides: object) -> SecurityAliasRecord:
    values: dict[str, object] = {
        "security_id": "security-alias",
        "old_symbol": "OLD",
        "new_symbol": "NEW",
        "old_series": "EQ",
        "new_series": "EQ",
        "effective_from": "2020-01-02",
        "effective_to": None,
        "reason": "rename",
        "source": "direct-alias-fixture",
        "available_at": "2020-01-02T18:00:00+05:30",
        "row_hash": "",
    }
    values.update(overrides)
    return SecurityAliasRecord(**values)  # type: ignore[arg-type]


def test_real_historical_rename_creates_one_identity_and_alias() -> None:
    result = ingest_security_master(_rows())
    assert len(result.security_master) == 2
    assert len({row.security_id for row in result.security_master}) == 1
    assert result.security_alias[0].old_symbol == "MPSEZ"
    assert result.security_alias[0].new_symbol == "ADANIPORTS"
    assert result.security_alias[0].effective_from == "2012-01-06"


def test_available_cutoff_excludes_future_rename_without_lookahead() -> None:
    before = ingest_security_master(_rows(), available_cutoff="2012-01-09T23:59:59+05:30")
    after = ingest_security_master(_rows(), available_cutoff="2012-01-10T18:00:00+05:30")
    assert [row.symbol for row in before.security_master] == ["MPSEZ"]
    assert before.security_alias == ()
    assert [row.symbol for row in after.security_master] == ["MPSEZ", "ADANIPORTS"]
    assert len(after.security_alias) == 1


def test_symbol_resolution_uses_effective_date_and_information_cutoff() -> None:
    result = ingest_security_master(_rows())
    assert resolve_security_id(result.security_master, "MPSEZ", as_of="2012-01-05")
    assert resolve_security_id(result.security_master, "ADANIPORTS", as_of="2012-01-09") is None
    assert resolve_security_id(result.security_master, "ADANIPORTS", as_of="2012-01-09", available_cutoff="2012-01-09T23:59:59+05:30") is None


def test_lineage_is_pit_safe_and_keeps_predecessor_and_successor_separate() -> None:
    rows = _rows() + [
        {
            "source_security_id": "NEWCO-EQ",
            "isin": "INE000X01010",
            "symbol": "NEWCO",
            "series": "EQ",
            "exchange": "NSE",
            "effective_from": "2012-01-06",
            "available_at": "2012-01-10T18:00:00+05:30",
            "source": "fixture-lineage-source",
            "source_hash": "lineage-source-1",
        },
    ]
    lineage = [{
        "predecessor_source_id": "APSEZ-EQ",
        "successor_source_id": "NEWCO-EQ",
        "relation": "relisting",
        "effective_date": "2012-01-06",
        "source_publication_date": "2012-01-06",
        "available_at": "2012-01-11T18:00:00+05:30",
        "allocation_ratio": 1.0,
        "source": "fixture-lineage-source",
        "provenance_hash": "lineage-source-1",
    }]
    before = ingest_security_master(rows, lineage, available_cutoff="2012-01-10T18:00:00+05:30")
    after = ingest_security_master(rows, lineage, available_cutoff="2012-01-11T18:00:00+05:30")
    assert before.security_lineage_edge == ()
    assert len(after.security_lineage_edge) == 1
    edge = after.security_lineage_edge[0]
    assert edge.predecessor_security_id != edge.successor_security_id
    assert edge.source_publication_date == "2012-01-06"


def test_ticker_only_rows_fail_closed() -> None:
    with pytest.raises(ValueError, match="source_security_id or identity_key"):
        ingest_security_master([{
            "symbol": "ONLYTICKER",
            "series": "EQ",
            "exchange": "NSE",
            "effective_from": "2024-01-01",
            "available_at": "2024-01-01T18:00:00+05:30",
            "source": "unsafe-fixture",
        }])


def test_same_isin_with_multiple_source_ids_requires_exact_lineage() -> None:
    rows = _rows()
    rows[1] = {**rows[1], "source_security_id": "NEWCO-EQ", "symbol": "NEWCO"}
    with pytest.raises(ValueError, match="multiple source IDs without exact lineage"):
        ingest_security_master(rows)


def test_same_source_id_conflicting_same_date_series_rejects() -> None:
    conflict = {**_rows()[0], "series": "BE", "available_at": "2007-11-28T18:00:00+05:30"}
    with pytest.raises(ValueError, match="same effective date and series"):
        ingest_security_master(_rows() + [conflict])


def test_duplicate_direct_lineage_edges_reject() -> None:
    rows = _rows() + [{
        "source_security_id": "NEWCO-EQ",
        "isin": "INE000X01010",
        "symbol": "NEWCO",
        "series": "EQ",
        "exchange": "NSE",
        "effective_from": "2012-01-06",
        "available_at": "2012-01-10T18:00:00+05:30",
        "source": "fixture-lineage-source",
        "source_hash": "lineage-source-1",
    }]
    edge = {
        "predecessor_source_id": "APSEZ-EQ",
        "successor_source_id": "NEWCO-EQ",
        "relation": "relisting",
        "effective_date": "2012-01-06",
        "source_publication_date": "2012-01-06",
        "available_at": "2012-01-11T18:00:00+05:30",
        "source": "fixture-lineage-source",
        "provenance_hash": "lineage-source-1",
    }
    with pytest.raises(ValueError, match="duplicate exact lineage edge"):
        ingest_security_master(rows, [edge, edge])


def test_direct_source_transition_closes_predecessor_and_keeps_exact_edge() -> None:
    rows = [
        {
            "source_security_id": "1001",
            "isin": "INE000X01010",
            "symbol": "A",
            "series": "EQ",
            "exchange": "NSE",
            "effective_from": "2020-01-01",
            "available_at": "2020-01-01T18:00:00+05:30",
            "source": "transition-fixture",
        },
        {
            "source_security_id": "1002",
            "isin": "INE000X01010",
            "symbol": "A",
            "series": "EQ",
            "exchange": "NSE",
            "effective_from": "2020-01-02",
            "available_at": "2020-01-03T18:00:00+05:30",
            "source": "transition-fixture",
        },
    ]
    lineage = {
        "predecessor_source_id": "1001",
        "successor_source_id": "1002",
        "relation": "relisting",
        "effective_date": "2020-01-02",
        "source_publication_date": "2020-01-02",
        "available_at": "2020-01-03T18:00:00+05:30",
        "source": "transition-lineage-fixture",
        "provenance_hash": "transition-lineage-1",
    }

    result = ingest_security_master(rows, [lineage])

    by_date = {row.effective_from: row for row in result.security_master}
    assert by_date["2020-01-01"].effective_to == "2020-01-02"
    assert by_date["2020-01-02"].effective_to is None
    assert by_date["2020-01-01"].security_id != by_date["2020-01-02"].security_id
    assert len(result.security_lineage_edge) == 1
    edge = result.security_lineage_edge[0]
    assert edge.predecessor_security_id == by_date["2020-01-01"].security_id
    assert edge.successor_security_id == by_date["2020-01-02"].security_id
    assert edge.effective_date == "2020-01-02"


def test_identity_key_lineage_requires_exact_direction_and_date() -> None:
    result = ingest_security_master(_identity_rows(), [_identity_lineage()])

    assert len(result.security_lineage_edge) == 1
    assert result.security_lineage_edge[0].predecessor_security_id != result.security_lineage_edge[0].successor_security_id
    first, second = sorted(result.security_master, key=lambda row: row.effective_from)
    assert first.effective_to == "2020-01-02"
    assert second.effective_to is None

    with pytest.raises(ValueError, match="lineage effective date"):
        ingest_security_master(_identity_rows(), [_identity_lineage("K2", "K1")])
    with pytest.raises(ValueError, match="lineage effective date"):
        ingest_security_master(_identity_rows(), [_identity_lineage(effective_date="2020-01-03")])


def test_missing_or_ambiguous_identity_lineage_endpoint_rejects() -> None:
    with pytest.raises(ValueError, match="not available in source history"):
        ingest_security_master(_identity_rows(), [_identity_lineage(successor="K3")])

    ambiguous_rows = [
        {
            **_identity_rows()[0],
            "source_security_id": "1001",
        },
        {
            **_identity_rows()[0],
            "source_security_id": "1002",
            "effective_from": "2020-01-01",
            "available_at": "2020-01-02T18:00:00+05:30",
        },
        _identity_rows()[1],
    ]
    with pytest.raises(ValueError, match="identity is ambiguous"):
        ingest_security_master(ambiguous_rows, [_identity_lineage()])


def test_duplicate_canonical_date_rejects_different_availability_or_source() -> None:
    duplicate = {**_rows()[0], "available_at": "2007-11-28T18:00:00+05:30", "source": "second-source"}
    with pytest.raises(ValueError, match="duplicate canonical observation date"):
        ingest_security_master(_rows()[:1] + [duplicate])


def test_lineage_publication_after_effective_date_and_non_string_publication_reject() -> None:
    row = _identity_lineage()
    with pytest.raises(ValueError, match="must not be after effective_date"):
        ingest_security_master(_identity_rows(), [{**row, "source_publication_date": "2020-01-03"}])
    with pytest.raises(ValueError, match="must be an ISO date string"):
        ingest_security_master(_identity_rows(), [{**row, "source_publication_date": 20200102}])


@pytest.mark.parametrize(
    "publication_date",
    [
        " 2020-01-02",
        "2020-01-02 ",
        "2020-1-02",
        "2020-01-2",
        "2020-01-02T00:00:00",
        "２０２０-０１-０２",
        "2020‐01‐02",
    ],
)
def test_lineage_publication_requires_exact_ascii_canonical_date(publication_date: object) -> None:
    row = _identity_lineage()
    with pytest.raises(ValueError, match="must be an ISO date string"):
        ingest_security_master(_identity_rows(), [{**row, "source_publication_date": publication_date}])


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective_from", " 2020-01-01"),
        ("effective_from", "2020-01-01T00:00:00"),
        ("effective_to", "2020-01-02 "),
        ("listed_at", "2020-01-01T00:00:00"),
        ("delisted_at", " 2020-01-03"),
    ],
)
def test_security_source_dates_require_exact_ascii_canonical_date(field: str, value: str) -> None:
    row = _source_row("SOURCE-1", "2020-01-01")
    row[field] = value
    with pytest.raises(ValueError, match="must be an ISO date string"):
        ingest_security_master([row])


@pytest.mark.parametrize("effective_date", [" 2020-01-02", "2020-01-02 ", "2020-01-02T00:00:00"])
def test_lineage_effective_date_requires_exact_ascii_canonical_date(effective_date: str) -> None:
    with pytest.raises(ValueError, match="must be an ISO date string"):
        ingest_security_master(_identity_rows(), [_identity_lineage(effective_date=effective_date)])


def test_lineage_publication_uses_available_calendar_date_in_original_offset() -> None:
    row = {
        **_identity_lineage(),
        "available_at": "2020-01-01T23:30:00-05:00",
        "source_publication_date": "2020-01-02",
    }
    with pytest.raises(ValueError, match="available_at"):
        LineageSourceRecord.from_mapping(row)


def _direct_lineage_edge(**overrides: object) -> SecurityLineageEdge:
    values: dict[str, object] = {
        "predecessor_security_id": "security-predecessor",
        "successor_security_id": "security-successor",
        "relation": "relisting",
        "effective_date": "2020-01-02",
        "source_publication_date": "2020-01-02",
        "available_at": "2020-01-02T00:30:00+05:30",
        "allocation_ratio": 1.0,
        "source": "direct-lineage-fixture",
        "provenance_hash": "direct-lineage-hash",
    }
    values.update(overrides)
    return SecurityLineageEdge(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "factory,field,value",
    [
        (_direct_master_record, "symbol", "TAMPERED"),
        (_direct_alias_record, "new_symbol", "TAMPERED"),
        (_direct_lineage_edge, "relation", "merger"),
    ],
)
def test_direct_records_reject_stale_row_hash(factory: object, field: str, value: object) -> None:
    record = factory()  # type: ignore[operator]
    tampered = {**record.as_dict(), field: value}
    with pytest.raises(ValueError, match="row_hash does not match"):
        type(record)(**tampered)


@pytest.mark.parametrize("factory", [_direct_master_record, _direct_alias_record, _direct_lineage_edge])
def test_direct_records_reject_malformed_row_hash(factory: object) -> None:
    record = factory()  # type: ignore[operator]
    with pytest.raises(ValueError, match="row_hash does not match"):
        type(record)(**{**record.as_dict(), "row_hash": "not-a-hash"})


def test_direct_records_accept_supplied_canonical_hash_and_ingest_matches() -> None:
    direct_records = (_direct_master_record(), _direct_alias_record(), _direct_lineage_edge())
    for record in direct_records:
        assert type(record)(**record.as_dict()) == record

    master_result = ingest_security_master(_rows())
    lineage_result = ingest_security_master(_identity_rows(), [_identity_lineage()])
    all_records = master_result.security_master + master_result.security_alias + lineage_result.security_lineage_edge
    assert all(record.row_hash == canonical_row_hash(record.as_dict()) for record in all_records)


@pytest.mark.parametrize(
    "value",
    [
        "2020-01-02T00:30:00Z",
        "2020-01-02T00:30:00+05:30",
        "2020-01-02T00:30:00-05:00",
        "2020-01-02T00:30:00.123456+00:00",
    ],
)
def test_identity_timestamp_canonical_matrix_has_direct_ingest_parity(value: str) -> None:
    direct = _direct_master_record(available_at=value)
    ingested = ingest_security_master([{**_rows()[0], "available_at": value}])
    assert direct.available_at == value
    assert ingested.security_master[0].available_at == value


@pytest.mark.parametrize(
    "value",
    [
        "2020-01-02 00:30:00+00:00",
        "2020-01-02T003000+0000",
        "2020-01-02T00:30:00z",
        "2020-01-02T00:30:00",
        " 2020-01-02T00:30:00+00:00",
        "2020-01-02T00:30:00+00:00 ",
        "２０２０-０１-０２T００:３０:００+００:００",
        "2020-13-02T00:30:00+00:00",
        "2020-01-02T00:30:00.1234567+00:00",
        True,
        20200102003000,
    ],
)
def test_identity_timestamp_rejects_noncanonical_direct_and_ingest_values(value: object) -> None:
    with pytest.raises(ValueError, match="timestamp|timezone"):
        _direct_master_record(available_at=value)
    with pytest.raises(ValueError, match="timestamp|timezone"):
        ingest_security_master([{**_rows()[0], "available_at": value}])


def test_security_source_direct_reconstruction_rejects_tampering_and_unknown_mapping_fields() -> None:
    direct = SecuritySourceRecord.from_mapping(_rows()[0])
    object.__setattr__(direct, "available_at", "2020-01-02 00:30:00+00:00")
    with pytest.raises(ValueError, match="timestamp"):
        ingest_security_master([direct])
    with pytest.raises(ValueError, match="unknown fields"):
        SecuritySourceRecord.from_mapping({**_rows()[0], "unexpected": "reject-me"})


@pytest.mark.parametrize("allocation_ratio", [False, True, -0.01, 1.01, math.nan, math.inf, -math.inf])
def test_lineage_allocation_ratio_rejects_non_contract_values(allocation_ratio: object) -> None:
    with pytest.raises(ValueError, match="allocation_ratio"):
        _direct_lineage_edge(allocation_ratio=allocation_ratio)


@pytest.mark.parametrize("allocation_ratio", [None, 0.0, 1.0])
def test_lineage_allocation_ratio_accepts_inclusive_contract_bounds(allocation_ratio: float | None) -> None:
    edge = _direct_lineage_edge(allocation_ratio=allocation_ratio)
    assert edge.allocation_ratio == allocation_ratio


@pytest.mark.parametrize("allocation_ratio", [False, True, "0.5", math.nan, math.inf, -math.inf, -0.01, 1.01])
def test_lineage_ingest_rejects_values_rejected_by_canonical_contract(allocation_ratio: object) -> None:
    with pytest.raises(ValueError, match="allocation_ratio"):
        ingest_security_master(
            _identity_rows(),
            [{**_identity_lineage(), "allocation_ratio": allocation_ratio}],
        )


def test_direct_lineage_edge_preserves_valid_timestamp_offset_and_spelling() -> None:
    edge = _direct_lineage_edge(
        source_publication_date="2020-01-01",
        available_at="2020-01-01T23:30:00-05:00",
    )

    assert edge.source_publication_date == "2020-01-01"
    assert edge.available_at == "2020-01-01T23:30:00-05:00"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_publication_date", " 2020-01-02"),
        ("source_publication_date", "2020-01-02 "),
        ("effective_date", " 2020-01-02"),
        ("effective_date", "2020-01-02 "),
    ],
)
def test_direct_lineage_edge_rejects_date_whitespace(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="must be an ISO date string"):
        _direct_lineage_edge(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_publication_date", "2020-01-02T00:00:00"),
        ("source_publication_date", "2020-1-02"),
        ("source_publication_date", "２０２０-０１-０２"),
        ("source_publication_date", 20200102),
        ("effective_date", "2020-01-02T00:00:00"),
    ],
)
def test_direct_lineage_edge_rejects_noncanonical_dates(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="ISO date string"):
        _direct_lineage_edge(**{field: value})


def test_direct_lineage_edge_rejects_publication_after_effective_date() -> None:
    with pytest.raises(ValueError, match="after effective_date"):
        _direct_lineage_edge(source_publication_date="2020-01-03")


def test_direct_lineage_edge_rejects_publication_after_available_original_offset_date() -> None:
    with pytest.raises(ValueError, match="available_at calendar date"):
        _direct_lineage_edge(
            source_publication_date="2020-01-02",
            available_at="2020-01-01T23:30:00-05:00",
        )


def test_direct_lineage_edge_rejects_naive_available_timestamp() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        _direct_lineage_edge(available_at="2020-01-02T00:30:00")


def test_repeated_predecessor_eras_close_at_their_next_lineage_boundary() -> None:
    rows = [
        _source_row("P", "2020-01-01"),
        _source_row("P", "2020-01-02"),
        _source_row("B1", "2020-01-02"),
        _source_row("B2", "2020-01-03"),
    ]
    lineage = [
        _source_lineage("P", "B1", "2020-01-02"),
        _source_lineage("P", "B2", "2020-01-03"),
    ]

    result = ingest_security_master(rows, lineage)
    predecessor = sorted(
        (row for row in result.security_master if row.symbol == "P"),
        key=lambda row: row.effective_from,
    )
    assert [(row.effective_from, row.effective_to) for row in predecessor] == [
        ("2020-01-01", "2020-01-02"),
        ("2020-01-02", "2020-01-03"),
    ]


def test_branching_same_day_lineage_closes_once_at_shared_boundary() -> None:
    rows = [
        _source_row("P", "2020-01-01"),
        _source_row("B1", "2020-01-02"),
        _source_row("B2", "2020-01-02"),
    ]
    lineage = [
        _source_lineage("P", "B1", "2020-01-02"),
        _source_lineage("P", "B2", "2020-01-02"),
    ]

    result = ingest_security_master(rows, lineage)
    assert next(row for row in result.security_master if row.symbol == "P").effective_to == "2020-01-02"
    assert len(result.security_lineage_edge) == 2


def test_lineage_edge_at_era_start_is_ignored_for_that_era() -> None:
    rows = [
        _source_row("P", "2020-01-01"),
        _source_row("P", "2020-01-02"),
        _source_row("B1", "2020-01-02"),
        _source_row("B2", "2020-01-03"),
    ]
    lineage = [
        _source_lineage("P", "B1", "2020-01-02"),
        _source_lineage("P", "B2", "2020-01-03"),
    ]

    result = ingest_security_master(rows, lineage)
    predecessor = sorted(
        (row for row in result.security_master if row.symbol == "P"),
        key=lambda row: row.effective_from,
    )
    assert predecessor[-1].effective_to == "2020-01-03"


def test_explicit_effective_to_conflicts_fail_closed() -> None:
    rows = [
        _source_row("P", "2020-01-01", effective_to="2020-01-04"),
        _source_row("B", "2020-01-02"),
    ]
    result = ingest_security_master(rows, [_source_lineage("P", "B", "2020-01-02")])
    assert next(row for row in result.security_master if row.symbol == "P").effective_to == "2020-01-02"

    rows[0] = _source_row("P", "2020-01-01", effective_to="2020-01-02")
    rows[1] = _source_row("B", "2020-01-03")
    result = ingest_security_master(rows, [_source_lineage("P", "B", "2020-01-03")])
    assert next(row for row in result.security_master if row.symbol == "P").effective_to == "2020-01-02"


def test_same_identity_explicit_intervals_must_be_contiguous() -> None:
    rows = [
        _source_row("P", "2020-01-01", effective_to="2020-01-02"),
        _source_row("P", "2020-01-02", effective_to="2020-01-04"),
    ]

    result = ingest_security_master(rows)

    assert [(row.effective_from, row.effective_to) for row in result.security_master] == [
        ("2020-01-01", "2020-01-02"),
        ("2020-01-02", "2020-01-04"),
    ]


@pytest.mark.parametrize("next_effective", ["2020-01-03", "2020-01-05"])
def test_same_identity_explicit_gap_fails_closed(next_effective: str) -> None:
    rows = [
        _source_row("P", "2020-01-01", effective_to="2020-01-02"),
        _source_row("P", next_effective),
    ]

    with pytest.raises(ValueError, match="gap between security intervals"):
        ingest_security_master(rows)


def test_terminal_explicit_effective_to_is_preserved() -> None:
    result = ingest_security_master([_source_row("P", "2020-01-01", effective_to="2020-01-04")])

    assert result.security_master[0].effective_to == "2020-01-04"


def test_same_identity_overlap_behavior_is_unchanged() -> None:
    rows = [
        _source_row("P", "2020-01-01", effective_to="2020-01-04"),
        _source_row("P", "2020-01-02"),
    ]

    with pytest.raises(ValueError, match="overlapping security intervals"):
        ingest_security_master(rows)


def test_gap_across_distinct_identities_requires_separate_lineage_histories() -> None:
    rows = [
        _source_row("P1", "2020-01-01", symbol="P", effective_to="2020-01-02"),
        _source_row("P2", "2020-01-03", symbol="P"),
    ]

    result = ingest_security_master(rows, [_source_lineage("P1", "P2", "2020-01-03")])
    intervals = sorted(result.security_master, key=lambda row: row.effective_from)

    assert len({row.security_id for row in intervals}) == 2
    assert [(row.effective_from, row.effective_to) for row in intervals] == [
        ("2020-01-01", "2020-01-02"),
        ("2020-01-03", None),
    ]
    assert len(result.security_lineage_edge) == 1


def test_a_to_b_to_a_lineage_closes_each_source_id_era() -> None:
    rows = [
        _source_row("A1", "2020-01-01", symbol="A"),
        _source_row("B", "2020-01-02", symbol="B"),
        _source_row("A2", "2020-01-03", symbol="A"),
    ]
    lineage = [
        _source_lineage("A1", "B", "2020-01-02"),
        _source_lineage("B", "A2", "2020-01-03"),
    ]

    result = ingest_security_master(rows, lineage)
    by_symbol = {row.symbol + row.effective_from: row for row in result.security_master}
    assert by_symbol["A2020-01-01"].effective_to == "2020-01-02"
    assert by_symbol["B2020-01-02"].effective_to == "2020-01-03"
    assert by_symbol["A2020-01-03"].effective_to is None


def test_identity_only_rows_with_same_isin_stay_distinct_without_lineage() -> None:
    rows = [{**row, "isin": "INE000X01010"} for row in _identity_rows()]
    result = ingest_security_master(rows)
    assert len({row.security_id for row in result.security_master}) == 2


def test_identity_only_rows_with_same_isin_merge_only_through_exact_lineage() -> None:
    rows = [{**row, "isin": "INE000X01010"} for row in _identity_rows()]
    result = ingest_security_master(rows, [_identity_lineage()])
    assert len({row.security_id for row in result.security_master}) == 2
    assert result.security_lineage_edge[0].predecessor_security_id != result.security_lineage_edge[0].successor_security_id


def test_same_identity_key_continuity_merges_without_isin_union() -> None:
    rows = [
        {**_identity_rows()[0], "isin": "INE000X01010"},
        {**_identity_rows()[0], "isin": "INE000X01010", "effective_from": "2020-01-02", "available_at": "2020-01-02T18:00:00+05:30"},
    ]
    result = ingest_security_master(rows)
    assert len({row.security_id for row in result.security_master}) == 1
    assert [(row.effective_from, row.effective_to) for row in result.security_master] == [("2020-01-01", "2020-01-02"), ("2020-01-02", None)]


def test_source_and_identity_lineage_endpoints_require_exact_intersection() -> None:
    rows = [
        _source_row("S1", "2020-01-01", identity_key="K1"),
        _source_row("S2", "2020-01-02", identity_key="K2"),
    ]
    edge = {
        **_source_lineage("S1", "S2", "2020-01-02"),
        "predecessor_identity_key": "K1",
        "successor_identity_key": "K2",
    }
    result = ingest_security_master(rows, [edge])
    assert len(result.security_lineage_edge) == 1

    mismatch = {**edge, "predecessor_identity_key": "K2"}
    with pytest.raises(ValueError, match="not available in source history"):
        ingest_security_master(rows, [mismatch])


def test_same_source_id_a_to_b_to_a_preserves_one_identity_and_alias_history() -> None:
    rows = [
        {**_rows()[0], "symbol": "A", "effective_from": "2020-01-01", "available_at": "2020-01-01T18:00:00+05:30"},
        {**_rows()[0], "symbol": "B", "effective_from": "2020-01-02", "available_at": "2020-01-02T18:00:00+05:30"},
        {**_rows()[0], "symbol": "A", "effective_from": "2020-01-03", "available_at": "2020-01-03T18:00:00+05:30"},
    ]

    result = ingest_security_master(rows)

    assert len({row.security_id for row in result.security_master}) == 1
    intervals = sorted(result.security_master, key=lambda row: row.effective_from)
    assert [(row.effective_from, row.effective_to) for row in intervals] == [
        ("2020-01-01", "2020-01-02"),
        ("2020-01-02", "2020-01-03"),
        ("2020-01-03", None),
    ]
    assert [(alias.old_symbol, alias.new_symbol) for alias in result.security_alias] == [("A", "B"), ("B", "A")]
