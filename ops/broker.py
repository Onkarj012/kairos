"""Paper broker and v1 live gate.

Paper execution is the default. Live execution requires the triple-key gate,
but v1 still refuses to place real orders even when the gate clears.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ops.halts import kill_switch_path


@dataclass(frozen=True)
class OrderTicket:
    ticket_id: str
    sleeve: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"
    limit_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    intended_for_live: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LiveExecutionGate:
    cli_live_flag: bool
    env_live_set: bool
    token_match: bool
    kill_switch_clear: bool
    detail: list[str]

    def is_clear(self) -> bool:
        return self.cli_live_flag and self.env_live_set and self.token_match and self.kill_switch_clear

    @classmethod
    def evaluate(
        cls,
        *,
        root: str | Path,
        sleeve: str,
        cli_live: bool = False,
        confirm_token_path: str | Path | None = None,
        env_var: str = "KAIROS_LIVE",
    ) -> "LiveExecutionGate":
        root = Path(root)
        detail: list[str] = []
        env_set = os.environ.get(env_var) == "1"
        detail.append(f"env {env_var} {'= 1' if env_set else '!= 1'}")
        reference = root / "LIVE_TOKEN"
        token_match = False
        if confirm_token_path is None:
            detail.append("--confirm-token not supplied")
        elif not reference.exists():
            detail.append(f"reference token missing at {reference}")
        elif not Path(confirm_token_path).exists():
            detail.append(f"confirm-token file not found: {confirm_token_path}")
        else:
            token_match = bool(reference.read_text().strip()) and reference.read_text().strip() == Path(confirm_token_path).read_text().strip()
            detail.append("token matches reference" if token_match else "token does not match reference")
        switch = kill_switch_path(root, sleeve)
        kill_clear = not switch.exists()
        detail.append("kill-switch clear" if kill_clear else f"kill-switch present: {switch}")
        detail.append("--live flag passed" if cli_live else "--live flag not passed")
        return cls(cli_live, env_set, token_match, kill_clear, detail)


class BrokerAdapter:
    def place_order(self, ticket: OrderTicket) -> dict:
        return {"status": "BROKER_BASE_NOOP", "ticket_id": ticket.ticket_id}


class PaperBrokerAdapter(BrokerAdapter):
    def __init__(self, ledger_path: str | Path):
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def place_order(self, ticket: OrderTicket) -> dict:
        order_id = f"PAPER-{uuid.uuid4().hex[:10]}"
        record = {
            "simulated_order_id": order_id,
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "ticket": ticket.to_dict(),
            "status": "PAPER_LOGGED",
        }
        with self.ledger_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return {"simulated_order_id": order_id, "status": "PAPER_LOGGED"}


class RefusingLiveBrokerAdapter(BrokerAdapter):
    def place_order(self, ticket: OrderTicket) -> dict:
        return {"status": "LIVE_REFUSED_V1", "ticket_id": ticket.ticket_id}


def make_broker(
    *,
    root: str | Path,
    sleeve: str,
    cli_live: bool = False,
    confirm_token_path: str | Path | None = None,
    paper_ledger_path: str | Path | None = None,
) -> tuple[BrokerAdapter, LiveExecutionGate]:
    gate = LiveExecutionGate.evaluate(
        root=root,
        sleeve=sleeve,
        cli_live=cli_live,
        confirm_token_path=confirm_token_path,
    )
    if gate.is_clear():
        return RefusingLiveBrokerAdapter(), gate
    ledger = paper_ledger_path or Path(root) / "broker" / f"{sleeve}_orders.jsonl"
    return PaperBrokerAdapter(ledger), gate
