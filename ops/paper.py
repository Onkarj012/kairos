"""Paper-trading lifecycle service over the canonical CSV ledger."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.ledger import LedgerEntry, append_ledger_entry, ledger_path, load_ledger


@dataclass(frozen=True)
class PaperSignal:
    sleeve: str
    signal_ts: str | datetime
    model_version: str
    symbol: str
    side: str
    quantity: float
    intended_price: float
    backtest_expected_fill_price: float
    simulated_fill_price: float | None = None
    costs: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def append_signal_to_ledger(root: str | Path, signal: PaperSignal | dict[str, Any]) -> dict[str, Any]:
    signal = signal if isinstance(signal, PaperSignal) else PaperSignal(**signal)
    path = ledger_path(root, signal.sleeve)
    rows = load_ledger(path)
    key = _signal_key(asdict(signal))
    for row in rows:
        if _signal_key(row) == key:
            return {"status": "DUPLICATE_SIGNAL", "duplicate": True, "ledger_id": row.get("ledger_id"), "signal_key": key}

    previous_hash = _last_chain_hash(rows)
    metadata = dict(signal.metadata)
    metadata.update({"signal_key": key, "previous_hash": previous_hash})
    fill_price = signal.simulated_fill_price if signal.simulated_fill_price is not None else signal.intended_price
    entry_kwargs = dict(
        sleeve=signal.sleeve,
        signal_ts=signal.signal_ts,
        model_version=signal.model_version,
        symbol=signal.symbol,
        side=signal.side,
        quantity=signal.quantity,
        intended_price=signal.intended_price,
        simulated_fill_price=fill_price,
        backtest_expected_fill_price=signal.backtest_expected_fill_price,
        costs=signal.costs,
        status="OPEN",
        metadata_json=json.dumps(metadata, sort_keys=True),
    )
    provisional = LedgerEntry(**entry_kwargs).to_row(f"L{len(rows)+1:06d}")
    metadata["chain_hash"] = _chain_hash(provisional, previous_hash)
    entry = LedgerEntry(**{**entry_kwargs, "metadata_json": json.dumps(metadata, sort_keys=True)})
    row = append_ledger_entry(path, entry)
    row["status"] = "OPEN"
    row["duplicate"] = False
    return row


def close_paper_trade(root: str | Path, *, sleeve: str, ledger_id: str, exit_price: float, exit_ts: str | datetime | None = None, costs: float | None = None) -> dict[str, Any]:
    path = ledger_path(root, sleeve)
    rows = load_ledger(path)
    updated: dict[str, Any] | None = None
    out = []
    for row in rows:
        if row.get("ledger_id") == ledger_id:
            side = str(row.get("side", "")).upper()
            qty = float(row.get("quantity") or 0)
            fill = float(row.get("simulated_fill_price") or 0)
            total_costs = float(row.get("costs") or 0) if costs is None else float(costs)
            multiplier = -1.0 if side in {"SELL", "SHORT"} else 1.0
            pnl = round((float(exit_price) - fill) * qty * multiplier - total_costs, 10)
            row = {**row, "exit_ts": _iso(exit_ts or datetime.now(timezone.utc)), "exit_price": exit_price, "pnl": pnl, "costs": total_costs, "status": "CLOSED"}
            metadata = _metadata(row)
            metadata["closed_at_hash_basis"] = datetime.now(timezone.utc).isoformat()
            row["metadata_json"] = json.dumps(metadata, sort_keys=True)
            updated = row
        out.append(row)
    if updated is None:
        raise KeyError(f"ledger_id not found: {ledger_id}")
    _write_rows(path, out)
    return updated


def paper_lifecycle_summary(root: str | Path, sleeve: str) -> dict[str, Any]:
    rows = load_ledger(ledger_path(root, sleeve))
    return {
        "sleeve": sleeve,
        "trades": len(rows),
        "open": sum(1 for row in rows if row.get("status") == "OPEN"),
        "closed": sum(1 for row in rows if row.get("status") == "CLOSED"),
        "pnl": sum(float(row.get("pnl") or 0) for row in rows),
        "max_abs_fill_divergence": max([abs(float(row.get("fill_divergence") or 0)) for row in rows] or [0.0]),
    }


def _signal_key(data: dict[str, Any]) -> str:
    parts = [data.get("sleeve"), _iso(data.get("signal_ts")), data.get("symbol"), data.get("side"), data.get("model_version")]
    return "|".join(str(part) for part in parts)


def _last_chain_hash(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "GENESIS"
    return _metadata(rows[-1]).get("chain_hash", "")


def _chain_hash(row: dict[str, Any], previous_hash: str) -> str:
    payload = {key: row.get(key) for key in sorted(row) if key != "metadata_json"}
    payload["previous_hash"] = previous_hash
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_metadata_json": raw}


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv
    from ops.ledger import LEDGER_COLUMNS

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in LEDGER_COLUMNS})


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)
