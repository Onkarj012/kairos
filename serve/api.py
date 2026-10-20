"""FastAPI recommendations API."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote


def _latest_recommendations(root: str | Path | None = None, path: str | Path | None = None) -> list[dict]:
    target = Path(path) if path is not None else None
    if target is None and root is not None:
        rec_dir = Path(root) / "recommendations"
        files = sorted(rec_dir.glob("*.json")) if rec_dir.exists() else []
        target = files[-1] if files else None
    if target is None or not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return list(payload.get("recommendations") or payload.get("picks") or [])


def _read_evidence(root: str | Path | None, token: str) -> dict:
    if root is None:
        return {"found": False, "error": "evidence root not configured"}
    safe = unquote(token)
    base = Path(root).resolve()
    candidates = [base / "evidence" / safe, base / "evidence" / f"{safe}.json"]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(base.resolve())):
                continue
            if resolved.exists() and resolved.is_file():
                if resolved.suffix == ".json":
                    return {"found": True, "path": str(resolved), "evidence": json.loads(resolved.read_text(encoding="utf-8"))}
                return {"found": True, "path": str(resolved), "text": resolved.read_text(encoding="utf-8")}
        except OSError:
            continue
    return {"found": False, "token": token}


def create_app(recommendations=None, *args, root=None, recommendations_path=None, **kwargs):
    from fastapi import FastAPI
    app = FastAPI(title="Kairos")
    static_data = list(recommendations or [])

    def current_recommendations():
        if recommendations_path is not None or root is not None:
            return _latest_recommendations(root=root, path=recommendations_path)
        return static_data

    @app.get("/recommendations")
    def list_recommendations():
        data = current_recommendations()
        return {
            "recommendations": data,
            "picks": [item for item in data if item.get("status") == "PICK" or item.get("side") in {"LONG", "SHORT"}],
            "no_trade": [item for item in data if item.get("status") == "NO_TRADE" or item.get("side") == "NO_TRADE"],
        }

    @app.get("/health")
    def health():
        return {"ok": True, "service": "Kairos", "recommendations": len(current_recommendations())}

    @app.get("/evidence/{token:path}")
    def evidence(token: str):
        payload = _read_evidence(root, token)
        return payload

    return app
