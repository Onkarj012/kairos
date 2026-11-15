"""Strategy-neutral Kite login settings and private access-token storage."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_TOKEN_PATH = Path.home() / ".config/kairos/kite-token.json"


class KiteError(RuntimeError):
    """A Kite login failed without exposing credentials."""


def read_login_env(path: Path) -> dict[str, str]:
    """Read the legacy KEY=VALUE login settings file without importing Telegram or runner code."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def save_token(path: Path, access_token: str, user_id: str, login_date: date) -> None:
    """Write one private token record, replacing it atomically."""
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"access_token": access_token, "user_id": user_id, "login_date": login_date.isoformat()}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
