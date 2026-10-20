"""CLI that builds the all-series daily panel from retained NSE cash bhavcopy bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Keep direct-file execution equivalent to `python -m scripts.data.build_daily_panel`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.adjustment_factors import read_factors
from ingest.daily_panel import PANEL_FILE, build_panel, derived_root, write_panel
from ingest.symbol_lineage import ID_MAP_FILE, read_id_map

TOLERANCE = 0.001
XLSX_SESSION = "2022-08-08"
PREVIOUS_SESSION = "2022-08-05"
NEXT_SESSION = "2022-08-10"


def _parser() -> argparse.ArgumentParser:
    """Command-line flags; defaults point into the retained derived-data root."""
    root = derived_root()
    parser = argparse.ArgumentParser(description="Build the all-series daily panel from retained cash bhavcopy bytes.")
    parser.add_argument("--readiness", type=Path, default=root / "daily-graph/feasibility-qualification-2026-09-29-terminal-v1/readiness.json")
    parser.add_argument("--ledger", type=Path, default=root / "daily-runner/session-ledger-v1/sessions.jsonl")
    parser.add_argument("--cash-root", dest="cash_roots", action="append", type=Path, default=[root / "daily-runner/cash-evidence-v1"], help="extra cash evidence root; repeatable")
    parser.add_argument("--out", type=Path, default=root / "daily-runner/panel-v1")
    parser.add_argument("--lineage", type=Path, default=root / "daily-runner/lineage-v1", help="lineage dir whose id_map.csv sets security_id")
    parser.add_argument("--no-lineage", action="store_true", help="skip lineage and keep the raw symbol as security_id")
    parser.add_argument("--adjustments", type=Path, default=root / "daily-runner/adjustments-v1", help="adjustments dir from build_adjustment_factors.py; fills the factor columns")
    parser.add_argument("--no-adjustments", action="store_true", help="skip adjustments and leave the factor columns at 1.0")
    return parser


def _equal_fraction(left: pd.Series, right: pd.Series) -> float | None:
    """Share of paired prices within tolerance, or None when nothing pairs."""
    if left.empty:
        return None
    return float(((left - right).abs() <= TOLERANCE).mean())


def _xlsx_checks(frame: pd.DataFrame) -> dict[str, float | int | None]:
    """Compare the XLSX session with its neighbours for symbols EQ on both days."""
    def eq_rows(session: str) -> pd.DataFrame:
        rows = frame[(frame["session_date"] == date.fromisoformat(session)) & (frame["series"] == "EQ")]
        return rows.set_index("symbol")

    xlsx = eq_rows(XLSX_SESSION)
    previous = eq_rows(PREVIOUS_SESSION)
    following = eq_rows(NEXT_SESSION)
    shared_previous = xlsx.index.intersection(previous.index)
    shared_following = xlsx.index.intersection(following.index)
    return {
        "eq_rows": len(xlsx),
        "prev_close_equals_0805_close": _equal_fraction(xlsx.loc[shared_previous, "prev_close"], previous.loc[shared_previous, "close"]),
        "close_equals_0810_prev_close": _equal_fraction(xlsx.loc[shared_following, "close"], following.loc[shared_following, "prev_close"]),
    }


def main(argv: list[str] | None = None) -> int:
    """Build and write the panel, then print the acceptance report as JSON."""
    args = _parser().parse_args(argv)
    lineage = None if args.no_lineage else args.lineage
    adjustments = None if args.no_adjustments else args.adjustments
    id_map = read_id_map(lineage / ID_MAP_FILE) if lineage is not None else None
    factors, factor_sha256s = (None, None) if adjustments is None else read_factors(adjustments)
    frame, manifest = build_panel(args.readiness, args.ledger, args.cash_roots, id_map, factors, factor_sha256s)
    write_panel(frame, manifest, args.out)
    report = {
        "expected_sessions": manifest["expected_sessions"],
        "present_sessions": manifest["present_sessions"],
        "missing": manifest["missing"],
        "excluded": manifest["excluded"],
        "xlsx_2022_08_08": _xlsx_checks(frame),
        "parquet_sha256": hashlib.sha256((args.out / PANEL_FILE).read_bytes()).hexdigest(),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
