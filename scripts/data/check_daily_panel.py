"""Stage-0 exit check: reads the finished daily-runner outputs and writes the acceptance report. Reads no forward returns."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Keep direct-file execution equivalent to `python -m scripts.data.check_daily_panel`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.daily_universe import TOP, UNIVERSE_SERIES, _identity, _unproven_member_ids
from ingest.adjustment_factors import EVENTS_FILE, REVIEW_MOVES_FILE, REVIEW_PURPOSES_FILE, SUMMARY_FILE, file_sha256
from ingest.daily_panel import MANIFEST_FILE, PANEL_FILE, _expected_window, derived_root, replace_directory
from ingest.fund_classification import OVERNIGHT_LEAKED_ETFS
from ingest.symbol_lineage import ID_MAP_FILE, PREV_CLOSE_TOLERANCE, read_id_map

DAILY = "daily-runner"
REPORT_DIR = "acceptance-v1"
BREAK_LIMIT = 100


@dataclass(frozen=True)
class Section:
    """One report section: its key, heading, status, data for report.json and its markdown body."""

    key: str
    title: str
    status: str | None
    data: dict[str, Any]
    body: str


def _paths(daily: Path) -> dict[str, Path]:
    """Every input file the check reads, keyed by its name in the report."""
    return {
        "panel": daily / "panel-v1" / PANEL_FILE,
        "panel_manifest": daily / "panel-v1" / MANIFEST_FILE,
        "ledger": daily / "session-ledger-v1" / "sessions.jsonl",
        "renames": daily / "lineage-v1" / "renames.csv",
        "id_map": daily / "lineage-v1" / ID_MAP_FILE,
        "classification": daily / "funds-v1" / "classification.csv",
        "cohort": daily / "cohort-v1" / "cohort.parquet",
        "ever_members": daily / "cohort-v1" / "ever_members.csv",
        "months": daily / "cohort-v1" / "months.csv",
        "dropped": daily / "cohort-v1" / "dropped.csv",
        "events": daily / "adjustments-v1" / EVENTS_FILE,
        "review_purposes": daily / "adjustments-v1" / REVIEW_PURPOSES_FILE,
        "review_moves": daily / "adjustments-v1" / REVIEW_MOVES_FILE,
        "adjustments_summary": daily / "adjustments-v1" / SUMMARY_FILE,
        "corporate_actions_manifest": daily / "corporate-actions-v1" / "manifest.json",
        "failures": daily / "corporate-actions-v1" / "failures.csv",
    }


def _csv(path: Path) -> pd.DataFrame:
    """Read a CSV with every cell as text, so ids and symbols keep their form."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _counts(values: pd.Series) -> dict[str, int]:
    """Row count per distinct value, keys sorted."""
    return {str(key): int(count) for key, count in values.value_counts().sort_index().items()}


def _cell(value: object) -> str:
    """One markdown table cell; floats get four decimals and pipes are escaped."""
    text = f"{value:.4f}" if isinstance(value, float) else str(value)
    return text.replace("|", "\\|")


def _table(headers: list[str], rows: list[list[object]]) -> str:
    """Markdown table, or the word none when there are no rows."""
    if not rows:
        return "none\n"
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _read_panel(path: Path) -> pd.DataFrame:
    """Panel columns the checks use: identity, series, presence and the prices for the chain."""
    columns = ["session_date", "security_id", "symbol", "series", "close", "prev_close", "adj_factor", "prev_close_factor"]
    return pd.read_parquet(path, columns=columns)


def _coverage(expected: list[date], present: set[date], excluded: list[dict[str, str]]) -> tuple[Section, list[date]]:
    """Expected ledger sessions against panel sessions; only recorded exclusions may be missing."""
    missing = [session for session in expected if session not in present]
    excluded_days = {row["session_date"] for row in excluded}
    unexplained = [session.isoformat() for session in missing if session.isoformat() not in excluded_days]
    latest = max(present).isoformat()
    data = {
        "first_expected": expected[0].isoformat(),
        "last_expected": expected[-1].isoformat(),
        "expected": len(expected),
        "present": len(present),
        "latest_session": latest,
        "missing": [session.isoformat() for session in missing],
        "excluded": excluded,
        "unexplained_missing": unexplained,
    }
    body = (
        f"- Expected sessions {expected[0]} to {expected[-1]}: {len(expected)}\n"
        f"- Present in panel: {len(present)}. Latest: {latest}\n"
        f"- Missing: {', '.join(data['missing']) or 'none'}\n"
        f"- Unexplained missing: {', '.join(unexplained) or 'none'}\n\n"
        + _table(["session", "excluded reason"], [[row["session_date"], row["excluded"]] for row in excluded])
    )
    status = "pass" if not unexplained else "fail"
    return Section("coverage", "Session coverage", status, data, body), missing


