"""Monthly top-N liquidity cohort of security ids, from traded value, presence, identity and fund class only."""
from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

TOP = 200
WINDOW = 60
MIN_OBS = 40
FIRST_MONTH = "2020-01"
UNIVERSE_SERIES = ("EQ", "BE", "BZ")
ONE_SIDE = "one_side_absent_from_EQ"
MEMBER_COLUMNS = ["month", "rank_session", "security_id", "rank", "median_traded_value", "fund_class"]
DROP_COLUMNS = ["month", "rank_session", "security_id", "reason", "pre_drop_rank", "median_traded_value"]
MONTH_COLUMNS = [
    "month",
    "rank_session",
    "ranked",
    "members",
    "dropped_fund",
    "dropped_unproven",
    "unknown_members",
    "reason",
]


@dataclass(frozen=True)
class Identity:
    """Security ids touched by the rename rules: the date each becomes unproven, and the report counts."""

    unproven_from: dict[str, date]
    direct: set[str]
    one_side_unproven: set[str]
    one_side_exempt: set[str]
    renamed: set[str]

    @property
    def unproven(self) -> set[str]:
        """Ids unproven from some date on."""
        return set(self.unproven_from)


@dataclass(frozen=True)
class Cohort:
    """Output tables and summary of one cohort build."""

    members: pd.DataFrame
    months: pd.DataFrame
    dropped: pd.DataFrame
    ever: pd.DataFrame
    summary: dict[str, object]


def build_cohort(
    panel: pd.DataFrame,
    sessions: list[date],
    renames: pd.DataFrame,
    funds: pd.DataFrame,
    top: int = TOP,
    window: int = WINDOW,
    min_obs: int = MIN_OBS,
    first_month: str = FIRST_MONTH,
    ledger_end: date | None = None,
) -> Cohort:
    """Rank each month's ids on the window before it, drop funds and unproven ids, keep the top."""
    identity = _identity(panel, renames)
    classes = _fund_classes(panel, funds)
    traded = _traded_matrix(panel, sessions)
    panel_end = panel["session_date"].max()

    member_rows: list[dict[str, object]] = []
    drop_rows: list[dict[str, object]] = []
    month_rows: list[dict[str, object]] = []
    ledger_end = ledger_end or sessions[-1]
    for label, rank_idx in _month_ranks(sessions, panel_end, first_month, ledger_end):
        rank_session = sessions[rank_idx]
        ranking = _rank(traded, rank_idx, window, min_obs)
        ranking["fund_class"] = ranking["security_id"].map(classes)
        ranking["unproven"] = [_unproven_on(identity, sid, rank_session) for sid in ranking["security_id"]]
        ranking["pre_drop_rank"] = ranking.index + 1
        dropped = ranking[ranking["unproven"] | (ranking["fund_class"] == "fund")]
        lost = dropped[dropped["pre_drop_rank"] <= top].copy()
        lost["reason"] = [_drop_reason(c, u) for c, u in zip(lost["fund_class"], lost["unproven"])]
        kept = ranking.drop(dropped.index).head(top).reset_index(drop=True)
        kept["rank"] = kept.index + 1

        drop_rows.extend(
            lost.assign(month=label, rank_session=rank_session)[DROP_COLUMNS].to_dict("records")
        )
        member_rows.extend(
            kept.assign(month=label, rank_session=rank_session)[MEMBER_COLUMNS].to_dict("records")
        )
        month_rows.append(
            {
                "month": label,
                "rank_session": rank_session,
                "ranked": len(ranking),
                "members": len(kept),
                "dropped_fund": int((lost["fund_class"] == "fund").sum()),
                "dropped_unproven": int(lost["unproven"].sum()),
                "unknown_members": int((kept["fund_class"] == "unknown").sum()),
                "reason": "" if len(kept) == top else f"only {len(kept)} eligible after drops",
            }
        )

    if not month_rows:
        raise ValueError("no complete months to build a cohort for")
    members = pd.DataFrame(member_rows, columns=MEMBER_COLUMNS)
    months = pd.DataFrame(month_rows, columns=MONTH_COLUMNS)
    dropped = pd.DataFrame(drop_rows, columns=DROP_COLUMNS)
    ever = _ever_members(members, _symbol_chains(panel))
    summary = _summary(months, ever, members, identity)
    return Cohort(members=members, months=months, dropped=dropped, ever=ever, summary=summary)


