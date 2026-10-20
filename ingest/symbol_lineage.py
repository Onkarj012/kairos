"""Link old NSE symbols to new ones when official renames and prev_close handoffs agree."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd

from ingest.daily_panel import KEPT_SERIES, SERIES_PRIORITY, replace_directory

PREV_CLOSE_TOLERANCE = 0.011
SUCCESSOR_LOOKAHEAD = 30
NAMECHANGE_SLACK = timedelta(days=7)
DATE_FORMAT = "%d-%b-%Y"
LINKED_VERDICTS = frozenset({"triple_confirmed", "gap_but_prevclose_match", "namechange_prevclose_match"})
RENAME_COLUMNS = [
    "source",
    "company",
    "old",
    "new",
    "effective",
    "old_last",
    "new_first",
    "session_gap",
    "close_old",
    "prev_close_new",
    "verdict",
    "linked",
]
ID_MAP_FILE = "id_map.csv"


@dataclass(frozen=True)
class Lineage:
    """Every evaluated rename row and the symbol to security_id map they link to."""

    renames: pd.DataFrame
    id_map: dict[str, str]


def prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Keep the five series, one row per symbol and session, with Timestamp session dates."""
    kept = panel[panel["series"].isin(KEPT_SERIES)].assign(
        session_date=lambda frame: pd.to_datetime(frame["session_date"]),
        _rank=lambda frame: frame["series"].map(SERIES_PRIORITY),
    )
    kept = kept.sort_values(["session_date", "symbol", "_rank"])
    return kept.drop_duplicates(["session_date", "symbol"]).drop(columns="_rank")


def _csv(body: bytes, names: list[str]) -> pd.DataFrame:
    """Read an NSE CSV by column position, with stripped names and values."""
    frame = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False, skipinitialspace=True)
    frame.columns = names
    return frame.map(str.strip)


def parse_symbolchange(body: bytes) -> pd.DataFrame:
    """Official symbolchange rows with company, old, new and the effective date."""
    frame = _csv(body, ["company", "old", "new", "effective"])
    frame["effective"] = pd.to_datetime(frame["effective"], format=DATE_FORMAT)
    return frame


def parse_namechange(body: bytes) -> pd.DataFrame:
    """Official namechange rows with the symbol and the effective date."""
    frame = _csv(body, ["symbol", "old_name", "new_name", "effective"])
    frame["effective"] = pd.to_datetime(frame["effective"], format=DATE_FORMAT)
    return frame[["symbol", "effective"]]


def _prices_match(prev_close: float | None, close: float | None) -> bool:
    """True when a prev_close and a close agree within the rename tolerance."""
    return prev_close is not None and close is not None and abs(prev_close - close) <= PREV_CLOSE_TOLERANCE


def _verdict(overlap: bool, gap: int, match: bool, max_gap: int | None) -> str:
    """Classify one old-to-new handoff from symbol overlap, session gap and prev_close match."""
    if overlap:
        return "overlap_symbol_reuse_or_conflict"
    if gap == 1:
        return "triple_confirmed" if match else "adjacent_prevclose_mismatch"
    if not match:
        return "gap_and_mismatch"
    if max_gap is not None and gap > max_gap:
        return "gap_over_max_prevclose_match"
    return "gap_but_prevclose_match"


def _sessions(panel: pd.DataFrame) -> pd.DatetimeIndex:
    """Sorted unique session dates in the panel."""
    return pd.DatetimeIndex(panel["session_date"].unique()).sort_values()


