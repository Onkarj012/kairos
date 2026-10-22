"""Nightly NSE corporate actions for the live panel, behind KAIROS_DAILY_ACTIONS=1.

Off by default. When on, the signal job asks NSE once, with no symbol, for every
equity's actions from the stage 0 coverage end to 30 days ahead. It keeps the raw
bytes and writes the factors from stage 0 plus the answer. Ex-dates map to sessions
on tonight's panel, which the job builds with stage 0 factors first, so an action
whose ex-session is tonight applies tonight. If the factors change, the job builds
the panel again with them. A revision and an original of one kind dated up to 90
days apart, or a conflicting ratio, fail the merge: the job keeps stage 0 factors
and alerts. The move guard still halts or excludes any name it cannot explain.
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

from ingest.adjustment_factors import EVENTS_FILE, SUMMARY_FILE, _parsed_actions, build_events, file_sha256, read_corporate_actions
from ingest.daily_panel import PANEL_FILE, replace_directory
from scripts.data.fetch_cohort_corporate_actions import ACCEPTED_SERIES, Member, _iso, _load_members, _write_attempt
from scripts.data.fetch_nse_corporate_actions import API_BASE, _NoRedirect, _action_type, _headers, _json_array, _revision_status, _sha256

if TYPE_CHECKING:
    from ops.daily_runner import RunnerPaths

SETTING = "KAIROS_DAILY_ACTIONS"
LOOKAHEAD_DAYS = 30
REVISION_WINDOW = pd.Timedelta(days=90)
REVISION_PAIR = ("revised", "original_or_unspecified")
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


def _next_sequence(raw_dir: Path) -> int:
    """One past the highest sequence on any retained body or receipt, so a body left without a receipt is never reused."""
    return 1 + max((int(path.name.split("_", 1)[0]) for path in raw_dir.glob("*.json")), default=0)


def _dated_actions(observations: pd.DataFrame) -> pd.DataFrame:
    """Parsed splits and bonuses that are originals or revisions, with the ex-date as a timestamp."""
    actions = _parsed_actions(observations)
    actions = actions.loc[actions["revision_status"].isin(REVISION_PAIR)]
    return actions.assign(day=pd.to_datetime(actions["ex_date"], errors="coerce"))


def _check_revisions(nightly: pd.DataFrame, observations: pd.DataFrame) -> None:
    """Raise when a nightly action and an action of its kind dated up to 90 days away are one revised and one original."""
    known = _dated_actions(observations)
    for action in _dated_actions(nightly).itertuples(index=False):
        other = known.loc[
            known["security_id"].eq(action.security_id)
            & known["kind"].eq(action.kind)
            & known["revision_status"].ne(action.revision_status)
            & known["day"].ne(action.day)
            & known["day"].sub(action.day).abs().le(REVISION_WINDOW)
        ]
        if not other.empty:
            raise ValueError(f"security_id {action.security_id}: a revised and an original {action.kind} both survive, dated {action.ex_date} and {other.iloc[0]['ex_date']}")


def _check_review(review: pd.DataFrame, stage0_review: pd.DataFrame) -> None:
    """Raise naming the securities whose conflicting or ambiguous action is new since stage 0."""
    known = stage0_review[["security_id", "ex_date", "reason"]].drop_duplicates()
    flagged = review.loc[review["reason"].isin(["conflicting_factors", "ambiguous_prev_close"])]
    added = flagged.merge(known, on=["security_id", "ex_date", "reason"], how="left", indicator=True).query('_merge == "left_only"')
    if not added.empty:
        raise ValueError(f"conflicting or ambiguous corporate action for security_id {', '.join(sorted(set(added['security_id'])))}")


def _usable(events: pd.DataFrame) -> pd.DataFrame:
    """The events a factor dir keeps: those whose prev_close state is not ambiguous."""
    return events.loc[events["nse_prev_close_adjusted"] != "ambiguous"]


def merge_nightly(paths: RunnerPaths, today: date, fetch: Fetcher) -> Path | None:
    """Fetch tonight's range and keep the bytes; return a factor dir only when stage 0 plus the answer change its events."""
    coverage_end = date.fromisoformat(json.loads(paths.actions_manifest.read_text(encoding="utf-8"))["range"]["end"])
    result = fetch(coverage_end, today + timedelta(days=LOOKAHEAD_DAYS))
    raw_dir = paths.runner / RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    _write_attempt(raw_dir, _next_sequence(raw_dir), "all-equities", result)
    rows = _rows(result)
    stage0_dir = paths.runner / STAGE0_DIR
    # An empty stage 0 file has no columns, so reindex gives it the shape the build reads.
    stage0 = read_corporate_actions(stage0_dir).reindex(columns=OBSERVATION_COLUMNS)
    nightly = _observations(rows, _load_members(paths.covered_ids))
    observations = pd.concat([stage0, nightly], ignore_index=True)
    _check_revisions(nightly, observations)
    panel = pd.read_parquet(paths.live_panel_dir / PANEL_FILE, columns=["security_id", "session_date", "close", "prev_close"])
    events, review = build_events(observations, panel)
    stage0_events, stage0_review = build_events(stage0, panel)
    _check_review(review, stage0_review)
    usable = _usable(events)
    if usable.to_csv(index=False) == _usable(stage0_events).to_csv(index=False):
        return None
    hashes = {"observations": file_sha256(stage0_dir / "observations.jsonl"), "nightly_response": _sha256(result["body"])}
    return _write_factors(paths.runner / FACTORS_DIR, usable, hashes)
