"""Owner service contract for one-time final-test vault openings.

This module is intentionally absent from the package initializer and from all
training imports. Production must run it in a dedicated OS identity/process
that owns the vault and audit log. A caller-provided Python object is not proof
of identity against arbitrary code running as the same OS user.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator, Mapping, Protocol

from .policy import AuthorizationError
from .storage import PackKind, _read_pack_rows, _verify

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_STYLE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class OpeningRequest:
    requested_at: str
    frozen_style: str
    model_fingerprint: str
    requester: str
    reason: str


@dataclass(frozen=True)
class OpeningAudit:
    requested_at: str
    frozen_style: str
    model_fingerprint: str
    requester: str
    reason: str
    request_sha256: str


class OwnerAuthorizer(Protocol):
    """Implemented by an OS-authenticated owner service, never by env flags."""

    def authorize(self, requester: str, reason: str) -> bool: ...


def model_fingerprint(weights: bytes, settings: Mapping[str, Any]) -> str:
    """Hash exact weight bytes together with canonical model settings."""
    settings_bytes = json.dumps(
        dict(settings), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(b"kairos-model-fingerprint-v1\0")
    digest.update(len(weights).to_bytes(8, "big"))
    digest.update(weights)
    digest.update(settings_bytes)
    return digest.hexdigest()


def validate_opening_request(request: OpeningRequest) -> None:
    """Validate synthetic request metadata without accessing vault data."""
    try:
        timestamp = datetime.fromisoformat(request.requested_at)
    except ValueError as exc:
        raise ValueError("requested_at must be an ISO timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("requested_at must include a timezone")
    if not _STYLE.fullmatch(request.frozen_style):
        raise ValueError("frozen_style must be a stable style identifier")
    if not _FINGERPRINT.fullmatch(request.model_fingerprint):
        raise ValueError("model_fingerprint must be a SHA-256 hex digest")
    if not request.requester.strip() or len(request.requester) > 200:
        raise ValueError("requester is required")
    if not request.reason.strip() or len(request.reason) > 1000:
        raise ValueError("reason is required")


def _request_hash(request: OpeningRequest) -> str:
    encoded = json.dumps(asdict(request), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def authorize_and_audit(
    request: OpeningRequest,
    *,
    authorizer: OwnerAuthorizer,
    audit_log: str | Path,
) -> OpeningAudit:
    """Authorize and durably record one opening request per frozen style.

    This function records policy only; it never reads a vault or returns rows.
    The caller that later opens the vault must run in the dedicated owner
    process, after this record is durable.
    """
    validate_opening_request(request)
    if not authorizer.authorize(request.requester, request.reason):
        raise AuthorizationError("owner service denied final-test opening")
    path = Path(audit_log)
    if path.is_symlink():
        raise ValueError("audit log cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_APPEND | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("audit log must be a regular file")
        with os.fdopen(fd, "a", encoding="utf-8", closefd=False) as log:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.fchmod(fd, 0o600)
                existing_styles: set[str] = set()
                with os.fdopen(os.dup(fd), "r", encoding="utf-8") as reader:
                    reader.seek(0)
                    for line in reader:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ValueError("audit log has a malformed record") from exc
                        style = record.get("frozen_style")
                        if isinstance(style, str):
                            existing_styles.add(style)
                if request.frozen_style in existing_styles:
                    raise AuthorizationError("this frozen style already has a final-test opening")
                audit = OpeningAudit(
                    requested_at=request.requested_at,
                    frozen_style=request.frozen_style,
                    model_fingerprint=request.model_fingerprint,
                    requester=request.requester,
                    reason=request.reason,
                    request_sha256=_request_hash(request),
                )
                log.write(json.dumps(asdict(audit), sort_keys=True, separators=(",", ":")) + "\n")
                log.flush()
                os.fsync(fd)
                return audit
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def open_final_test_rows(
    request: OpeningRequest,
    *,
    authorizer: OwnerAuthorizer,
    audit_log: str | Path,
    vault_root: str | Path,
    pack: str | Path,
) -> tuple[OpeningAudit, PackKind, Iterator[dict[str, Any]]]:
    """Owner-service entry point. Audit is durable before held-out rows open.

    Deploy this function only in the OS-isolated owner process. The training
    process must not have filesystem access to ``vault_root`` or ``audit_log``.
    Tests must never call this function.
    """
    root = Path(vault_root)
    if root.is_symlink():
        raise ValueError("vault root cannot be a symlink")
    root = root.resolve(strict=True)
    requested_pack = Path(pack)
    candidate = requested_pack if requested_pack.is_absolute() else root / requested_pack
    cursor = candidate
    while cursor != root and root in cursor.parents:
        if cursor.is_symlink():
            raise ValueError("vault pack path cannot contain symlinks")
        cursor = cursor.parent
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("vault pack escapes the configured root") from exc
    audit = authorize_and_audit(request, authorizer=authorizer, audit_log=audit_log)
    manifest, _ = _verify(resolved, expected_partition="blind")
    return audit, manifest.kind, _read_pack_rows(resolved, expected_partition="blind")