def _renames(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Rename rows in column order, with linked set from the verdict and sorted deterministically."""
    frame = pd.DataFrame(rows, columns=RENAME_COLUMNS)
    frame["linked"] = frame["verdict"].isin(LINKED_VERDICTS)
    return frame.sort_values(["source", "old", "new", "effective"], na_position="last").reset_index(drop=True)


def _route_a(panel: pd.DataFrame, symbolchange: pd.DataFrame, max_gap: int | None) -> pd.DataFrame:
    """Verdict for each official symbolchange row that touches a panel symbol."""
    sessions = _sessions(panel)
    life = panel.groupby("symbol")["session_date"].agg(first="min", last="max")
    close = panel.set_index(["symbol", "session_date"])["close"]
    prev_close = panel.set_index(["symbol", "session_date"])["prev_close"]
    window = symbolchange[(symbolchange["effective"] > sessions[0]) & (symbolchange["effective"] <= sessions[-1])]
    window = window[window["old"].isin(life.index) | window["new"].isin(life.index)]
    rows: list[dict[str, Any]] = []
    for row in window.itertuples():
        record = {"source": "symbolchange", "company": row.company, "old": row.old, "new": row.new, "effective": row.effective.date().isoformat()}
        if row.old not in life.index or row.new not in life.index:
            rows.append(record | {"verdict": "one_side_absent_from_EQ"})
            continue
        old_last, new_first = life.loc[row.old, "last"], life.loc[row.new, "first"]
        gap = sessions.get_loc(new_first) - sessions.get_loc(old_last)
        close_old = close.get((row.old, old_last))
        prev_close_new = prev_close.get((row.new, new_first))
        verdict = _verdict(old_last >= new_first, gap, _prices_match(prev_close_new, close_old), max_gap)
        rows.append(
            record
            | {
                "old_last": old_last.date().isoformat(),
                "new_first": new_first.date().isoformat(),
                "session_gap": gap,
                "close_old": close_old,
                "prev_close_new": prev_close_new,
                "verdict": verdict,
            }
        )
    return _renames(rows)


def _renamed_by(effectives: list[pd.Timestamp], old_last: pd.Timestamp, new_first: pd.Timestamp) -> bool:
    """True when an official namechange falls from the old symbol's last session to a week after the new one's first."""
    return any(old_last <= effective <= new_first + NAMECHANGE_SLACK for effective in effectives)


def _route_b(panel: pd.DataFrame, namechange: pd.DataFrame, route_a: pd.DataFrame) -> pd.DataFrame:
    """Verdict for each dying symbol whose successor is born with matching prev_close and a namechange row."""
    sessions = _sessions(panel)
    life = panel.groupby("symbol")["session_date"].agg(first="min", last="max")
    close = panel.set_index(["symbol", "session_date"])["close"]
    prev_close = panel.set_index(["symbol", "session_date"])["prev_close"]
    changes = {symbol: list(dates) for symbol, dates in namechange.groupby("symbol")["effective"]}
    route_a_olds = set(route_a["old"])
    linked_route_a_news = set(route_a.loc[route_a["linked"], "new"])
    deaths = life[(life["last"] < sessions[-1]) & ~life.index.isin(route_a_olds)]
    births = life[life["first"] > sessions[0]].groupby("first").groups
    rows: list[dict[str, Any]] = []
    links: list[tuple[str, dict[str, Any]]] = []
    for symbol, death in deaths.iterrows():
        last_index = sessions.get_loc(death["last"])
        old_close = close.get((symbol, death["last"]))
        candidates = []
        for index in range(last_index + 1, min(last_index + 1 + SUCCESSOR_LOOKAHEAD, len(sessions))):
            first = sessions[index]
            for new in births.get(first, []):
                if _prices_match(prev_close.get((new, first)), old_close):
                    candidates.append((new, first))
        kept = [(new, first) for new, first in candidates if _renamed_by(changes.get(new, []), death["last"], first)]
        record = {"source": "namechange", "old": symbol, "old_last": death["last"].date().isoformat(), "close_old": old_close}
        if len(kept) == 1:
            new, first = kept[0]
            links.append(
                (
                    new,
                    record
                    | {
                        "new": new,
                        "new_first": first.date().isoformat(),
                        "session_gap": sessions.get_loc(first) - last_index,
                        "prev_close_new": prev_close.get((new, first)),
                    },
                )
            )
        elif len(kept) > 1:
            rows.append(record | {"new": "|".join(new for new, _ in kept), "verdict": "namechange_ambiguous"})
    claims = Counter(new for new, _ in links)
    for new, row in links:
        # A successor that route A or another route B row already claims stays unlinked.
        contested = new in linked_route_a_news or claims[new] > 1
        rows.append(row | {"verdict": "namechange_conflict" if contested else "namechange_prevclose_match"})
    return _renames(rows)


def _id_map(renames: pd.DataFrame, panel: pd.DataFrame) -> dict[str, str]:
    """Map each linked old symbol to the last symbol of its chain, refusing two successors or two overlapping predecessors."""
    successor: dict[str, str] = {}
    for old, new in renames.loc[renames["linked"], ["old", "new"]].itertuples(index=False):
        if successor.setdefault(old, new) != new:
            raise ValueError(f"{old} links to both {successor[old]} and {new}")
    life = panel.groupby("symbol")["session_date"].agg(first="min", last="max")
    predecessors: dict[str, list[str]] = {}
    for old, new in successor.items():
        predecessors.setdefault(new, []).append(old)
    for new, olds in predecessors.items():
        for a, b in combinations(sorted(olds), 2):
            if life.loc[a, "first"] <= life.loc[b, "last"] and life.loc[b, "first"] <= life.loc[a, "last"]:
                raise ValueError(f"{a} and {b} both link to {new} and traded in the same sessions")
    parent: dict[str, str] = {}

    def find(symbol: str) -> str:
        """Root of a symbol's chain in the union-find."""
        parent.setdefault(symbol, symbol)
        while parent[symbol] != symbol:
            symbol = parent[symbol]
        return symbol

    for old, new in successor.items():
        parent[find(old)] = find(new)
    return {symbol: find(symbol) for symbol in list(parent) if find(symbol) != symbol}


def build_lineage(panel: pd.DataFrame, symbolchange: bytes, namechange: bytes, max_gap: int | None = 30) -> Lineage:
    """Evaluate official renames and namechange successors against the panel, then link them."""
    prepared = prepare_panel(panel)
    route_a = _route_a(prepared, parse_symbolchange(symbolchange), max_gap)
    route_b = _route_b(prepared, parse_namechange(namechange), route_a)
    renames = pd.concat([route_a, route_b], ignore_index=True)
    return Lineage(renames=renames, id_map=_id_map(renames, prepared))


def route_a_verdicts(panel: pd.DataFrame, symbolchange: bytes, max_gap: int | None = 30) -> pd.DataFrame:
    """Symbolchange verdicts alone, used to check the overnight prototype's counts."""
    return _route_a(prepare_panel(panel), parse_symbolchange(symbolchange), max_gap)


def read_id_map(path: Path) -> dict[str, str]:
    """The symbol to security_id map that write_lineage wrote."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(frame["symbol"], frame["security_id"]))


def write_lineage(lineage: Lineage, summary: dict[str, Any], out_dir: Path) -> None:
    """Write renames, id map and summary in a staging dir, then swap it over out_dir."""
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        lineage.renames.to_csv(staging / "renames.csv", index=False)
        pd.DataFrame(sorted(lineage.id_map.items()), columns=["symbol", "security_id"]).to_csv(staging / ID_MAP_FILE, index=False)
        (staging / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


__all__ = ["Lineage", "build_lineage", "prepare_panel", "read_id_map", "route_a_verdicts", "write_lineage"]
