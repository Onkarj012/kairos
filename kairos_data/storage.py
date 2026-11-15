"""Checksummed, immutable JSONL packs for daily and minute observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import BinaryIO, Iterable, Iterator, Mapping, Any


class PackKind(StrEnum):
    DAILY = "daily"
    MINUTE = "minute"


@dataclass(frozen=True)
class PackManifest:
    format_version: int
    kind: PackKind
    partition: str
    rows: int
    sha256: str
    first_date: str | None
    last_date: str | None

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PackManifest":
        return cls(
            format_version=int(value["format_version"]),
            kind=PackKind(value["kind"]),
            partition=str(value["partition"]),
            rows=int(value["rows"]),
            sha256=str(value["sha256"]),
            first_date=value["first_date"],
            last_date=value["last_date"],
        )


def _record_date(row: Mapping[str, Any]) -> date:
    try:
        return date.fromisoformat(str(row["date"])[:10])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("each row must have an ISO date field") from exc


_FORBIDDEN_LIVE_KEY = re.compile(
    r"(^|_)(next|lead|forward|fwd|label|target|future|answer|score|return|pnl|outcome)(_|$)|^y_",
    re.I,
)


def _validate_partition_row(row: Mapping[str, Any], kind: PackKind, partition: str) -> date:
    row_date = _record_date(row)
    if partition == "pre2025" and row_date >= date(2025, 1, 1):
        raise ValueError("pre2025 packs cannot contain rows dated 2025-01-01 or later")
    if partition == "blind" and row_date < date(2025, 1, 1):
        raise ValueError("blind packs cannot contain rows before 2025-01-01")
    if partition == "live":
        if kind != PackKind.DAILY:
            raise ValueError("live input packs must use daily rows")
        if any(_FORBIDDEN_LIVE_KEY.search(str(key)) for key in row):
            raise ValueError("live input packs cannot store labels, answers, future fields, scores, or returns")
        if any(not isinstance(value, (str, int, float, bool, type(None))) for value in row.values()):
            raise ValueError("live input packs require flat scalar values")
    return row_date


def _json_line(row: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(row), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _manifest_bytes(manifest: PackManifest) -> bytes:
    value = asdict(manifest)
    value["kind"] = manifest.kind.value
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_manifest(directory: Path) -> PackManifest:
    path = directory / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("pack manifest must be a regular file")
    try:
        return PackManifest.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid pack manifest") from exc


def _verify(
    directory: Path, expected_partition: str | None = None, *, snapshot: BinaryIO | None = None
) -> tuple[PackManifest, Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("pack must be a regular directory, not a symlink")
    manifest = _read_manifest(directory)
    if manifest.format_version != 1 or manifest.rows < 0:
        raise ValueError("unsupported or invalid pack manifest")
    if expected_partition is not None and manifest.partition != expected_partition:
        raise ValueError("pack is outside the requested partition")
    if manifest.partition == "blind" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise PermissionError("blind pack directory must deny group and other access")
    rows_path = directory / "rows.jsonl"
    if rows_path.is_symlink() or not rows_path.is_file():
        raise ValueError("pack rows must be a regular file")

    digest = hashlib.sha256()
    count = 0
    first: date | None = None
    last: date | None = None
    live_date: date | None = None
    with rows_path.open("rb") as source:
        for raw in source:
            digest.update(raw)
            if snapshot is not None:
                snapshot.write(raw)
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("pack contains invalid JSONL") from exc
            if not isinstance(row, dict):
                raise ValueError("pack rows must be JSON objects")
            row_date = _validate_partition_row(row, manifest.kind, manifest.partition)
            if manifest.partition == "live":
                if live_date is not None and row_date != live_date:
                    raise ValueError("each live pack must contain exactly one daily session")
                live_date = row_date
            first = row_date if first is None else min(first, row_date)
            last = row_date if last is None else max(last, row_date)
            count += 1
    if (count, digest.hexdigest(), first, last) != (
        manifest.rows,
        manifest.sha256,
        date.fromisoformat(manifest.first_date) if manifest.first_date else None,
        date.fromisoformat(manifest.last_date) if manifest.last_date else None,
    ):
        raise ValueError("pack row count, date range, or checksum does not match manifest")
    return manifest, rows_path


def _read_pack_rows(
    directory: str | Path, *, expected_partition: str
) -> Iterator[dict[str, Any]]:
    """Internal verified reader used by ordinary readers and owner service."""
    pack_dir = Path(directory)
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as source:
        _verify(pack_dir, expected_partition, snapshot=source)
        source.seek(0)
        for raw in source:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("pack rows must be JSON objects")
            yield row


def read_verified_pack(
    directory: str | Path, *, expected_partition: str
) -> Iterator[dict[str, Any]]:
    """Read only an ordinary pre2025 or live pack, never a blind pack."""
    if expected_partition not in {"pre2025", "live"}:
        raise ValueError("ordinary readers cannot open the blind partition")
    yield from _read_pack_rows(directory, expected_partition=expected_partition)


def _existing_matches(directory: Path, expected: PackManifest) -> bool:
    actual, _ = _verify(directory, expected.partition)
    return actual == expected


def write_pack(
    directory: str | Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    kind: PackKind | str,
    partition: str,
) -> PackManifest:
    """Create an immutable pack atomically; identical retries are idempotent.

    The caller owns source preservation. This function never removes or edits
    source files. A crash can leave a temporary directory, which can be safely
    discarded after checking that the destination was not published.
    """
    destination = Path(directory)
    if destination.is_symlink():
        raise ValueError("pack destination cannot be a symlink")
    absolute_parent = Path(os.path.abspath(destination.parent))
    if absolute_parent != absolute_parent.resolve(strict=False):
        raise ValueError("pack destination path cannot traverse symlinked parent directories")
    kind = PackKind(kind)
    if partition not in {"pre2025", "blind", "live"}:
        raise ValueError("unknown pack partition")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        row_file = staging / "rows.jsonl"
        digest = hashlib.sha256()
        count = 0
        first: date | None = None
        last: date | None = None
        with row_file.open("xb") as output:
            for row in rows:
                row_date = _validate_partition_row(row, kind, partition)
                if partition == "live" and first is not None and row_date != first:
                    raise ValueError("each live pack must contain exactly one daily session")
                raw = _json_line(row)
                output.write(raw)
                digest.update(raw)
                first = row_date if first is None else min(first, row_date)
                last = row_date if last is None else max(last, row_date)
                count += 1
            output.flush()
            os.fsync(output.fileno())
        manifest = PackManifest(
            format_version=1,
            kind=kind,
            partition=partition,
            rows=count,
            sha256=digest.hexdigest(),
            first_date=first.isoformat() if first else None,
            last_date=last.isoformat() if last else None,
        )
        with (staging / "manifest.json").open("xb") as output:
            output.write(_manifest_bytes(manifest))
            output.flush()
            os.fsync(output.fileno())
        directory_fd = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if destination.exists():
            if _existing_matches(destination, manifest):
                return manifest
            raise FileExistsError(f"refusing to replace different pack at {destination}")
        try:
            os.replace(staging, destination)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            if not _existing_matches(destination, manifest):
                raise FileExistsError(f"refusing to replace different pack at {destination}") from exc
        parent_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def partition_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split input records at the inclusive blind boundary, preserving rows."""
    from .policy import BLIND_START

    pre2025: list[dict[str, Any]] = []
    blind: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        (blind if _record_date(copy) >= BLIND_START else pre2025).append(copy)
    return pre2025, blind
