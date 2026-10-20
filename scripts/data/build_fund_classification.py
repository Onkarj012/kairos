"""CLI that marks every traded panel symbol as equity, fund or unknown and writes the review list."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Keep direct-file execution equivalent to `python -m scripts.data.build_fund_classification`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.daily_panel import PANEL_FILE, derived_root
from ingest.fund_classification import FUNDS_DIR, write_fund_classification


def _parser() -> argparse.ArgumentParser:
    """Command-line flags; defaults point into the retained derived-data root."""
    root = derived_root()
    parser = argparse.ArgumentParser(description="Classify panel symbols as equity, fund or unknown.")
    parser.add_argument("--panel", type=Path, default=root / "daily-runner/panel-v1" / PANEL_FILE)
    parser.add_argument("--out", type=Path, default=root / FUNDS_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the outputs and print the summary."""
    args = _parser().parse_args(argv)
    summary = write_fund_classification(args.panel, None, args.out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
