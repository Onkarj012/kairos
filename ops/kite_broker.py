"""Build dry-run Kite orders from the paper account event logs."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from evidence.research_ledger import read_events
from ops.daily_account import DailyAccount, PAPER_ACCOUNTS

DEFAULT_TOKEN_PATH = Path.home() / ".config/kairos/kite-token.json"


class KiteError(RuntimeError):
    """A Kite request failed without exposing credentials."""


class KiteReader(Protocol):
    """Read the logged-in user's holdings."""

    def holdings(self) -> dict[str, int]: ...


def order_payload(side: str, security_id: str, qty: int) -> dict[str, object]:
    """Build the fixed CNC market order that dry-run mode would send."""
    if qty <= 0:
        raise ValueError("quantity must be positive")
    transaction_type = {"buy": "BUY", "sell": "SELL"}.get(side.lower())
    if transaction_type is None:
        raise ValueError("side must be buy or sell")
    return {
        "variety": "amo", "exchange": "NSE", "tradingsymbol": security_id,
        "transaction_type": transaction_type, "quantity": qty, "product": "CNC",
        "order_type": "MARKET", "market_protection": 2, "validity": "DAY", "tag": "kairos",
    }


def save_token(path: Path, access_token: str, user_id: str, login_date: date) -> None:
    """Write one private token record, replacing it atomically."""
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"access_token": access_token, "user_id": user_id, "login_date": login_date.isoformat()}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_token(path: Path, today: date) -> str | None:
    """Return today's token, if a valid token record exists."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if data.get("login_date") == today.isoformat() and isinstance(data.get("access_token"), str):
            return data["access_token"]
    except (OSError, ValueError, AttributeError):
        return None
    return None


@dataclass(frozen=True)
class KiteSession:
    """Read holdings through Kite Connect using one day's access token."""

    api_key: str
    access_token: str

    def __repr__(self) -> str:
        return "KiteSession()"

    def holdings(self) -> dict[str, int]:
        """Return net holdings by exchange tradingsymbol."""
        try:
            from kiteconnect import KiteConnect

            client = KiteConnect(api_key=self.api_key)
            client.set_access_token(self.access_token)
            return {
                str(row["tradingsymbol"]): int(row.get("quantity", 0)) + int(row.get("t1_quantity", 0))
                for row in client.holdings()
            }
        except Exception as exc:
            raise KiteError(f"Kite holdings failed: {type(exc).__name__}") from None


def dry_run_place(accounts_root: Path, log_dir: Path, session: date, reader: KiteReader | None,
                  ready: set[str] | None = None) -> list[dict[str, object]]:
    """Write this session's paper orders as dry-run JSON lines and return them; ``ready`` limits it to accounts the job placed for."""
    holdings = reader.holdings() if reader is not None else None
    rows: list[dict[str, object]] = []
    day = session.isoformat()
    for spec in PAPER_ACCOUNTS:
        if spec.capital != 50_000 or (ready is not None and spec.account_id not in ready):
            continue
        path = Path(accounts_root) / spec.account_id / "events.jsonl"
        if not path.exists():
            continue
        events = read_events(path)
        sent_rows = {
            str(event.payload.get("intent_id")): event.payload
            for event in events
            if event.event_type == "ORDER_SENT" and event.payload.get("session") == day
        }
        for intent, event in sent_rows.items():
            rows.append({
                "account_id": spec.account_id, "intent_id": intent, "payload": order_payload("buy", str(event["security_id"]), int(event["qty"])),
                "paper_qty": int(event["qty"]), "holdings_qty": None, "mode": "dry_run",
            })
        state = DailyAccount(accounts_root, spec).view().state
        for holding in state.holdings:
            # A holding still open past its exit day had no price to exit on; it sells at the next open, so it is logged again.
            if holding.exit_due > day:
                continue
            quantity = holdings.get(holding.security_id, 0) if holdings is not None else 0
            qty = quantity if quantity > 0 else holding.qty
            rows.append({
                "account_id": spec.account_id, "entry": f"{holding.entry_session}:{holding.security_id}",
                "payload": order_payload("sell", holding.security_id, qty), "paper_qty": holding.qty,
                "holdings_qty": holdings.get(holding.security_id, 0) if holdings is not None else None,
                "mode": "dry_run",
            })
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{day}.jsonl"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=log_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return rows
