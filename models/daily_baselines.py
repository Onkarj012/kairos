"""Strategy interface for the daily swing runner and the two baseline strategies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


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
