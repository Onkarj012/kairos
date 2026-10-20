"""Offline check of the classification rule order on tiny synthetic inputs."""
from __future__ import annotations

from datetime import date

import pandas as pd

from ingest.fund_classification import classify_symbols

EQUITY_L = (
    "SYMBOL,NAME OF COMPANY, SERIES,ISIN NUMBER\n"
    "ALPHA,Alpha Fund Trust,EQ,INF000000001\n"
    "BETA,Beta Works Limited,EQ,INE000000002\n"
    "GAMMA,Gamma Nifty ETF,EQ,\n"
    "DELTA,XYZ Limited,EQ,\n"
)
NAMECHANGE = "SYMBOL,OLD NAME OF THE COMPANY,NEW NAME OF THE COMPANY,EFFECTIVE DATE\n"
SYMBOLCHANGE = "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"


def test_classification_follows_rule_order() -> None:
    """Each symbol takes the first rule that applies, in spec order."""
    panel = pd.DataFrame(
        {
            "session_date": [date(2026, 9, 11)] * 5,
            "symbol": ["ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON"],
            "series": ["EQ"] * 5,
            "isin": ["INF000000001", None, None, None, None],
            "traded_value": [1.0] * 5,
        }
    )
    classification = classify_symbols(panel, EQUITY_L.encode(), NAMECHANGE.encode(), SYMBOLCHANGE.encode())
    got = {row["symbol"]: (row["class"], row["basis"]) for _, row in classification.iterrows()}
    assert got == {
        "ALPHA": ("fund", "isin_inf_udiff"),
        "BETA": ("equity", "isin_equity_l"),
        "GAMMA": ("fund", "name_fund_marker"),
        "DELTA": ("equity", "name_company"),
        "EPSILON": ("unknown", "no_isin_no_name"),
    }
