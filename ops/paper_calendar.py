"""Calendar and answer timing used by the local paper dashboard."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from evidence.daily_session_ledger import load_calendar_evidence
from ingest.calendar import parse_holiday_rows

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
    root = Path(root)
    runner = root / "daily-runner"
    ledger = runner / "session-ledger-v1/sessions.jsonl"
    frozen = {
        date.fromisoformat(row["session_date"]): str(row["state"])
        for row in map(json.loads, ledger.read_text(encoding="utf-8").splitlines())
    }
    if shutil.which("pdftotext") is None:
        raise CalendarMissing("pdftotext is not on PATH, so the retained NSE holiday PDFs cannot be read")
    holidays: dict[int, dict[date, str]] = {}
    annuals, _notices, _sources = load_calendar_evidence(root / "daily-graph/calendar-evidence-v1")
    for annual in annuals:
        rows = parse_holiday_rows(annual.text, year=annual.year)
        if rows:
            holidays[annual.year] = {
                date.fromisoformat(row["session_date"]): str(row.get("session_type", "holiday"))
                for row in rows
            }
    return Calendar(frozen, holidays)


def answer_cutoff(calendar: Calendar, signal_session: date) -> datetime:
    return datetime.combine(calendar.sessions_after(signal_session, 1)[0], ANSWER_CUTOFF, tzinfo=IST)


def now_ist() -> datetime:
    return datetime.now(IST)
