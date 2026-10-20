"""Kite market-data ingest boundary.

Secrets are read from environment variables only. The optional ``kiteconnect``
dependency is imported lazily so offline tests do not need broker SDKs.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ingest.quality import freshness_stamp, gap_report, zero_volume_report


def data_root() -> Path:
    return Path(os.environ.get("KAIROS_DATA_ROOT", "./data"))


@dataclass(frozen=True)
class SourceStamp:
    source: str
    fetched_at: str
    instrument_token: int | None = None
    interval: str | None = None


def _kite_connect_class() -> type:
    try:
        from kiteconnect import KiteConnect  # type: ignore
    except ImportError as exc:
        raise RuntimeError("kiteconnect is required only for real Kite ingest") from exc
    return KiteConnect


class KiteClient:
    """Thin, env-driven wrapper around Zerodha Kite historical data."""

    def __init__(self, api_key: str, api_secret: str = "", access_token: str = ""):
        KiteConnect = _kite_connect_class()
        self.api_key = api_key
        self.api_secret = api_secret
        self.kite = KiteConnect(api_key=api_key)
        if access_token:
            self.kite.set_access_token(access_token)

    @classmethod
    def from_env(cls) -> "KiteClient":
        api_key = os.environ.get("KITE_API_KEY")
        if not api_key:
            raise RuntimeError("KITE_API_KEY is required for Kite ingest")
        return cls(
            api_key=api_key,
            api_secret=os.environ.get("KITE_API_SECRET", ""),
            access_token=os.environ.get("KITE_ACCESS_TOKEN", ""),
        )

    def set_access_token(self, token: str) -> None:
        self.kite.set_access_token(token)

    def instruments(self, exchange: str) -> pd.DataFrame:
        return pd.DataFrame(self.kite.instruments(exchange))

    def nse_tokens(self, symbols: list[str]) -> dict[str, int]:
        df = self.instruments("NSE")
        if df.empty:
            return {}
        if "segment" in df.columns:
            df = df[df["segment"] == "NSE"]
        mapping = df.set_index("tradingsymbol")["instrument_token"].to_dict()
        return {sym: int(mapping[sym]) for sym in symbols if sym in mapping}

    def nearest_future_token(self, symbol: str, expiry: date | None = None) -> tuple[int, date]:
        df = self.instruments("NFO")
        futs = df[(df["name"] == symbol) & (df["instrument_type"] == "FUT")].copy()
        futs["expiry"] = pd.to_datetime(futs["expiry"]).dt.date
        futs = futs[futs["expiry"] >= date.today()].sort_values("expiry")
        if expiry is not None:
            futs = futs[futs["expiry"] == expiry]
        if futs.empty:
            raise ValueError(f"No live FUT contract found for {symbol}")
        row = futs.iloc[0]
        return int(row["instrument_token"]), row["expiry"]

    def fetch_bars(
        self,
        instrument_token: int,
        from_dt: datetime,
        to_dt: datetime,
        *,
        interval: str = "minute",
        oi: bool = False,
        chunk_days: int = 59,
        sleep_seconds: float = 0.35,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        cursor = from_dt
        while cursor < to_dt:
            chunk_end = min(cursor + timedelta(days=chunk_days), to_dt)
            candles = self.kite.historical_data(
                instrument_token,
                cursor.strftime("%Y-%m-%d %H:%M:%S"),
                chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                interval,
                oi=oi,
            )
            if candles:
                frames.append(pd.DataFrame(candles))
            cursor = chunk_end + timedelta(seconds=1)
            if sleep_seconds:
                time.sleep(sleep_seconds)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        df.attrs["source_stamp"] = SourceStamp(
            source="kite",
            fetched_at=pd.Timestamp.now("UTC").isoformat(),
            instrument_token=instrument_token,
            interval=interval,
        ).__dict__
        df.attrs["quality"] = {
            "zero_volume": zero_volume_report(df).to_dict(),
            "gaps": gap_report(df, timestamp_col="date", freq="1min" if interval == "minute" else None).to_dict(),
            "freshness": freshness_stamp(df, timestamp_col="date", source="kite").to_dict(),
        }
        return df


def save_bars(df: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    if not out.is_absolute():
        out = data_root() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".parquet":
        df.to_parquet(out, index=False)
    else:
        df.to_csv(out, index=False)
    return out


def token_refresh_check() -> dict[str, Any]:
    return {
        "has_api_key": bool(os.environ.get("KITE_API_KEY")),
        "has_access_token": bool(os.environ.get("KITE_ACCESS_TOKEN")),
    }