def _renames(paths: dict[str, Path], panel: pd.DataFrame, ever: pd.DataFrame, members: pd.DataFrame) -> Section:
    """Rename verdicts by route, and how many cohort ever-members with a rename are proven."""
    renames = pd.read_csv(paths["renames"], dtype={"old": str, "new": str, "linked": bool}, keep_default_na=False)
    id_map = read_id_map(paths["id_map"])
    route_a = _counts(renames.loc[renames["source"] == "symbolchange", "verdict"])
    route_b = _counts(renames.loc[renames["source"] == "namechange", "verdict"])
    identity = _identity(panel, renames)
    unproven_mask = ever["security_id"].isin(_unproven_member_ids(members, identity))
    has_rename = ever["symbols"].str.contains("|", regex=False) | ever["security_id"].isin(identity.renamed)
    data = {
        "route_a": route_a,
        "route_b": route_b,
        "linked": int(renames["linked"].sum()),
        "evaluated": len(renames),
        "id_map_symbols": len(id_map),
        "ever_with_rename": int(has_rename.sum()),
        "proven": int((has_rename & ~unproven_mask).sum()),
        "unproven": int(unproven_mask.sum()),
    }
    verdicts = sorted(set(route_a) | set(route_b))
    rows = [[verdict, route_a.get(verdict, 0), route_b.get(verdict, 0)] for verdict in verdicts]
    body = (
        _table(["verdict", "route A (symbolchange)", "route B (namechange)"], rows)
        + f"\n- Linked rename rows: {data['linked']} of {data['evaluated']}\n"
        + f"- ID map symbols: {data['id_map_symbols']}\n"
        + f"- Cohort ever-members with a rename: {data['ever_with_rename']}\n"
        + f"- Proven: {data['proven']}. Unproven: {data['unproven']} (must be 0)\n"
    )
    return Section("renames", "Rename proof", "fail" if data["unproven"] else "pass", data, body)


def _funds(paths: dict[str, Path], panel: pd.DataFrame, ever: pd.DataFrame) -> Section:
    """Fund classes, cohort drops for fund, the overnight ETFs, and unknown ever-members."""
    classes = _csv(paths["classification"])
    dropped = _csv(paths["dropped"])
    fund_drops = dropped[dropped["reason"].str.split("+").map(lambda parts: "fund" in parts)]
    symbol_class = dict(zip(classes["symbol"], classes["class"]))
    pairs = panel[["symbol", "security_id"]].drop_duplicates()
    symbol_id = dict(zip(pairs["symbol"], pairs["security_id"]))
    ever_ids = set(ever["security_id"])
    etfs = []
    for symbol in OVERNIGHT_LEAKED_ETFS:
        security_id = symbol_id.get(symbol, "-")
        etfs.append(
            {
                "symbol": symbol,
                "class": symbol_class.get(symbol, "absent"),
                "security_id": security_id,
                "in_cohort": security_id in ever_ids,
            }
        )
    etfs_in_cohort = sum(etf["in_cohort"] for etf in etfs)
    unknown = ever.loc[ever["fund_class"] == "unknown", ["symbols", "first_month", "last_month"]]
    data = {
        "classes": _counts(classes["class"]),
        "dropped_as_fund_rows": len(fund_drops),
        "dropped_as_fund_ids": int(fund_drops["security_id"].nunique()),
        "overnight_etfs": etfs,
        "etfs_in_cohort": etfs_in_cohort,
        "unknown_ever_members": unknown.to_dict("records"),
    }
    body = (
        "- Classes: " + ", ".join(f"{name} {count}" for name, count in data["classes"].items()) + "\n"
        f"- Would-be members dropped as fund: {data['dropped_as_fund_rows']} rows, {data['dropped_as_fund_ids']} ids\n"
        f"- Overnight ETFs in cohort: {etfs_in_cohort} (must be 0)\n\n"
        + _table(
            ["symbol", "class", "security_id", "in cohort"],
            [[etf["symbol"], etf["class"], etf["security_id"], "yes" if etf["in_cohort"] else "no"] for etf in etfs],
        )
        + "\nUnknown ever-members:\n\n"
        + _table(["symbols", "first month", "last month"], unknown.values.tolist())
    )
    status = "fail" if etfs_in_cohort else "review" if len(unknown) else "pass"
    return Section("funds", "Fund exclusions", status, data, body)


