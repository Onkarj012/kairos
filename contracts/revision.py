"""Immutable source revision relationships."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


REVISION_FORMAT = "meridian.source-revision.v1"


@dataclass(frozen=True)
class RevisionRelationship:
    """The revision lineage bound to one retained source body."""

    revision_id: str
    status: str = "original"
    supersedes_revision_id: str | None = None

    def __post_init__(self) -> None:
        revision_id = str(self.revision_id).strip()
        if not revision_id:
            raise ValueError("revision_id is required")
        status = str(self.status).strip().lower()
        if status not in {"original", "current", "corrected", "superseded"}:
            raise ValueError(f"unsupported revision status: {status}")
        if status == "original" and self.supersedes_revision_id is not None:
            raise ValueError("an original revision cannot supersede another revision")
        object.__setattr__(self, "revision_id", revision_id)
        object.__setattr__(self, "status", status)
        if self.supersedes_revision_id is not None:
            superseded = str(self.supersedes_revision_id).strip()
            if not superseded:
                raise ValueError("supersedes_revision_id cannot be blank")
            if superseded == revision_id:
                raise ValueError("a revision cannot supersede itself")
            object.__setattr__(self, "supersedes_revision_id", superseded)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
