"""Owner-installed service lifecycle. Importing this module starts nothing."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import fcntl
import hashlib
import io
import os
import signal
import socket
import stat
import sys

from .owner_boundary import KernelPeerOwnerAuthorizer, validate_service_identities
from .owner_protocol import SESSION_SECONDS, serve_connection
from .owner_service import open_final_test_session
from .protected_io import check_regular, open_absolute_directory, open_regular, read_bounded

PATHS = {
    "vault_root": "/var/lib/kairos-owner/packs",
    "audit_dir": "/var/lib/kairos-owner/audit",
    "snapshot_dir": "/var/lib/kairos-owner/snapshots",
    "runtime_dir": "/run/kairos-owner",
}
CANARY = b"kairos-owner-synthetic-isolation-canary-v1\n"


def check_runtime_dependencies() -> dict:
    """Check the actual Parquet runtime using one in-memory pre-2025 canary."""
    import pyarrow as arrow
    import pyarrow.parquet as parquet

    row = {"date": "2024-12-31", "symbol": "SYNTHETIC_RUNTIME_CANARY", "close": 1.0}
    buffer = io.BytesIO()
    parquet.write_table(arrow.Table.from_pylist([row]), buffer)
    buffer.seek(0)
    batches = parquet.ParquetFile(buffer).iter_batches(batch_size=1, use_threads=False)
    if [item for batch in batches for item in batch.to_pylist()] != [row]:
        raise RuntimeError("synthetic Parquet runtime check failed")
    return {"pyarrow": arrow.__version__, "synthetic_parquet": "passed"}


@dataclass(frozen=True)
class ServiceConfig:
    service_uid: int
    training_uid: int
    final_test_enabled: bool = False

    @classmethod
    def from_value(cls, value: dict) -> ServiceConfig:
        if not isinstance(value, dict) or set(value) != {
            "version", "service_uid", "training_uid", "final_test_enabled", *PATHS
        }:
            raise ValueError("configuration has missing or unknown fields")
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("unsupported configuration version")
        if any(value[name] != path for name, path in PATHS.items()):
            raise ValueError("configuration must use the reviewed protected layout")
        service_uid, training_uid = validate_service_identities(value["service_uid"], value["training_uid"])
        if type(value["final_test_enabled"]) is not bool:
            raise ValueError("final_test_enabled must be a boolean")
        return cls(service_uid, training_uid, value["final_test_enabled"])


class ServiceBackend:
    def __init__(self, config: ServiceConfig):
        if sys.platform != "linux" or os.geteuid() != config.service_uid:
            raise PermissionError("service must run on Linux as its dedicated non-root identity")
        self.config = config
        self.final_test_enabled = config.final_test_enabled
        self._resources = ExitStack()
        self.directories = {}
        try:
            for name, path in PATHS.items():
                descriptor = open_absolute_directory(path, owner=config.service_uid, private=True)
                self._resources.callback(os.close, descriptor)
                self.directories[name] = descriptor
        except BaseException:
            self.close()
            raise

    def close(self):
        self._resources.close()

    def canary(self) -> str:
        directory_fd = self.directories["audit_dir"]
        try:
            descriptor = os.open(
                "isolation-canary", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=directory_fd,
            )
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(CANARY)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory_fd)
        descriptor = open_regular(directory_fd, "isolation-canary", owner=self.config.service_uid)
        try:
            content = read_bounded(descriptor, len(CANARY))
        finally:
            os.close(descriptor)
        if content != CANARY:
            raise ValueError("synthetic isolation canary is damaged")
        return hashlib.sha256(content).hexdigest()

    def open_session(self, request, connection):
        authorizer = KernelPeerOwnerAuthorizer(
            connection, service_uid=self.config.service_uid, training_uid=self.config.training_uid
        )
        return open_final_test_session(
            request, authorizer=authorizer, audit_log="openings.jsonl",
            audit_directory_fd=self.directories["audit_dir"], vault_root=PATHS["vault_root"],
            vault_directory_fd=self.directories["vault_root"],
            snapshot_directory=f"/proc/self/fd/{self.directories['snapshot_dir']}",
        )


def _remove_socket(directory_fd: int, *, owner: int, expected_inode: int | None = None) -> None:
    try:
        metadata = os.stat("owner.sock", dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != owner
        or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600
        or expected_inode is not None and metadata.st_ino != expected_inode
    ):
        raise PermissionError("refusing to remove an unexpected runtime socket")
    os.unlink("owner.sock", dir_fd=directory_fd)


def run(config: ServiceConfig) -> None:
    """Lock before binding; serialize clients; close resources on every exit."""
    os.umask(0o177)
    check_runtime_dependencies()
    with ExitStack() as resources:
        backend = ServiceBackend(config)
        resources.callback(backend.close)
        directory_fd = backend.directories["runtime_dir"]
        lock_fd = os.open(
            "daemon.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            0o600, dir_fd=directory_fd,
        )
        resources.callback(os.close, lock_fd)
        check_regular(os.fstat(lock_fd), owner=config.service_uid, immutable=False)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _remove_socket(directory_fd, owner=config.service_uid)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        resources.callback(listener.close)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        listener.bind(f"/proc/self/fd/{directory_fd}/owner.sock")
        inode = None
        resources.callback(lambda: _remove_socket(directory_fd, owner=config.service_uid, expected_inode=inode))
        inode = os.stat("owner.sock", dir_fd=directory_fd, follow_symlinks=False).st_ino
        listener.listen(4)
        listener.settimeout(1.0)

        def stop(signum, frame):
            raise SystemExit(0)

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.signal(signum, stop)
            resources.callback(signal.signal, signum, previous)

        def deadline_expired(signum, frame):
            raise TimeoutError("service session deadline exceeded")

        previous = signal.signal(signal.SIGALRM, deadline_expired)
        resources.callback(signal.signal, signal.SIGALRM, previous)
        while True:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                signal.setitimer(signal.ITIMER_REAL, SESSION_SECONDS)
                try:
                    serve_connection(connection, backend)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
