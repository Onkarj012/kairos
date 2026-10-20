"""Live-eligibility checks separate from broker triple-key execution."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ops.halts import kill_switch_path


@dataclass(frozen=True)
class LiveEligibilityPolicy:
    min_capital: float = 1_000_000.0
    sleeve_cap_pct: float = 0.30
    notional_cap: float = 300_000.0
    approved_sleeves: set[str] = field(default_factory=set)
    approved_models: set[str] = field(default_factory=set)


def evaluate_live_eligibility(
    *,
    root: str | Path,
    sleeve: str,
    model_version: str,
    capital: float,
    requested_notional: float,
    graduation: dict[str, Any],
    policy: LiveEligibilityPolicy | dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy if isinstance(policy, LiveEligibilityPolicy) else LiveEligibilityPolicy(**(policy or {}))
    switch = kill_switch_path(root, sleeve)
    checks = {
        "graduation": _check(bool(graduation.get("live_eligible")), graduation.get("live_eligible"), "paper graduation pass"),
        "capital": _check(float(capital) >= policy.min_capital, capital, f">= {policy.min_capital}"),
        "sleeve_cap": _check(float(requested_notional) <= float(capital) * policy.sleeve_cap_pct, requested_notional, f"<= {policy.sleeve_cap_pct:.2%} of capital"),
        "notional_cap": _check(float(requested_notional) <= policy.notional_cap, requested_notional, f"<= {policy.notional_cap}"),
        "approved_sleeve": _check(sleeve in policy.approved_sleeves, sleeve, f"in {sorted(policy.approved_sleeves)}"),
        "approved_model": _check(model_version in policy.approved_models, model_version, f"in {sorted(policy.approved_models)}"),
        "kill_switch": _check(not switch.exists(), str(switch), "absent"),
    }
    reasons = [f"{name}: observed {check['observed']} required {check['required']}" for name, check in checks.items() if not check["pass"]]
    return {"live_eligible": all(check["pass"] for check in checks.values()), "checks": checks, "reasons": reasons, "policy": _policy_dict(policy)}


def _check(passed: bool, observed: Any, required: str) -> dict[str, Any]:
    return {"pass": bool(passed), "observed": observed, "required": required}


def _policy_dict(policy: LiveEligibilityPolicy) -> dict[str, Any]:
    data = asdict(policy)
    data["approved_sleeves"] = sorted(policy.approved_sleeves)
    data["approved_models"] = sorted(policy.approved_models)
    return data