def _ids_of(symbols: Iterable[str], symbol_ids: dict[str, str]) -> set[str]:
    """Security ids of the symbols the panel knows."""
    return {symbol_ids[s] for s in symbols if s in symbol_ids}


def _evidence_date(row: Any) -> date:
    """Day an unlinked rename became evidence: its effective date, or for namechange rows the successor's first or the old symbol's last session."""
    if row.source == "symbolchange":
        return pd.Timestamp(row.effective).date()
    # Namechange rows read from CSV leave a blank new_first when the successor is ambiguous.
    if isinstance(row.new_first, str) and row.new_first:
        return pd.Timestamp(row.new_first).date()
    return pd.Timestamp(row.old_last).date()


def _split_cell(cell: str) -> list[str]:
    """The symbols in one rename cell; an ambiguous successor joins several with |."""
    return cell.split("|")


def _evidence_dates(rows: pd.DataFrame) -> dict[str, date]:
    """Earliest evidence date of each symbol on the given unlinked rename rows."""
    dates: dict[str, date] = {}
    for row in rows.itertuples(index=False):
        when = _evidence_date(row)
        for symbol in (*_split_cell(row.old), *_split_cell(row.new)):
            dates[symbol] = min(when, dates.get(symbol, when))
    return dates


def _identity(panel: pd.DataFrame, renames: pd.DataFrame) -> Identity:
    """Unlinked renames make an id unproven from their evidence date on; a placeholder side that never traded EQ/BE/BZ does not."""
    symbol_ids = dict(zip(panel["symbol"], panel["security_id"]))
    eq_symbols = set(panel.loc[panel["series"] == "EQ", "symbol"])
    traded_symbols = set(panel.loc[panel["series"].isin(UNIVERSE_SERIES), "symbol"])
    unlinked = renames[~renames["linked"]]
    direct = unlinked[unlinked["verdict"] != ONE_SIDE]
    one_side = unlinked[unlinked["verdict"] == ONE_SIDE]
    one_side_symbols = set(one_side["old"]) | set(one_side["new"])
    # The side absent from EQ is the one that can hide a real identity break.
    absent_traded = {s for s in one_side_symbols if s not in eq_symbols and s in traded_symbols}
    evidence = _evidence_dates(direct)
    for symbol, when in _evidence_dates(one_side).items():
        if symbol in absent_traded:
            evidence[symbol] = min(when, evidence.get(symbol, when))
    unproven_from: dict[str, date] = {}
    for symbol, when in evidence.items():
        if symbol in symbol_ids:
            sid = symbol_ids[symbol]
            unproven_from[sid] = min(when, unproven_from.get(sid, when))
    linked = renames[renames["linked"]]
    return Identity(
        unproven_from=unproven_from,
        direct=_ids_of({s for cell in (*direct["old"], *direct["new"]) for s in _split_cell(cell)}, symbol_ids),
        one_side_unproven=_ids_of(absent_traded, symbol_ids),
        one_side_exempt=_ids_of(one_side_symbols, symbol_ids) - set(unproven_from),
        renamed=_ids_of(set(linked["old"]) | set(linked["new"]), symbol_ids),
    )


def _unproven_on(identity: Identity, security_id: str, rank_session: date) -> bool:
    """True when the id's rename evidence is on or before the month's rank session."""
    evidence = identity.unproven_from.get(security_id)
    return evidence is not None and rank_session >= evidence


def _unproven_member_ids(members: pd.DataFrame, identity: Identity) -> set[str]:
    """Ids that were in the cohort in a month where they were unproven."""
    return {
        sid for sid, session in zip(members["security_id"], members["rank_session"]) if _unproven_on(identity, sid, session)
    }


def _fund_classes(panel: pd.DataFrame, funds: pd.DataFrame) -> dict[str, str]:
    """Id class: fund if any symbol is fund, else equity if any is equity, else unknown."""
    symbol_class = dict(zip(funds["symbol"], funds["class"]))
    pairs = panel[["security_id", "symbol"]].drop_duplicates()
    symbol_classes = pairs.assign(fund_class=pairs["symbol"].map(symbol_class).fillna("unknown"))
    found = symbol_classes.groupby("security_id")["fund_class"].agg(set)
    return {
        sid: "fund" if "fund" in classes else "equity" if "equity" in classes else "unknown"
        for sid, classes in found.items()
    }


