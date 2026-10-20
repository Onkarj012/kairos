"""CLI that turns retained split and bonus actions into panel price factors and review lists."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

# Keep direct-file execution equivalent to `python -m scripts.data.build_adjustment_factors`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.adjustment_factors import (
    EVENTS_FILE,
    REVIEW_MOVES_FILE,
    REVIEW_PURPOSES_FILE,
    SUMMARY_FILE,
    adjusted_return,
    build_events,
    factors_frame,
    file_sha256,
    read_corporate_actions,
    review_moves,
)
from ingest.daily_panel import PANEL_FILE, _expected_window, apply_factors, derived_root, replace_directory

MOVE_CHECK_SESSION = pd.Timestamp("2026-08-24").date()
MOVE_CHECK_SYMBOL = "TDPOWERSYS"


def _parser() -> argparse.ArgumentParser:
    """Command-line flags; defaults point into the retained derived-data root."""
    root = derived_root()
    parser = argparse.ArgumentParser(description="Build split and bonus adjustment factors and their review lists.")
    parser.add_argument("--panel", type=Path, default=root / "daily-runner/panel-v1" / PANEL_FILE, help="unadjusted panel to derive factors from")
    parser.add_argument("--corporate-actions", type=Path, default=root / "daily-runner/corporate-actions-v1", help="corporate-actions dir; its hashes are checked first")
    parser.add_argument("--cohort", type=Path, default=root / "daily-runner/cohort-v1/cohort.parquet")
    parser.add_argument("--ledger", type=Path, default=root / "daily-runner/session-ledger-v1/sessions.jsonl")
    parser.add_argument("--out", type=Path, default=root / "daily-runner/adjustments-v1")
    return parser


def _counts(values: pd.Series) -> dict[str, int]:
    """Value counts as a sorted plain dict for JSON."""
    return {str(key): int(count) for key, count in values.value_counts().sort_index().items()}


def _summary(events: pd.DataFrame, review: pd.DataFrame, moves: pd.DataFrame, adjusted: pd.DataFrame, input_sha256: dict[str, str]) -> dict[str, Any]:
    """Counts and checks for summary.json."""
    check = adjusted.loc[(adjusted["symbol"] == MOVE_CHECK_SYMBOL) & (adjusted["session_date"] == MOVE_CHECK_SESSION)]
    return {
        "events_applied": len(events),
        "events_by_kind": _counts(events["kind"]),
        "prev_close_adjusted": _counts(events["nse_prev_close_adjusted"]),
        "review_purposes": _counts(review["reason"]),
        "review_moves": {"total": len(moves), "by_reason": _counts(moves["reason"])},
        "tdpowersys_2026_08_24_adjusted_r": None if check.empty else float(adjusted_return(check).iloc[0]),
        "input_sha256": input_sha256,
    }


def _write(out: Path, files: dict[str, pd.DataFrame | dict[str, Any]]) -> None:
    """Write the outputs in a staging dir, then swap it over out."""
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        for name, content in files.items():
            if isinstance(content, pd.DataFrame):
                content.to_csv(staging / name, index=False, lineterminator="\n")
            else:
                (staging / name).write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        replace_directory(staging, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Build the factor events, review lists and summary, then print the summary."""
    args = _parser().parse_args(argv)
    panel = pd.read_parquet(args.panel)
    observations = read_corporate_actions(args.corporate_actions)
    events, review = build_events(observations, panel)
    panel_events = events.loc[events["nse_prev_close_adjusted"] != "ambiguous"]
    adjusted = apply_factors(panel, factors_frame(panel_events))
    cohort = pd.read_parquet(args.cohort)
    moves = review_moves(adjusted, cohort, observations, events, _expected_window(args.ledger))
    input_sha256 = {
        "observations": file_sha256(args.corporate_actions / "observations.jsonl"),
        "cohort": file_sha256(args.cohort),
        "ledger": file_sha256(args.ledger),
        "panel": file_sha256(args.panel),
    }
    summary = _summary(events, review, moves, adjusted, input_sha256)
    _write(args.out, {EVENTS_FILE: events, REVIEW_PURPOSES_FILE: review, REVIEW_MOVES_FILE: moves, SUMMARY_FILE: summary})
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
