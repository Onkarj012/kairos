"""Recommendation response schemas."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

@dataclass
class RecommendationSchema:
    symbol: str = ""
    side: str = "NO_TRADE"
    sleeve: str = ""
    entry_zone: dict[str, float] | None = None
    entry_window: dict[str, str] | None = None
    stop: float | None = None
    target: float | None = None
    horizon: str = ""
    probability: float = 0.0
    size_pct: float = 0.0
    evidence: str = ""
    evidence_link: str = ""
    model_version: str = ""
    model_link: str = ""
    gate_link: str = ""
    timestamp: str = ""
    status: str = "NO_TRADE"
    no_trade_reason: str = ""

    def validate(self, **kwargs):
        data = {**asdict(self), **kwargs}
        if data["side"] not in {"LONG", "SHORT", "NO_TRADE"}:
            raise ValueError("side must be LONG, SHORT, or NO_TRADE")
        if data["status"] not in {"PICK", "NO_TRADE", "HALTED", "EXPIRED"}:
            raise ValueError("status must be PICK, NO_TRADE, HALTED, or EXPIRED")
        if data["side"] == "NO_TRADE":
            data["status"] = "NO_TRADE"
            data["size_pct"] = 0.0
        elif data["status"] == "NO_TRADE":
            data["status"] = "PICK"
        data["timestamp"] = data["timestamp"] or datetime.now(timezone.utc).isoformat()
        return data


def no_trade_recommendation(reason: str, *, sleeve: str = "", timestamp: str | None = None) -> dict[str, Any]:
    return RecommendationSchema().validate(
        side="NO_TRADE",
        sleeve=sleeve,
        status="NO_TRADE",
        no_trade_reason=reason,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
    )
