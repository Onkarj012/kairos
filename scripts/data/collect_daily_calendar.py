#!/usr/bin/env python3
"""Retain bounded, auditable evidence for one official calendar or notice URL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.daily_calendar_evidence import (
    ANNUAL_CALENDAR,
    SPECIAL_SESSION,
    BoundedRetriever,
    EvidenceStore,
    SourceReceipt,
    collect_source,
    read_receipts,
    replay_retained,
)


DEFAULT_OUTPUT_ROOT = Path("derived_data/daily-graph/calendar-evidence-v1")


def build_report(receipt: SourceReceipt) -> dict[str, Any]:
    """Report one retained source without presenting it as a calendar ledger."""
    return {
        "schema": "meridian.daily-calendar-evidence.report.v1",
        "requested_url": receipt.requested_url,
        "source_kind": receipt.source_kind,
        "declared_year": receipt.declared_year,
        "accepted": receipt.accepted,
        "rejection_reason": receipt.rejection_reason,
        "receipt": receipt.metadata(),
        "coverage": {
            "is_calendar_ledger": False,
            "establishes_complete_special_session_coverage": False,
            "note": "A retained official source is evidence only; it is not a session ledger and does not prove complete special-session coverage.",
        },
        "date_separation": {
            "explicit_known": {"declared_year": receipt.declared_year, "declared_year_basis": "cli_supplied_year"},
            "absent": ["publication_date", "available_at", "authoritative_session_dates"],
            "filename_or_retrieval_date_inference": "none",
        },
    }


def build_replay_report(root: Path) -> dict[str, Any]:
    results = replay_retained(root)
    return {
        "schema": "meridian.daily-calendar-evidence.replay-report.v1",
        "source_root": str(root),
        "retained_receipt_count": len(read_receipts(root)),
        "replayed": [
            {
                "logical_name": item.logical_name,
                "requested_url": item.requested_url,
                "stored_decision": item.stored_decision,
                "replay_decision": item.replay_decision,
                "matches_stored": item.matches_stored,
                "integrity_ok": item.integrity_ok,
                "integrity_reason": item.integrity_reason,
                "media_kind": item.media_kind,
                "sha256": item.sha256,
                "rejection_reason": item.rejection_reason,
            }
            for item in results
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="explicit official source URL")
    parser.add_argument("--kind", choices=sorted({ANNUAL_CALENDAR, SPECIAL_SESSION}), help="source kind")
    parser.add_argument("--year", type=int, help="declared calendar year for the supplied source")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--replay", action="store_true", help="re-validate retained responses without network access")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.replay:
        if args.url or args.kind or args.year:
            raise SystemExit("--replay cannot be combined with --url/--kind/--year")
        root = args.output_root
        if not root.exists():
            raise SystemExit(f"--replay: evidence root does not exist and was not created: {root}")
        if not (root / "receipts.jsonl").is_file():
            raise SystemExit(f"--replay: no retained receipt journal under {root}")
        report = build_replay_report(root)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0

    if not args.url or not args.kind or args.year is None:
        raise SystemExit("--url, --kind and --year are required unless --replay is used")
    store = EvidenceStore(args.output_root)
    retriever = BoundedRetriever()
    receipt = collect_source(
        url=args.url,
        source_kind=args.kind,
        year=args.year,
        retriever=retriever,
        store=store,
    )
    print(json.dumps(build_report(receipt), sort_keys=True, indent=2))
    return 0 if receipt.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
