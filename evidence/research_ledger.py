"""Compatibility exports for the shared hash-chained event log."""
from ops.event_log import (
    Event,
    EventLogWriter,
    EVENT_TYPES,
    RESEARCH_LEDGER_FORMAT,
    LedgerConflictError,
    LedgerError,
    compute_event_hash,
    from_json_line,
    read_events,
    to_json_line,
    verify_chain,
)

ResearchEvent = Event
ResearchLedgerWriter = EventLogWriter

__all__ = [
    "EVENT_TYPES",
    "RESEARCH_LEDGER_FORMAT",
    "LedgerConflictError",
    "LedgerError",
    "ResearchEvent",
    "ResearchLedgerWriter",
    "compute_event_hash",
    "from_json_line",
    "read_events",
    "to_json_line",
    "verify_chain",
]
