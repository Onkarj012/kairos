"""All-series daily panel built from retained NSE cash bhavcopy bytes. No network access."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ingest.daily_cash import _rooted_reference, verify_retained_session
from ingest.nse_bhavcopy import _cash_frame

START = date(2019, 10, 1)
# NSE served the 2021-11-03 body for this Muhurat session, so it must never be used.
EXCLUDED_SESSIONS = {"2021-11-04": "muhurat"}
KEPT_SERIES = ("EQ", "BE", "BZ", "SM", "ST")
SERIES_PRIORITY = {series: rank for rank, series in enumerate(KEPT_SERIES)}
NUMERIC_COLUMNS = ("open", "high", "low", "close", "prev_close", "traded_value", "volume")
PANEL_COLUMNS = [
    "session_date",
    "security_id",
    "symbol",
    "series",
    "isin",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "traded_value",
    "volume",
    "adj_factor",
    "prev_close_factor",
]
PANEL_FILE = "panel.parquet"
MANIFEST_FILE = "manifest.json"
# A pinned XLSX body that NSE served under a .csv URL. Its receipt says rejected; we accept only these bytes.
PINNED_XLSX_SESSION = date(2022, 8, 8)
PINNED_XLSX_ROOT = "daily-graph/acquisition-qualification-2026-09-29-v1/cash-evidence"
PINNED_XLSX_RECEIPT = "requests/2022-08-08/nsearchives_full_bhavdata/attempt-001/receipt.json"
PINNED_XLSX_SHA256 = "431a71e2774243590bcdf24379599e8d299eae811dacce54af98fb3054194f7d"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Source:
    """One retained body whose bytes matched the recorded sha256."""

    session: date
    root: Path
    receipt: str
    body_sha256: str
    body: bytes


def derived_root() -> Path:
    """Root of retained derived data; override with KAIROS_DERIVED_DATA."""
    return Path(os.environ.get("KAIROS_DERIVED_DATA", "derived_data"))


def _json_bytes(value: Any) -> bytes:
    """Serialize a manifest with stable key order and no timestamps."""
    return (json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _expected_window(ledger: Path) -> list[date]:
    """Expected sessions from START through the ledger's last expected session."""
    expected: set[date] = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("state") == "expected":
            expected.add(date.fromisoformat(row["session_date"]))
    return sorted(session for session in expected if session >= START)


def _load_source(root: Path, receipt: str, session: date, body_sha256: str) -> _Source:
    """Read a body through its receipt and refuse it unless the recorded sha256 matches."""
    try:
        body_path = json.loads((root / receipt).read_text(encoding="utf-8"))["body_path"]
        # Older receipts store body_path under <root>/requests; _rooted_reference accepts both layouts.
        body = _rooted_reference(root, body_path, "original body").read_bytes()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{session.isoformat()}: retained receipt or body is unreadable") from exc
    if hashlib.sha256(body).hexdigest() != body_sha256:
        raise ValueError(f"{session.isoformat()}: body sha256 does not match the recorded hash")
    return _Source(session, root, receipt, body_sha256, body)


def _readiness_sources(readiness: Path, keep: set[date]) -> list[_Source]:
    """Sources the readiness inventory records for the kept sessions."""
    data = json.loads(readiness.read_text(encoding="utf-8"))
    entries = data["source_inventory"]["price_recovery"]["by_root_and_receipt"]
    sources: list[_Source] = []
    for entry in entries:
        session = date.fromisoformat(entry["session_date"])
        if session in keep:
            sources.append(_load_source(Path(entry["root"]), entry["receipt"], session, entry["body_sha256"]))
    return sources


def body_kind(body: bytes, session: date) -> str:
    """Tell a Full Bhavdata CSV, an XLSX, and a UDiFF ZIP apart from their bytes."""
    if not body.startswith(b"PK"):
        return "full_bhavdata"
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{session.isoformat()}: body starts with PK but is not a ZIP") from exc
    return "xlsx" if any(name.startswith("xl/worksheets/") for name in names) else "udiff"


