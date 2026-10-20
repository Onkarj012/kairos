#!/usr/bin/env python3
"""Build the daily-runner session ledger from retained evidence, offline.

Mirrors the final ``build_ledger`` call in the issue03 rebuild, so the same
inputs reproduce ``session-ledger-v5`` byte for byte at its ``as_of``.  It never
performs network access, refuses an existing output root, and writes nothing
when the denominator is not qualified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.daily_session_ledger import (
    LEDGER_START,
    LedgerError,
    build_ledger,
    load_annual_calendars,
    load_calendar_evidence,
)
from ingest.calendar import publish_directory


def _read_object(path: Path) -> dict[str, Any]:
    """Read a JSON file that must hold an object."""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit(f"input json must be an object: {path}")
    return loaded


def _parser() -> argparse.ArgumentParser:
    """Define the command-line flags."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="timestamp with explicit offset")
    parser.add_argument("--phase02-annual-root", type=Path, required=True)
    parser.add_argument("--v1-evidence-root", type=Path, required=True)
    parser.add_argument("--v4-evidence-root", type=Path, required=True)
    parser.add_argument("--amendments", type=Path, required=True)
    parser.add_argument("--calendar-review", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cash-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the ledger and publish it under a new output root."""
    args = _parser().parse_args(argv)
    if args.output_root.exists() or args.output_root.is_symlink():
        raise SystemExit(f"refusing existing immutable output root: {args.output_root}")

    try:
        phase02_annuals, phase02_sources = load_annual_calendars(args.phase02_annual_root)
        v1_annuals, _, v1_sources = load_calendar_evidence(args.v1_evidence_root)
        _, notices, v4_sources = load_calendar_evidence(args.v4_evidence_root)
        annual_sources = [s for s in v1_sources if s.source_kind == "annual_calendar"]
        result = build_ledger(
            start=LEDGER_START,
            as_of=args.as_of,
            annual_calendars=list(phase02_annuals) + list(v1_annuals),
            notices=notices,
            retained_sources=list(phase02_sources) + annual_sources + list(v4_sources),
            amendments=json.loads(args.amendments.read_text(encoding="utf-8")),
            calendar_review=_read_object(args.calendar_review),
            legacy_inventory=_read_object(args.inventory),
            legacy_inventory_path=str(args.inventory),
            cash_root=args.cash_root,
        )
    except LedgerError as exc:
        raise SystemExit(f"ledger could not be built: {exc}") from exc

    denominator = result.report["denominator"]
    if not denominator["denominator_qualified"]:
        raise SystemExit(f"denominator not qualified, nothing written: {result.report['blockers']}")

    publish_directory(
        args.output_root,
        {"report.json": result.report_bytes, "sessions.jsonl": result.sessions_bytes},
    )
    print(json.dumps({
        "output_root": str(args.output_root),
        "session_rows": len(result.rows),
        "expected_session_count": denominator["expected_session_count"],
        "qualified_expected_denominator": denominator["qualified_expected_denominator"],
        "denominator_qualified": denominator["denominator_qualified"],
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
