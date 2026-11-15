"""Calendar and answer timing used by the local paper dashboard."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ANSWER_CUTOFF = time(8, 40)
EXCLUDED_SESSIONS = {"2021-11-04"}


class CalendarMissing(RuntimeError):
    """Raised when retained session calendar evidence is unavailable."""


@dataclass(frozen=True)
class Calendar:
    frozen: dict[date, str]
    holidays: dict[int, dict[date, str]]

    def state(self, day: date) -> str:
        if day in self.frozen:
            return self.frozen[day]
        year = self.holidays.get(day.year)
        if year is None:
            raise CalendarMissing(f"no retained NSE holiday calendar for {day.year}")
        kind = year.get(day)
        if kind is not None:
            return "non_session" if kind == "holiday" else "expected"
        return "non_session" if day.weekday() >= 5 else "expected"

    def is_session(self, day: date) -> bool:
        return self.state(day) == "expected" and day.isoformat() not in EXCLUDED_SESSIONS

    def sessions_after(self, day: date, count: int) -> list[date]:
        found: list[date] = []
        current = day
        while len(found) < count:
            current += timedelta(days=1)
            if self.is_session(current):
                found.append(current)
        return found


def load_calendar(root: str | Path) -> Calendar:
    """Load retained normalized sessions, independent of the old proof chain.

    ``root/calendar`` uses the existing ``ingest.calendar.normalized_payload``
    sessions/manifest format. Only explicitly represented dates are usable;
    missing files, dates or invalid checksums close approvals, not account pages.
    """
    calendar_root = Path(root) / "calendar"
    try:
        manifest = json.loads((calendar_root / "manifest.json").read_text(encoding="utf-8"))
        payload = (calendar_root / "sessions.jsonl").read_bytes()
        rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
        if manifest["schema"] != "meridian.nse-calendar.public-v3":
            raise ValueError("unsupported normalized calendar schema")
        if manifest["session_sha256"] != hashlib.sha256(payload).hexdigest() or manifest["session_count"] != len(rows):
            raise ValueError("normalized calendar checksum or count mismatch")
        states = {"holiday": "non_session", **dict.fromkeys(
            ("regular", "muhurat", "special", "shortened", "contingency"), "expected"
        )}
        frozen: dict[date, str] = {}
        for row in rows:
            day = date.fromisoformat(row["session_date"])
            if day in frozen:
                raise ValueError("duplicate calendar session")
            frozen[day] = states[row["session_type"]]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise CalendarMissing(f"retained normalized calendar is unavailable: {exc}") from exc
    return Calendar(frozen=frozen, holidays={})


def answer_cutoff(calendar: Calendar, signal_session: date) -> datetime:
    return datetime.combine(calendar.sessions_after(signal_session, 1)[0], ANSWER_CUTOFF, tzinfo=IST)


def now_ist() -> datetime:
    return datetime.now(IST)
