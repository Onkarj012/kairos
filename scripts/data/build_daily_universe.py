"""CLI that builds the monthly top-200 liquidity cohort from the daily panel, ledger, lineage and fund classes."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

# Keep direct-file execution equivalent to `python -m scripts.data.build_daily_universe`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.daily_universe import Cohort, build_cohort
from ingest.daily_panel import PANEL_FILE, START, derived_root, replace_directory

COHORT_DIR = "daily-runner/cohort-v1"
PANEL_DIR = "daily-runner/panel-v1"
LEDGER = "daily-runner/session-ledger-v1/sessions.jsonl"
LINEAGE_DIR = "daily-runner/lineage-v1"
RENAMES_FILE = "renames.csv"
FUNDS = "daily-runner/funds-v1/classification.csv"


def _parser() -> argparse.ArgumentParser:
    """Command-line flags; defaults point into the retained derived-data root."""
    root = derived_root()
    parser = argparse.ArgumentParser(description="Build the monthly top-200 liquidity cohort.")
    parser.add_argument("--panel", type=Path, default=root / PANEL_DIR, help="panel dir holding panel.parquet")
    parser.add_argument("--lineage", type=Path, default=root / LINEAGE_DIR, help="lineage dir holding renames.csv")
    parser.add_argument("--out", type=Path, default=root / COHORT_DIR)
    return parser


def _sha256(path: Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ledger_sessions(path: Path) -> list[date]:
    """Expected sessions from START on. Excluded sessions stay in, unobserved, so 60-session windows keep their length."""
    sessions = {
        date.fromisoformat(row["session_date"])
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())
        if row["state"] == "expected"
    }
    return sorted(s for s in sessions if s >= START)


def _ledger_coverage_end(path: Path) -> date:
    """Last calendar date covered by the session ledger."""
    return max(date.fromisoformat(row["session_date"]) for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()))


def _write_outputs(out_dir: Path, cohort: Cohort, hashes: dict[str, str]) -> None:
    """Write every output into a staging dir beside out_dir, then swap it into place."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        cohort.members.to_parquet(staging / "cohort.parquet", index=False)
        cohort.ever.to_csv(staging / "ever_members.csv", index=False)
        cohort.months.to_csv(staging / "months.csv", index=False)
        cohort.dropped.to_csv(staging / "dropped.csv", index=False)
        summary = {**cohort.summary, **hashes}
        (staging / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Read the inputs, build the cohort, write the outputs and print the summary."""
    args = _parser().parse_args(argv)
    root = derived_root()
    panel_path = args.panel / PANEL_FILE
    renames_path = args.lineage / RENAMES_FILE
    cohort = build_cohort(
        pd.read_parquet(panel_path),
        _ledger_sessions(root / LEDGER),
        pd.read_csv(renames_path, dtype={"old": str, "new": str, "linked": bool}, keep_default_na=False),
        pd.read_csv(root / FUNDS, dtype={"symbol": str}, keep_default_na=False),
        ledger_end=_ledger_coverage_end(root / LEDGER),
    )
    hashes = {
        "panel_sha256": _sha256(panel_path),
        "lineage_sha256": _sha256(renames_path),
        "funds_sha256": _sha256(root / FUNDS),
    }
    _write_outputs(args.out, cohort, hashes)
    print(json.dumps({**cohort.summary, **hashes}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
