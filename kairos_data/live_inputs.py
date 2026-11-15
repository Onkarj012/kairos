"""Bounded, input-only reader for recent daily live rows."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any

from .storage import PackKind, _verify, read_verified_pack

_FORBIDDEN_COLUMN = re.compile(
    r"(^|_)(next|lead|forward|fwd|label|target|future|answer|score|return|pnl|outcome)(_|$)|^y_",
    re.I,
)
_SESSION_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_live_inputs(
    live_root: str | Path,
    *,
    as_of: date,
    input_columns: tuple[str, ...],
    sessions: int = 60,
) -> list[dict[str, Any]]:
    """Read only the last 60 session packs dated on or before ``as_of``.

    Each child pack is one session and contains input columns only. Directory
    names are the public date index, so future session files are never opened
    or parsed while answering an earlier request.
    """
    if sessions < 1 or sessions > 60:
        raise ValueError("live input window must be between 1 and 60 sessions")
    if not input_columns or len(set(input_columns)) != len(input_columns):
        raise ValueError("input_columns must be nonempty and unique")
    if any(
        not isinstance(name, str)
        or name.lower() in {"date", "symbol"}
        or _FORBIDDEN_COLUMN.search(name)
        for name in input_columns
    ):
        raise ValueError("input_columns may not include identifiers, labels, answers, future fields, scores, or returns")
    root = Path(live_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("live root must be a regular directory")
    dated_dirs: list[tuple[date, Path]] = []
    for entry in root.iterdir():
        if not _SESSION_NAME.fullmatch(entry.name):
            continue
        try:
            session_date = date.fromisoformat(entry.name)
        except ValueError:
            continue
        if session_date <= as_of:
            dated_dirs.append((session_date, entry))
    selected = sorted(dated_dirs, key=lambda item: item[0])[-sessions:]

    result: list[dict[str, Any]] = []
    for session_date, pack_path in selected:
        manifest, _ = _verify(pack_path, expected_partition="live")
        if manifest.kind != PackKind.DAILY:
            raise ValueError("live input reader accepts daily packs only")
        if manifest.first_date != session_date.isoformat() or manifest.last_date != session_date.isoformat():
            raise ValueError("live pack date must match its session directory")
        for row in read_verified_pack(pack_path, expected_partition="live"):
            missing = set(input_columns) - row.keys()
            if missing:
                raise ValueError(f"requested input columns are missing: {sorted(missing)}")
            output = {"date": row["date"]}
            if "symbol" in row:
                output["symbol"] = row["symbol"]
            output.update({name: row[name] for name in input_columns})
            result.append(output)
    return result
