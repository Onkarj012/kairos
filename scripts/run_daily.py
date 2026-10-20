"""CLI for the daily paper runner: the three scheduled jobs plus start, clear, status and replay."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Keep direct-file execution equivalent to `python -m scripts.run_daily`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.daily_runner import IST, JOBS, Runner, clear, default_paths, now_ist, replay_check, start, status


def _parser() -> argparse.ArgumentParser:
    """Build the command line."""
    parser = argparse.ArgumentParser(description="Run the Kairos daily paper runner.")
    parser.add_argument("--now", help="IST timestamp override, YYYY-MM-DDTHH:MM, for manual runs and tests")
    commands = parser.add_subparsers(dest="command", required=True)
    for job in JOBS:
        commands.add_parser(job, help=f"run the scheduled {job} job")
    begin = commands.add_parser("start", help="open the paper accounts and reset the run-marker chain")
    begin.add_argument("--note", required=True)
    resume = commands.add_parser("clear", help="clear halts after a manual check")
    resume.add_argument("--note", required=True)
    resume.add_argument("--account", help="clear one account only")
    commands.add_parser("status", help="print each account's state")
    check = commands.add_parser("replay", help="replay 20 dev sessions and compare with run_backtest")
    check.add_argument("--out", type=Path, default=default_paths().runner / "replay-v1")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command and print its JSON result."""
    args = _parser().parse_args(argv)
    paths = default_paths()
    now = datetime.fromisoformat(args.now).replace(tzinfo=IST) if args.now else now_ist()
    if args.command in JOBS:
        result: object = Runner(paths, now).run(args.command)
        code = 1 if isinstance(result, dict) and result.get("status") == "halted" else 0
    elif args.command == "start":
        result, code = start(paths, now, args.note), 0
    elif args.command == "clear":
        result, code = clear(paths, now, args.note, args.account), 0
    elif args.command == "status":
        result, code = status(paths), 0
    else:
        result = replay_check(paths, args.out)
        code = 0 if result["match"] else 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
