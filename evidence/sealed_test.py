"""Sealed-test vault helpers."""

import json
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

from evidence.research_ledger import ResearchEvent, ResearchLedgerWriter, read_events


HOLDOUT_START = date(2025, 10, 1)
HOLDOUT_END = date(2026, 9, 30)


def load_dev_panel(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Load only development sessions from a parquet panel."""
    frame = pd.read_parquet(path, columns=columns, engine="pyarrow", filters=[("session_date", "<", HOLDOUT_START)])
    assert_dev_only(frame["session_date"])
    return frame


def assert_dev_only(sessions: Iterable[date]) -> None:
    """Reject any session in the sealed holdout window or later."""
    if any(session >= HOLDOUT_START for session in sessions):
        raise ValueError("development data contains a sealed holdout session")


HOLDOUT_SEAL_ID = "holdout-sealed-2025-10-01"


def ensure_holdout_sealed(ledger: ResearchLedgerWriter) -> ResearchEvent:
    """Append the holdout seal as the ledger's first event; a repeat call returns the stored seal."""
    events = read_events(ledger.path)
    if events and events[0].event_id != HOLDOUT_SEAL_ID:
        raise ValueError("holdout must be sealed before any research ledger event")
    return ledger.append(
        event_id=HOLDOUT_SEAL_ID, event_type="HOLDOUT_SEALED",
        study_id="daily-runner-stage1", job_id="holdout", attempt=1,
        payload={
            "holdout_start": HOLDOUT_START.isoformat(),
            "holdout_end": HOLDOUT_END.isoformat(),
            "policy": "dev runs must not read sessions >= holdout_start; spending the holdout needs Onkar's approval",
        },
    )


def holdout_sealed(path: Path) -> bool:
    """Return whether the ledger contains the holdout seal event."""
    return any(event.event_type == "HOLDOUT_SEALED" for event in read_events(path))


def run_sealed_test(selection_path, result_path, evaluator=None, **kwargs):
    selection_path = Path(selection_path)
    result_path = Path(result_path)
    if result_path.exists():
        raise ValueError("sealed test result already exists; holdout can be spent once")
    selection = json.loads(selection_path.read_text(encoding="utf-8")) if selection_path.exists() else {}
    result = evaluator(selection) if evaluator else {"passed": False, "reason": "no_evaluator"}
    payload = {"selection": selection, "sealed_test": result, "spent_once": True}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
