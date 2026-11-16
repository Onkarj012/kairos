"""Pure, immutable metadata for an explicitly selected owner pack session.

This module validates selection metadata only. It does not inspect a vault,
discover packs, authenticate an owner, or grant access to pack rows.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable

from .storage import PackKind

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_SELECTION_PACKS = 256


@dataclass(frozen=True)
class PackExpectation:
    """One explicitly named blind pack and its frozen expected identity."""

    identifier: str
    kind: PackKind
    manifest_sha256: str
    content_sha256: str


@dataclass(frozen=True)
class FrozenPackSelection:
    """Canonical ordered pack selection included in the durable audit record."""

    packs: tuple[PackExpectation, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(selection_bytes(self)).hexdigest()


def validate_pack_identifier(identifier: str) -> None:
    """Require a canonical nonempty relative POSIX path without traversal."""
    if (
        not isinstance(identifier, str)
        or not identifier
        or "\x00" in identifier
        or "\\" in identifier
        or identifier.startswith("/")
        or ":" in identifier
    ):
        raise ValueError("pack identifier must be a relative canonical path")
    parts = identifier.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("pack identifier cannot contain empty or traversal components")


def freeze_pack_selection(packs: Iterable[PackExpectation]) -> FrozenPackSelection:
    """Validate and freeze an explicit ordered selection; never enumerate files."""
    if isinstance(packs, (str, bytes)):
        raise ValueError("pack selection must be an ordered collection of metadata")
    collected = []
    for entry in packs:
        collected.append(entry)
        if len(collected) > MAX_SELECTION_PACKS:
            raise ValueError("pack selection exceeds the supported pack count")
    entries = tuple(collected)
    if not entries:
        raise ValueError("pack selection must not be empty")
    identifiers: set[str] = set()
    for entry in entries:
        if not isinstance(entry, PackExpectation):
            raise ValueError("pack selection contains malformed metadata")
        validate_pack_identifier(entry.identifier)
        if entry.identifier in identifiers:
            raise ValueError("pack selection contains a duplicate identifier")
        identifiers.add(entry.identifier)
        if not isinstance(entry.kind, PackKind):
            raise ValueError("expected pack kind must be daily or minute")
        if not isinstance(entry.manifest_sha256, str) or not _SHA256.fullmatch(entry.manifest_sha256):
            raise ValueError("expected manifest digest must be SHA-256 hex")
        if not isinstance(entry.content_sha256, str) or not _SHA256.fullmatch(entry.content_sha256):
            raise ValueError("expected content digest must be SHA-256 hex")
    return FrozenPackSelection(entries)


def selection_value(selection: FrozenPackSelection) -> list[dict[str, str]]:
    """Return stable JSON-compatible metadata in the authorized order."""
    validated = (
        freeze_pack_selection(selection.packs)
        if selection.packs
        else FrozenPackSelection(())
    )
    return [
        {
            "identifier": entry.identifier,
            "kind": entry.kind.value,
            "manifest_sha256": entry.manifest_sha256,
            "content_sha256": entry.content_sha256,
        }
        for entry in validated.packs
    ]


def selection_bytes(selection: FrozenPackSelection) -> bytes:
    return json.dumps(
        selection_value(selection), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
