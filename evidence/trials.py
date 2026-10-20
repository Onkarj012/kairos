"""Load declared and observed strategy trial counts."""
from __future__ import annotations

import json
from pathlib import Path

from evidence.research_ledger import read_events


DEFAULT_TRIALS_PATH = Path(__file__).with_name("trials.json")


def global_k(path: Path = DEFAULT_TRIALS_PATH) -> int:
    """Load the declared global strategy trial count."""
    return int(json.loads(Path(path).read_text(encoding="utf-8"))["global_k"])


def kairos_trial_count(ledger_path: Path) -> int:
    """Count distinct strategy names recorded in gate verdict events."""
    return len({
        event.payload["strategy"]
        for event in read_events(ledger_path)
        if event.event_type == "GATE_VERDICT" and isinstance(event.payload.get("strategy"), str)
    })
