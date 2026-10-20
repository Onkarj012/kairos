"""Strategy interface for the daily swing runner and the two baseline strategies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

from ingest.daily_panel import EXCLUDED_SESSIONS


@dataclass(frozen=True)
class Candidate:
    """One ranked buy candidate; lower list index means stronger."""

    security_id: str
    score: float
    reason: str


class Strategy(Protocol):
    """Ranks cohort members using only panel rows up to and including as_of."""

    name: str
    version: str
    lookback_sessions: int

    def rank(self, panel: pd.DataFrame, cohort: list[str], as_of: date) -> list[Candidate]: ...


def trading_sessions(ledger_sessions: list[date]) -> list[date]:
    """Return sorted ledger sessions except excluded market sessions."""
    excluded = {date.fromisoformat(session) for session in EXCLUDED_SESSIONS}
    return sorted(session for session in ledger_sessions if session not in excluded)


def panel_as_of(
    panel: pd.DataFrame,
    sessions: list[date],
    as_of: date,
    ids: list[str] | set[str],
    lookback_sessions: int,
) -> pd.DataFrame:
    """Slice the session window; for speed, pass a frame sorted/indexed by security_id then session_date."""
    if as_of not in sessions:
        raise ValueError(f"as_of {as_of} is not in sessions")
    end = sessions.index(as_of)
    window_sessions = sessions[max(0, end - lookback_sessions + 1) : end + 1]
    start = window_sessions[0]
    indexed = isinstance(panel.index, pd.MultiIndex) and panel.index.names[:2] == ["security_id", "session_date"]
    if indexed:
        available = panel.index.levels[0].intersection(ids)
        selected = panel.loc[available]
        dates = selected.index.get_level_values("session_date")
        result = selected.loc[(dates >= start) & (dates <= as_of) & dates.isin(window_sessions)].reset_index()
    else:
        result = panel.loc[
            panel["security_id"].isin(ids)
            & panel["session_date"].between(start, as_of)
            & panel["session_date"].isin(window_sessions)
        ].copy()
    if result.empty:
        return result.reset_index(drop=True)
    if not indexed:
        result = result.sort_values(["security_id", "session_date"])
    factors = result.loc[result["session_date"] <= as_of].groupby("security_id")["adj_factor"].last()
    divisor = result["security_id"].map(factors)
    result["adj_factor"] = result["adj_factor"] / divisor
    result["prev_close_factor"] = result["prev_close_factor"] / divisor
    return result.reset_index(drop=True)


def _rank_prices(panel: pd.DataFrame, cohort: list[str], as_of: date, lag_pair: tuple[int, int], name: str) -> list[Candidate]:
    """Rank cohort ids using valid adjusted closes at the requested session offsets."""
    if (panel["session_date"] > as_of).any():
        raise ValueError("panel contains a session after as_of")
    dates = sorted(panel["session_date"].unique())
    if as_of not in dates:
        return []
    current_idx = dates.index(as_of)
    first_lag, second_lag = lag_pair
    if current_idx < max(first_lag, second_lag):
        return []
    targets = [dates[current_idx - first_lag], dates[current_idx - second_lag]]
    candidates: list[Candidate] = []
    cohort_set = set(cohort)
    for security_id, rows in panel[panel["security_id"].isin(cohort_set)].groupby("security_id", sort=False):
        rows = rows.sort_values("session_date")
        if not (rows["session_date"] == as_of).any():
            continue
        prices: list[float] = []
        for target in targets:
            eligible = rows[rows["session_date"] <= target]
            if eligible.empty:
                break
            row = eligible.iloc[-1]
            price = row["close"] * row["adj_factor"]
            if pd.isna(price) or price <= 0:
                break
            prices.append(float(price))
        if len(prices) != 2:
            continue
        score = prices[0] / prices[1] - 1
        candidates.append(Candidate(security_id, score, f"{name}={score:.4f}"))
    return sorted(candidates, key=lambda item: ((item.score if name == "ret5" else -item.score), item.security_id))


@dataclass(frozen=True)
class ReversalFiveDay:
    """Rank ids by five-session adjusted close return, lowest first."""

    name: str = "reversal_5d"
    version: str = "1"
    lookback_sessions: int = 6

    def rank(self, panel: pd.DataFrame, cohort: list[str], as_of: date) -> list[Candidate]:
        """Rank the cohort by adjusted close return over five sessions."""
        return _rank_prices(panel, cohort, as_of, (0, 5), "ret5")


@dataclass(frozen=True)
class MomentumTwelveOne:
    """Rank ids by the twelve-month-to-one-month adjusted close return, highest first."""

    name: str = "momentum_12_1"
    version: str = "1"
    lookback_sessions: int = 253

    def rank(self, panel: pd.DataFrame, cohort: list[str], as_of: date) -> list[Candidate]:
        """Rank the cohort by adjusted close return from 252 to 21 sessions back."""
        return _rank_prices(panel, cohort, as_of, (21, 252), "mom12_1")


STRATEGIES: dict[str, Strategy] = {
    "reversal_5d": ReversalFiveDay(),
    "momentum_12_1": MomentumTwelveOne(),
}