def _adjustments(paths: dict[str, Path]) -> Section:
    """Split and bonus events by kind, the review lists in full, and the TDPOWERSYS check value."""
    events = _csv(paths["events"])
    purposes = _csv(paths["review_purposes"])
    moves = _csv(paths["review_moves"])
    summary = json.loads(paths["adjustments_summary"].read_text(encoding="utf-8"))
    corporate_actions = json.loads(paths["corporate_actions_manifest"].read_text(encoding="utf-8"))
    failures = _csv(paths["failures"])
    data = {
        "events_by_kind": _counts(events["kind"]),
        "prev_close_adjusted": _counts(events["nse_prev_close_adjusted"]),
        "review_purposes": purposes.to_dict("records"),
        "review_moves": moves.to_dict("records"),
        "tdpowersys_2026_08_24_adjusted_r": summary["tdpowersys_2026_08_24_adjusted_r"],
        "corporate_actions_run_status": corporate_actions["run_status"],
        "corporate_action_failures": len(failures),
    }
    body = (
        "- Events by kind: " + ", ".join(f"{name} {count}" for name, count in data["events_by_kind"].items()) + "\n"
        "- Prev_close adjusted by NSE: " + ", ".join(f"{name} {count}" for name, count in data["prev_close_adjusted"].items()) + "\n"
        f"- TDPOWERSYS 2026-08-24 adjusted r: {data['tdpowersys_2026_08_24_adjusted_r']:.6f}\n"
        f"- Corporate-action run: {data['corporate_actions_run_status']}, {data['corporate_action_failures']} failed symbols\n\n"
        "Failed symbols:\n\n"
        + _table(list(failures.columns), failures.values.tolist())
        + "\nPurpose review:\n\n"
        + _table(list(purposes.columns), purposes.values.tolist())
        + "\nMove review:\n\n"
        + _table(list(moves.columns), moves.values.tolist())
    )
    accounting = corporate_actions["request_accounting"]
    incomplete = corporate_actions["run_status"] == "aborted" or accounting["incomplete"] or accounting["not_requested"]
    status = "fail" if incomplete else "review" if len(failures) or len(purposes) or len(moves) else "pass"
    return Section("adjustments", "Adjustment review", status, data, body)


def _prev_close_chain(panel: pd.DataFrame, ever_ids: set[str], missing: list[date]) -> Section:
    """prev_close chain on adjusted prices for cohort ever-members; links over a globally missing session are skipped."""
    rows = panel[panel["security_id"].isin(ever_ids) & panel["series"].isin(UNIVERSE_SERIES)]
    rows = rows.sort_values(["security_id", "session_date"])
    frame = rows.assign(
        day=rows["session_date"].map(date.toordinal),
        adj_close=rows["close"] * rows["adj_factor"],
        adj_prev_close=rows["prev_close"] * rows["prev_close_factor"],
    )
    grouped = frame.groupby("security_id")
    links = frame.assign(
        prev_day=grouped["day"].shift(),
        prev_session=grouped["session_date"].shift(),
        prev_symbol=grouped["symbol"].shift(),
        prior_adj_close=grouped["adj_close"].shift(),
    ).dropna(subset=["prev_day"])
    gap = np.array([session.toordinal() for session in missing], dtype=np.int64)
    # A link jumps a missing session when one lies strictly between its two days.
    jumps = np.searchsorted(gap, links["day"].to_numpy(dtype=np.int64), side="left") - np.searchsorted(
        gap, links["prev_day"].to_numpy(dtype=np.int64), side="right"
    )
    linked = links[jumps == 0]
    passed = (linked["adj_prev_close"] - linked["prior_adj_close"]).abs() <= PREV_CLOSE_TOLERANCE * linked["prev_close_factor"]
    breaks = linked[~passed].sort_values(["session_date", "security_id"])
    break_rows = [
        [
            row.security_id,
            row.symbol,
            row.prev_symbol,
            row.series,
            row.prev_session.isoformat(),
            row.session_date.isoformat(),
            row.adj_prev_close,
            row.prior_adj_close,
        ]
        for row in breaks.head(BREAK_LIMIT).itertuples(index=False)
    ]
    data = {
        "links": len(linked),
        "skipped_links": len(links) - len(linked),
        "breaks": len(breaks),
        "ids_with_breaks": int(breaks["security_id"].nunique()),
        "pass_rate": float(passed.mean()),
        "break_list": break_rows,
    }
    body = (
        f"- Links: {data['links']}. Skipped over a missing session: {data['skipped_links']}\n"
        f"- Breaks: {data['breaks']} on {data['ids_with_breaks']} ids. Pass rate: {data['pass_rate']:.6f}\n"
        "- Context only: the overnight raw run had 765,411 links, 20 breaks and pass rate 0.999974.\n\n"
        + _table(
            ["security_id", "symbol", "previous symbol", "series", "previous session", "session", "adj prev_close", "adj previous close"],
            break_rows,
        )
    )
    return Section("prev_close_chain", "prev_close chain", "pass" if not len(breaks) else "review", data, body)


