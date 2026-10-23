from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ingest.adjustment_factors import read_factors
from ingest.daily_panel import PANEL_FILE
from ops import daily_actions
from ops.alerts import alert_path
from ops.daily_runner import IST, Runner, RunnerPaths, start
from scripts.data import build_daily_panel, build_daily_universe
from tests.test_daily_paper_runner import CALENDAR, LIVE, FakeCash, _live_paths, _patch_strategies, _row, _run

URL = "https://www.nseindia.com/api/corporates-corporateActions"
SPLIT_BODY = json.dumps([{
    "symbol": "A", "series": "EQ", "isin": "INE000000001", "comp": "Name A", "exDate": "21-Oct-2026", "recDate": "21-Oct-2026",
    "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share",
}]).encode("utf-8")
Fetch = Callable[[date, date], dict[str, Any]]


def _fetch(requests: list[tuple[date, date]], failed: bool = False, body: bytes = SPLIT_BODY) -> Fetch:
    """A fake NSE request that records each range; failed answers like a timed-out request."""
    def fetch(start: date, end: date) -> dict[str, Any]:
        """Answer one range request with the body, or with a transport error."""
        requests.append((start, end))
        if failed:
            return {"requested_url": URL, "final_url": None, "status": None, "headers": [], "content_type": None, "retrieved_at": "2026-10-21T14:05:00+00:00", "body": b"", "error": "TimeoutError: timed out"}
        return {"requested_url": URL, "final_url": URL, "status": 200, "headers": [], "content_type": "application/json", "retrieved_at": "2026-10-21T14:05:00+00:00", "body": body, "error": None}
    return fetch


def _nightly_paths(tmp_path: Path) -> RunnerPaths:
    """Live test data plus the stage 0 files and symbol map the nightly merge reads."""
    paths = _live_paths(tmp_path)
    paths.covered_ids.write_text("security_id,symbols\nA,A\nB,B\nC,C\n", encoding="utf-8")
    observations = paths.runner / "corporate-actions-v1" / "observations.jsonl"
    observations.write_bytes(b"")
    manifest = {"range": {"end": "2026-10-19"}, "observations": {"file": observations.name, "sha256": hashlib.sha256(b"").hexdigest()}, "symbols": []}
    paths.actions_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return paths


def _stage0(paths: RunnerPaths, rows: list[dict[str, str]]) -> None:
    """Write the stage 0 observations and record their hash in the corporate-actions manifest."""
    body = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    (paths.runner / "corporate-actions-v1" / "observations.jsonl").write_bytes(body)
    manifest = json.loads(paths.actions_manifest.read_text(encoding="utf-8"))
    manifest["observations"]["sha256"] = hashlib.sha256(body).hexdigest()
    paths.actions_manifest.write_text(json.dumps(manifest), encoding="utf-8")


def _observation(ex_date: str, subject: str, revision_status: str) -> dict[str, str]:
    """One stage 0 observation for symbol A, in the stored shape."""
    return {"security_id": "A", "symbol": "A", "series": "EQ", "subject": subject, "ex_date": ex_date, "record_date": ex_date, "action_type_hint": "split", "revision_status": revision_status}


def _nightly_body(ex_date: str, subject: str) -> bytes:
    """An NSE answer holding one action for symbol A."""
    return json.dumps([{"symbol": "A", "series": "EQ", "isin": "INE000000001", "comp": "Name A", "exDate": ex_date, "recDate": ex_date, "subject": subject}]).encode("utf-8")


