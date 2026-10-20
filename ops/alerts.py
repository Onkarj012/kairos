"""Operational alert sink."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Alert:
    level: str
    message: str
    context: dict
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def alert_path(root: str | Path) -> Path:
    return Path(root) / "alerts" / "alerts.jsonl"


def send_alert(level: str, message: str, *, root: str | Path | None = None, path: str | Path | None = None, **context) -> Alert:
    alert = Alert(
        level=level,
        message=message,
        context=context,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    if root is None and path is None:
        return alert
    target = Path(path) if path is not None else alert_path(Path(root))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(alert.to_dict(), sort_keys=True) + "\n")
    except Exception:
        if level.lower() in {"error", "fatal", "critical", "failure", "failed"}:
            raise
    return alert
