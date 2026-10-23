"""Split and bonus price factors from retained NSE corporate actions, with review lists. No network access."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

EVENTS_FILE = "events.csv"
REVIEW_PURPOSES_FILE = "review_purposes.csv"
REVIEW_MOVES_FILE = "review_moves.csv"
SUMMARY_FILE = "summary.json"
CORPORATE_ACTIONS_MANIFEST = "manifest.json"
MOVE_THRESHOLD = 0.35
SPLIT_RE = re.compile(r"face\s*value\s*split.*?\bfrom\s*(?:rs|re)\.?\s*(\d+(?:\.\d+)?).*?\bto\s*(?:rs|re)\.?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
BONUS_RE = re.compile(r"bonus[\s-]*(\d+)\s*:\s*(\d+)", re.IGNORECASE)
ACTION_WORDS = r"split|bonus|consolidat"
EVENT_COLUMNS = ["security_id", "symbol", "ex_date", "ex_session", "subject", "kind", "factor", "c0", "pc", "close", "nse_prev_close_adjusted"]
REVIEW_COLUMNS = ["security_id", "symbol", "ex_date", "subject", "reason"]
MOVE_COLUMNS = ["security_id", "symbol", "session_date", "r", "close", "prev_close", "reason"]


@dataclass(frozen=True)
class Purpose:
    """A split or bonus and the price factor it implies."""

    kind: str
    factor: float


def parse_purpose(subject: str) -> Purpose | None:
    """Price factor for a split or bonus subject, or None when it has no equity share ratio."""
    text = " ".join(subject.split())
    if split := SPLIT_RE.search(text):
        old, new = float(split[1]), float(split[2])
        return Purpose("split", new / old) if old > 0 and new > 0 else None
    if bonus := BONUS_RE.search(text):
        new_shares, held = int(bonus[1]), int(bonus[2])
        return Purpose("bonus", held / (new_shares + held)) if new_shares > 0 and held > 0 else None
    return None


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_observations(path: Path) -> pd.DataFrame:
    """Corporate-action observations, one row per JSON line."""
    return pd.DataFrame([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()])


def read_corporate_actions(directory: Path) -> pd.DataFrame:
    """Observations from a corporate-actions dir, refusing any file whose sha256 differs from the manifest."""
    manifest = json.loads((directory / CORPORATE_ACTIONS_MANIFEST).read_text(encoding="utf-8"))
    observations_path = directory / manifest["observations"]["file"]
    if file_sha256(observations_path) != manifest["observations"]["sha256"]:
        raise ValueError(f"{observations_path}: sha256 does not match the corporate-actions manifest")
    for entry in manifest["symbols"]:
        if entry["file"] is None:
            continue
        response_path = directory / entry["file"]
        if file_sha256(response_path) != entry["sha256"]:
            raise ValueError(f"{response_path}: sha256 does not match the corporate-actions manifest")
    return read_observations(observations_path)


def _day_ns(values: pd.Series) -> pd.Series:
    """Dates as datetime64[ns], so merge keys from strings and from date objects match."""
    return pd.to_datetime(values).astype("datetime64[ns]")


def _map_to_panel(frame: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Per row: first panel session on or after its ex-date, that session's raw close and prev_close, and the close before it."""
    prices = panel[["security_id", "session_date", "close", "prev_close"]].astype({"security_id": str}).sort_values(["security_id", "session_date"])
    prices = prices.assign(c0=prices.groupby("security_id")["close"].shift(), key=_day_ns(prices["session_date"]))
    left = frame[["security_id", "ex_date"]].astype({"security_id": str}).reset_index(drop=True).assign(ex=lambda f: _day_ns(f["ex_date"]))
    left = left.assign(row=left.index).sort_values("ex")
    merged = pd.merge_asof(left, prices.sort_values("key"), left_on="ex", right_on="key", by="security_id", direction="forward")
    merged = merged.sort_values("row").reset_index(drop=True)
    return merged.rename(columns={"session_date": "ex_session", "prev_close": "pc"})[["ex_session", "c0", "pc", "close"]]


def _prev_close_state(c0: float, pc: float, factor: float) -> str:
    """NSE's ex-session prev_close: left at the old level (false), already adjusted (true), or neither (ambiguous)."""
    if pd.isna(c0):
        return "ambiguous"
    tol = max(0.011, 0.005 * c0)
    if abs(pc - c0) <= tol:
        return "false"
    if abs(pc - c0 * factor) <= tol:
        return "true"
    return "ambiguous"


