"""Parse and atomically publish official NSE calendar evidence."""
from __future__ import annotations

import ctypes
import errno
from datetime import date, datetime, timedelta, time
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from contracts.calendar import (
    CalendarSession, CalendarSession as Session, SessionType, SourceEvidence,
    REGULAR_CLOSE, REGULAR_OPEN, canonical_json, ist_interval,
)


class CalendarError(ValueError):
    pass


_DATE_RE = re.compile(r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<day>\d{1,2}),?\s*(?P<year>20\d{2})", re.I)
_DATE_PREFIX_RE = re.compile(r"(?P<day>\d{1,2})\s+(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<year>20\d{2})", re.I)
_DATE_SHORT_RE = re.compile(r"(?P<day>\d{1,2})[- ](?P<month>[A-Za-z]{3})[- ](?P<year>20\d{2})", re.I)
_MONTHS = {name.lower(): index for index, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
_SHORT_MONTHS = {name[:3].lower(): index for name, index in _MONTHS.items()}


def _extract_date(value: str) -> date:
    match = _DATE_RE.search(value) or _DATE_PREFIX_RE.search(value) or _DATE_SHORT_RE.search(value)
    if not match:
        raise CalendarError(f"no calendar date in line: {value!r}")
    month = match.group("month")
    month_number = _MONTHS.get(month.lower()) if len(month) > 3 else _SHORT_MONTHS.get(month.lower())
    if month_number is None:
        raise CalendarError(f"unknown month in line: {value!r}")
    return date(int(match.group("year")), month_number, int(match.group("day")))


def _line_rows(text: str) -> list[tuple[date, str]]:
    rows: list[tuple[date, str]] = []
    for line in text.splitlines():
        if not _DATE_RE.search(line) and not _DATE_PREFIX_RE.search(line) and not _DATE_SHORT_RE.search(line):
            continue
        try:
            rows.append((_extract_date(line), re.sub(r"\s+", " ", line).strip()))
        except CalendarError:
            continue
    return rows


def parse_holiday_rows(text: str, *, year: int) -> list[dict[str, str]]:
    """Extract official holiday rows without inventing dates from another year."""
    statement_dates = {
        _extract_date(match.group("date"))
        for match in re.finditer(
            r"Muhurat\s+Trading\s+will\s+be\s+conducted\s+on\s+"
            r"(?:[A-Za-z]+,\s+)?(?P<date>"
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+"
            r"\d{1,2},?\s*20\d{2})",
            re.sub(r"\s+", " ", text),
            re.IGNORECASE,
        )
    }
    for holiday_date, line in _line_rows(text):
        if "muhurat trading" in line.lower() and holiday_date.year == year:
            statement_dates.add(holiday_date)
    result: list[dict[str, str]] = []
    for holiday_date, line in _line_rows(text):
        if holiday_date.year != year:
            continue
        if "muhurat trading will be conducted" in line.lower():
            continue
        result.append({
            "session_date": holiday_date.isoformat(),
            "label": line,
            "session_type": SessionType.MUHURAT.value if holiday_date in statement_dates else SessionType.HOLIDAY.value,
        })
    represented = {row["session_date"] for row in result}
    for holiday_date in sorted(statement_dates):
        if holiday_date.year == year and holiday_date.isoformat() not in represented:
            result.append({
                "session_date": holiday_date.isoformat(),
                "label": "Muhurat Trading date stated in annual holiday circular",
                "session_type": SessionType.MUHURAT.value,
            })
    return result


def _source_session(source: SourceEvidence, session_date: date, session_type: SessionType, label: str, interval: tuple[str, str] | None) -> CalendarSession:
    return CalendarSession(
        session_date=session_date.isoformat(), session_type=session_type, label=label,
        open_at=None if interval is None else interval[0], close_at=None if interval is None else interval[1],
        source_url=source.source_url, source_sha256=source.source_sha256,
        retrieved_at=source.retrieved_at, publication_date=source.publication_date,
        effective_date=source.effective_date, available_at=source.available_at,
        source_kind=source.source_kind, source_path=source.source_path,
    )


def build_calendar(
    start_date: date,
    end_date: date,
    holiday_sources: Mapping[int, tuple[SourceEvidence, str]],
    *,
    special_sessions: Iterable[Mapping[str, Any]] = (),
    contingency_sessions: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[CalendarSession], list[dict[str, Any]]]:
    """Build weekday sessions and official holiday overrides for an inclusive range."""
    if start_date > end_date:
        raise CalendarError("start_date must be on or before end_date")
    holidays: dict[str, CalendarSession] = {}
    gaps: list[dict[str, Any]] = []
    for year, (source, text) in holiday_sources.items():
        for row in parse_holiday_rows(text, year=year):
            day = date.fromisoformat(row["session_date"])
            if start_date <= day <= end_date:
                if row["session_date"] in holidays and holidays[row["session_date"]].label != row["label"]:
                    raise CalendarError(f"duplicate/conflicting holiday date: {row['session_date']}")
                if row["session_date"] in holidays:
                    raise CalendarError(f"duplicate holiday date: {row['session_date']}")
                session_type = SessionType(str(row.get("session_type", SessionType.HOLIDAY.value)))
                holidays[row["session_date"]] = _source_session(source, day, session_type, row["label"], None)
    sessions: dict[str, CalendarSession] = {}
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() < 5:
            source_pair = holiday_sources.get(cursor.year)
            if source_pair is None:
                gaps.append({"kind": "missing_year_source", "year": cursor.year})
            else:
                source, _ = source_pair
                opening, closing = ist_interval(cursor, REGULAR_OPEN, REGULAR_CLOSE)
                sessions[cursor.isoformat()] = _source_session(source, cursor, SessionType.REGULAR, "regular trading day", (opening, closing))
        cursor += timedelta(days=1)
    sessions.update(holidays)
    overrides: dict[str, CalendarSession] = {}
    for raw in list(special_sessions) + list(contingency_sessions):
        day = date.fromisoformat(str(raw["session_date"]))
        if not start_date <= day <= end_date:
            continue
        source = raw.get("source")
        if not isinstance(source, SourceEvidence):
            gaps.append({"kind": "missing_special_provenance", "session_date": day.isoformat()})
            continue
        session_type = SessionType(str(raw.get("session_type", "special")))
        start = raw.get("open_time")
        close = raw.get("close_time")
        if not isinstance(start, time) or not isinstance(close, time):
            gaps.append({"kind": "missing_special_times", "session_date": day.isoformat(), "session_type": session_type.value})
            continue
        opening, closing = ist_interval(day, start, close)
        key = day.isoformat()
        session = _source_session(source, day, session_type, str(raw.get("label", session_type.value)), (opening, closing))
        if key in overrides and overrides[key] != session:
            raise CalendarError(f"conflicting session definitions: {key}")
        overrides[key] = session
    sessions.update(overrides)
    ordered = [sessions[key] for key in sorted(sessions)]
    _validate_sessions(ordered)
    return ordered, _unique_gaps(gaps)


def _unique_gaps(gaps: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for gap in gaps:
        key = json.dumps(gap, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(dict(gap))
    return result


def _validate_sessions(sessions: Sequence[CalendarSession]) -> None:
    seen: set[str] = set()
    for session in sessions:
        if session.session_date in seen:
            raise CalendarError(f"duplicate session date: {session.session_date}")
        seen.add(session.session_date)


def validate_no_traversal(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CalendarError(f"unsafe relative path: {relative}")
    target = (root / path).resolve()
    if root.resolve() not in target.parents:
        raise CalendarError(f"path escapes root: {relative}")
    return target


def _atomic_noreplace_directory(staging: Path, root: Path) -> None:
    if root.exists() or root.is_symlink():
        raise CalendarError(f"output root collision: {root}")
    libc = ctypes.CDLL(None, use_errno=True)
    if os.sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(-2, os.fsencode(staging), -2, os.fsencode(root), 0x00000004)
    elif os.sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(staging), -100, os.fsencode(root), 0x00000001)
    else:
        raise CalendarError("atomic no-replace directory publication is unavailable")
    if result != 0:
        error = OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), str(root))
        if error.errno == errno.EEXIST:
            raise CalendarError(f"output root collision: {root}") from error
        raise CalendarError(f"atomic output publication failed: {root}") from error


def publish_directory(root: Path, files: Mapping[str, bytes]) -> None:
    """Publish a fresh immutable directory; never replaces an existing root."""
    root = Path(root)
    if root.exists() or root.is_symlink():
        raise CalendarError(f"output root collision: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    committed = False
    try:
        for relative, payload in files.items():
            target = validate_no_traversal(staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        for relative, payload in files.items():
            target = validate_no_traversal(staging, relative)
            if target.read_bytes() != payload:
                raise CalendarError(f"staging verification failed: {relative}")
        directory_fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        _atomic_noreplace_directory(staging, root)
        committed = True
    finally:
        if not committed and staging.exists():
            shutil.rmtree(staging)


def normalized_payload(sessions: Sequence[CalendarSession], gaps: Sequence[Mapping[str, Any]]) -> dict[str, bytes]:
    if any(not row.source_path for row in sessions):
        raise CalendarError("every normalized session requires a retained source_path")
    rows = b"".join(canonical_json(row.as_dict()) for row in sessions)
    gap_bytes = canonical_json({"gaps": list(gaps)})
    manifest = {
        "schema": "meridian.nse-calendar.public-v3",
        "session_count": len(sessions),
        "regular_trading_session_count": sum(1 for row in sessions if row.session_type in {SessionType.REGULAR, SessionType.MUHURAT, SessionType.SPECIAL, SessionType.SHORTENED, SessionType.CONTINGENCY}),
        "session_type_counts": {key.value: sum(1 for row in sessions if row.session_type is key) for key in SessionType},
        "gap_count": len(gaps),
        "session_sha256": hashlib.sha256(rows).hexdigest(),
        "gaps_sha256": hashlib.sha256(gap_bytes).hexdigest(),
        "available_at_policy": "null unless authoritative source fact; retrieved_at is not a substitute",
    }
    return {"sessions.jsonl": rows, "gaps.json": gap_bytes, "manifest.json": canonical_json(manifest)}
