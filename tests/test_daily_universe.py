"""Offline check of the cohort rules on tiny synthetic frames with known answers."""
from __future__ import annotations

import pandas as pd

from features.daily_universe import build_cohort

# Weekdays from 2019-10-01 to 2020-01-31: 66 sessions in 2019, so 2020-01 starts at index 66 and ranks on index 65.
SESSIONS = [ts.date() for ts in pd.bdate_range("2019-10-01", "2020-01-31")]
N = len(SESSIONS)
WINDOW_START = 6  # The 60-session window ending at index 65 starts at index 6.


def _rows(
    security_id: str, values: list[float | None], symbol: str | None = None, series: str = "EQ"
) -> pd.DataFrame:
    """Panel rows for one id; None means no row that session."""
    return pd.DataFrame(
        [
            {
                "session_date": SESSIONS[i],
                "security_id": security_id,
                "symbol": symbol or security_id,
                "series": series,
                "traded_value": value,
            }
            for i, value in enumerate(values)
            if value is not None
        ]
    )


def _renames(rows: list[tuple[str, str, str, bool, str]]) -> pd.DataFrame:
    """Symbolchange rename rows (old, new, verdict, linked, effective) with the columns the cohort reads."""
    frame = pd.DataFrame(rows, columns=["old", "new", "verdict", "linked", "effective"]).astype({"linked": bool})
    return frame.assign(source="symbolchange", old_last=None, new_first=None)


def test_ranking_uses_observed_days_and_ties_and_drops_funds() -> None:
    """Gaps stay out of the median, short windows drop out, ties go to the lower id, funds are logged."""
    aaa = [700.0] * N
    bbb = [500.0] * N
    for i in range(6, 16):
        bbb[i] = None  # Ledger gap inside the window: 50 observed days, median 500.
    for i in range(16, 41):
        bbb[i] = 400.0
    for i in range(41, 66):
        bbb[i] = 600.0
    ccc = [9000.0 if WINDOW_START <= i < 36 else None for i in range(N)]  # Only 30 observed days.
    panel = pd.concat(
        [
            _rows("AAA", aaa),
            _rows("BBB", bbb),
            _rows("CCC", ccc),
            _rows("DDD", [900.0] * N),
            _rows("EEE", [500.0] * N),
        ]
    )
    funds = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
            "class": ["equity", "equity", "equity", "fund", "equity"],
        }
    )

    cohort = build_cohort(panel, SESSIONS, _renames([]), funds, top=2, window=60, min_obs=40)

    assert cohort.members[["security_id", "rank"]].values.tolist() == [["AAA", 1], ["BBB", 2]]
    assert cohort.members["median_traded_value"].tolist() == [700.0, 500.0]
    assert cohort.months["ranked"].tolist() == [4]
    assert cohort.dropped[["month", "security_id", "reason"]].values.tolist() == [["2020-01", "DDD", "fund"]]


def test_unproven_id_is_dropped_and_placeholder_side_is_not() -> None:
    """A direct unlinked rename makes its id unproven from its date on; an EQ id whose other side never traded does not."""
    panel = pd.concat(
        [
            _rows("FFF", [800.0] * N, symbol="FFFNEW"),
            _rows("HHH", [900.0] * N, symbol="HHHNEW"),
            _rows("GGG", [750.0] * N, symbol="GGGNEW"),
            _rows("AAA", [700.0] * N),
        ]
    )
    funds = pd.DataFrame({"symbol": ["FFFNEW", "HHHNEW", "GGGNEW", "AAA"], "class": ["equity"] * 4})
    # AAA's rename is dated after the 2020-01 rank session (2019-12-31), so it must not drop AAA that month.
    # HHHNEW is one of two ambiguous successors, so it must be dropped from the 2020-01 cohort on its own.
    renames = _renames(
        [
            ("FFFOLD", "FFFNEW", "adjacent_prevclose_mismatch", False, "2019-10-15"),
            ("NUMOLD", "GGGNEW", "one_side_absent_from_EQ", False, "2019-10-15"),
            ("AAAOLD", "AAA", "adjacent_prevclose_mismatch", False, "2020-01-15"),
            ("HHHOLD", "HHHNEW|JJJNEW", "namechange_ambiguous", False, "2019-10-15"),
        ]
    )

    cohort = build_cohort(panel, SESSIONS, renames, funds, top=2, window=60, min_obs=40)

    assert cohort.members[["security_id", "rank"]].values.tolist() == [["GGG", 1], ["AAA", 2]]
    assert cohort.dropped[["month", "security_id", "reason"]].values.tolist() == [
        ["2020-01", "HHH", "unproven"],
        ["2020-01", "FFF", "unproven"],
    ]
    assert cohort.summary["identity"]["unproven_ids"] == 3
    assert cohort.summary["ever_members_with_unproven_rename"] == 0
