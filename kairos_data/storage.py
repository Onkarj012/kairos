"""Checksummed, immutable JSONL and Parquet daily and minute packs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
import errno
import hashlib
from itertools import chain
import json
import math
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
    encoding: str = "jsonl"
    schema: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PackManifest":
        required = {"format_version", "kind", "partition", "rows", "sha256", "first_date", "last_date"}
        if not isinstance(value, Mapping) or not required.issubset(value) or set(value) - required - {"encoding", "schema"}:
            raise ValueError("malformed manifest fields")
        encoding = value.get("encoding", "jsonl")
        schema_value = value.get("schema", [])
        if encoding not in {"jsonl", "parquet"}:
            raise ValueError("unknown pack encoding")
        if not isinstance(schema_value, list) or any(
            not isinstance(field, dict) or set(field) != {"name", "type"}
            or not isinstance(field["name"], str) or not field["name"]
            or not isinstance(field["type"], str) or not field["type"]
            for field in schema_value
        ):
            raise ValueError("malformed schema metadata")
        if encoding == "jsonl" and schema_value:
            raise ValueError("JSONL packs cannot declare Parquet schema metadata")
        if encoding == "parquet" and (
            len({item["name"] for item in schema_value}) != len(schema_value)
            or not schema_value
            or any(item["type"] not in {"string", "bool", "int64", "double", "null"} for item in schema_value)
        ):
            raise ValueError("Parquet packs require unique schema metadata")
        if type(value["format_version"]) is not int or type(value["rows"]) is not int:
            raise ValueError("manifest version and row count must be integers")
        if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
            raise ValueError("manifest checksum must be SHA-256 hex")
        if value["partition"] not in {"pre2025", "blind", "live"}:
            raise ValueError("unknown pack partition")
        if (value["first_date"] is None) != (value["last_date"] is None):
            raise ValueError("manifest date coverage must be empty or complete")
        for name in ("first_date", "last_date"):
            if value[name] is not None:
                parsed = date.fromisoformat(value[name])
                if parsed.isoformat() != value[name]:
                    raise ValueError("manifest dates must be ISO dates")
        return cls(
            format_version=value["format_version"],
            kind=PackKind(value["kind"]),
            partition=str(value["partition"]),
            rows=value["rows"],
            sha256=str(value["sha256"]),
            first_date=value["first_date"],
            last_date=value["last_date"],
            encoding=encoding,
            schema=tuple((field["name"], field["type"]) for field in schema_value),
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
    value["schema"] = [{"name": name, "type": type_name} for name, type_name in manifest.schema]
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_manifest(directory: Path) -> PackManifest:
    path = directory / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("pack manifest must be a regular file")
    try:
        return PackManifest.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid pack manifest") from exc


def _arrow_type(type_name: str):
    import pyarrow as pa

    types = {
        "string": pa.string(), "bool": pa.bool_(), "int64": pa.int64(),
        "float64": pa.float64(), "null": pa.null(),
    }
    try:
        return types[type_name]
    except KeyError as exc:
        raise ValueError("unsupported Parquet schema type") from exc


def _scan_parquet(source: BinaryIO, manifest: PackManifest):
    import pyarrow.parquet as parquet

    source.seek(0)
    reader = parquet.ParquetFile(source)
    schema = tuple((field.name, str(field.type)) for field in reader.schema_arrow)
    if schema != manifest.schema:
        raise ValueError("Parquet schema does not match manifest")
    if len({name for name, _ in schema}) != len(schema) or any(
        type_name not in {"string", "bool", "int64", "double", "null"}
        for _, type_name in schema
    ):
        raise ValueError("Parquet schema contains unsupported or duplicate fields")
    if manifest.rows and (not schema or schema[0][0] != "date" or schema[0][1] != "string"):
        raise ValueError("Parquet schema must begin with an ISO string date column")
    metadata = reader.schema_arrow.metadata or {}
    if metadata.get(b"kairos.encoding") != b"parquet-v1" or metadata.get(b"kairos.schema") != json.dumps(
        [{"name": name, "type": type_name} for name, type_name in manifest.schema],
        separators=(",", ":"),
    ).encode():
        raise ValueError("Parquet schema metadata is missing or malformed")
    count = 0
    first = last = live_date = None
    for batch in reader.iter_batches(batch_size=4096):
        for row in batch.to_pylist():
            if not isinstance(row, dict):
                raise ValueError("Parquet rows must be records")
            try:
                row_date = _validate_partition_row(row, manifest.kind, manifest.partition)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Parquet row violates pack schema or date policy") from exc
            if manifest.partition == "live":
                if live_date is not None and row_date != live_date:
                    raise ValueError("each live pack must contain exactly one daily session")
                live_date = row_date
            first = row_date if first is None else min(first, row_date)
            last = row_date if last is None else max(last, row_date)
            count += 1
    return count, first, last, live_date


def _write_parquet(path: Path, rows: Iterable[Mapping[str, Any]], *, row_group_size: int):
    import pyarrow as pa
    import pyarrow.parquet as parquet

    iterator = iter(rows)
    first_row = next(iterator, None)
    if first_row is None:
        raise ValueError("Parquet packs require at least one row to define the schema")
    if not isinstance(first_row, Mapping) or not first_row:
        raise ValueError("Parquet rows must be nonempty mappings")
    names = tuple(first_row.keys())
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Parquet column names must be nonempty strings")
    if "date" not in names:
        raise ValueError("Parquet rows require a date field")
    names = ("date", *(name for name in names if name != "date"))
    if not isinstance(first_row["date"], str):
        raise ValueError("Parquet date values must be strings")
    types: dict[str, type | None] = {name: (None if first_row[name] is None else type(first_row[name])) for name in names}
    if any(t not in {None, str, bool, int, float} for t in types.values()):
        raise ValueError("Parquet packs support flat scalar values only")
    _check_types(first_row, types)

    batch = []
    count = 0
    schema = None
    writer = None
    try:
        for row in chain((first_row,), iterator):
            if not isinstance(row, Mapping) or set(row.keys()) != set(names):
                raise ValueError("Parquet row schema drift (missing or new fields)")
            _check_types(row, types, frozen=writer is not None)
            batch.append({name: row[name] for name in names})
            if len(batch) == row_group_size:
                if writer is None:
                    schema = _make_arrow_schema(names, types)
                    schema = schema.with_metadata(_parquet_metadata(names, types))
                    writer = parquet.ParquetWriter(path, schema)
                writer.write_table(pa.Table.from_pylist(batch, schema=schema), row_group_size=row_group_size)
                count += len(batch)
                batch = []
        if batch:
            if writer is None:
                schema = _make_arrow_schema(names, types)
                schema = schema.with_metadata(_parquet_metadata(names, types))
                writer = parquet.ParquetWriter(path, schema)
            writer.write_table(pa.Table.from_pylist(batch, schema=schema), row_group_size=row_group_size)
            count += len(batch)
    finally:
        if writer is not None:
            writer.close()
    return count, tuple((field.name, str(field.type)) for field in schema)


def _parquet_metadata(names, types):
    schema = [{"name": name, "type": str(_arrow_type(_type_name(types[name] or type(None))))} for name in names]
    return {
        b"kairos.encoding": b"parquet-v1",
        b"kairos.schema": json.dumps(schema, separators=(",", ":")).encode(),
    }


def _type_name(value_type):
    return {str: "string", bool: "bool", int: "int64", float: "float64", type(None): "null"}[value_type]


def _make_arrow_schema(names, types):
    import pyarrow as pa

    fields = []
    for name in names:
        value_type = types[name] or type(None)
        fields.append(pa.field(name, _arrow_type(_type_name(value_type))))
    return pa.schema(fields)


def _check_types(row, types, *, frozen: bool = False):
    for name, value in row.items():
        if value is not None:
            if type(value) not in {str, bool, int, float}:
                raise ValueError("Parquet packs support flat scalar values only")
            if type(value) is float and not math.isfinite(value):
                raise ValueError("Parquet float values must be finite")
            if types[name] is None:
                if frozen:
                    raise ValueError("Parquet row type inconsistency after schema was fixed")
                types[name] = type(value)
            elif type(value) is not types[name]:
                raise ValueError("Parquet row type inconsistency")

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
    rows_path = directory / ("rows.jsonl" if manifest.encoding == "jsonl" else "rows.parquet")
    if rows_path.is_symlink() or not rows_path.is_file():
        raise ValueError("pack rows must be a regular file")

    with rows_path.open("rb") as source:
        _verify_stream(source, manifest, snapshot=snapshot)
    return manifest, rows_path


MAX_JSONL_ROW_BYTES = 1024 * 1024


def _verify_stream(source: BinaryIO, manifest: PackManifest, *, snapshot: BinaryIO | None = None) -> None:
    """Validate an already pinned payload, retaining the same verified bytes."""
    digest = hashlib.sha256()
    count = 0
    first: date | None = None
    last: date | None = None
    live_date: date | None = None
    if manifest.encoding == "jsonl":
        while raw := source.readline(MAX_JSONL_ROW_BYTES + 1):
            if len(raw) > MAX_JSONL_ROW_BYTES:
                raise ValueError("JSONL row exceeds the supported byte limit")
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
    else:
        while raw := source.read(1024 * 1024):
            digest.update(raw)
            if snapshot is not None:
                snapshot.write(raw)
        count, first, last, live_date = _scan_parquet(source if snapshot is None else snapshot, manifest)
    if (count, digest.hexdigest(), first, last) != (
        manifest.rows,
        manifest.sha256,
        date.fromisoformat(manifest.first_date) if manifest.first_date else None,
        date.fromisoformat(manifest.last_date) if manifest.last_date else None,
    ):
        raise ValueError("pack row count, date range, or checksum does not match manifest")
    if manifest.encoding == "parquet" and count and live_date is None and manifest.partition == "live":
        raise ValueError("empty live pack")


def _read_pack_rows(
    directory: str | Path,
    *,
    expected_partition: str,
    expected_kind: PackKind | None = None,
    expected_date: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Internal verified reader used by ordinary readers and owner service."""
    pack_dir = Path(directory)
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as source:
        manifest, _ = _verify(pack_dir, expected_partition, snapshot=source)
        if expected_kind is not None and manifest.kind != expected_kind:
            raise ValueError("pack kind does not match the requested reader")
        if expected_date is not None and (
            manifest.first_date != expected_date or manifest.last_date != expected_date
        ):
            raise ValueError("pack date does not match the selected session")
        source.seek(0)
        if manifest.encoding == "jsonl":
            for raw in source:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError("pack rows must be JSON objects")
                yield row
        else:
            import pyarrow.parquet as parquet
            reader = parquet.ParquetFile(source)
            for batch in reader.iter_batches(batch_size=4096):
                yield from batch.to_pylist()


