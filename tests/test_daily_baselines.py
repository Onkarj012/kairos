from datetime import date, timedelta

import pandas as pd
import pytest

from models.daily_baselines import MomentumTwelveOne, ReversalFiveDay, panel_as_of


def _panel(sessions: list[date], values: dict[str, list[float]], factors: dict[str, list[float]] | None = None) -> pd.DataFrame:
    """Build a small synthetic daily panel for strategy checks."""
    rows = []
    for security_id, closes in values.items():
        for session, close, factor in zip(sessions, closes, (factors or {}).get(security_id, [1.0] * len(sessions))):
            rows.append(
                {
                    "session_date": session,
                    "security_id": security_id,
                    "symbol": security_id,
                    "series": "EQ",
                    "isin": security_id,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "prev_close": close,
                    "traded_value": 1.0,
                    "volume": 1,
                    "adj_factor": factor,
                    "prev_close_factor": factor,
                }
            )
    return pd.DataFrame(rows)


def test_rank_rejects_future_panel_rows() -> None:
    """Strategies reject data dated after their ranking session."""
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(7)]
    panel = _panel(sessions, {"A": [10.0] * 7})
    with pytest.raises(ValueError, match="after as_of"):
        ReversalFiveDay().rank(panel, ["A"], sessions[-2])


def test_split_after_as_of_does_not_change_reversal_rank() -> None:
    """As-of rebasing removes a future split factor from past adjusted prices."""
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(6)]
    values = {"A": [100.0, 98.0, 95.0, 92.0, 90.0, 88.0], "B": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]}
    plain = _panel(sessions, values)
    split = _panel(sessions, values, {"A": [0.5] * 6, "B": [1.0] * 6})
    plain_slice = panel_as_of(plain, sessions, sessions[-1], ["A", "B"], 6)
    split_slice = panel_as_of(split, sessions, sessions[-1], ["A", "B"], 6)
    assert plain_slice["adj_factor"].tolist() == split_slice["adj_factor"].tolist()
    plain_rank = ReversalFiveDay().rank(plain_slice, ["A", "B"], sessions[-1])
    split_rank = ReversalFiveDay().rank(split_slice, ["A", "B"], sessions[-1])
    assert plain_rank == split_rank


def test_reversal_five_day_hand_computed_rank() -> None:
    """The lowest five-session return ranks first."""
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(6)]
    panel = _panel(sessions, {"A": [100, 100, 100, 100, 100, 90], "B": [100, 100, 100, 100, 100, 105]})
    ranked = ReversalFiveDay().rank(panel, ["B", "A"], sessions[-1])
    assert [candidate.security_id for candidate in ranked] == ["A", "B"]
    assert ranked[0].score == pytest.approx(-0.1)
    assert ranked[0].reason == "ret5=-0.1000"


def test_momentum_twelve_one_hand_computed_rank() -> None:
    """Momentum uses the adjusted closes 21 and 252 sessions back."""
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(253)]
    a = [100.0] * 253
    b = [100.0] * 253
    a[231] = 105.0
    b[231] = 90.0
    ranked = MomentumTwelveOne().rank(_panel(sessions, {"A": a, "B": b}), ["A", "B"], sessions[-1])
    assert [candidate.security_id for candidate in ranked] == ["A", "B"]
    assert ranked[0].score == pytest.approx(0.05)
    assert ranked[0].reason == "mom12_1=0.0500"
