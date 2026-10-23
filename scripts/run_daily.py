"""CLI for the daily paper runner: the three scheduled jobs plus start, clear, status and replay."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Keep direct-file execution equivalent to `python -m scripts.run_daily`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO))

from ops.alerts import send_alert
from ops.daily_runner import IST, JOBS, Runner, clear, default_paths, now_ist, replay_check, start, status
from ops.kite_broker import DEFAULT_TOKEN_PATH, KiteSession, load_token
from ops.notify import Notifier, NotifyError, mac_notifier
from ops.telegram import BotApi, TelegramError, load_bot, read_env


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
    commands.add_parser("ping", help="send a test message on each enabled channel: Telegram and macOS")
    check = commands.add_parser("replay", help="replay 20 dev sessions and compare with run_backtest")
    check.add_argument("--out", type=Path, default=default_paths().runner / "replay-v1")
    return parser


def _ping(bot: BotApi | None, mac: Notifier | None) -> tuple[dict[str, str], int]:
    """Send a test message on each enabled channel; the exit code is 1 when one fails or none is enabled."""
    results: dict[str, str] = {}
    failed = False
    if bot is None:
        results["telegram"] = "not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
    else:
        try:
            bot.send("Kairos daily runner: Telegram is connected.")
            results["telegram"] = "sent"
        except TelegramError as exc:
            results["telegram"], failed = str(exc), True
    if mac is None:
        results["macos"] = "off: KAIROS_MAC_NOTIFY=0 in .env or the environment"
    else:
        try:
            mac.send("Kairos daily runner: macOS notifications are connected.")
            results["macos"] = "sent"
        except NotifyError as exc:
            results["macos"], failed = str(exc), True
    return results, 1 if failed or (bot is None and mac is None) else 0


def main(argv: list[str] | None = None) -> int:
    """Run one command and print its JSON result."""
    args = _parser().parse_args(argv)
    paths = default_paths()
    now = datetime.fromisoformat(args.now).replace(tzinfo=IST) if args.now else now_ist()
    env = read_env(REPO / ".env")
    bot = load_bot(REPO / ".env")
    mac = mac_notifier(env)
    if args.command in JOBS:
        access_token = load_token(DEFAULT_TOKEN_PATH, now.date())
        kite = KiteSession(env["KITE_API_KEY"], access_token) if env.get("KITE_API_KEY") and access_token else None
        runner = Runner(paths, now, telegram=bot, mac=mac, kite=kite)
        try:
            result: object = runner.run(args.command)
        except Exception as exc:
            # A crash records no run marker, so the next job halts; the alert says why.
            send_alert("error", f"daily runner {args.command} crashed", root=paths.runner, error=f"{type(exc).__name__}: {exc}")
            runner.post_halts()
            runner.notify(f"Daily runner {args.command} crashed: {type(exc).__name__}. The next job halts with missed_run.")
            raise
        code = 1 if isinstance(result, dict) and result.get("status") == "halted" else 0
    elif args.command == "start":
        result, code = start(paths, now, args.note), 0
    elif args.command == "clear":
        result, code = clear(paths, now, args.note, args.account), 0
    elif args.command == "status":
        result, code = status(paths), 0
    elif args.command == "ping":
        result, code = _ping(bot, mac)
    else:
        result = replay_check(paths, args.out)
        code = 0 if result["match"] else 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
