"""Daily paper account: an append-only, hash-chained event log folded through the shared session engine.

A session step is complete once its marker exists: ``RECONCILED`` for a settle and
``SIGNAL`` for a plan. Events from an incomplete step are left out of the fold, and
the job re-runs that step. Event ids are deterministic, so the re-run's events that
already exist append as no-ops and only the missing tail is written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from evidence.research_ledger import ResearchEvent, ResearchLedgerWriter, read_events
from evidence.sealed_test import HOLDOUT_END, HOLDOUT_START
from ops.daily_rules import (
    ENGINE_EVENTS,
    AccountEvent,
    AccountState,
    apply_event,
    equity_frame,
    new_state,
    trades_frame,
)

ACCOUNTS_DIR = "daily-runner/accounts"
SETTLE_EVENTS = frozenset({
    "INTENT_EXPIRED", "UNFILLED", "POSITION_OPENED", "POSITION_RESCALED",
    "EXIT_PENDING", "POSITION_CLOSED", "RECONCILED",
})
PLAN_EVENTS = frozenset({"INTENT_CREATED", "SIGNAL"})


@dataclass(frozen=True)
class AccountSpec:
    """One paper capital pot running one registered strategy."""

    account_id: str
    strategy: str
    capital: int


PAPER_ACCOUNTS = tuple(
    AccountSpec(f"paper-{strategy}-{capital // 1000}k", strategy, capital)
    for strategy in ("reversal_5d", "momentum_12_1")
    for capital in (50_000, 100_000)
)


@dataclass
class AccountView:
    """The folded account: engine state, completed steps and open halts."""

    spec: AccountSpec
    state: AccountState
    engine_events: list[AccountEvent] = field(default_factory=list)
    settled: set[str] = field(default_factory=set)
    signalled: set[str] = field(default_factory=set)
    halts: list[dict[str, object]] = field(default_factory=list)
    clears: int = 0

    @property
    def halted(self) -> bool:
        """Return whether any halt is open."""
        return bool(self.halts)


def _event_key(event: AccountEvent) -> str:
    """Return the deterministic id suffix that makes a re-run append idempotent."""
    data = event.data
    if event.kind in {"INTENT_CREATED", "ORDER_SENT", "INTENT_EXPIRED", "UNFILLED", "POSITION_OPENED"}:
        return str(data["intent_id"])
    if event.kind in {"EXIT_PENDING", "POSITION_CLOSED"}:
        return f"{data['entry_session']}:{data['security_id']}"
    if event.kind == "POSITION_RESCALED":
        return f"{event.session}:{data['security_id']}"
    return event.session


def _job_id(event: AccountEvent) -> str:
    """Return the step a given event belongs to, so re-runs from another job still match."""
    if event.kind in SETTLE_EVENTS:
        return f"settle:{event.session}"
    if event.kind in PLAN_EVENTS:
        return f"signal:{event.session}"
    if event.kind == "ORDER_SENT":
        return f"place:{event.session}"
    return f"account:{event.session}"


def _to_engine(event: ResearchEvent) -> AccountEvent:
    """Rebuild an engine event from its stored ledger form."""
    payload = dict(event.payload)
    session = str(payload.pop("session"))
    return AccountEvent(event.event_type, session, payload)


class DailyAccount:
    """Read and append one account's event log."""

    def __init__(self, root: Path, spec: AccountSpec) -> None:
        self.spec = spec
        self.path = Path(root) / spec.account_id / "events.jsonl"
        self.writer = ResearchLedgerWriter(self.path)

    def exists(self) -> bool:
        """Return whether the account has been opened."""
        return self.path.exists()

    def open(self, *, session: str, verdict: str) -> None:
        """Write the ACCOUNT_OPENED event once."""
        self._append(AccountEvent("ACCOUNT_OPENED", session, {
            "account_id": self.spec.account_id,
            "strategy": self.spec.strategy,
            "capital": self.spec.capital,
            "mode": "paper",
            "stage1_verdict": verdict,
        }))

    def view(self) -> AccountView:
        """Fold the log into account state, leaving out events of incomplete steps."""
        events = read_events(self.path)
        if not events or events[0].event_type != "ACCOUNT_OPENED":
            raise ValueError(f"{self.spec.account_id}: log must start with ACCOUNT_OPENED")
        settled = {str(e.payload["session"]) for e in events if e.event_type == "RECONCILED"}
        signalled = {str(e.payload["session"]) for e in events if e.event_type == "SIGNAL"}
        view = AccountView(self.spec, new_state(float(events[0].payload["capital"])), settled=settled, signalled=signalled)
        for stored in events[1:]:
            event = _to_engine(stored)
            if event.kind in SETTLE_EVENTS and event.session not in settled:
                continue
            if event.kind == "INTENT_CREATED" and event.session not in signalled:
                continue
            if event.kind in ENGINE_EVENTS:
                apply_event(view.state, event)
                view.engine_events.append(event)
            elif event.kind == "HALTED":
                view.halts.append({"session": event.session, **event.data})
            elif event.kind == "HALT_CLEARED":
                view.halts = []
                view.clears += 1
        return view

    def append(self, events: list[AccountEvent]) -> None:
        """Append engine or signal events in order; existing identical events are no-ops."""
        for event in events:
            self._append(event)

    def halt(self, view: AccountView, *, session: str, reason: str, detail: str) -> None:
        """Open a halt that blocks the account until a manual clear."""
        if any(h["reason"] == reason for h in view.halts):
            return
        event = AccountEvent("HALTED", session, {"reason": reason, "detail": detail})
        self._append(event, key=f"{session}:{reason}:{view.clears}")
        view.halts.append({"session": session, "reason": reason, "detail": detail})

    def clear_halts(self, *, session: str, note: str) -> list[str]:
        """Clear every open halt with a note; return the cleared reasons."""
        view = self.view()
        if not view.halts:
            return []
        reasons = [str(h["reason"]) for h in view.halts]
        self._append(
            AccountEvent("HALT_CLEARED", session, {"cleared": reasons, "note": note}),
            key=str(view.clears + 1),
        )
        return reasons

    def trades(self) -> pd.DataFrame:
        """Return the account's trades in the backtest's table shape."""
        return trades_frame(self.view().engine_events)

    def equity(self) -> pd.DataFrame:
        """Return the account's daily marks in the backtest's table shape."""
        return equity_frame(self.view().engine_events)

    def _append(self, event: AccountEvent, *, key: str | None = None) -> None:
        """Write one event, refusing any settled or planned session inside the sealed holdout."""
        day = date.fromisoformat(event.session)
        if HOLDOUT_START <= day <= HOLDOUT_END:
            raise ValueError(f"{event.session} is inside the sealed holdout; accounts never record it")
        if "session" in event.data:
            raise ValueError("event data must not carry its own session key")
        self.writer.append(
            event_id=f"{event.kind}:{key or _event_key(event)}",
            event_type=event.kind,
            study_id=self.spec.account_id,
            job_id=_job_id(event),
            attempt=1,
            payload={"session": event.session, **event.data},
        )
