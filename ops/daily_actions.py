"""Nightly NSE corporate actions for the live panel, behind KAIROS_DAILY_ACTIONS=1.

Off by default. When on, the signal job asks NSE once, with no symbol, for every
equity's actions from the stage 0 coverage end to 30 days ahead. It keeps the raw
bytes and writes the factors from stage 0 plus the answer. An ex-date maps to a
session only when the previous live panel already has that session, so an action
is not applied until then. Until it is, the move guard halts or excludes any name
it cannot explain.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import pandas as pd

from ingest.adjustment_factors import EVENTS_FILE, SUMMARY_FILE, build_events, file_sha256, read_corporate_actions
from ingest.daily_panel import PANEL_FILE, replace_directory
from scripts.data.fetch_cohort_corporate_actions import ACCEPTED_SERIES, RECEIPT_SUFFIX, Member, _iso, _load_members, _write_attempt
from scripts.data.fetch_nse_corporate_actions import API_BASE, _NoRedirect, _action_type, _headers, _json_array, _revision_status, _sha256

if TYPE_CHECKING:
    from ops.daily_runner import RunnerPaths

SETTING = "KAIROS_DAILY_ACTIONS"
LOOKAHEAD_DAYS = 30
TIMEOUT_SECONDS = 30
RAW_DIR = "corporate-actions-nightly-v1"
STAGE0_DIR = "corporate-actions-v1"
FACTORS_DIR = "adjustments-live-v1"
OBSERVATION_COLUMNS = ["security_id", "symbol", "series", "subject", "ex_date", "record_date", "action_type_hint", "revision_status"]
Fetcher = Callable[[date, date], dict[str, Any]]


def enabled(env: Mapping[str, str]) -> bool:
    """Return whether the nightly request is switched on; only the value 1 turns it on."""
    return env.get(SETTING) == "1"


def request_range(start: date, end: date) -> dict[str, Any]:
    """Request every equity's corporate actions for a date range in one call, with no symbol."""
    url = f"{API_BASE}?index=equities&series=EQ&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
    request = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "Kairos/2.0"})
    result: dict[str, Any] = {"requested_url": url, "final_url": None, "status": None, "headers": [], "content_type": None, "retrieved_at": datetime.now(timezone.utc).isoformat(), "body": b"", "error": None}
    try:
        with build_opener(_NoRedirect).open(request, timeout=TIMEOUT_SECONDS) as response:
            result |= {"final_url": response.geturl(), "status": int(response.status), "headers": _headers(response), "content_type": response.headers.get("Content-Type"), "body": response.read()}
    except HTTPError as exc:
        result |= {"final_url": exc.geturl(), "status": exc.code, "headers": _headers(exc), "content_type": exc.headers.get("Content-Type"), "body": exc.read(), "error": f"HTTPError: {exc.code}"}
    except (OSError, HTTPException) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the rows of a successful JSON array response, or raise saying why it is unusable."""
    if result["error"] is not None or result["status"] != 200:
        raise ValueError(f"request failed: {result['error'] or result['status']}")
    if str(result["content_type"] or "").split(";", 1)[0].strip().casefold() != "application/json":
        raise ValueError(f"response is not JSON: {result['content_type']}")
    array, rows = _json_array(result["body"])
    if not array or not all(isinstance(row, dict) for row in rows):
        raise ValueError("response is not a JSON array of objects")
    return rows


def _observations(rows: list[dict[str, Any]], members: Mapping[str, Member]) -> pd.DataFrame:
    """Dated actions for ever-member symbols, shaped like the stage 0 observations."""
    kept = []
    for raw in rows:
        symbol = str(raw.get("symbol") or "").strip().upper()
        if symbol not in members or raw.get("series") not in ACCEPTED_SERIES:
            continue
        subject = str(raw.get("subject") or "").strip()
        kept.append({
            "security_id": members[symbol].security_id, "symbol": symbol, "series": raw.get("series"), "subject": subject,
            "ex_date": _iso(raw.get("exDate")), "record_date": _iso(raw.get("recDate")),
            "action_type_hint": _action_type(subject), "revision_status": _revision_status(subject),
        })
    return pd.DataFrame(kept, columns=OBSERVATION_COLUMNS)


def _write_factors(out_dir: Path, events: pd.DataFrame, hashes: Mapping[str, str]) -> Path:
    """Write the factor events and their summary to a staging dir, then swap it into out_dir."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        frame = events.assign(ex_session=pd.to_datetime(events["ex_session"]).dt.strftime("%Y-%m-%d"))
        frame.to_csv(staging / EVENTS_FILE, index=False, lineterminator="\n")
        summary = {"input_sha256": dict(hashes), "events": len(frame)}
        (staging / SUMMARY_FILE).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return out_dir


def merge_nightly(paths: RunnerPaths, today: date, fetch: Fetcher) -> Path | None:
    """Fetch tonight's range, keep the bytes, and return a factor dir from stage 0 plus the answer; None before the first live panel exists."""
    coverage_end = date.fromisoformat(json.loads(paths.actions_manifest.read_text(encoding="utf-8"))["range"]["end"])
    result = fetch(coverage_end, today + timedelta(days=LOOKAHEAD_DAYS))
    raw_dir = paths.runner / RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    _write_attempt(raw_dir, len(list(raw_dir.glob(f"*{RECEIPT_SUFFIX}"))) + 1, "all-equities", result)
    rows = _rows(result)
    panel_path = paths.live_panel_dir / PANEL_FILE
    if not panel_path.exists():
        return None
    stage0_dir = paths.runner / STAGE0_DIR
    observations = pd.concat([read_corporate_actions(stage0_dir), _observations(rows, _load_members(paths.covered_ids))], ignore_index=True)
    panel = pd.read_parquet(panel_path, columns=["security_id", "session_date", "close", "prev_close"])
    events, _review = build_events(observations, panel)
    usable = events.loc[events["nse_prev_close_adjusted"] != "ambiguous"]
    hashes = {"observations": file_sha256(stage0_dir / "observations.jsonl"), "nightly_response": _sha256(result["body"])}
    return _write_factors(paths.runner / FACTORS_DIR, usable, hashes)
