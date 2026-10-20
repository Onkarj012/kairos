"""Point-in-time corporate-action contracts and price adjustment rules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timezone
import math
from typing import Any, Literal, cast


CorporateActionType = Literal[
    "split",
    "reverse_split",
    "bonus",
    "rights",
    "merger",
    "demerger",
    "acquisition",
    "other",
]

CORPORATE_ACTION_FORMAT = "meridian.corporate-action.v1"
CORPORATE_ACTION_SCHEMA: tuple[tuple[str, str], ...] = (
    ("security_id", "str"),
    ("action_type", "CorporateActionType"),
    ("announcement_date", "str|None"),
    ("ex_date", "str|None"),
    ("record_date", "str|None"),
    ("payable_date", "str|None"),
    ("adjustment_factor", "float"),
    ("factor_provenance", "str"),
    ("available_at", "str"),
    ("source", "str"),
    ("source_hash", "str"),
    ("row_identity", "str"),
    ("row_hash", "str"),
)

SUPPORTED_ACTION_TYPES = frozenset(
    {"split", "reverse_split", "bonus", "rights", "merger", "demerger", "acquisition", "other"}
)


def _required_text(value: object, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field_name} cannot be empty")
    return result


def _optional_date(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    result = _required_text(value, field_name)[:10]
    try:
        date.fromisoformat(result)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date: {value!r}") from exc
    return result


def _timestamp(value: str, field_name: str) -> str:
    result = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return result


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class CorporateActionRecord:
    """One canonical action for one immutable security identity."""

    security_id: str
    action_type: CorporateActionType
    announcement_date: str | None
    ex_date: str | None
    record_date: str | None
    payable_date: str | None
    adjustment_factor: float
    factor_provenance: str
    available_at: str
    source: str
    source_hash: str
    row_identity: str
    row_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "security_id", _required_text(self.security_id, "security_id"))
        action_type = _required_text(self.action_type, "action_type").lower()
        if action_type not in SUPPORTED_ACTION_TYPES:
            raise ValueError(f"unsupported corporate action type: {action_type}")
        object.__setattr__(self, "action_type", action_type)

        announcement = _optional_date(self.announcement_date, "announcement_date")
        ex_date = _optional_date(self.ex_date, "ex_date")
        record_date = _optional_date(self.record_date, "record_date")
        payable = _optional_date(self.payable_date, "payable_date")
        if not any((announcement, ex_date, record_date, payable)):
            raise ValueError("corporate action requires at least one event date")
        if announcement and any(event and announcement > event for event in (ex_date, record_date, payable)):
            raise ValueError("announcement_date cannot follow an action event date")
        if ex_date and payable and ex_date > payable:
            raise ValueError("ex_date cannot follow payable_date")
        if record_date and payable and record_date > payable:
            raise ValueError("record_date cannot follow payable_date")
        object.__setattr__(self, "announcement_date", announcement)
        object.__setattr__(self, "ex_date", ex_date)
        object.__setattr__(self, "record_date", record_date)
        object.__setattr__(self, "payable_date", payable)

        factor = float(self.adjustment_factor)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("adjustment_factor must be a finite positive number")
        object.__setattr__(self, "adjustment_factor", factor)
        object.__setattr__(self, "factor_provenance", _required_text(self.factor_provenance, "factor_provenance"))
        object.__setattr__(self, "available_at", _timestamp(self.available_at, "available_at"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))
        object.__setattr__(self, "row_identity", _required_text(self.row_identity, "row_identity"))
        object.__setattr__(self, "row_hash", _required_text(self.row_hash, "row_hash"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


CorporateAction = CorporateActionRecord


def normalize_action_type(value: str) -> CorporateActionType:
    normalized = _required_text(value, "action_type").lower()
    if normalized not in SUPPORTED_ACTION_TYPES:
        raise ValueError(f"unsupported corporate action type: {normalized}")
    return cast(CorporateActionType, normalized)


def schema_field_names(contract: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(contract))


assert schema_field_names(CorporateActionRecord) == tuple(name for name, _ in CORPORATE_ACTION_SCHEMA)


def action_effective_date(action: CorporateActionRecord) -> str | None:
    """Return the date on which a factor can affect a price series."""
    return action.ex_date or action.record_date or action.payable_date


def action_visible_at(action: CorporateActionRecord, decision_time: str | datetime) -> bool:
    """Return whether an action belongs in the information set at a cutoff."""
    cutoff = _coerce_cutoff(decision_time)
    if _instant(action.available_at) > cutoff:
        return False
    semantic_date = action.announcement_date or action_effective_date(action)
    return semantic_date is not None and cutoff.date() >= date.fromisoformat(semantic_date)


def corporate_actions_available_at(
    actions: list[CorporateActionRecord] | tuple[CorporateActionRecord, ...],
    decision_time: str | datetime,
) -> tuple[CorporateActionRecord, ...]:
    """Filter actions by source availability and announcement/effective date."""
    return tuple(action for action in actions if action_visible_at(action, decision_time))


def adjustment_factor_at(
    actions: list[CorporateActionRecord] | tuple[CorporateActionRecord, ...],
    security_id: str,
    price_date: str,
    *,
    available_cutoff: str | datetime | None = None,
) -> float:
    """Return the PIT factor for a raw price on ``price_date``.

    Factors are expressed as ``new_price / old_price``.  An action therefore
    scales prices before its effective date when the action was already known
    at the requested information cutoff.  Prices on or after the effective
    date are already on the new scale.
    """
    price_day = _date_value(price_date, "price_date")
    cutoff = available_cutoff or f"{price_day}T23:59:59.999999+00:00"
    factor = 1.0
    for action in corporate_actions_available_at(actions, cutoff):
        if action.security_id != security_id:
            continue
        effective = action_effective_date(action)
        if effective is not None and price_day < date.fromisoformat(effective):
            factor *= action.adjustment_factor
    return factor


def adjust_price(
    raw_price: float,
    security_id: str,
    price_date: str,
    actions: list[CorporateActionRecord] | tuple[CorporateActionRecord, ...],
    *,
    available_cutoff: str | datetime | None = None,
) -> float:
    """Apply all known pre-effective corporate-action factors to a raw price."""
    return float(raw_price) * adjustment_factor_at(
        actions,
        security_id,
        price_date,
        available_cutoff=available_cutoff,
    )


def _date_value(value: str, field_name: str) -> date:
    parsed = _optional_date(value, field_name)
    if parsed is None:
        raise ValueError(f"{field_name} is required")
    return date.fromisoformat(parsed)


def _coerce_cutoff(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("decision_time must include a timezone")
    return parsed.astimezone(timezone.utc)
