"""Retained NSE reference CSVs (symbolchange, namechange, EQUITY_L) read through their sha256 receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ingest.daily_panel import derived_root

REFERENCE_DIR = "daily-runner/reference-v1"


def reference_dir() -> Path:
    """Directory holding the retained reference files and receipts.json."""
    return derived_root() / REFERENCE_DIR


def read_reference(name: str, root: Path | None = None) -> bytes:
    """Return one retained reference file's bytes after checking its recorded sha256."""
    base = Path(root) if root is not None else reference_dir()
    receipts = json.loads((base / "receipts.json").read_text(encoding="utf-8"))
    entry = next((item for item in receipts["files"] if item["name"] == name), None)
    if entry is None:
        raise ValueError(f"no receipt for reference file {name}")
    body = (base / name).read_bytes()
    if hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise ValueError(f"reference file {name} does not match its recorded sha256")
    return body