def _xlsx_rows(body: bytes) -> list[list[str]]:
    """Stdlib XLSX reader: the first worksheet as rows of cell text."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        out: list[list[str]] = []
        for r in ET.fromstring(z.read(sheet)).iter(f"{{{NS['m']}}}row"):
            vals: dict[str, str] = {}
            for c in r.findall("m:c", NS):
                col = re.match(r"[A-Z]+", c.get("r")).group()
                v = c.find("m:v", NS)
                is_ = c.find("m:is", NS)
                if c.get("t") == "s" and v is not None:
                    vals[col] = shared[int(v.text)]
                elif is_ is not None:
                    vals[col] = "".join(t.text or "" for t in is_.iter(f"{{{NS['m']}}}t"))
                else:
                    vals[col] = v.text if v is not None else ""
            out.append([vals[k] for k in sorted(vals, key=lambda s: (len(s), s))])
    return out


def _xlsx_frame(body: bytes) -> pd.DataFrame:
    """Read the first worksheet of an XLSX body into a frame of cell text."""
    header, *data = _xlsx_rows(body)
    return pd.DataFrame(data, columns=header, dtype=object)


def _stripped(raw: pd.DataFrame) -> pd.DataFrame:
    """Strip column names and cell values; NSE pads both with spaces."""
    raw.columns = [str(column).lstrip("﻿").strip() for column in raw.columns]
    return raw.apply(lambda column: column.str.strip())


def _check_dates(values: pd.Series, date_format: str, session: date) -> None:
    """Refuse a body whose trade dates are not all the requested session."""
    dates = {datetime.strptime(value, date_format).date() for value in values.astype(str)}
    if dates != {session}:
        raise ValueError("trade dates do not all equal the session")


def _finish(frame: pd.DataFrame, session: date, turnover_scale: float) -> pd.DataFrame:
    """Keep the panel series and convert the kept rows to typed panel columns."""
    kept = frame[frame["series"].isin(KEPT_SERIES)]
    numbers = kept[list(NUMERIC_COLUMNS)].apply(pd.to_numeric).astype("float64")
    if numbers.isna().any().any():
        raise ValueError("blank or non-numeric price, volume, or turnover on a kept series row")
    if (numbers["volume"] % 1 != 0).any():
        raise ValueError("non-integer volume on a kept series row")
    return pd.DataFrame(
        {
            "session_date": session,
            "security_id": kept["symbol"],
            "symbol": kept["symbol"],
            "series": kept["series"],
            "isin": kept["isin"],
            "open": numbers["open"],
            "high": numbers["high"],
            "low": numbers["low"],
            "close": numbers["close"],
            "prev_close": numbers["prev_close"],
            "traded_value": numbers["traded_value"] * turnover_scale,
            "volume": numbers["volume"].astype("int64"),
            "adj_factor": 1.0,
            "prev_close_factor": 1.0,
        }
    )


def _full_bhavdata_frame(raw: pd.DataFrame, session: date) -> pd.DataFrame:
    """Map Full Bhavdata columns, from CSV or XLSX, onto the panel columns."""
    raw = _stripped(raw)
    _check_dates(raw["DATE1"], "%d-%b-%Y", session)
    return _finish(
        pd.DataFrame(
            {
                "symbol": raw["SYMBOL"],
                "series": raw["SERIES"],
                "isin": None,
                "open": raw["OPEN_PRICE"],
                "high": raw["HIGH_PRICE"],
                "low": raw["LOW_PRICE"],
                "close": raw["CLOSE_PRICE"],
                "prev_close": raw["PREV_CLOSE"],
                "volume": raw["TTL_TRD_QNTY"],
                "traded_value": raw["TURNOVER_LACS"],
            }
        ),
        session,
        turnover_scale=100000.0,
    )


def _udiff_frame(raw: pd.DataFrame, session: date) -> pd.DataFrame:
    """Map UDiFF cash columns, where traded value is already rupees, onto the panel columns."""
    raw = _stripped(raw)
    _check_dates(raw["TradDt"], "%Y-%m-%d", session)
    return _finish(
        pd.DataFrame(
            {
                "symbol": raw["TckrSymb"],
                "series": raw["SctySrs"],
                "isin": raw["ISIN"],
                "open": raw["OpnPric"],
                "high": raw["HghPric"],
                "low": raw["LwPric"],
                "close": raw["ClsPric"],
                "prev_close": raw["PrvsClsgPric"],
                "volume": raw["TtlTradgVol"],
                "traded_value": raw["TtlTrfVal"],
            }
        ),
        session,
        turnover_scale=1.0,
    )


def parse_body(body: bytes, session: date) -> pd.DataFrame:
    """Parse one retained body into kept-series panel rows, naming the session on failure."""
    kind = body_kind(body, session)
    try:
        if kind == "udiff":
            return _udiff_frame(_cash_frame(body), session)
        raw = _xlsx_frame(body) if kind == "xlsx" else pd.read_csv(io.BytesIO(body), dtype=str)
        return _full_bhavdata_frame(raw, session)
    except (ValueError, TypeError, KeyError, IndexError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{session.isoformat()}: cannot parse {kind} body: {exc}") from exc


def _resolve_duplicates(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per symbol and session by series priority; a repeated series raises."""
    repeated = frame.duplicated(["session_date", "symbol", "series"], keep=False)
    if repeated.any():
        row = frame[repeated].iloc[0]
        raise ValueError(f"{row['session_date'].isoformat()}: duplicate {row['symbol']} {row['series']} rows")
    ranked = frame.assign(_rank=frame["series"].map(SERIES_PRIORITY))
    ranked = ranked.sort_values(["session_date", "symbol", "_rank"])
    return ranked.drop_duplicates(["session_date", "symbol"], keep="first").drop(columns="_rank")