def _symbol_chains(panel: pd.DataFrame) -> dict[str, str]:
    """Every symbol of each id, oldest first, joined with |."""
    first = panel.groupby(["security_id", "symbol"], as_index=False)["session_date"].min()
    first = first.sort_values(["security_id", "session_date", "symbol"])
    return first.groupby("security_id")["symbol"].agg(lambda s: "|".join(s)).to_dict()


def _traded_matrix(panel: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    """Traded value per ledger session and security id; a session with no rows stays NaN."""
    universe = panel[panel["series"].isin(UNIVERSE_SERIES)]
    daily = universe.groupby(["session_date", "security_id"])["traded_value"].sum(min_count=1)
    return daily.unstack("security_id").reindex(sessions)


def _month_ranks(sessions: list[date], panel_end: date, first_month: str, ledger_end: date) -> list[tuple[str, int]]:
    """Complete months from first_month, each with the index of the session before its first one."""
    first_index: dict[str, int] = {}
    for i, session in enumerate(sessions):
        first_index.setdefault(session.strftime("%Y-%m"), i)
    plan: list[tuple[str, int]] = []
    for label, i in first_index.items():
        year, month = (int(part) for part in label.split("-"))
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        last_expected = max(session for session in sessions if session.strftime("%Y-%m") == label)
        if label >= first_month and i > 0 and last_expected <= panel_end and month_end <= ledger_end:
            plan.append((label, i - 1))
    return plan


def _rank(traded: pd.DataFrame, rank_idx: int, window: int, min_obs: int) -> pd.DataFrame:
    """Eligible ids over the window ending at rank_idx, best median first, ties to the lower security_id."""
    sub = traded.iloc[max(0, rank_idx - window + 1) : rank_idx + 1]
    ranking = pd.DataFrame(
        {
            "security_id": sub.columns,
            "median_traded_value": sub.median().to_numpy(),
            "observed": sub.notna().sum().to_numpy(),
        }
    )
    ranking = ranking[ranking["observed"] >= min_obs]
    return ranking.sort_values(
        ["median_traded_value", "security_id"], ascending=[False, True], ignore_index=True
    )


def _drop_reason(fund_class: str, unproven: bool) -> str:
    """Why an id left the cohort; both reasons are joined when both apply."""
    reasons = [name for name, hit in (("fund", fund_class == "fund"), ("unproven", unproven)) if hit]
    return "+".join(reasons)


def _ever_members(members: pd.DataFrame, chains: dict[str, str]) -> pd.DataFrame:
    """One row per id that was ever in the cohort."""
    ever = (
        members.groupby("security_id")
        .agg(
            first_month=("month", "min"),
            last_month=("month", "max"),
            months=("month", "size"),
            fund_class=("fund_class", "first"),
        )
        .reset_index()
    )
    ever.insert(1, "symbols", ever["security_id"].map(chains))
    return ever.sort_values("security_id", ignore_index=True)


def _summary(months: pd.DataFrame, ever: pd.DataFrame, members: pd.DataFrame, identity: Identity) -> dict[str, object]:
    """Counts and flags for summary.json."""
    has_chain = ever["symbols"].str.contains("|", regex=False)
    return {
        "months": len(months),
        "first_month": months["month"].iloc[0],
        "last_month": months["month"].iloc[-1],
        "distinct_ever_members": len(ever),
        "ever_members_with_rename": int((has_chain | ever["security_id"].isin(identity.renamed)).sum()),
        "ever_members_with_unproven_rename": len(_unproven_member_ids(members, identity)),
        "unknown_ever_members": ever.loc[ever["fund_class"] == "unknown", "security_id"].tolist(),
        "min_members": int(months["members"].min()),
        "max_members": int(months["members"].max()),
        "identity": {
            "unproven_ids": len(identity.unproven),
            "direct_rule_ids": len(identity.direct),
            "one_side_unproven_ids": len(identity.one_side_unproven),
            "one_side_exempt_ids": len(identity.one_side_exempt),
        },
    }
