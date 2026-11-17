"""Root-installed isolated bootstrap. No imports from a mutable checkout."""

import os
from pathlib import PurePosixPath
import stat
import sys


def _directory(metadata):
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise PermissionError("trusted directories must be root-controlled")


def _file(metadata):
    if (
        not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
        or metadata.st_mode & 0o222 or metadata.st_nlink != 1
    ):
        raise PermissionError("trusted files must be root-owned, immutable and singly linked")


def _open_directory(path):
    if str(PurePosixPath(path)) != path or not path.startswith("/") or ".." in path.split("/"):
        raise ValueError("trusted path is not canonical")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _directory(os.fstat(descriptor))
        for name in path.split("/")[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            try:
                _directory(os.fstat(child))
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_tree(directory_fd, *, depth=0, budget=None):
    if budget is None:
        budget = [100000]
    if depth > 20:
        raise ValueError("trusted dependency tree exceeds depth limit")
    for name in os.listdir(directory_fd):
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError("trusted tree exceeds entry limit")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        try:
            metadata = os.fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                _directory(metadata)
                _verify_tree(descriptor, depth=depth + 1, budget=budget)
            else:
                _file(metadata)
        finally:
            os.close(descriptor)


def main():
    if sys.platform != "linux" or not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("bootstrap requires Linux python3 -I -S -B")
    if len(sys.argv) < 2 or sys.argv[1] != "/etc/kairos-owner/config.json":
        raise ValueError("only the fixed protected configuration is accepted")
    release = str(PurePosixPath(__file__).parent)
    if not release.startswith("/opt/kairos-owner/releases/") or len(PurePosixPath(release).parts) != 5:
        raise ValueError("bootstrap must be installed in a protected release")
    descriptors = []
    try:
        release_fd = _open_directory(release)
        descriptors.append(release_fd)
        _verify_tree(release_fd)
        config_dir = _open_directory("/etc/kairos-owner")
        descriptors.append(config_dir)
        descriptor = os.open("config.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=config_dir)
        with os.fdopen(descriptor, "rb") as source:
            _file(os.fstat(source.fileno()))
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError("configuration is too large")
        sys.path.insert(0, f"/proc/self/fd/{release_fd}")
        try:
            dependency_fd = _open_directory("/opt/kairos-owner/deps")
        except FileNotFoundError:
            dependency_fd = None
        if dependency_fd is not None:
            descriptors.append(dependency_fd)
            _verify_tree(dependency_fd)
            sys.path.append(f"/proc/self/fd/{dependency_fd}")
        from kairos_data.owner_protocol import decode_json
        from kairos_data.owner_daemon import ServiceConfig, check_runtime_dependencies, run

        config = ServiceConfig.from_value(decode_json(raw))
        if len(sys.argv) == 2:
            run(config)
        elif sys.argv[2:] == ["runtime-check"]:
            import json

            print(json.dumps(check_runtime_dependencies(), sort_keys=True))
        else:
            from kairos_data.owner_client import main as client_main

            client_main(config, sys.argv[2:])
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


if __name__ == "__main__":
    main()
