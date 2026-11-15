"""Read durable prior attempt counts for the daily cash collector."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence


class CheckpointError(ValueError):
    """A retained collector budget input is missing or malformed."""


def _audit_seed_counts(path: Path | None) -> dict[str, int]:
    if path is None or not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"default shared-budget summary is unreadable: {path}") from exc
    if not isinstance(state, Mapping):
        raise CheckpointError(f"default shared-budget summary must be an object: {path}")
    urls = state.get("urls")
    if not isinstance(urls, Mapping):
        raise CheckpointError(f"default shared-budget summary has no urls mapping: {path}")
    counts: dict[str, int] = {}
    for url, entry in urls.items():
        if not isinstance(entry, Mapping):
            raise CheckpointError(f"shared-budget URL entry must be an object: {url!r}")
        attempts = entry.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise CheckpointError(f"shared-budget URL attempts must be a non-negative integer: {url!r}")
        counts[str(url)] = attempts
    return counts


def _retained_reservation_counts(roots: Sequence[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for root in roots:
        requests_root = Path(root) / "requests"
        if not requests_root.is_dir():
            continue
        for reservation_path in sorted(requests_root.glob("**/attempt-*/reservation.json")):
            try:
                reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CheckpointError(f"retained reservation is unreadable: {reservation_path}") from exc
            if not isinstance(reservation, Mapping):
                raise CheckpointError(f"retained reservation is not an object: {reservation_path}")
            url = reservation.get("requested_url")
            if not isinstance(url, str) or not url:
                raise CheckpointError(f"retained reservation has no requested_url: {reservation_path}")
            counts[url] = counts.get(url, 0) + 1
    return counts


def aggregate_seed_counts(
    seed_state_path: str | Path | None,
    existing_cash_roots: Sequence[str | Path],
) -> dict[str, int]:
    """Merge overlapping budget histories without double-counting attempts."""
    seed = _audit_seed_counts(Path(seed_state_path) if seed_state_path is not None else None)
    retained = _retained_reservation_counts([Path(value) for value in existing_cash_roots])
    urls = set(seed) | set(retained)
    return {url: max(seed.get(url, 0), retained.get(url, 0)) for url in urls}
