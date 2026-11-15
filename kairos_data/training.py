"""Reader for an isolated pre-2025 training pack."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .policy import BLIND_START, DateBoundaryError
from .storage import _read_manifest, read_verified_pack


def _path_under(root: Path, pack: Path) -> Path:
    if root.is_symlink():
        raise ValueError("training root cannot be a symlink")
    root = root.resolve(strict=True)
    candidate = pack if pack.is_absolute() else root / pack
    # Reject symlinks in every component, including nested pack files.
    cursor = candidate
    while cursor != root and root in cursor.parents:
        if cursor.is_symlink():
            raise ValueError("training pack path cannot contain symlinks")
        cursor = cursor.parent
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("training pack escapes the configured root") from exc
    return resolved


def read_training_rows(
    training_root: str | Path,
    pack: str | Path,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[dict[str, Any]]:
    """Read only the pre2025 partition after rejecting the query boundary.

    Date validation happens before opening the pack. The root must contain only
    pre-2025 material; held-out packs belong under a different OS-protected
    root and are not accepted by this API.
    """
    if start is not None and start >= BLIND_START:
        raise DateBoundaryError("training requests must start before 2025-01-01")
    if end is not None and end >= BLIND_START:
        raise DateBoundaryError("training requests must end before 2025-01-01")
    if start is not None and end is not None and start > end:
        raise ValueError("start must not be after end")
    pack_path = _path_under(Path(training_root), Path(pack))
    manifest = _read_manifest(pack_path)
    if manifest.partition != "pre2025":
        raise ValueError("training reader accepts only the pre2025 partition")
    if manifest.last_date is not None and date.fromisoformat(manifest.last_date) >= BLIND_START:
        raise DateBoundaryError("training pack manifest crosses the 2025-01-01 boundary")
    manifest_and_rows = read_verified_pack(pack_path, expected_partition="pre2025")
    # read_verified_pack verifies manifest and bytes before yielding records.
    for row in manifest_and_rows:
        row_date = date.fromisoformat(str(row["date"])[:10])
        if row_date >= BLIND_START:
            raise DateBoundaryError("pre2025 pack contains a blind-period row")
        if start is not None and row_date < start:
            continue
        if end is not None and row_date > end:
            continue
        yield row
