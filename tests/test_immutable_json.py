"""Tests for durable immutable JSON publication."""
from __future__ import annotations

import json
import threading

import pytest

from contracts.immutable_json import (
    ImmutableCollisionError,
    canonical_json_text,
    write_immutable_json,
)


def test_first_write_creates_stable_pretty_json(tmp_path) -> None:
    path = tmp_path / "nested" / "value.json"
    value = {"z": 2, "a": [1, "two"]}

    result = write_immutable_json(path, value)

    expected = json.dumps(value, indent=2, sort_keys=True) + "\n"
    assert result["created"] is True
    assert result["path"] == str(path)
    assert path.read_text(encoding="utf-8") == expected


def test_identical_rewrite_is_idempotent_and_preserves_bytes(tmp_path) -> None:
    path = tmp_path / "value.json"
    first = write_immutable_json(path, {"value": 1})
    original = path.read_bytes()

    second = write_immutable_json(path, {"value": 1})

    assert second == {**first, "created": False}
    assert path.read_bytes() == original


def test_differing_rewrite_raises_collision_and_preserves_original(tmp_path) -> None:
    path = tmp_path / "value.json"
    write_immutable_json(path, {"value": 1})
    original = path.read_bytes()

    with pytest.raises(ImmutableCollisionError):
        write_immutable_json(path, {"value": 2})

    assert path.read_bytes() == original


def test_canonical_json_is_order_independent_and_rejects_nan() -> None:
    assert canonical_json_text({"b": 2, "a": 1}) == canonical_json_text({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        canonical_json_text({"value": float("nan")})


def test_concurrent_identical_writers_create_once(tmp_path) -> None:
    path = tmp_path / "value.json"
    barrier = threading.Barrier(8)
    results: list[dict[str, object]] = []
    result_lock = threading.Lock()

    def write() -> None:
        barrier.wait()
        result = write_immutable_json(path, {"value": [1, 2, 3]})
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert sum(result["created"] is True for result in results) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": [1, 2, 3]}
