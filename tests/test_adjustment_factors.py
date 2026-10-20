from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ingest.adjustment_factors import _on_day_hints, adjusted_return, build_events, factors_frame, parse_purpose
from ingest.daily_panel import apply_factors

RE_SPLIT_SUBJECT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"
SPLIT_SUBJECT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
DEBENTURE_SUBJECT = "Scheme Of Arangement- Bonus - 1 Debenture For 1 Equity Share Held"
DAYS = [date(2024, 1, day) for day in (1, 2, 3, 4)]


def test_parse_purpose_reads_split_and_bonus_ratios() -> None:
    """Rs-to-Re splits and A:B bonuses give their factors; a debenture bonus goes to review."""
    split = parse_purpose(RE_SPLIT_SUBJECT)
    assert split is not None and split.kind == "split" and split.factor == pytest.approx(0.1)
    bonus_one_to_one = parse_purpose("Bonus 1:1")
    assert bonus_one_to_one is not None and bonus_one_to_one.factor == pytest.approx(0.5)
    bonus_three_to_two = parse_purpose("Bonus- 3:2")
    assert bonus_three_to_two is not None and bonus_three_to_two.factor == pytest.approx(0.4)
    assert parse_purpose(DEBENTURE_SUBJECT) is None
    observations = pd.DataFrame(
        [{"security_id": "DEB", "symbol": "DEB", "ex_date": "2024-01-02", "subject": DEBENTURE_SUBJECT, "action_type_hint": "bonus"}]
    )
    panel = pd.DataFrame(_panel("DEB", [10.0] * 4, [10.0] * 4))
    events, review = build_events(observations, panel)
    assert events.empty
    assert list(review["reason"]) == ["unparsed_purpose"]


def test_undated_actions_are_reviewed_and_excluded_from_event_and_hint_joins() -> None:
    """Undated actions go to review and never reach merge_asof."""
    observations = pd.DataFrame(
        [{"security_id": "UND", "symbol": "UND", "ex_date": None, "subject": "Bonus 1:1", "action_type_hint": "bonus"}]
    )
    panel = pd.DataFrame(_panel("UND", [10.0] * 4, [10.0] * 4))

    events, review = build_events(observations, panel)

    assert events.empty
    assert review[["security_id", "reason"]].values.tolist() == [["UND", "missing_ex_date"]]
    assert _on_day_hints(observations, panel) == {}


def test_cancelled_and_conflicting_actions_go_to_review_but_repeats_and_kinds_apply() -> None:
    """Cancelled and conflicting ratios are withheld while duplicate and distinct same-day kinds behave as before."""
    observations = pd.DataFrame(
        [
            {"security_id": "CANCEL", "symbol": "CANCEL", "ex_date": "2024-01-01", "record_date": "2023-12-20", "subject": "Bonus 1:1", "revision_status": "original_or_unspecified"},
            {"security_id": "CANCEL", "symbol": "CANCEL", "ex_date": "2024-01-01", "record_date": "2023-12-21", "subject": "Bonus 1:1 - Cancelled", "revision_status": "cancelled"},
            {"security_id": "CANCEL", "symbol": "CANCEL", "ex_date": "2024-01-02", "record_date": "2023-12-22", "subject": "Bonus 1:1", "revision_status": "original_or_unspecified"},
            {"security_id": "CANCEL", "symbol": "CANCEL", "ex_date": "2024-01-03", "record_date": "2023-12-22", "subject": "Bonus 1:1 - Cancelled", "revision_status": "cancelled"},
            {"security_id": "CANCEL", "symbol": "CANCEL", "ex_date": "2024-01-04", "record_date": "2023-12-23", "subject": "Bonus 1:1", "revision_status": "original_or_unspecified"},
            {"security_id": "CANCEL", "symbol": "CANCEL", "ex_date": "2024-01-05", "record_date": "2023-12-24", "subject": "Bonus 1:1 - Cancelled", "revision_status": "cancelled"},
            {"security_id": "CONFLICT", "symbol": "CONFLICT", "ex_date": "2024-01-02", "subject": "Bonus 1:1", "revision_status": "original_or_unspecified"},
            {"security_id": "CONFLICT", "symbol": "CONFLICT", "ex_date": "2024-01-02", "subject": "Bonus 2:1", "revision_status": "original_or_unspecified"},
            {"security_id": "OK", "symbol": "OK", "ex_date": "2024-01-02", "subject": "Bonus 1:1", "revision_status": "original_or_unspecified"},
            {"security_id": "OK", "symbol": "OK", "ex_date": "2024-01-02", "subject": "Bonus 1:1", "revision_status": "original_or_unspecified"},
            {"security_id": "OK", "symbol": "OK", "ex_date": "2024-01-02", "subject": SPLIT_SUBJECT, "revision_status": "original_or_unspecified"},
        ]
    )
    panel = pd.concat([pd.DataFrame(_panel(sid, [10.0, 10.0, 5.0, 5.0], [9.0, 10.0, 10.0, 5.0])) for sid in ("CANCEL", "CONFLICT", "OK")])

    events, review = build_events(observations, panel)

    assert events[["security_id", "kind"]].values.tolist() == [["CANCEL", "bonus"], ["OK", "bonus"], ["OK", "split"]]
    assert events.loc[events["kind"] == "bonus", "factor"].tolist() == [0.5, 0.5]
    assert review[["security_id", "reason"]].values.tolist() == [
        ["CANCEL", "cancelled_action"],
        ["CANCEL", "cancelled_action"],
        ["CANCEL", "cancelled_action"],
        ["CANCEL", "cancelled_action"],
        ["CANCEL", "cancelled_action"],
        ["CONFLICT", "conflicting_factors"],
        ["CONFLICT", "conflicting_factors"],
    ]


