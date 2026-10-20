"""Append-only per-sleeve paper ledger with fill divergence tracking."""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_COLUMNS=["ledger_id","sleeve","signal_ts","model_version","symbol","side","quantity","intended_price","simulated_fill_price","backtest_expected_fill_price","fill_divergence","costs","exit_ts","exit_price","pnl","status","created_at","metadata_json"]

@dataclass(frozen=True)
class LedgerEntry:
    sleeve: str
    signal_ts: str | datetime
    model_version: str
    symbol: str
    side: str
    quantity: float
    intended_price: float
    simulated_fill_price: float
    backtest_expected_fill_price: float
    costs: float = 0.0
    exit_ts: str | datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None
    status: str = "OPEN"
    metadata_json: str = "{}"
    ledger_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_row(self, next_id: str) -> dict[str, Any]:
        row=asdict(self); row["ledger_id"]=self.ledger_id or next_id
        row["signal_ts"]=_iso(self.signal_ts); row["exit_ts"]=None if self.exit_ts is None else _iso(self.exit_ts)
        row["fill_divergence"]=float(self.simulated_fill_price)-float(self.backtest_expected_fill_price)
        return row

def ledger_path(root: str | Path, sleeve: str) -> Path:
    return Path(root)/"paper_ledger"/f"{sleeve}.csv"

def load_ledger(path: str | Path) -> list[dict[str, Any]]:
    p=Path(path)
    if not p.exists(): return []
    with p.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))

def append_ledger_entry(path: str | Path, entry: LedgerEntry | dict[str, Any]) -> dict[str, Any]:
    p=Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    existing=load_ledger(p); next_id=_next_ledger_id(existing)
    if isinstance(entry, dict): entry=LedgerEntry(**entry)
    row=entry.to_row(next_id)
    write_header=not p.exists() or p.stat().st_size==0
    with p.open("a", newline="", encoding="utf-8") as handle:
        writer=csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS);
        if write_header: writer.writeheader()
        writer.writerow({key: row.get(key) for key in LEDGER_COLUMNS})
    return row

def summarize_pnl(path: str | Path) -> dict[str, float | int]:
    rows=load_ledger(path); pnl=sum(float(row.get("pnl") or 0) for row in rows)
    return {"rows": len(rows), "pnl": pnl}

def _next_ledger_id(rows: list[dict[str, Any]]) -> str:
    return f"L{len(rows)+1:06d}"

def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)