def _build_commit() -> str | None:
    """The HEAD commit of this checkout, or None when git is unavailable."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _source_record(source: _Source) -> dict[str, str]:
    """Manifest entry for one body that fed the panel."""
    return {
        "session_date": source.session.isoformat(),
        "kind": body_kind(source.body, source.session),
        "root": str(source.root),
        "receipt": source.receipt,
        "body_sha256": source.body_sha256,
    }


def apply_factors(frame: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Fill adj_factor and prev_close_factor from split and bonus events; raw prices stay as they are."""
    codes, uniques = pd.factorize(frame["security_id"])
    sessions = pd.to_datetime(frame["session_date"]).to_numpy()
    adj = np.ones(len(frame))
    day = np.ones(len(frame))
    for event in factors.itertuples(index=False):
        same = codes == uniques.get_loc(event.security_id)
        ex = pd.Timestamp(event.ex_session).to_datetime64()
        adj[same & (sessions < ex)] *= event.factor
        if not event.nse_prev_close_adjusted:
            # NSE left prev_close at the old level, so the ex-session prev_close needs this factor once.
            day[same & (sessions == ex)] *= event.factor
    return frame.assign(adj_factor=adj, prev_close_factor=adj * day)


def build_panel(
    readiness_path: Path,
    ledger_path: Path,
    cash_roots: Sequence[Path] = (),
    id_map: Mapping[str, str] | None = None,
    factors: pd.DataFrame | None = None,
    factor_sha256s: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the all-series panel and its manifest from retained bytes only; id_map maps symbol to security_id."""
    readiness = Path(readiness_path)
    window = _expected_window(Path(ledger_path))
    excluded = {date.fromisoformat(session) for session in EXCLUDED_SESSIONS}
    included = [session for session in window if session not in excluded]

    candidates = _readiness_sources(readiness, set(included))
    if PINNED_XLSX_SESSION in included:
        # The readiness file sits at <root>/daily-graph/<name>/readiness.json.
        pinned_root = readiness.parents[2] / PINNED_XLSX_ROOT
        candidates.append(_load_source(pinned_root, PINNED_XLSX_RECEIPT, PINNED_XLSX_SESSION, PINNED_XLSX_SHA256))
    found: dict[date, _Source] = {}
    for source in candidates:
        if source.session in found:
            raise ValueError(f"{source.session.isoformat()}: more than one retained body")
        found[source.session] = source
    for root in cash_roots:
        for session in included:
            if session in found:
                continue
            verified = verify_retained_session(root, session)
            if verified is not None:
                found[session] = _load_source(Path(verified["root"]), verified["receipt_path"], session, verified["body_sha256"])

    frames = [parse_body(found[session].body, session) for session in sorted(found)]
    frame = _resolve_duplicates(pd.concat(frames, ignore_index=True))
    frame = frame[PANEL_COLUMNS].sort_values(["session_date", "symbol"]).reset_index(drop=True)
    renames = None
    if id_map is not None:
        frame["security_id"] = frame["symbol"].map(lambda symbol: id_map.get(symbol, symbol))
        repeated = frame.duplicated(["session_date", "security_id"], keep=False)
        if repeated.any():
            row = frame[repeated].iloc[0]
            raise ValueError(f"{row['session_date'].isoformat()}: {row['security_id']} has two rows after the id map")
        renames = {
            "id_map_sha256": hashlib.sha256(_json_bytes(sorted(id_map.items()))).hexdigest(),
            "linked": len(id_map),
        }
    corporate_actions = None
    if factors is not None:
        frame = apply_factors(frame, factors)
        corporate_actions = {**(factor_sha256s or {}), "events_applied": len(factors)}
    manifest: dict[str, Any] = {
        "window": {"start": START.isoformat(), "end": window[-1].isoformat()},
        "expected_sessions": len(window),
        "present_sessions": len(found),
        "missing": [session.isoformat() for session in window if session not in found],
        "excluded": [{"session_date": session, "excluded": reason} for session, reason in sorted(EXCLUDED_SESSIONS.items())],
        "sources": [_source_record(found[session]) for session in sorted(found)],
        "row_count": len(frame),
        "series_counts": {str(series): int(count) for series, count in frame["series"].value_counts().sort_index().items()},
        "build_commit": _build_commit(),
        "renames": renames,
        "corporate_actions": corporate_actions,
    }
    return frame, manifest


def replace_directory(staging: Path, out_dir: Path) -> None:
    """Swap a fully written staging dir into out_dir, restoring the previous out_dir if the swap fails."""
    retired = out_dir.with_name(f".{out_dir.name}.retired")
    # An earlier run that died after moving out_dir aside leaves the only copy in retired.
    if retired.exists() and not out_dir.exists():
        os.rename(retired, out_dir)
    if retired.exists():
        shutil.rmtree(retired)
    had_previous = out_dir.exists()
    if had_previous:
        os.rename(out_dir, retired)
    try:
        os.rename(staging, out_dir)
    except BaseException:
        if had_previous:
            os.rename(retired, out_dir)
        raise
    if had_previous:
        shutil.rmtree(retired)


def write_panel(frame: pd.DataFrame, manifest: dict[str, Any], out_dir: Path) -> None:
    """Write the panel and manifest in a staging dir, then swap it over out_dir."""
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        frame.to_parquet(staging / PANEL_FILE, index=False, engine="pyarrow", compression="snappy")
        (staging / MANIFEST_FILE).write_bytes(_json_bytes(manifest))
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


__all__ = ["apply_factors", "build_panel", "derived_root", "parse_body", "replace_directory", "write_panel"]