def read_verified_pack(
    directory: str | Path,
    *,
    expected_partition: str,
    expected_kind: PackKind | None = None,
    expected_date: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Read only an ordinary pre2025 or live pack, never a blind pack."""
    if expected_partition not in {"pre2025", "live"}:
        raise ValueError("ordinary readers cannot open the blind partition")
    yield from _read_pack_rows(
        directory,
        expected_partition=expected_partition,
        expected_kind=expected_kind,
        expected_date=expected_date,
    )


def _existing_matches(directory: Path, expected: PackManifest) -> bool:
    actual, _ = _verify(directory, expected.partition)
    return actual == expected


def write_pack(
    directory: str | Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    kind: PackKind | str,
    partition: str,
    encoding: str = "jsonl",
    row_group_size: int = 4096,
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
    if encoding not in {"jsonl", "parquet"}:
        raise ValueError("unknown pack encoding")
    if type(row_group_size) is not int or not 1 <= row_group_size <= 65536:
        raise ValueError("row_group_size must be between 1 and 65536")
    if partition not in {"pre2025", "blind", "live"}:
        raise ValueError("unknown pack partition")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        row_file = staging / ("rows.jsonl" if encoding == "jsonl" else "rows.parquet")
        digest = hashlib.sha256()
        count = 0
        first: date | None = None
        last: date | None = None
        schema: tuple[tuple[str, str], ...] = ()
        if encoding == "jsonl":
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
        else:
            def validated_rows():
                nonlocal first, last
                for row in rows:
                    row_date = _validate_partition_row(row, kind, partition)
                    if partition == "live" and first is not None and row_date != first:
                        raise ValueError("each live pack must contain exactly one daily session")
                    first = row_date if first is None else min(first, row_date)
                    last = row_date if last is None else max(last, row_date)
                    yield row
            count, schema = _write_parquet(row_file, validated_rows(), row_group_size=row_group_size)
            with row_file.open("rb") as source:
                while raw := source.read(1024 * 1024):
                    digest.update(raw)
            descriptor = os.open(row_file, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        manifest = PackManifest(
            format_version=1,
            kind=kind,
            partition=partition,
            rows=count,
            sha256=digest.hexdigest(),
            first_date=first.isoformat() if first else None,
            last_date=last.isoformat() if last else None,
            encoding=encoding,
            schema=schema,
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
