"""Small Linux owner-boundary checks for a later isolated service process.

This module is metadata and adapter code only. It does not create sockets,
provision identities, inspect protected paths, or make the existing owner
opening functions safe to call from an untrusted process.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import socket
import struct
import stat
import sys


_U32_MAX = (1 << 32) - 1
_UCRED = struct.Struct("=iII")


def _valid_uid(value: object) -> bool:
    return type(value) is int and 1 <= value < _U32_MAX


def validate_service_identities(service_uid: int, training_uid: int) -> tuple[int, int]:
    """Require distinct, valid, non-root Linux service and training UIDs."""
    if not _valid_uid(service_uid) or not _valid_uid(training_uid):
        raise ValueError("service and training UIDs must be non-root unsigned integers")
    if service_uid == training_uid:
        raise ValueError("service and training UIDs must differ")
    return service_uid, training_uid


@dataclass(frozen=True)
class PeerCredentials:
    """Kernel-reported credentials returned by Linux SO_PEERCRED."""

    pid: int
    uid: int
    gid: int


def peer_credentials(connected_stream: object) -> PeerCredentials:
    """Read peer PID/UID/GID from an already-connected Linux AF_UNIX stream.

    The handle must expose the ordinary socket ``family``, ``type``,
    ``getpeername`` and ``getsockopt`` interface. A fake Python object can
    imitate that interface; this check cannot defend against malicious code
    already running inside the trusted service process.
    """
    if sys.platform != "linux":
        raise OSError("SO_PEERCRED owner authentication requires Linux")
    try:
        family = getattr(connected_stream, "family", None)
        kind = getattr(connected_stream, "type", None)
    except Exception as exc:
        raise OSError("could not inspect peer socket metadata") from exc
    if family != socket.AF_UNIX:
        raise OSError("peer handle must be an AF_UNIX socket")
    if kind != socket.SOCK_STREAM:
        raise OSError("peer handle must be a stream socket")

    try:
        getpeername = getattr(connected_stream, "getpeername", None)
        getsockopt = getattr(connected_stream, "getsockopt", None)
        if not callable(getpeername) or not callable(getsockopt):
            raise OSError("peer handle does not provide connected socket operations")
        peer_address = getpeername()
        if not isinstance(peer_address, (str, bytes)):
            raise OSError("connected peer address is malformed")
        raw = getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
    except Exception as exc:
        raise OSError("could not read connected peer credentials") from exc
    if not isinstance(raw, bytes) or len(raw) != _UCRED.size:
        raise ValueError("kernel peer credentials have an invalid representation")
    pid, uid, gid = _UCRED.unpack(raw)
    if pid <= 0 or not 0 <= uid < _U32_MAX or not 0 <= gid < _U32_MAX:
        raise ValueError("kernel peer credentials contain an invalid PID, UID, or GID")
    return PeerCredentials(pid=pid, uid=uid, gid=gid)


def canonical_peer_requester(credentials: PeerCredentials) -> str:
    """Return the only requester string accepted for these kernel credentials."""
    return f"unix-peer:pid={credentials.pid}:uid={credentials.uid}:gid={credentials.gid}"


class KernelPeerOwnerAuthorizer:
    """Adapt one connected peer to the existing ``OwnerAuthorizer`` protocol.

    Authorization succeeds only for a root-UID peer, with the requester bound
    exactly to that peer's kernel credentials, a nonblank reason, and this
    process running under the configured non-root service UID. The configured
    training UID must be different. This is an adapter for a later deployment,
    not a listener, connection manager, or process-isolation mechanism.
    """

    def __init__(self, connected_stream: object, *, service_uid: int, training_uid: int):
        self._connected_stream = connected_stream
        self._service_uid, self._training_uid = validate_service_identities(
            service_uid, training_uid
        )

    def authorize(self, requester: str, reason: str) -> bool:
        if not isinstance(requester, str) or not requester:
            return False
        if not isinstance(reason, str) or not reason.strip():
            return False
        if os.geteuid() != self._service_uid:
            return False
        try:
            credentials = peer_credentials(self._connected_stream)
        except (OSError, ValueError):
            return False
        return (
            credentials.uid == 0
            and requester == canonical_peer_requester(credentials)
        )

    @property
    def requester(self) -> str:
        """Canonical request identity bound to the captured peer credentials."""
        return canonical_peer_requester(peer_credentials(self._connected_stream))


@dataclass(frozen=True)
class PathMetadata:
    """Immutable supplied stat-like evidence; no path is opened or inspected."""

    path: str
    mode: int
    uid: int
    is_symlink: bool = False


@dataclass(frozen=True)
class ProtectedLayout:
    """Evidence groups for code/config, their ancestors, and private dirs."""

    code_and_config: tuple[PathMetadata, ...]
    ancestors: tuple[PathMetadata, ...]
    data_dir: PathMetadata
    audit_dir: PathMetadata
    runtime_dir: PathMetadata


def _validate_metadata_path(item: PathMetadata) -> None:
    if not isinstance(item, PathMetadata):
        raise ValueError("layout contains malformed metadata")
    if (
        not isinstance(item.path, str)
        or not item.path.startswith("/")
        or "\x00" in item.path
        or (
            item.path != "/"
            and any(part in {"", ".", ".."} for part in item.path.split("/")[1:])
        )
    ):
        raise ValueError("layout paths must be canonical absolute paths")
    if type(item.mode) is not int or item.mode < 0:
        raise ValueError("layout mode is malformed")
    if type(item.uid) is not int or not 0 <= item.uid < _U32_MAX:
        raise ValueError("layout UID is malformed")
    if type(item.is_symlink) is not bool or item.is_symlink:
        raise ValueError("protected layout cannot contain symlinks")
    if stat.S_ISLNK(item.mode):
        raise ValueError("protected layout cannot contain symlinks")


def validate_protected_layout(layout: ProtectedLayout, *, service_uid: int) -> bool:
    """Validate caller-supplied mode/owner evidence for a later owner layout.

    Code/config files must be root-owned regular files with no write bits.
    Ancestors must be root-owned directories with no group/other write bits.
    Data, audit, and runtime directories must be service-owned directories,
    owner-accessible, and inaccessible to group/other. This validates only
    supplied evidence: it is not race-proof enforcement, provisioning, or
    proof of live ownership. Deployment must derive evidence safely and
    enforce protected paths before loading service code.
    """
    if not _valid_uid(service_uid):
        raise ValueError("service UID must be a valid non-root unsigned integer")
    if not isinstance(layout, ProtectedLayout):
        raise ValueError("protected layout metadata is required")
    groups = (layout.code_and_config, layout.ancestors)
    if any(type(group) is not tuple or not group for group in groups):
        raise ValueError("code/config and ancestor evidence must be nonempty tuples")
    if len(layout.code_and_config) < 2:
        raise ValueError("layout must describe code, config, data, audit, and runtime paths")

    private_dirs = (layout.data_dir, layout.audit_dir, layout.runtime_dir)
    all_items = (*layout.code_and_config, *layout.ancestors, *private_dirs)
    for item in all_items:
        _validate_metadata_path(item)
    all_paths = [item.path for item in all_items]
    if len(all_paths) != len(set(all_paths)):
        raise ValueError("protected layout paths must be unique")
    ancestor_paths = {item.path for item in layout.ancestors}
    for item in (*layout.code_and_config, *private_dirs):
        parent = item.path.rpartition("/")[0] or "/"
        while True:
            if parent not in ancestor_paths:
                raise ValueError("every protected path parent must have root-controlled metadata")
            if parent == "/":
                break
            parent = parent.rpartition("/")[0] or "/"

    for item in layout.code_and_config:
        if item.uid != 0 or not stat.S_ISREG(item.mode) or item.mode & 0o222:
            raise ValueError("code/config must be root-owned, non-writable regular files")
    for item in layout.ancestors:
        if item.uid != 0 or not stat.S_ISDIR(item.mode) or item.mode & 0o022:
            raise ValueError("ancestors must be root-owned directories without group/other write")
    for item in private_dirs:
        if (
            item.uid != service_uid
            or not stat.S_ISDIR(item.mode)
            or item.mode & 0o077
            or item.mode & 0o700 != 0o700
        ):
            raise ValueError("private directories must be service-owned and owner-only")
    return True
