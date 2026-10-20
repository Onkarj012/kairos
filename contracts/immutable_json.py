"""Canonical JSON identity and atomic immutable JSON publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


IMMUTABLE_JSON_FORMAT = "meridian.immutable-json.v1"


class ImmutableCollisionError(RuntimeError):
    """Raised when an immutable path already contains different bytes."""


def canonical_json_bytes(value: object) -> bytes:
    return canonical_json_text(value).encode("utf-8")


def canonical_json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def write_immutable_json(path: str | Path, value: object) -> dict[str, object]:
    destination = Path(path)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError as exc:
            if destination.read_bytes() == encoded:
                return {"path": str(destination), "sha256": digest, "created": False}
            raise ImmutableCollisionError(f"immutable JSON collision: {destination}") from exc
        temporary_path.unlink()
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"path": str(destination), "sha256": digest, "created": True}


def read_immutable_json(path: str | Path) -> object:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
