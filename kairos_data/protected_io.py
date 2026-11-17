"""Descriptor-relative Linux filesystem operations for the dedicated service."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
import stat

from .owner_session import PackExpectation, validate_pack_identifier
from .storage import PackManifest, _manifest_bytes, _verify_stream
import hashlib

MAX_MANIFEST_BYTES = 256 * 1024


def check_directory(metadata: os.stat_result, *, owner: int, private: bool) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner:
        raise PermissionError("directory type or owner does not match policy")
    forbidden = 0o077 if private else 0o022
    if metadata.st_mode & forbidden or private and metadata.st_mode & 0o700 != 0o700:
        raise PermissionError("directory permissions do not match policy")


def check_regular(metadata: os.stat_result, *, owner: int, immutable: bool) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner
        or metadata.st_nlink != 1
        or metadata.st_mode & (0o222 if immutable else 0o077)
    ):
        raise PermissionError("file type, owner, links or permissions do not match policy")


def _component(name: str) -> None:
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise ValueError("expected one relative path component")


def open_child_directory(parent_fd: int, name: str, *, owner: int, private: bool) -> int:
    _component(name)
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd
    )
    try:
        check_directory(os.fstat(descriptor), owner=owner, private=private)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_absolute_directory(path: str, *, owner: int, private: bool) -> int:
    if (
        not isinstance(path, str) or not path.startswith("/") or path == "/"
        or any(part in {"", ".", ".."} for part in path.split("/")[1:])
    ):
        raise ValueError("expected a canonical absolute directory path")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        check_directory(os.fstat(descriptor), owner=0, private=False)
        parts = path.split("/")[1:]
        for index, name in enumerate(parts):
            last = index == len(parts) - 1
            child = open_child_directory(
                descriptor, name, owner=owner if last else 0, private=private if last else False
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_regular(parent_fd: int, name: str, *, owner: int, immutable: bool = False) -> int:
    _component(name)
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent_fd
    )
    try:
        check_regular(os.fstat(descriptor), owner=owner, immutable=immutable)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_bounded(descriptor: int, maximum: int) -> bytes:
    if os.fstat(descriptor).st_size > maximum:
        raise ValueError("file exceeds the supported size")
    with os.fdopen(os.dup(descriptor), "rb") as source:
        value = source.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("file exceeds the supported size")
    return value


def verify_selected_pack(root_fd: int, expected: PackExpectation, snapshot, *, owner: int,
                         expected_partition: str) -> PackManifest:
    """Pin every component and both files; never resolve or reopen a pathname."""
    validate_pack_identifier(expected.identifier)
    with ExitStack() as resources:
        directory_fd = root_fd
        for name in expected.identifier.split("/"):
            directory_fd = open_child_directory(directory_fd, name, owner=owner, private=True)
            resources.callback(os.close, directory_fd)
        manifest_fd = open_regular(directory_fd, "manifest.json", owner=owner)
        resources.callback(os.close, manifest_fd)
        manifest = PackManifest.from_json(json.loads(read_bounded(manifest_fd, MAX_MANIFEST_BYTES)))
        if manifest.format_version != 1 or manifest.rows < 0 or manifest.partition != expected_partition:
            raise ValueError("selected pack has an invalid version, count or partition")
        if (
            manifest.kind != expected.kind
            or hashlib.sha256(_manifest_bytes(manifest)).hexdigest() != expected.manifest_sha256
            or manifest.sha256 != expected.content_sha256
        ):
            raise ValueError("selected pack does not match its frozen identity")
        filename = "rows.jsonl" if manifest.encoding == "jsonl" else "rows.parquet"
        payload_fd = open_regular(directory_fd, filename, owner=owner)
        with os.fdopen(payload_fd, "rb") as source:
            _verify_stream(source, manifest, snapshot=snapshot)
        return manifest
