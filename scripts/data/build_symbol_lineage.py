"""CLI that links renamed NSE symbols to security ids from official renames and prev_close handoffs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Keep direct-file execution equivalent to `python -m scripts.data.build_symbol_lineage`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.daily_panel import derived_root
from ingest.nse_reference import read_reference, reference_dir
from ingest.symbol_lineage import Lineage, build_lineage, route_a_verdicts, write_lineage

READ_COLUMNS = ["session_date", "symbol", "series", "prev_close", "close"]
# Relative to the checkout that holds derived_data.
OVERNIGHT_PANEL = ".scratch/overnight-2026-10-19/out/series_panel.parquet"


def _parser() -> argparse.ArgumentParser:
    """Command-line flags; defaults point into the retained derived-data root."""
    root = derived_root()
    parser = argparse.ArgumentParser(description="Link renamed NSE symbols to security ids.")
    parser.add_argument("--panel", type=Path, default=root / "daily-runner/panel-v1/panel.parquet")
    parser.add_argument("--out", type=Path, default=root / "daily-runner/lineage-v1")
    parser.add_argument("--reproduce-overnight", action="store_true", help="route A only on the overnight prototype panel; writes nothing")
    parser.add_argument("--overnight-panel", type=Path, default=root.parent / OVERNIGHT_PANEL, help="prototype series panel for --reproduce-overnight")
    return parser


def _counts(verdicts: pd.Series) -> dict[str, int]:
    """Verdict tallies keyed by verdict name, sorted."""
    return {str(verdict): int(count) for verdict, count in verdicts.value_counts().sort_index().items()}


def _summary(lineage: Lineage, panel: Path) -> dict[str, Any]:
    """Counts, linked total, reference receipts and the panel hash for summary.json."""
    renames = lineage.renames
    route_a = renames[renames["source"] == "symbolchange"]
    route_b = renames[renames["source"] == "namechange"]
    receipts = json.loads((reference_dir() / "receipts.json").read_text(encoding="utf-8"))
    return {
        "official_renames_in_window": len(route_a),
        "route_a_verdicts": _counts(route_a["verdict"]),
        "route_b_verdicts": _counts(route_b["verdict"]),
        "linked": int(renames["linked"].sum()),
        "reference_sha256": {entry["name"]: entry["sha256"] for entry in receipts["files"]},
        "panel_sha256": hashlib.sha256(panel.read_bytes()).hexdigest(),
    }


def _reproduce(overnight_panel: Path, symbolchange: bytes) -> int:
    """Print the route A counts the overnight prototype produced, without writing anything."""
    renames = route_a_verdicts(pd.read_parquet(overnight_panel), symbolchange, max_gap=None)
    print(json.dumps({"official_renames": len(renames), "verdicts": _counts(renames["verdict"])}, indent=1, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Build and write the lineage from the retained panel, or reproduce the overnight counts."""
    args = _parser().parse_args(argv)
    symbolchange = read_reference("symbolchange.csv")
    if args.reproduce_overnight:
        return _reproduce(args.overnight_panel, symbolchange)
    panel = pd.read_parquet(args.panel, columns=READ_COLUMNS)
    lineage = build_lineage(panel, symbolchange, read_reference("namechange.csv"))
    summary = _summary(lineage, args.panel)
    write_lineage(lineage, summary, args.out)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
