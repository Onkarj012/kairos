from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
import pandas as pd
import pytest

from ingest.acquisition_budget import CheckpointError, aggregate_seed_counts
from ingest.daily_panel import build_panel
from ingest.daily_cash import HttpResponse, collect_daily_cash, nse_sec_bhavdata_full_url
from serve.api import trusted_origin
from scripts.data import collect_daily_cash as collector_cli


def test_collector_budget_merges_seed_and_retained_attempts_without_reset(tmp_path: Path) -> None:
    url = "https://nsearchives.nseindia.com/products/content/example.csv"
    seed = tmp_path / "budget.json"
    seed.write_text(json.dumps({"urls": {url: {"attempts": 2}}}), encoding="utf-8")
    cash_root = tmp_path / "cash"
    reservation = cash_root / "requests/2024-01-02/route/attempt-001/reservation.json"
    reservation.parent.mkdir(parents=True)
    reservation.write_text(json.dumps({"requested_url": url}), encoding="utf-8")

    assert aggregate_seed_counts(seed, (cash_root,)) == {url: 2}
    assert aggregate_seed_counts(None, (cash_root,)) == {url: 1}


def test_daily_collector_dry_run_uses_only_injected_fake_fetch(tmp_path: Path) -> None:
    session = date(2024, 1, 2)
    body = pd.DataFrame([{
        "SYMBOL": "AAA", "SERIES": "EQ", "DATE1": "02-Jan-2024",
        "PREV_CLOSE": "98", "OPEN_PRICE": "100", "HIGH_PRICE": "105",
        "LOW_PRICE": "99", "LAST_PRICE": "103", "CLOSE_PRICE": "104",
        "AVG_PRICE": "102", "TTL_TRD_QNTY": "1200", "TURNOVER_LACS": "12.345",
        "NO_OF_TRADES": "42", "DELIV_QTY": "600", "DELIV_PER": "50",
    }]).to_csv(index=False).encode()
    requests = []

    def fake_fetch(url: str, _connect: float, _read: float) -> HttpResponse:
        requests.append(url)
        return HttpResponse(
            status=200,
            headers={"Content-Type": "text/csv", "Last-Modified": "Tue, 02 Jan 2024 12:00:00 GMT"},
            body=body,
            final_url=url,
            retrieved_at="2024-01-02T18:00:00+00:00",
        )

    result = collect_daily_cash(session, tmp_path / "cash", transport=fake_fetch)

    assert result.status == "accepted"
    assert requests == [nse_sec_bhavdata_full_url(session)]


def test_daily_panel_builds_from_synthetic_retained_nse_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = date(2019, 10, 1)
    body = pd.DataFrame([{
        "SYMBOL": "AAA", "SERIES": "EQ", "DATE1": "01-Oct-2019",
        "OPEN_PRICE": "100", "HIGH_PRICE": "105", "LOW_PRICE": "99",
        "CLOSE_PRICE": "104", "PREV_CLOSE": "98", "TTL_TRD_QNTY": "1200",
        "TURNOVER_LACS": "12.345", "LAST_PRICE": "103", "AVG_PRICE": "102",
        "NO_OF_TRADES": "42", "DELIV_QTY": "600", "DELIV_PER": "50",
    }]).to_csv(index=False).encode()
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "body.csv").write_bytes(body)
    (source_root / "receipt.json").write_text(json.dumps({"body_path": "body.csv"}), encoding="utf-8")
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"source_inventory": {"price_recovery": {"by_root_and_receipt": [{
        "root": str(source_root), "receipt": "receipt.json", "session_date": session.isoformat(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }]}}}), encoding="utf-8")
    ledger = tmp_path / "sessions.jsonl"
    ledger.write_text(json.dumps({"session_date": session.isoformat(), "state": "expected"}) + "\n", encoding="utf-8")
    monkeypatch.setattr("ingest.daily_panel._build_commit", lambda: "synthetic")

    factors = pd.DataFrame([{
        "security_id": "AAA", "ex_session": session.isoformat(), "factor": 0.5,
        "nse_prev_close_adjusted": False,
    }])
    panel, manifest = build_panel(readiness, ledger, factors=factors)

    assert len(panel) == 1
    assert panel.iloc[0]["symbol"] == "AAA"
    assert panel.iloc[0]["close"] == 104
    assert panel.iloc[0]["adj_factor"] == 1
    assert panel.iloc[0]["prev_close_factor"] == 0.5
    assert manifest["present_sessions"] == 1
    assert manifest["missing"] == []


def test_dashboard_origin_guard_is_kept() -> None:
    assert trusted_origin({"origin": "http://127.0.0.1:8765"}, 8765)
    assert not trusted_origin({"origin": "https://example.invalid"}, 8765)


def test_collector_budget_rejects_malformed_attempt_counts(tmp_path: Path) -> None:
    seed = tmp_path / "budget.json"
    seed.write_text(json.dumps({"urls": {"url": {"attempts": True}}}), encoding="utf-8")
    with pytest.raises(CheckpointError, match="non-negative integer"):
        aggregate_seed_counts(seed, ())


def test_collector_budget_rejects_corrupt_non_object_and_negative_counts(tmp_path: Path) -> None:
    seed = tmp_path / "budget.json"
    for encoded in ('{not json', '[]', '{"urls":{"u":{"attempts":-1}}}'):
        seed.write_text(encoded, encoding="utf-8")
        with pytest.raises(CheckpointError):
            aggregate_seed_counts(seed, ())


def test_collector_cli_refuses_exhausted_budget_before_collection(tmp_path, monkeypatch):
    session = date(2024, 1, 2)
    url = nse_sec_bhavdata_full_url(session)
    seed = tmp_path / "budget.json"
    seed.write_text(json.dumps({"urls": {url: {"attempts": collector_cli.PLAN_ATTEMPT_LIMIT}}}))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("collector must not be invoked with exhausted budget")

    monkeypatch.setattr(collector_cli, "collect_daily_cash", forbidden)
    with pytest.raises(SystemExit, match="durably exhausted"):
        collector_cli.main(["--date", session.isoformat(), "--output-root", str(tmp_path / "cash"), "--seed-budget", str(seed)])
    assert calls == []
