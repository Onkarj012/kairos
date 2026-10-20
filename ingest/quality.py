"""Data-quality checks for ingested market data."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class ZeroVolumeReport:
    checked_rows: int
    zero_volume_rows: int
    zero_volume_ratio: float
    symbols: list[str]
    def to_dict(self) -> dict: return asdict(self)

@dataclass(frozen=True)
class GapReport:
    checked_rows: int
    expected_rows: int
    missing_rows: int
    first_missing: str | None
    last_missing: str | None
    def to_dict(self) -> dict: return asdict(self)

@dataclass(frozen=True)
class FreshnessStamp:
    source: str
    last_timestamp: str | None
    checked_at: str
    age_seconds: float | None
    stale: bool
    max_age_seconds: float | None
    def to_dict(self) -> dict: return asdict(self)

def _records(rows):
    if rows is None: return []
    if isinstance(rows, dict):
        keys=list(rows)
        return [dict(zip(keys, values)) for values in zip(*(rows[k] for k in keys))]
    return [dict(row) for row in rows]

def zero_volume_report(rows, *, volume_col="volume", symbol_col="symbol") -> ZeroVolumeReport:
    data=_records(rows)
    zero=[row for row in data if float(row.get(volume_col,0) or 0)==0]
    symbols=sorted({str(row.get(symbol_col)) for row in zero if row.get(symbol_col) is not None})
    return ZeroVolumeReport(len(data), len(zero), len(zero)/len(data) if data else 0.0, symbols)

def _dt(value):
    if isinstance(value, datetime): return value
    return datetime.fromisoformat(str(value).replace('Z','+00:00'))

def gap_report(rows, *, timestamp_col="date", freq="1min") -> GapReport:
    data=sorted([_dt(row[timestamp_col]) for row in _records(rows) if row.get(timestamp_col)], key=lambda x:x)
    if not data: return GapReport(0,0,0,None,None)
    step=60 if freq in {"1min","min","1T"} else 60
    expected=[]; cur=data[0]
    while cur<=data[-1]:
        expected.append(cur); cur=cur.fromtimestamp(cur.timestamp()+step)
    actual={x for x in data}
    missing=[x for x in expected if x not in actual]
    return GapReport(len(data), len(expected), len(missing), missing[0].isoformat() if missing else None, missing[-1].isoformat() if missing else None)

def freshness_stamp(rows, *, timestamp_col="date", source, now=None, max_age_seconds=None) -> FreshnessStamp:
    checked_at=_dt(now) if now is not None else datetime.now(timezone.utc)
    data=[_dt(row[timestamp_col]) for row in _records(rows) if row.get(timestamp_col)]
    if not data: return FreshnessStamp(source, None, checked_at.isoformat(), None, True, max_age_seconds)
    last=max(data)
    age=(checked_at-last).total_seconds()
    stale=bool(max_age_seconds is not None and age>max_age_seconds)
    return FreshnessStamp(source, last.isoformat(), checked_at.isoformat(), float(age), stale, max_age_seconds)
