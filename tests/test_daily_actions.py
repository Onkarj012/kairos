from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from ingest.adjustment_factors import read_factors
from ops.alerts import alert_path
from ops.daily_runner import IST, Runner, RunnerPaths, start
from scripts.data import build_daily_panel, build_daily_universe
from tests.test_daily_paper_runner import CALENDAR, LIVE, FakeCash, _live_paths, _patch_strategies, _run

URL = "https://www.nseindia.com/api/corporates-corporateActions"
SPLIT_BODY = json.dumps([{
    "symbol": "A", "series": "EQ", "isin": "INE000000001", "comp": "Name A", "exDate": "21-Oct-2026", "recDate": "21-Oct-2026",
    "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share",
}]).encode("utf-8")
Fetch = Callable[[date, date], dict[str, Any]]


def _fetch(requests: list[tuple[date, date]], failed: bool = False) -> Fetch:
    """A fake NSE request that records each range; failed answers like a timed-out request."""
    def fetch(start: date, end: date) -> dict[str, Any]:
        """Answer one range request with the split, or with a transport error."""
        requests.append((start, end))
        if failed:
            return {"requested_url": URL, "final_url": None, "status": None, "headers": [], "content_type": None, "retrieved_at": "2026-10-21T14:05:00+00:00", "body": b"", "error": "TimeoutError: timed out"}
        return {"requested_url": URL, "final_url": URL, "status": 200, "headers": [], "content_type": "application/json", "retrieved_at": "2026-10-21T14:05:00+00:00", "body": SPLIT_BODY, "error": None}
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


def _stub_cli_builds(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record the panel CLI arguments the live build passes, without running the real builds."""
    panel_calls: list[list[str]] = []
    monkeypatch.setattr(build_daily_panel, "main", lambda argv: panel_calls.append(argv) or 0)
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