def _stub_cli_builds(monkeypatch: pytest.MonkeyPatch, fresh: pd.DataFrame | None = None) -> list[list[str]]:
    """Record the panel CLI arguments each live build passes; with a fresh panel, each build writes it as its output."""
    panel_calls: list[list[str]] = []

    def panel_main(argv: list[str]) -> int:
        """Record one panel build, and write the fresh panel to its output dir when one is given."""
        panel_calls.append(argv)
        if fresh is not None:
            out = Path(argv[argv.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            fresh.to_parquet(out / PANEL_FILE, index=False)
        return 0

    monkeypatch.setattr(build_daily_panel, "main", panel_main)
    monkeypatch.setattr(build_daily_universe, "main", lambda argv: 0)
    return panel_calls


def _ready(paths: RunnerPaths) -> None:
    """Start the account logs and run the reconcile job that the signal job expects first."""
    start(paths, datetime(2026, 10, 21, 12, 0, tzinfo=IST), "test start", CALENDAR)
    _run(paths, "reconcile", LIVE[0], "18:50")


def _signal(paths: RunnerPaths, tmp_path: Path, setting: str | None, fetch: Fetch) -> dict[str, object]:
    """Run the signal job with the setting in a .env file, or with no file when setting is None."""
    env_file = tmp_path / ".env"
    if setting is not None:
        env_file.write_text(f"KAIROS_DAILY_ACTIONS={setting}\n", encoding="utf-8")
    now = datetime.fromisoformat(f"{LIVE[0].isoformat()}T19:35").replace(tzinfo=IST)
    runner = Runner(paths, now, cash=FakeCash(set(LIVE)), calendar=CALENDAR, log=lambda _m: None, env_file=env_file, fetch_actions=fetch)
    return runner.run("signal")


def test_signal_makes_no_actions_request_when_setting_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    requests: list[tuple[date, date]] = []
    assert _signal(paths, tmp_path, None, _fetch(requests))["status"] == "ok"
    assert requests == []
    assert all("--adjustments" not in call for call in panel_calls)


def test_split_from_the_nightly_request_reaches_the_live_panel_factors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    requests: list[tuple[date, date]] = []
    assert _signal(paths, tmp_path, "1", _fetch(requests))["status"] == "ok"
    assert requests == [(date(2026, 10, 19), date(2026, 11, 20))]
    live_call = panel_calls[-1]
    factors, _hashes = read_factors(Path(live_call[live_call.index("--adjustments") + 1]))
    row = factors.iloc[0]
    assert (row["security_id"], row["ex_session"], row["factor"]) == ("A", LIVE[0], 0.5)


def test_failed_nightly_request_alerts_and_the_signal_job_still_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    requests: list[tuple[date, date]] = []
    assert _signal(paths, tmp_path, "1", _fetch(requests, failed=True))["status"] == "ok"
    assert "--adjustments" not in panel_calls[-1]
    alerts = [json.loads(line) for line in alert_path(paths.runner).read_text(encoding="utf-8").splitlines()]
    assert [(alert["level"], alert["message"]) for alert in alerts] == [("warning", "nightly corporate actions failed")]


SPLIT_SUBJECT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
TWO_RUPEE_SUBJECT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
REVISED_SUBJECT = "Revised: Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
REVISED_TWO_RUPEE_SUBJECT = "Revised: Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
CANCELLED_SUBJECT = "Cancelled: Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
BONUS_1_1_SUBJECT = "Bonus 1:1"
BONUS_1_2_SUBJECT = "Bonus 1:2"


def _panel(sessions: list[date]) -> pd.DataFrame:
    """A flat live panel for securities A, B and C over the given sessions."""
    return pd.DataFrame([_row(session, sid) for session in sessions for sid in ("A", "B", "C")])


def _alerts(paths: RunnerPaths) -> list[dict[str, Any]]:
    """Every alert the test root has written, oldest first."""
    return [json.loads(line) for line in alert_path(paths.runner).read_text(encoding="utf-8").splitlines()]


def test_action_whose_ex_session_is_tonight_is_applied_tonight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _panel([date(2026, 10, 19)]).to_parquet(paths.live_panel_dir / PANEL_FILE, index=False)
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch, fresh=_panel([date(2026, 10, 19), LIVE[0]]))
    assert _signal(paths, tmp_path, "1", _fetch([]))["status"] == "ok"
    assert [("--adjustments" in call) for call in panel_calls] == [False, True]
    live_call = panel_calls[-1]
    factors, _hashes = read_factors(Path(live_call[live_call.index("--adjustments") + 1]))
    row = factors.iloc[0]
    assert (row["security_id"], row["ex_session"], row["factor"]) == ("A", LIVE[0], 0.5)


def test_original_and_revision_with_different_ex_dates_fall_back_to_stage_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _stage0(paths, [_observation("2026-10-19", SPLIT_SUBJECT, "original_or_unspecified")])
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    assert _signal(paths, tmp_path, "1", _fetch([], body=_nightly_body("21-Oct-2026", REVISED_SUBJECT)))["status"] == "ok"
    assert all("--adjustments" not in call for call in panel_calls)
    alerts = _alerts(paths)
    assert [(alert["level"], alert["message"]) for alert in alerts] == [("warning", "nightly corporate actions failed")]
    assert "ValueError" in alerts[0]["context"]["error"] and "security_id A" in alerts[0]["context"]["error"]


def test_cancelled_original_does_not_block_its_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    body = json.dumps([
        {"symbol": "A", "series": "EQ", "isin": "INE000000001", "comp": "Name A", "exDate": ex_date, "recDate": ex_date, "subject": subject}
        for ex_date, subject in [("21-Oct-2026", SPLIT_SUBJECT), ("21-Oct-2026", CANCELLED_SUBJECT), ("22-Oct-2026", REVISED_TWO_RUPEE_SUBJECT)]
    ]).encode("utf-8")
    assert _signal(paths, tmp_path, "1", _fetch([], body=body))["status"] == "ok"
    assert not alert_path(paths.runner).exists()
    live_call = panel_calls[-1]
    factors, _hashes = read_factors(Path(live_call[live_call.index("--adjustments") + 1]))
    assert len(factors) == 1
    row = factors.iloc[0]
    assert (row["security_id"], row["ex_session"], row["factor"]) == ("A", LIVE[1], 0.2)


def test_conflicting_ratio_for_a_stage_0_action_falls_back_to_stage_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _stage0(paths, [_observation("2026-10-21", SPLIT_SUBJECT, "original_or_unspecified")])
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    assert _signal(paths, tmp_path, "1", _fetch([], body=_nightly_body("21-Oct-2026", TWO_RUPEE_SUBJECT)))["status"] == "ok"
    assert all("--adjustments" not in call for call in panel_calls)
    alerts = _alerts(paths)
    assert [(alert["level"], alert["message"]) for alert in alerts] == [("warning", "nightly corporate actions failed")]
    assert "ValueError" in alerts[0]["context"]["error"] and "security_id A" in alerts[0]["context"]["error"]


def test_new_split_conflict_is_not_hidden_by_a_stage_0_bonus_conflict_on_the_same_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_strategies(monkeypatch)
    monkeypatch.delenv("KAIROS_DAILY_ACTIONS", raising=False)
    paths = _nightly_paths(tmp_path)
    _stage0(paths, [
        _observation("2026-10-21", SPLIT_SUBJECT, "original_or_unspecified"),
        _observation("2026-10-21", BONUS_1_1_SUBJECT, "original_or_unspecified"),
        _observation("2026-10-21", BONUS_1_2_SUBJECT, "original_or_unspecified"),
    ])
    _ready(paths)
    panel_calls = _stub_cli_builds(monkeypatch)
    assert _signal(paths, tmp_path, "1", _fetch([], body=_nightly_body("21-Oct-2026", TWO_RUPEE_SUBJECT)))["status"] == "ok"
    assert all("--adjustments" not in call for call in panel_calls)
    alerts = _alerts(paths)
    assert [(alert["level"], alert["message"]) for alert in alerts] == [("warning", "nightly corporate actions failed")]
    assert "ValueError" in alerts[0]["context"]["error"] and "security_id A" in alerts[0]["context"]["error"]


def test_body_without_receipt_does_not_block_the_next_merge(tmp_path: Path) -> None:
    paths = _nightly_paths(tmp_path)
    raw_dir = paths.runner / daily_actions.RAW_DIR
    raw_dir.mkdir(parents=True)
    (raw_dir / "001_all-equities.json").write_bytes(b"[")  # a crash after the body write, before its receipt
    assert daily_actions.merge_nightly(paths, LIVE[0], _fetch([])) is not None
    assert (raw_dir / "002_all-equities.headers.json").exists()