def _cohort_sizes(paths: dict[str, Path]) -> Section:
    """Members per month, with the reason for every month that is not full."""
    months = _csv(paths["months"])
    members = months["members"].astype(int)
    short = months.loc[members != TOP, ["month", "members", "reason"]]
    data = {
        "months": len(months),
        "first_month": months["month"].iloc[0],
        "last_month": months["month"].iloc[-1],
        "min_members": int(members.min()),
        "max_members": int(members.max()),
        "not_full": short.to_dict("records"),
    }
    body = (
        f"- Months: {data['months']}, {data['first_month']} to {data['last_month']}\n"
        f"- Members per month: min {data['min_members']}, max {data['max_members']}\n\n"
        + _table(["month", "members", "reason"], short.values.tolist())
    )
    return Section("cohort_sizes", "Cohort size per month", None, data, body)


def _inputs(paths: dict[str, Path], panel_manifest: dict[str, Any]) -> Section:
    """sha256 of every input file the check read, and the panel build commit."""
    files = {name: {"path": str(path), "sha256": file_sha256(path)} for name, path in paths.items()}
    data = {"panel_build_commit": panel_manifest.get("build_commit"), "files": files}
    rows = [[name, info["sha256"], info["path"]] for name, info in files.items()]
    body = f"- Panel build commit: {data['panel_build_commit']}\n\n" + _table(["input", "sha256", "path"], rows)
    return Section("inputs", "Inputs", None, data, body)


def _markdown(sections: list[Section]) -> str:
    """Report as markdown: a status table, then one heading per section."""
    parts = ["# Stage 0 acceptance report\n", _table(["section", "status"], [[s.title, s.status or "info"] for s in sections])]
    for number, section in enumerate(sections, 1):
        heading = f"## {number}. {section.title}"
        if section.status:
            heading += f": {section.status}"
        parts.append(f"{heading}\n\n{section.body}")
    return "\n".join(parts)


def main() -> int:
    """Read the stage-0 outputs, write report.json and report.md, print the markdown. Always exits 0."""
    daily = derived_root() / DAILY
    paths = _paths(daily)
    panel_manifest = json.loads(paths["panel_manifest"].read_text(encoding="utf-8"))
    expected = _expected_window(paths["ledger"])
    panel = _read_panel(paths["panel"])
    ever = _csv(paths["ever_members"])
    members = pd.read_parquet(paths["cohort"])
    coverage, missing = _coverage(expected, set(panel["session_date"]), panel_manifest["excluded"])
    sections = [
        coverage,
        _renames(paths, panel, ever, members),
        _funds(paths, panel, ever),
        _adjustments(paths),
        _prev_close_chain(panel, set(ever["security_id"]), missing),
        _cohort_sizes(paths),
        _inputs(paths, panel_manifest),
    ]
    markdown = _markdown(sections)
    report = {section.key: {"status": section.status, **section.data} for section in sections}
    out_dir = daily / REPORT_DIR
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=daily))
    try:
        (staging / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (staging / "report.md").write_text(markdown, encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
