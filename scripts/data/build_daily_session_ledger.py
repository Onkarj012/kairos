#!/usr/bin/env python3
"""Emit the authoritative exchange-session ledger and report from retained evidence.

The CLI reads only retained local evidence: the phase02 annual calendars, the
daily-graph calendar evidence store, the legacy inventory, and optional daily
cash receipts.  It never performs network access and never overwrites an
existing output root.  Amendments and the calendar review are caller-supplied
JSON; amendments are rejected unless their ``source_sha256`` binds them to a
retained accepted source.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
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


DEFAULT_ANNUAL_ROOT = Path("derived_data/phase02/raw/nse_calendar/public-v1")
DEFAULT_EVIDENCE_ROOT = Path("derived_data/daily-graph/calendar-evidence-v1")
DEFAULT_INVENTORY = Path(".scratch/daily-graph-system/legacy-inventory.json")
DEFAULT_CASH_ROOT = Path("derived_data/daily-graph/cash-evidence-v1")


def _iso_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must be canonical YYYY-MM-DD")
    return parsed


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise SystemExit(f"input json does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"input json is not valid JSON: {path}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_iso_date, default=LEDGER_START)
    parser.add_argument("--as-of", required=True, help="frozen preflight timestamp with explicit offset")
    parser.add_argument("--annual-root", type=Path, default=DEFAULT_ANNUAL_ROOT)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--cash-root", type=Path, default=DEFAULT_CASH_ROOT, help="optional daily cash evidence root")
    parser.add_argument("--amendments", type=Path, default=None, help="hash-bound interpreted amendments JSON")
    parser.add_argument("--calendar-review", type=Path, default=None, help="unresolved completeness facts JSON")
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    try:
        annuals, annual_sources = load_annual_calendars(args.annual_root)
        evidence_annuals, notices, evidence_sources = load_calendar_evidence(args.evidence_root)
    except LedgerError as exc:
        raise SystemExit(f"retained calendar evidence could not be read: {exc}") from exc

    amendments: list[dict[str, Any]] = []
    if args.amendments is not None:
        loaded = _read_json(args.amendments)
        if not isinstance(loaded, list):
            raise SystemExit("--amendments JSON must be a list of amendment objects")
        amendments = loaded

    review: dict[str, Any] | None = None
    if args.calendar_review is not None:
        loaded_review = _read_json(args.calendar_review)
        if not isinstance(loaded_review, dict):
            raise SystemExit("--calendar-review JSON must be an object")
        review = loaded_review

    inventory: dict[str, Any] | None = None
    if args.inventory is not None:
        if not args.inventory.is_file():
            raise SystemExit(f"--inventory does not exist: {args.inventory}")
        loaded_inventory = _read_json(args.inventory)
        if not isinstance(loaded_inventory, dict):
            raise SystemExit("--inventory JSON must be an object")
        inventory = loaded_inventory

    try:
        result = build_ledger(
            start=args.start,
            as_of=args.as_of,
            annual_calendars=list(annuals) + list(evidence_annuals),
            notices=notices,
            retained_sources=list(annual_sources) + list(evidence_sources),
            amendments=amendments,
            calendar_review=review,
            legacy_inventory=inventory,
            legacy_inventory_path=str(args.inventory) if inventory is not None else None,
            cash_root=args.cash_root,
        )
    except LedgerError as exc:
        raise SystemExit(f"ledger could not be built: {exc}") from exc

    if args.output_root.exists() or args.output_root.is_symlink():
        raise SystemExit(f"refusing existing immutable output root: {args.output_root}")
    publish_directory(
        args.output_root,
        {"report.json": result.report_bytes, "sessions.jsonl": result.sessions_bytes},
    )

    denominator = result.report["denominator"]
    summary = {
        "output_root": str(args.output_root),
        "session_rows": len(result.rows),
        "expected_session_count": denominator["expected_session_count"],
        "qualified_expected_denominator": denominator["qualified_expected_denominator"],
        "denominator_qualified": denominator["denominator_qualified"],
        "historical_checkpoint_blocked": denominator["historical_checkpoint_blocked"],
        "blocker_count": len(result.report["blockers"]),
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
