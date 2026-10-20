from __future__ import annotations

import pandas as pd

from ingest.symbol_lineage import build_lineage

SYMBOLCHANGE_HEADER = "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
NAMECHANGE_HEADER = "SYMBOL,OLD NAME OF THE COMPANY,NEW NAME OF THE COMPANY,EFFECTIVE DATE\n"


def _panel(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    """A minimal EQ panel from (symbol, session, close, prev_close) rows."""
    frame = pd.DataFrame(rows, columns=["symbol", "session_date", "close", "prev_close"])
    return frame.assign(series="EQ", session_date=pd.to_datetime(frame["session_date"]))


def test_namechange_picks_the_only_successor_with_a_namechange_row() -> None:
    panel = _panel([
        ("STABLE", "2026-10-01", 10, 10), ("STABLE", "2026-10-02", 10, 10), ("STABLE", "2026-10-03", 10, 10),
        ("STABLE", "2026-10-04", 10, 10), ("STABLE", "2026-10-05", 10, 10),
        ("RUCHI", "2026-10-01", 90, 90), ("RUCHI", "2026-10-02", 95, 90), ("RUCHI", "2026-10-03", 100, 95),
        ("PATANJALI", "2026-10-04", 100, 100), ("PATANJALI", "2026-10-05", 101, 100),
        ("SPORTKING", "2026-10-04", 50, 100), ("SPORTKING", "2026-10-05", 51, 50),
    ])
    namechange = (NAMECHANGE_HEADER + "PATANJALI,RUCHI LTD,PATANJALI LTD,04-OCT-2026\n").encode()

    lineage = build_lineage(panel, SYMBOLCHANGE_HEADER.encode(), namechange)

    row = lineage.renames.set_index("old").loc["RUCHI"]
    assert row["verdict"] == "namechange_prevclose_match"
    assert bool(row["linked"])
    assert lineage.id_map == {"RUCHI": "PATANJALI"}


def test_adjacent_namechange_and_triple_confirmed_symbolchange_link_together() -> None:
    panel = _panel([
        ("STABLE", "2026-10-01", 10, 10), ("STABLE", "2026-10-02", 10, 10), ("STABLE", "2026-10-03", 10, 10),
        ("STABLE", "2026-10-04", 10, 10), ("STABLE", "2026-10-05", 10, 10),
        ("BURGERKING", "2026-10-01", 45, 45), ("BURGERKING", "2026-10-02", 50, 45),
        ("RBA", "2026-10-03", 50, 50), ("RBA", "2026-10-04", 52, 50), ("RBA", "2026-10-05", 53, 52),
        ("OLDCO", "2026-10-01", 79, 79), ("OLDCO", "2026-10-02", 80, 79),
        ("NEWCO", "2026-10-03", 81, 80), ("NEWCO", "2026-10-04", 82, 81), ("NEWCO", "2026-10-05", 83, 82),
    ])
    symbolchange = (SYMBOLCHANGE_HEADER + "OLDCO LTD,OLDCO,NEWCO,03-OCT-2026\n").encode()
    namechange = (NAMECHANGE_HEADER + "RBA,BURGERKING LTD,RBA LTD,03-OCT-2026\n").encode()

    lineage = build_lineage(panel, symbolchange, namechange)

    verdicts = lineage.renames.set_index("old")["verdict"]
    assert verdicts["BURGERKING"] == "namechange_prevclose_match"
    assert verdicts["OLDCO"] == "triple_confirmed"
    assert lineage.id_map == {"BURGERKING": "RBA", "OLDCO": "NEWCO"}


def test_symbol_reuse_is_flagged_and_not_linked() -> None:
    panel = _panel([
        ("STABLE", "2026-10-01", 10, 10), ("STABLE", "2026-10-02", 10, 10), ("STABLE", "2026-10-03", 10, 10),
        ("STABLE", "2026-10-04", 10, 10), ("STABLE", "2026-10-05", 10, 10),
        ("QUALITY30", "2026-10-01", 20, 20), ("QUALITY30", "2026-10-02", 21, 20),
        ("QUALITY30", "2026-10-03", 21, 21), ("QUALITY30", "2026-10-04", 22, 21),
        ("QUALITY30", "2026-10-05", 23, 22),
    ])
    symbolchange = (SYMBOLCHANGE_HEADER + "QUALITY30 LTD,QUALITY30,QUALITY30,03-OCT-2026\n").encode()

    lineage = build_lineage(panel, symbolchange, (NAMECHANGE_HEADER).encode())

    row = lineage.renames.set_index("old").loc["QUALITY30"]
    assert row["verdict"] == "overlap_symbol_reuse_or_conflict"
    assert not bool(row["linked"])
    assert lineage.id_map == {}


def test_namechange_successor_already_taken_by_symbolchange_is_a_conflict() -> None:
    panel = _panel([
        ("STABLE", "2026-10-01", 10, 10), ("STABLE", "2026-10-02", 10, 10), ("STABLE", "2026-10-03", 10, 10),
        ("STABLE", "2026-10-04", 10, 10), ("STABLE", "2026-10-05", 10, 10),
        ("XCO", "2026-10-01", 80, 80), ("XCO", "2026-10-02", 80, 80),
        ("YCO", "2026-10-01", 80, 80), ("YCO", "2026-10-02", 80, 80),
        ("ZCO", "2026-10-03", 81, 80), ("ZCO", "2026-10-04", 82, 81), ("ZCO", "2026-10-05", 83, 82),
    ])
    symbolchange = (SYMBOLCHANGE_HEADER + "XCO LTD,XCO,ZCO,03-OCT-2026\n").encode()
    namechange = (NAMECHANGE_HEADER + "ZCO,YCO LTD,ZCO LTD,03-OCT-2026\n").encode()

    lineage = build_lineage(panel, symbolchange, namechange)

    row = lineage.renames.set_index("old").loc["YCO"]
    assert row["verdict"] == "namechange_conflict"
    assert not bool(row["linked"])
    assert lineage.id_map == {"XCO": "ZCO"}