def test_ambiguous_factors_are_refused_and_adjustment_build_writes_review(tmp_path, monkeypatch) -> None:
    """Panel factors refuse ambiguity while the adjustment CLI still writes the reviewable event."""
    from argparse import Namespace

    from scripts.data import build_adjustment_factors

    panel = pd.DataFrame(_panel("AMB", [10.0, 10.0, 7.0, 7.0], [9.0, 10.0, 8.0, 7.0]))
    observations = pd.DataFrame(
        [{"security_id": "AMB", "symbol": "AMB", "ex_date": "2024-01-03", "subject": "Bonus 1:1", "action_type_hint": "bonus"}]
    )
    events, review = build_events(observations, panel)
    with pytest.raises(ValueError, match="security_id AMB.*2024-01-03"):
        factors_frame(events)

    panel_path = tmp_path / "panel.parquet"
    cohort_path = tmp_path / "cohort.parquet"
    ledger_path = tmp_path / "sessions.jsonl"
    actions_dir = tmp_path / "actions"
    out = tmp_path / "out"
    panel.assign(series="EQ").to_parquet(panel_path, index=False)
    pd.DataFrame(columns=["security_id", "month"]).to_parquet(cohort_path, index=False)
    ledger_path.write_text("{}\n", encoding="utf-8")
    actions_dir.mkdir()
    (actions_dir / "observations.jsonl").write_text("[]\n", encoding="utf-8")
    args = Namespace(panel=panel_path, corporate_actions=actions_dir, cohort=cohort_path, ledger=ledger_path, out=out)
    monkeypatch.setattr(build_adjustment_factors, "_parser", lambda: Namespace(parse_args=lambda argv: args))
    monkeypatch.setattr(build_adjustment_factors, "read_corporate_actions", lambda _: observations)
    monkeypatch.setattr(build_adjustment_factors, "_expected_window", lambda _: DAYS)

    assert build_adjustment_factors.main([]) == 0
    assert pd.read_csv(out / "events.csv")["nse_prev_close_adjusted"].tolist() == ["ambiguous"]
    assert pd.read_csv(out / "review_purposes.csv")["reason"].tolist() == ["ambiguous_prev_close"]
    assert pd.read_json(out / "summary.json")["events_applied"].iloc[0] == 0


def _panel(security_id: str, closes: list[float], prev_closes: list[float]) -> list[dict[str, object]]:
    """Four sessions of one security with the given raw closes and prev_closes."""
    return [
        {"security_id": security_id, "symbol": security_id, "session_date": day, "close": close, "prev_close": prev}
        for day, close, prev in zip(DAYS, closes, prev_closes)
    ]


def test_factors_hold_levels_across_the_split_and_adjust_prev_close_once() -> None:
    """Pre-ex rows carry the factor, the ex-session prev_close is adjusted once, and the move stays near market."""
    # NOADJ: NSE left prev_close at 102 (old level). ADJ: NSE already wrote 20.4 (102 x 0.2).
    closes = [100.0, 102.0, 21.0, 21.2]
    panel = pd.DataFrame(
        _panel("NOADJ", closes, [99.0, 100.0, 102.0, 21.0]) + _panel("ADJ", closes, [99.0, 100.0, 20.4, 21.0])
    )
    observations = pd.DataFrame(
        [
            {"security_id": sid, "symbol": sid, "ex_date": "2024-01-03", "subject": SPLIT_SUBJECT, "action_type_hint": "split"}
            for sid in ("NOADJ", "ADJ")
        ]
    )

    events, review = build_events(observations, panel)
    assert dict(zip(events["security_id"], events["nse_prev_close_adjusted"])) == {"NOADJ": "false", "ADJ": "true"}
    assert review.empty
    frame = apply_factors(panel, factors_frame(events))

    def row(security_id: str, day: date) -> pd.Series:
        """The panel row for one security on one session."""
        return frame[(frame["security_id"] == security_id) & (frame["session_date"] == day)].iloc[0]

    for security_id in ("NOADJ", "ADJ"):
        assert row(security_id, DAYS[1])["adj_factor"] == pytest.approx(0.2)
        assert row(security_id, DAYS[2])["adj_factor"] == pytest.approx(1.0)
        assert row(security_id, DAYS[3])["adj_factor"] == pytest.approx(1.0)
        ex_row = row(security_id, DAYS[2])
        assert abs(adjusted_return(frame.loc[[ex_row.name]]).iloc[0]) < 0.05
    assert row("NOADJ", DAYS[2])["prev_close_factor"] == pytest.approx(0.2)
    assert row("ADJ", DAYS[2])["prev_close_factor"] == pytest.approx(1.0)