def _parsed_actions(observations: pd.DataFrame) -> pd.DataFrame:
    """Parsed dated actions, with repeats collapsed and conflicting or cancelled actions removed."""
    rows = []
    for o in observations.itertuples(index=False):
        purpose = parse_purpose(o.subject)
        if purpose is not None:
            rows.append({"security_id": o.security_id, "symbol": o.symbol, "ex_date": o.ex_date, "record_date": getattr(o, "record_date", None), "subject": o.subject, "kind": purpose.kind, "factor": purpose.factor, "revision_status": getattr(o, "revision_status", "original_or_unspecified")})
    return pd.DataFrame(rows, columns=["security_id", "symbol", "ex_date", "record_date", "subject", "kind", "factor", "revision_status"])


def build_events(observations: pd.DataFrame, panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split and bonus events on their panel ex-sessions, and the rows a person must review."""
    actions = _parsed_actions(observations)
    valid_date = pd.to_datetime(actions["ex_date"], errors="coerce").notna()
    cancelled_actions = actions.loc[actions["revision_status"].eq("cancelled")]
    withheld_indexes = set(cancelled_actions.index)
    for _, cancellation in cancelled_actions.iterrows():
        same_action = (
            actions["security_id"].eq(cancellation["security_id"])
            & actions["kind"].eq(cancellation["kind"])
            & actions["factor"].eq(cancellation["factor"])
            & actions["revision_status"].ne("cancelled")
        )
        same_ex_date = actions["ex_date"].eq(cancellation["ex_date"]) & actions["ex_date"].notna() & pd.notna(cancellation["ex_date"])
        same_record_date = actions["record_date"].eq(cancellation["record_date"]) & actions["record_date"].notna() & pd.notna(cancellation["record_date"])
        withheld_indexes.update(actions.index[same_action & (same_ex_date | same_record_date)])
    withheld = actions.loc[actions.index.isin(withheld_indexes)]
    cancelled = withheld.loc[valid_date.loc[withheld.index], ["security_id", "symbol", "ex_date", "subject"]].assign(reason="cancelled_action")
    eligible = actions.loc[valid_date & actions["revision_status"].ne("cancelled")].copy()
    eligible = eligible.loc[~eligible.index.isin(withheld_indexes)]
    group = ["security_id", "ex_date", "kind"]
    has_revision = eligible.groupby(group)["revision_status"].transform(lambda status: status.eq("revised").any())
    superseded = eligible.loc[has_revision & eligible["revision_status"].ne("revised"), ["security_id", "symbol", "ex_date", "subject"]].assign(reason="superseded_by_revision")
    eligible = eligible.loc[~has_revision | eligible["revision_status"].eq("revised")]
    conflict_keys = eligible.groupby(["security_id", "ex_date", "kind"])["factor"].nunique()
    conflict_keys = set(conflict_keys[conflict_keys > 1].index)
    conflicting = eligible.loc[
        eligible.set_index(["security_id", "ex_date", "kind"]).index.isin(conflict_keys),
        ["security_id", "symbol", "ex_date", "subject"],
    ].assign(reason="conflicting_factors")
    actions = eligible.loc[~eligible.set_index(["security_id", "ex_date", "kind"]).index.isin(conflict_keys)]
    actions = actions.drop_duplicates(["security_id", "ex_date", "kind"]).sort_values(["security_id", "ex_date", "kind"], ignore_index=True)
    actions = actions.join(_map_to_panel(actions, panel))
    total = actions.groupby(["security_id", "ex_session"])["factor"].transform("prod")
    states = [_prev_close_state(c0, pc, factor) for c0, pc, factor in zip(actions["c0"], actions["pc"], total)]
    actions = actions.assign(nse_prev_close_adjusted=states)
    mapped = actions["ex_session"].notna()
    events = actions.loc[mapped, EVENT_COLUMNS].reset_index(drop=True)

    observation_dates = pd.to_datetime(observations["ex_date"], errors="coerce").notna()
    missing_observation_date = observations.loc[~observation_dates, ["security_id", "symbol", "ex_date", "subject"]].assign(reason="missing_ex_date")
    unparsed_subject = observations["subject"].map(lambda subject: parse_purpose(subject) is None and re.search(ACTION_WORDS, subject, re.IGNORECASE) is not None)
    unparsed = observations.loc[observation_dates & unparsed_subject, ["security_id", "symbol", "ex_date", "subject"]].assign(reason="unparsed_purpose")
    unmapped = actions.loc[~mapped, ["security_id", "symbol", "ex_date", "subject"]].assign(reason="no_panel_session_on_or_after_ex_date")
    ambiguous = events.loc[events["nse_prev_close_adjusted"] == "ambiguous", ["security_id", "symbol", "ex_date", "subject"]].assign(reason="ambiguous_prev_close")
    review = pd.concat([missing_observation_date, cancelled, superseded, conflicting, unparsed, unmapped, ambiguous], ignore_index=True)[REVIEW_COLUMNS]
    return events, review.sort_values(["reason", "security_id", "ex_date"], ignore_index=True)


def factors_frame(events: pd.DataFrame) -> pd.DataFrame:
    """Events as the panel builder reads them, refusing factors with ambiguous prev_close state."""
    ambiguous = events.loc[events["nse_prev_close_adjusted"] == "ambiguous"]
    if not ambiguous.empty:
        event = ambiguous.iloc[0]
        raise ValueError(f"ambiguous adjustment for security_id {event['security_id']} at ex_session {event['ex_session']}")
    return pd.DataFrame(
        {
            "security_id": events["security_id"],
            "ex_session": events["ex_session"],
            "factor": events["factor"],
            "nse_prev_close_adjusted": events["nse_prev_close_adjusted"] == "true",
        }
    )


def read_factors(directory: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    """Factors from an adjustments directory, plus the input hashes the panel manifest records."""
    events_path = directory / EVENTS_FILE
    summary = json.loads((directory / SUMMARY_FILE).read_text(encoding="utf-8"))
    events = pd.read_csv(events_path, dtype=str, keep_default_na=False)
    events = events.assign(ex_session=events["ex_session"].map(date.fromisoformat), factor=events["factor"].astype(float))
    hashes = {"observations_sha256": summary["input_sha256"]["observations"], "events_sha256": file_sha256(events_path)}
    return factors_frame(events), hashes


def adjusted_return(panel: pd.DataFrame) -> pd.Series:
    """Same-day return on adjusted prices; NaN where either adjusted price is not positive."""
    prev_adjusted = panel["prev_close"] * panel["prev_close_factor"]
    valid = (prev_adjusted > 0) & (panel["close"] > 0)
    return panel["close"] * panel["adj_factor"] / prev_adjusted.where(valid) - 1


def _on_day_hints(observations: pd.DataFrame, panel: pd.DataFrame) -> dict[tuple[str, date], str]:
    """First non-split/bonus observation hint per security and ex-session."""
    dated = observations.loc[pd.to_datetime(observations["ex_date"], errors="coerce").notna()]
    sessions = _map_to_panel(dated, panel)["ex_session"]
    hints: dict[tuple[str, date], str] = {}
    for security_id, session, hint in zip(dated["security_id"], sessions, dated["action_type_hint"]):
        if hint not in ("split", "bonus") and pd.notna(session):
            hints.setdefault((security_id, session), hint)
    return hints


def review_moves(panel: pd.DataFrame, cohort: pd.DataFrame, observations: pd.DataFrame, events: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    """Cohort-member sessions whose adjusted same-day return passes the move threshold, each with a reason."""
    ordered = panel.sort_values(["security_id", "session_date"], ignore_index=True)
    grouped = ordered.groupby("security_id")
    ordered = ordered.assign(
        prev_session=grouped["session_date"].shift(),
        prev_series=grouped["series"].shift(),
        r=adjusted_return(ordered),
        month=pd.to_datetime(ordered["session_date"]).dt.strftime("%Y-%m"),
    )
    members = ordered.merge(cohort[["security_id", "month"]].drop_duplicates(), on=["security_id", "month"])
    flagged = members.loc[members["r"].abs() > MOVE_THRESHOLD]
    hints = _on_day_hints(observations, panel)
    ambiguous = {(sid, session) for sid, session, state in zip(events["security_id"], events["ex_session"], events["nse_prev_close_adjusted"]) if state == "ambiguous"}
    position = {session: index for index, session in enumerate(sessions)}
    rows = [
        {
            "security_id": row.security_id,
            "symbol": row.symbol,
            "session_date": row.session_date,
            "r": row.r,
            "close": row.close,
            "prev_close": row.prev_close,
            "reason": _move_reason(row, hints, ambiguous, position),
        }
        for row in flagged.itertuples(index=False)
    ]
    return pd.DataFrame(rows, columns=MOVE_COLUMNS).sort_values(["session_date", "security_id"], ignore_index=True)


def _move_reason(row: Any, hints: dict[tuple[str, date], str], ambiguous: set[tuple[str, date]], position: dict[date, int]) -> str:
    """Why a flagged move happened, using only what the data shows."""
    key = (row.security_id, row.session_date)
    if key in hints:
        return f"action_{hints[key]}_on_day"
    if isinstance(row.prev_session, date):
        skipped = position[row.session_date] - position[row.prev_session] - 1
        if skipped > 0:
            return f"after_gap_{skipped}_sessions"
    if isinstance(row.prev_series, str) and row.prev_series != row.series:
        return "series_change"
    if key in ambiguous:
        return "ambiguous_prev_close_event"
    return "unexplained"
