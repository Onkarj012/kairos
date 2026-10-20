import inspect
import json
from datetime import datetime, timedelta

from ops.broker import LiveExecutionGate
from ops.cron import run_kairos_daily_dag
from ops.graduation import evaluate_paper_graduation
from ops.ledger import ledger_path, load_ledger
from ops.live_eligibility import LiveEligibilityPolicy, evaluate_live_eligibility
from ops.paper import PaperSignal, append_signal_to_ledger, close_paper_trade, paper_lifecycle_summary
from serve.api import create_app
from serve.dashboard import build_dashboard_payload
from serve.schemas import RecommendationSchema, no_trade_recommendation


def test_paper_lifecycle_appends_hash_chain_guards_duplicates_and_closes(tmp_path):
    signal = PaperSignal(
        sleeve="F",
        signal_ts="2026-01-01T09:15:00",
        model_version="router-v1",
        symbol="NIFTY",
        side="LONG",
        quantity=2,
        intended_price=100.0,
        simulated_fill_price=100.2,
        backtest_expected_fill_price=100.0,
        costs=1.0,
    )

    first = append_signal_to_ledger(tmp_path, signal)
    duplicate = append_signal_to_ledger(tmp_path, signal)
    closed = close_paper_trade(tmp_path, sleeve="F", ledger_id=first["ledger_id"], exit_price=105.0, exit_ts="2026-01-01T10:15:00")
    rows = load_ledger(ledger_path(tmp_path, "F"))
    metadata = json.loads(rows[0]["metadata_json"])

    assert first["ledger_id"] == "L000001"
    assert duplicate["duplicate"] is True
    assert closed["status"] == "CLOSED"
    assert float(closed["pnl"]) == 8.6
    assert metadata["previous_hash"] == "GENESIS"
    assert metadata["chain_hash"]
    assert paper_lifecycle_summary(tmp_path, "F")["closed"] == 1


def test_graduation_passes_and_fails_for_phase_11_2_thresholds():
    start = datetime(2025, 1, 1)
    passing_ledger = [
        {
            "signal_ts": (start + timedelta(days=i)).isoformat(),
            "pnl": 10.0,
            "fill_divergence": 0.1,
        }
        for i in range(220)
    ]
    passing = evaluate_paper_graduation(
        passing_ledger,
        probabilities=[0.8] * 200,
        outcomes=[1] * 160 + [0] * 40,
        incidents=[],
    )
    failing = evaluate_paper_graduation(
        passing_ledger[:42],
        probabilities=[0.6, 0.6],
        outcomes=[0, 0],
        incidents=[{"type": "duplicate_signal", "explained": False}],
    )

    assert passing["live_eligible"] is True
    assert passing["checks"]["duration"]["pass"] is True
    assert failing["live_eligible"] is False
    assert failing["checks"]["trades"]["pass"] is False
    assert failing["checks"]["duplicate_signals"]["pass"] is False


def test_kairos_daily_dag_persists_manifest_and_failure_alert(tmp_path):
    def ok_step():
        return {"rows": 1}

    def failing_step():
        raise RuntimeError("fixture failure")

    result = run_kairos_daily_dag(
        [("19:00_ist_eod_ingest", ok_step), ("19:10_ist_quality_contracts", failing_step)],
        root=tmp_path,
    )

    alerts = (tmp_path / "alerts" / "alerts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    manifests = (tmp_path / "manifests" / "daily_dag.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert result["ok"] is False
    assert result["steps"][-1]["step"] == "19:10_ist_quality_contracts"
    assert "fixture failure" in alerts[-1]
    assert json.loads(manifests[-1])["flow"] == "kairos_daily_ist"


def test_api_serves_full_recommendation_no_trade_health_and_evidence(tmp_path):
    rec_path = tmp_path / "recommendations" / "2026-07-06.json"
    rec_path.parent.mkdir(parents=True)
    evidence_path = tmp_path / "evidence" / "gate-F.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(json.dumps({"gate": "pass"}), encoding="utf-8")

    pick = RecommendationSchema().validate(
        symbol="NIFTY",
        side="LONG",
        sleeve="F",
        entry_zone={"low": 100.0, "high": 101.0},
        entry_window={"start": "09:20", "end": "10:00"},
        stop=98.0,
        target=106.0,
        horizon="intraday",
        probability=0.68,
        size_pct=0.05,
        model_version="router-v1",
        gate_link="gate-F",
        evidence_link="gate-F",
        status="PICK",
    )
    no_trade = no_trade_recommendation("no signal cleared tau", sleeve="X")
    rec_path.write_text(json.dumps({"recommendations": [pick, no_trade]}), encoding="utf-8")

    app = create_app(root=tmp_path)
    recommendations = _call_route(app, "/recommendations")
    health = _call_route(app, "/health")
    evidence = _call_route(app, "/evidence/{token:path}", token="gate-F")
    dashboard = build_dashboard_payload([pick, no_trade], evidence={"gate-F": evidence}, halt={"hard_halt": False}, drift={"ok": True}, ledger={"rows": 1}, graduation={"live_eligible": False})

    assert health["ok"] is True
    assert recommendations["picks"][0]["symbol"] == "NIFTY"
    assert recommendations["no_trade"][0]["no_trade_reason"] == "no signal cleared tau"
    assert evidence["found"] is True
    assert dashboard["picks"][0]["size_pct"] == 0.05
    assert dashboard["no_trade"][0]["status"] == "NO_TRADE"
    assert set(["halt", "drift", "ledger", "graduation", "evidence"]).issubset(dashboard)


def test_live_eligibility_is_separate_from_triple_key_and_requires_graduation(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_LIVE", "1")
    token = tmp_path / "confirm"
    token.write_text("abc")
    (tmp_path / "LIVE_TOKEN").write_text("abc")

    triple_key = LiveExecutionGate.evaluate(root=tmp_path, sleeve="F", cli_live=True, confirm_token_path=token)
    denied = evaluate_live_eligibility(
        root=tmp_path,
        sleeve="F",
        model_version="router-v1",
        capital=2_000_000,
        requested_notional=100_000,
        graduation={"live_eligible": False},
        policy=LiveEligibilityPolicy(
            min_capital=1_000_000,
            sleeve_cap_pct=0.20,
            notional_cap=250_000,
            approved_sleeves={"F"},
            approved_models={"router-v1"},
        ),
    )
    allowed = evaluate_live_eligibility(
        root=tmp_path,
        sleeve="F",
        model_version="router-v1",
        capital=2_000_000,
        requested_notional=100_000,
        graduation={"live_eligible": True},
        policy={
            "min_capital": 1_000_000,
            "sleeve_cap_pct": 0.20,
            "notional_cap": 250_000,
            "approved_sleeves": {"F"},
            "approved_models": {"router-v1"},
        },
    )

    assert triple_key.is_clear() is True
    assert denied["live_eligible"] is False
    assert denied["checks"]["graduation"]["pass"] is False
    assert allowed["live_eligible"] is True


def _call_route(app, path: str, **kwargs):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            result = route.endpoint(**kwargs)
            if inspect.isawaitable(result):
                raise AssertionError("test helper expected sync route")
            return result
    raise AssertionError(f"route not found: {path}")
