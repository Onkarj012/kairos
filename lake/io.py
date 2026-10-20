from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Iterable


Row = dict[str, str]


def read_csv(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def deterministic_csv_sample(path: Path, limit_per_split: int, split_for_row: callable) -> dict[str, list[Row]]:
    """One-pass bounded sample, stable across interruptions and machines.

    Every source row is inspected but only the lowest hash-ranked rows for each
    temporal split are retained.  This is the safe fallback for multi-GB legacy
    CSV compatibility inputs; Parquet remains the preferred full-data engine.
    """
    ranked: dict[str, list[tuple[bytes, Row]]] = {"train": [], "validation": [], "test": []}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            split = split_for_row(row)
            if split not in ranked or row.get("label_excluded") == "true":
                continue
            key = hashlib.sha256(f"{row.get('symbol')}|{row.get('timestamp')}".encode()).digest()
            bucket = ranked[split]
            bucket.append((key, row))
            if len(bucket) > limit_per_split * 2:
                bucket.sort(key=lambda item: item[0])
                del bucket[limit_per_split:]
    return {name: [row for _, row in sorted(values, key=lambda item: item[1]["timestamp"])] for name, values in ranked.items()}


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def write_parquet_cache(csv_path: Path, parquet_path: Path) -> Path:
    """Write a partition-compatible Parquet cache without changing CSV CLIs."""
    try:
        import polars as pl
    except ImportError as exc:  # pragma: no cover - dependency is declared by project
        raise RuntimeError("Parquet cache requires polars") from exc
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    pl.read_csv(csv_path, try_parse_dates=True).write_parquet(parquet_path)
    return parquet_path


def parse_simple_yaml(path: Path) -> dict[str, object]:
    """Tiny YAML subset parser for repo configs: mappings and string lists only."""
    result: dict[str, object] = {}
    stack_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and stack_key:
            result.setdefault(stack_key, [])
            assert isinstance(result[stack_key], list)
            result[stack_key].append(line[4:].strip())
            continue
        if line.startswith("  ") and stack_key:
            key, value = line.strip().split(":", 1)
            if not isinstance(result.get(stack_key), dict):
                result[stack_key] = {}
            result[stack_key][key.strip()] = coerce_scalar(value.strip())
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            result[key] = coerce_scalar(value)
            stack_key = None
        else:
            result[key] = []
            stack_key = key
    return result


def coerce_scalar(value: str) -> object:
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
