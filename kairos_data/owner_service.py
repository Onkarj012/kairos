"""Owner service contract for one-time final-test vault openings.

This module is intentionally absent from the package initializer and from all
training imports. Production must run it in a dedicated OS identity/process
that owns the vault and audit log. A caller-provided Python object is not proof
of identity against arbitrary code running as the same OS user.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, BinaryIO, Iterator, Mapping, Protocol

from .owner_session import (
    FrozenPackSelection,
    PackExpectation,
    freeze_pack_selection,
    selection_value,
    validate_pack_identifier,
)
from .policy import AuthorizationError
from .protected_io import check_regular, open_absolute_directory, verify_selected_pack
from .storage import PackKind, PackManifest

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_STYLE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class OpeningRequest:
    requested_at: str
    frozen_style: str
    model_fingerprint: str
    requester: str
    reason: str
    pack_selection: tuple[PackExpectation, ...] = ()


@dataclass(frozen=True)
class OpeningAudit:
    requested_at: str
    frozen_style: str
    model_fingerprint: str
    requester: str
    reason: str
    pack_selection: tuple[PackExpectation, ...]
    selection_sha256: str
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
    if type(request.pack_selection) is not tuple:
        raise ValueError("pack selection must be an immutable tuple")
    if request.pack_selection:
        freeze_pack_selection(request.pack_selection)


def _request_hash(request: OpeningRequest) -> str:
    selection = FrozenPackSelection(request.pack_selection)
    if request.pack_selection:
        selection = freeze_pack_selection(request.pack_selection)
    encoded = json.dumps(
        {
            "requested_at": request.requested_at,
            "frozen_style": request.frozen_style,
            "model_fingerprint": request.model_fingerprint,
            "requester": request.requester,
            "reason": request.reason,
            "pack_selection": selection_value(selection),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validated_audit_style(record: object) -> str:
    fields = {
        "requested_at", "frozen_style", "model_fingerprint", "requester", "reason",
        "pack_selection", "selection_sha256", "request_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("audit log has a malformed record")
    if any(not isinstance(record[name], str) for name in fields - {"pack_selection"}):
        raise ValueError("audit log metadata must be text")
    selected = record["pack_selection"]
    if not isinstance(selected, list) or len(selected) > 256:
        raise ValueError("audit log has a malformed selection")
    packs = []
    for entry in selected:
        if not isinstance(entry, dict) or set(entry) != {"identifier", "kind", "manifest_sha256", "content_sha256"}:
            raise ValueError("audit log has a malformed pack identity")
        packs.append(PackExpectation(
            entry["identifier"], PackKind(entry["kind"]), entry["manifest_sha256"], entry["content_sha256"]
        ))
    request = OpeningRequest(
        record["requested_at"], record["frozen_style"], record["model_fingerprint"],
        record["requester"], record["reason"], tuple(packs),
    )
    validate_opening_request(request)
    selection = FrozenPackSelection(tuple(packs))
    if record["selection_sha256"] != selection.sha256 or record["request_sha256"] != _request_hash(request):
        raise ValueError("audit log record digest does not match its metadata")
    return request.frozen_style


def authorize_and_audit(
    request: OpeningRequest,
    *,
    authorizer: OwnerAuthorizer,
    audit_log: str | Path,
    audit_directory_fd: int | None = None,
) -> OpeningAudit:
    """Authorize and durably record one opening request per frozen style.

    This function records policy only; it never reads a vault or returns rows.
    The caller that later opens the vault must run in the dedicated owner
    process, after this record is durable.
    """
    validate_opening_request(request)
    selection = FrozenPackSelection(request.pack_selection)
    if request.pack_selection:
        selection = freeze_pack_selection(request.pack_selection)
    if not authorizer.authorize(request.requester, request.reason):
        raise AuthorizationError("owner service denied final-test opening")
    path = Path(audit_log)
    if audit_directory_fd is not None and (path.name != str(path) or path.name in {"", ".", ".."}):
        raise ValueError("descriptor-relative audit requires one filename")
    if audit_directory_fd is None and path.is_symlink():
        raise ValueError("audit log cannot be a symlink")
    if audit_directory_fd is None and not path.parent.is_dir():
        raise FileNotFoundError("audit directory must be durably provisioned before authorization")
    flags = os.O_CREAT | os.O_APPEND | os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600, dir_fd=audit_directory_fd)
    try:
        check_regular(os.fstat(fd), owner=os.geteuid(), immutable=False)
        with os.fdopen(fd, "a", encoding="utf-8", closefd=False) as log:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.fchmod(fd, 0o600)
                if os.fstat(fd).st_size > 8 * 1024 * 1024:
                    raise ValueError("audit log exceeds its supported size; owner maintenance required")
                existing_styles: set[str] = set()
                with os.fdopen(os.dup(fd), "r", encoding="utf-8") as reader:
                    reader.seek(0)
                    while line := reader.readline(256 * 1024 + 1):
                        if len(line) > 256 * 1024 or not line.endswith("\n"):
                            raise ValueError("audit log has an oversized or incomplete record")
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ValueError("audit log has a malformed record") from exc
                        style = _validated_audit_style(record)
                        if style in existing_styles:
                            raise ValueError("audit log contains duplicate style authorizations")
                        existing_styles.add(style)
                if request.frozen_style in existing_styles:
                    raise AuthorizationError("this frozen style already has a final-test opening")
                audit = OpeningAudit(
                    requested_at=request.requested_at,
                    frozen_style=request.frozen_style,
                    model_fingerprint=request.model_fingerprint,
                    requester=request.requester,
                    reason=request.reason,
                    pack_selection=selection.packs,
                    selection_sha256=hashlib.sha256(
                        json.dumps(
                            selection_value(selection),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    request_sha256=_request_hash(request),
                )
                line = _audit_line(audit)
                if len(line.encode("utf-8")) > 256 * 1024:
                    raise ValueError("audit record exceeds its supported size")
                log.write(line)
                log.flush()
                os.fsync(fd)
                parent_fd = (
                    os.dup(audit_directory_fd) if audit_directory_fd is not None
                    else os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                )
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
                return audit
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _audit_value(audit: OpeningAudit) -> dict[str, Any]:
    return {
        "requested_at": audit.requested_at,
        "frozen_style": audit.frozen_style,
        "model_fingerprint": audit.model_fingerprint,
        "requester": audit.requester,
        "reason": audit.reason,
        "pack_selection": selection_value(FrozenPackSelection(audit.pack_selection)),
        "selection_sha256": audit.selection_sha256,
        "request_sha256": audit.request_sha256,
    }


def _audit_line(audit: OpeningAudit) -> str:
    return json.dumps(_audit_value(audit), sort_keys=True, separators=(",", ":")) + "\n"


def _read_snapshot_rows(source: BinaryIO, manifest: PackManifest) -> Iterator[dict[str, Any]]:
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


class _SessionSnapshot:
    """Bound cumulative private snapshot storage for one explicitly sized session."""

    def __init__(self, source: BinaryIO, limit: int):
        self.source = source
        self.limit = limit
        self.bytes_written = 0

    def write(self, data: bytes) -> int:
        if self.bytes_written + len(data) > self.limit:
            raise ValueError("selected pack collection exceeds the session snapshot limit")
        written = self.source.write(data)
        self.bytes_written += written
        return written

    def seek(self, *args):
        return self.source.seek(*args)

    def __getattr__(self, name):
        return getattr(self.source, name)


_MAX_SESSION_SNAPSHOT_BYTES = 8 * 1024 * 1024 * 1024
_MAX_SESSION_SNAPSHOT_MEMORY_BYTES = 8 * 1024 * 1024


class _SnapshotRows(Iterator[dict[str, Any]]):
    def __init__(self, rows: Iterator[dict[str, Any]], snapshot: BinaryIO):
        self._rows = rows
        self._snapshot = snapshot
        self._closed = False

    def __iter__(self) -> _SnapshotRows:
        return self

    def __next__(self) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        try:
            return next(self._rows)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close_rows = getattr(self._rows, "close", None)
            if close_rows is not None:
                close_rows()
        finally:
            self._snapshot.close()


@dataclass(frozen=True)
class OpenedPackRows:
    identifier: str
    kind: PackKind
    rows: _SnapshotRows


@dataclass(frozen=True)
class OpenedSession:
    """Own all selected snapshots; use a context manager or call close()."""

    audit: OpeningAudit
    packs: tuple[OpenedPackRows, ...]

    def close(self) -> None:
        with ExitStack() as resources:
            for pack in self.packs:
                resources.callback(pack.rows.close)

    def __enter__(self) -> OpenedSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def open_final_test_session(
    request: OpeningRequest,
    *,
    authorizer: OwnerAuthorizer,
    audit_log: str | Path,
    vault_root: str | Path,
    audit_directory_fd: int | None = None,
    vault_directory_fd: int | None = None,
    snapshot_directory: str | None = None,
) -> OpenedSession:
    """Open exactly the request's frozen pack collection after durable audit.

    Deploy this function only in the OS-isolated owner process. The training
    process must not have filesystem access to ``vault_root`` or ``audit_log``.
    It resolves no directory listing and yields no rows unless every selected
    pack matches its frozen kind, manifest digest, and content digest.
    Use the returned session as a context manager or close it explicitly to
    release every snapshot, including packs that were never iterated.
    """
    validate_opening_request(request)
    selection = freeze_pack_selection(request.pack_selection)
    audit = authorize_and_audit(
        request, authorizer=authorizer, audit_log=audit_log, audit_directory_fd=audit_directory_fd
    )
    snapshots: list[tuple[PackExpectation, PackManifest, BinaryIO]] = []
    remaining = _MAX_SESSION_SNAPSHOT_BYTES
    memory_remaining = _MAX_SESSION_SNAPSHOT_MEMORY_BYTES
    root_fd = None
    try:
        root_fd = (
            os.dup(vault_directory_fd) if vault_directory_fd is not None
            else open_absolute_directory(os.fspath(vault_root), owner=os.geteuid(), private=True)
        )
        if snapshot_directory is not None:
            space = os.statvfs(snapshot_directory)
            remaining = min(remaining, max(0, space.f_bavail * space.f_frsize - 64 * 1024 * 1024))
        for expected in selection.packs:
            spool_limit = max(1, min(memory_remaining, remaining))
            snapshot = tempfile.SpooledTemporaryFile(max_size=spool_limit, mode="w+b", dir=snapshot_directory)
            bounded_snapshot = _SessionSnapshot(snapshot, remaining)
            try:
                manifest = verify_selected_pack(
                    root_fd, expected, bounded_snapshot, owner=os.geteuid(), expected_partition="blind"
                )
                size = bounded_snapshot.bytes_written
                remaining -= size
                memory_remaining = max(0, memory_remaining - min(size, spool_limit))
                snapshots.append((expected, manifest, snapshot))
            except BaseException:
                snapshot.close()
                raise

        opened = tuple(
            OpenedPackRows(
                expected.identifier, manifest.kind,
                _SnapshotRows(_read_snapshot_rows(snapshot, manifest), snapshot),
            )
            for expected, manifest, snapshot in snapshots
        )
        return OpenedSession(audit, opened)
    except BaseException:
        with ExitStack() as cleanup:
            for _, _, snapshot in snapshots:
                cleanup.callback(snapshot.close)
        raise
    finally:
        if root_fd is not None:
            os.close(root_fd)


def open_final_test_rows(
    request: OpeningRequest,
    *,
    authorizer: OwnerAuthorizer,
    audit_log: str | Path,
    vault_root: str | Path,
    pack: str | Path,
) -> tuple[OpeningAudit, PackKind, _SnapshotRows]:
    """Compatibility wrapper for exactly one pack named by the frozen request.

    The caller must supply a one-pack authorized selection. This wrapper cannot
    infer a digest by reading the pack before the durable authorization record.
    Close the returned row iterator if it is not fully consumed.
    """
    selected = freeze_pack_selection(request.pack_selection)
    if len(selected.packs) != 1:
        raise ValueError("single-pack opening requires exactly one frozen pack selection")
    supplied_path = os.fspath(pack)
    if not isinstance(supplied_path, str):
        raise ValueError("single-pack opening path must be a relative text path")
    identifier = supplied_path.replace(os.sep, "/")
    validate_pack_identifier(identifier)
    if identifier != selected.packs[0].identifier:
        raise ValueError("single-pack path must match the frozen selection identifier")
    session = open_final_test_session(
        request,
        authorizer=authorizer,
        audit_log=audit_log,
        vault_root=vault_root,
    )
    item = session.packs[0]
    return session.audit, item.kind, item.rows
