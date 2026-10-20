"""Daily cron DAG scaffold."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path

from ops.alerts import send_alert


def run_daily_dag(steps: list[tuple[str, Callable[[], object]]]) -> dict:
    results = []
    for name, fn in steps:
        try:
            results.append({"step": name, "ok": True, "result": fn()})
        except Exception as exc:
            results.append({"step": name, "ok": False, "error": str(exc)})
            break
    return {"ok": all(r["ok"] for r in results), "steps": results}


@dataclass(frozen=True)
class KairosDailyDAG:
    steps: list[tuple[str, Callable[[], object]]]
    root: str | Path | None = None
    flow_timezone: str = "Asia/Kolkata"
    flow_name: str = "kairos_daily_ist"
    metadata: dict = field(default_factory=dict)

    @classmethod
    def default(cls, *, root: str | Path | None = None, **steps: Callable[[], object]) -> "KairosDailyDAG":
        ordered = [
            ("19:00_ist_eod_ingest", steps.get("eod_ingest", _noop)),
            ("19:10_ist_quality_contracts", steps.get("quality_contracts", _noop)),
            ("19:20_ist_features", steps.get("features", _noop)),
            ("19:35_ist_recommendations", steps.get("recommendations", _noop)),
            ("19:45_ist_paper_ledger", steps.get("paper_ledger", _noop)),
            ("19:50_ist_halts_drift_alerts", steps.get("halts_drift_alerts", _noop)),
            ("08:30_ist_preopen_update", steps.get("preopen_update", _noop)),
        ]
        return cls(ordered, root=root)

    def run(self) -> dict:
        started_at = datetime.now(timezone.utc).isoformat()
        results = []
        for name, fn in self.steps:
            try:
                results.append({"step": name, "ok": True, "result": fn()})
            except Exception as exc:
                failure = {"step": name, "ok": False, "error": str(exc), "type": type(exc).__name__}
                results.append(failure)
                if self.root is not None:
                    send_alert("error", f"Kairos daily DAG failed at {name}", root=self.root, flow=self.flow_name, failure=failure)
                break
        manifest = {
            "flow": self.flow_name,
            "timezone": self.flow_timezone,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": all(result["ok"] for result in results),
            "steps": results,
            "metadata": self.metadata,
        }
        if self.root is not None:
            _append_manifest(Path(self.root), manifest)
        return manifest

    def to_dict(self) -> dict:
        data = asdict(self)
        data["root"] = None if self.root is None else str(self.root)
        data["steps"] = [name for name, _ in self.steps]
        return data


def run_kairos_daily_dag(steps: list[tuple[str, Callable[[], object]]] | None = None, *, root: str | Path | None = None, **named_steps: Callable[[], object]) -> dict:
    dag = KairosDailyDAG(steps, root=root) if steps is not None else KairosDailyDAG.default(root=root, **named_steps)
    return dag.run()


def manifest_path(root: str | Path) -> Path:
    return Path(root) / "manifests" / "daily_dag.jsonl"


def _append_manifest(root: Path, manifest: dict) -> None:
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, sort_keys=True, default=str) + "\n")


def _noop() -> dict:
    return {"status": "skipped"}
