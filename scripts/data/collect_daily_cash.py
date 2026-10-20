"""CLI for explicit-date, resumable NSE Full Bhavdata retention."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Keep direct-file execution equivalent to `python -m scripts.data.collect_daily_cash`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.daily_acquisition_checkpoint import CheckpointError, aggregate_seed_counts
from ingest.daily_cash import (
    CASH_URL_ROUTES,
    DEFAULT_ROUTE,
    collect_daily_cash,
    route_url,
    verify_retained_session,
)

DEFAULT_SEED_BUDGET = Path(".scratch/daily-graph-system/network-budget-state.json")
PLAN_ATTEMPT_LIMIT = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Retain one or more explicitly requested NSE Full Bhavdata sessions.")
    parser.add_argument("--date", dest="dates", action="append", required=True, help="Session date YYYY-MM-DD; repeat explicitly for more dates.")
    parser.add_argument("--output-root", type=Path, default=Path("derived_data/daily-graph/cash-evidence-v1"))
    parser.add_argument("--resume", action="store_true", help="explicitly retry a prior terminal validation rejection")
    parser.add_argument(
        "--route",
        choices=sorted(CASH_URL_ROUTES),
        default=DEFAULT_ROUTE,
        help="allowlisted official host route for the same Full Bhavdata filename; each route has its own budget",
    )
    parser.add_argument(
        "--seed-budget",
        type=Path,
        default=DEFAULT_SEED_BUDGET,
        help="durable per-URL budget summary (audit + retained reservations) used to fail closed on known exhaustion",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        known = aggregate_seed_counts(args.seed_budget, (args.output_root,))
    except CheckpointError as exc:
        raise SystemExit(f"shared budget summary is malformed; refusing to acquire: {exc}") from exc
    results = []
    for value in args.dates:
        requested = date.fromisoformat(value)
        route = CASH_URL_ROUTES[args.route]
        if verify_retained_session(args.output_root, requested, route=args.route) is None:
            url = route_url(route, requested)
            attempts = known.get(url, 0)
            if attempts >= PLAN_ATTEMPT_LIMIT:
                raise SystemExit(
                    f"refusing {requested.isoformat()}: shared per-URL budget for {url} is "
                    f"durably exhausted ({attempts}/{PLAN_ATTEMPT_LIMIT}); use a retained artifact "
                    "or a different allowlisted route"
                )
        result = collect_daily_cash(requested, args.output_root, resume=args.resume, route=args.route)
        results.append(
            {
                "requested_date": result.requested_date.isoformat(),
                "status": result.status,
                "attempts": result.attempts,
                "accepted_path": str(result.accepted_path) if result.accepted_path else None,
                "pointer_updated": result.pointer_updated,
                "reason": result.reason,
            }
        )
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(item["status"] == "accepted" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
