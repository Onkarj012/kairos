"""Owner-reviewed offline plans; only an explicit root --apply changes the host.

Run this reviewed tool with /usr/bin/python3 -I -S -B. Planning never imports
service code, resolves OS identities, inspects production paths, or runs commands.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys


UNIT_NAME = "kairos-owner.service"
UNIT_PATH = "/etc/systemd/system/" + UNIT_NAME
OPT_ROOT = "/opt/kairos-owner"
CONFIG_PATH = "/etc/kairos-owner/config.json"
STATE_ROOT = "/var/lib/kairos-owner"
RUNTIME_ROOT = "/run/kairos-owner"
TEMPLATE_SOURCE = "ops/owner-service/kairos-owner.service.in"
REQUIRED_SOURCE_FILES = {
    "bootstrap.py": "ops/owner-service/bootstrap.py",
    "kairos_data/__init__.py": "kairos_data/__init__.py",
    "kairos_data/live_inputs.py": "kairos_data/live_inputs.py",
    "kairos_data/owner_boundary.py": "kairos_data/owner_boundary.py",
    "kairos_data/owner_client.py": "kairos_data/owner_client.py",
    "kairos_data/owner_daemon.py": "kairos_data/owner_daemon.py",
    "kairos_data/owner_protocol.py": "kairos_data/owner_protocol.py",
    "kairos_data/owner_service.py": "kairos_data/owner_service.py",
    "kairos_data/owner_session.py": "kairos_data/owner_session.py",
    "kairos_data/policy.py": "kairos_data/policy.py",
    "kairos_data/protected_io.py": "kairos_data/protected_io.py",
    "kairos_data/storage.py": "kairos_data/storage.py",
    "kairos_data/training.py": "kairos_data/training.py",
}
SOURCE_ALLOWLIST = REQUIRED_SOURCE_FILES
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
RELEASE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
USER_PATTERN = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z", re.ASCII)
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
PREREQUISITE = (
    "Parquet requires pyarrow and its dependencies staged by the owner in "
    "/opt/kairos-owner/deps, root-owned, with no write bits or links. "
    "No dependencies are fetched, imported, or installed by this tool. "
    "Bootstrap must add only that validated sealed dependency directory, "
    "without site processing. Owner acceptance must separately verify JSONL "
    "and Parquet dependency readiness. Missing pyarrow prevents daemon startup. "
    "Pack migration is separate: source-pack private files must be service-owned "
    "0600 or 0400. This tool does not inspect, migrate or repair pack contents."
)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def validate_release(release: str) -> str:
    if not isinstance(release, str) or not RELEASE_PATTERN.fullmatch(release):
        raise ValueError("release must be 1-64 safe ASCII letters, digits, underscore or hyphen")
    return release


def validate_identity(service_user: str, service_uid: int, training_uid: int) -> None:
    if not isinstance(service_user, str) or not USER_PATTERN.fullmatch(service_user):
        raise ValueError("service user must be a safe ASCII local account name")
    for identity in (service_uid, training_uid):
        if type(identity) is not int or not 0 < identity < 2**31:
            raise ValueError("service and training UIDs must be non-root integers")
    if service_uid == training_uid:
        raise ValueError("service and training UIDs must differ")


def render_unit(template: bytes, release: str, service_user: str) -> bytes:
    validate_release(release)
    if not USER_PATTERN.fullmatch(service_user):
        raise ValueError("unsafe service user")
    text = template.decode("ascii")
    if set(re.findall(r"@[A-Z_]+@", text)) != {"@RELEASE@", "@SERVICE_USER@"}:
        raise ValueError("unit template has unexpected or missing replacements")
    rendered = text.replace("@RELEASE@", release).replace("@SERVICE_USER@", service_user)
    if "@" in rendered:
        raise ValueError("unresolved unit replacement")
    return rendered.encode("ascii")


def file_signature(metadata: os.stat_result) -> tuple:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
        metadata.st_gid, metadata.st_nlink, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def read_regular(descriptor: int, *, maximum: int) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("expected an unlinked-to-others regular file")
    if before.st_size > maximum:
        raise ValueError("file exceeds the bounded source/receipt size")
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise ValueError("file grew beyond the size bound")
    if file_signature(before) != file_signature(os.fstat(descriptor)):
        raise ValueError("file metadata changed while reading")
    return b"".join(chunks)


def open_source_directory(source: Path) -> int:
    absolute = Path(os.path.abspath(source))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_source(root_descriptor: int, relative: str) -> bytes:
    parts = relative.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("source paths must be explicit relative allowlist entries")
    directory = os.dup(root_descriptor)
    try:
        for component in parts[:-1]:
            next_directory = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            return read_regular(descriptor, maximum=MAX_SOURCE_BYTES)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def build_setup_plan(
    source: Path, release: str, service_user: str, service_uid: int, training_uid: int,
) -> dict:
    validate_release(release)
    validate_identity(service_user, service_uid, training_uid)
    descriptor = open_source_directory(source)
    try:
        sources = {
            destination: read_source(descriptor, relative)
            for destination, relative in REQUIRED_SOURCE_FILES.items()
        }
        unit = render_unit(read_source(descriptor, TEMPLATE_SOURCE), release, service_user)
    finally:
        os.close(descriptor)
    config = {
        "version": 1,
        "service_uid": service_uid,
        "training_uid": training_uid,
        "vault_root": STATE_ROOT + "/packs",
        "audit_dir": STATE_ROOT + "/audit",
        "snapshot_dir": STATE_ROOT + "/snapshots",
        "runtime_dir": RUNTIME_ROOT,
        "final_test_enabled": False,
    }
    config_bytes = canonical_json(config)
    receipt = {
        "kind": "kairos-owner-install-v1",
        "release": release,
        "service_user": service_user,
        "service_uid": service_uid,
        "training_uid": training_uid,
        "sources": {name: digest(content) for name, content in sources.items()},
        "config_sha256": digest(config_bytes),
        "unit_sha256": digest(unit),
    }
    release_root = OPT_ROOT + "/releases/" + release
    files = {release_root + "/" + name: content for name, content in sources.items()}
    files[CONFIG_PATH] = config_bytes
    files[release_root + "/receipt.json"] = canonical_json(receipt)
    files[UNIT_PATH] = unit
    return {
        "action": "setup",
        "release": release,
        "release_root": release_root,
        "service_user": service_user,
        "service_uid": service_uid,
        "training_uid": training_uid,
        "config": config,
        "files": files,
        "prerequisite": PREREQUISITE,
        "activation": "none: owner must separately review, reload and explicitly start the unit",
    }


def build_rollback_plan(release: str) -> dict:
    validate_release(release)
    return {
        "action": "rollback",
        "release": release,
        "receipt": OPT_ROOT + "/releases/" + release + "/receipt.json",
        "remove_only": [UNIT_PATH],
        "commands_before_removal": [systemctl_arguments("stop"), systemctl_arguments("disable")],
        "preserve": [OPT_ROOT, "/etc/kairos-owner", STATE_ROOT, RUNTIME_ROOT, "all OS identities"],
        "reload_after_removal": systemctl_arguments("daemon-reload"),
        "activation": "none",
    }


def systemctl_arguments(operation: str) -> list[str]:
    if operation not in {"show", "stop", "disable", "daemon-reload"}:
        raise ValueError("systemctl operation is outside the owner tool allowlist")
    arguments = ["/usr/bin/systemctl", "--system", "--no-pager", operation]
    if operation == "show":
        arguments += [
            "--property=FragmentPath", "--property=DropInPaths", "--property=ActiveState",
            "--property=LoadState", "--property=UnitFileState", "--property=Names",
            "--property=MainPID", "--property=ControlPID",
        ]
    if operation != "daemon-reload":
        arguments += ["--", UNIT_NAME]
    return arguments


class RootTree:
    """Descriptor-relative production operations, reachable only from apply functions."""

    def __init__(self):
        self.root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.validate_directory(os.fstat(self.root), 0, 0, None)

    def close(self):
        os.close(self.root)

    @staticmethod
    def validate_directory(metadata, uid, gid, mode):
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or metadata.st_gid != gid:
            raise ValueError("directory must have its expected owner and group")
        permissions = stat.S_IMODE(metadata.st_mode)
        if mode is not None:
            if permissions != mode:
                raise ValueError("existing directory has unexpected permissions")
        elif permissions & 0o7022:
            raise ValueError("root ancestor must not have special or group/other write bits")

    def directory(self, path, *, uid=0, gid=0, mode=None, create=False):
        parts = Path(path).parts
        if not parts or parts[0] != "/" or any(part in (".", "..") for part in parts):
            raise ValueError("production path must be canonical and absolute")
        descriptor = os.dup(self.root)
        try:
            for index, component in enumerate(parts[1:], 1):
                final = index == len(parts) - 1
                created = False
                if final and create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                        created = True
                    except FileExistsError:
                        pass
                next_descriptor = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                try:
                    if created:
                        os.fchown(next_descriptor, uid, gid)
                        os.fchmod(next_descriptor, mode)
                        os.fsync(next_descriptor)
                        os.fsync(descriptor)
                    self.validate_directory(
                        os.fstat(next_descriptor), uid if final else 0,
                        gid if final else 0, mode if final else None,
                    )
                except BaseException:
                    os.close(next_descriptor)
                    raise
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def read_file(self, path, *, maximum=MAX_SOURCE_BYTES):
        directory = self.directory(str(Path(path).parent))
        try:
            descriptor = os.open(
                Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            try:
                metadata = os.fstat(descriptor)
                if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o444:
                    raise ValueError("managed files must be root:root mode 0444")
                return read_regular(descriptor, maximum=maximum)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)

    def verify_or_missing(self, path, content):
        try:
            existing = self.read_file(path)
        except FileNotFoundError:
            return False
        if existing != content:
            raise ValueError("refusing to overwrite conflicting existing file: " + path)
        return True

    def publish(self, path, content):
        if self.verify_or_missing(path, content):
            return
        directory = self.directory(str(Path(path).parent))
        temporary = ".kairos-install-" + secrets.token_hex(16)
        descriptor = None
        created = False
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            created = True
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write publishing managed file")
                view = view[written:]
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o444)
            os.fsync(descriptor)
            try:
                os.link(temporary, Path(path).name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError:
                self.verify_or_missing(path, content)
            os.fsync(directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                if created:
                    os.unlink(temporary, dir_fd=directory)
                    os.fsync(directory)
            finally:
                os.close(directory)

    def remove_unit(self, expected_digest):
        current = self.read_file(UNIT_PATH)
        if digest(current) != expected_digest:
            raise ValueError("managed unit changed; rollback refused")
        directory = self.directory(str(Path(UNIT_PATH).parent))
        try:
            os.unlink(Path(UNIT_PATH).name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)


def require_owner_apply():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise PermissionError("explicit apply requires the Linux OS owner running as root")
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise PermissionError("apply must run with /usr/bin/python3 -I -S -B")


@contextmanager
def owner_lock(tree):
    directory = tree.directory(OPT_ROOT, mode=0o755, create=True)
    descriptor = None
    try:
        descriptor = os.open(
            ".management.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600, dir_fd=directory,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_gid != 0 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("management lock must be a private root-owned regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def run_systemctl(operation):
    completed = subprocess.run(
        systemctl_arguments(operation), stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=45, check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "SYSTEMD_PAGER": ""},
    )
    missing_show = (
        operation == "show" and completed.returncode in (1, 4)
        and "LoadState=not-found" in completed.stdout.splitlines()
    )
    if completed.returncode and not missing_show:
        raise RuntimeError("systemctl " + operation + " failed: " + completed.stderr.strip())
    return completed.stdout


def inspect_unit():
    values = {}
    for line in run_systemctl("show").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("unexpected systemctl show output")
        values[key] = value
    if set(values) != {
        "FragmentPath", "DropInPaths", "ActiveState", "LoadState", "UnitFileState",
        "Names", "MainPID", "ControlPID",
    }:
        raise ValueError("incomplete systemctl unit state")
    if values["FragmentPath"] not in ("", UNIT_PATH) or values["DropInPaths"]:
        raise ValueError("foreign unit fragment or drop-ins: owner review required")
    if values["Names"] not in ("", UNIT_NAME):
        raise ValueError("unit aliases require separate owner review")
    return values


def validate_dependencies(tree):
    try:
        descriptor = tree.directory(OPT_ROOT + "/deps", mode=0o555)
    except FileNotFoundError:
        return

    budget = [100000]

    def visit(directory, depth=0):
        if depth > 20:
            raise ValueError("sealed dependency tree exceeds the depth limit")
        for name in os.listdir(directory):
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError("sealed dependency tree exceeds the entry limit")
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if metadata.st_uid != 0 or metadata.st_gid != 0 or metadata.st_mode & 0o7222:
                raise ValueError("dependencies must be root-sealed without write or special bits")
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    if file_signature(metadata) != file_signature(os.fstat(child)):
                        raise ValueError("dependency directory replaced")
                    visit(child, depth + 1)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("dependency links and special files are forbidden")

    try:
        visit(descriptor)
    finally:
        os.close(descriptor)


def setup_directories(plan, service_gid):
    release_root = plan["release_root"]
    return [
        (OPT_ROOT, 0, 0, 0o755),
        (OPT_ROOT + "/releases", 0, 0, 0o555),
        (release_root, 0, 0, 0o555),
        (release_root + "/kairos_data", 0, 0, 0o555),
        ("/etc/kairos-owner", 0, 0, 0o555),
        (STATE_ROOT, 0, 0, 0o755),
        *[(STATE_ROOT + "/" + name, plan["service_uid"], service_gid, 0o700)
          for name in ("packs", "audit", "snapshots")],
    ]


def check_release_entries(tree, plan):
    expected = {"bootstrap.py", "kairos_data", "receipt.json"}
    package_entries = {
        Path(path).name for path in plan["files"]
        if str(Path(path).parent) == plan["release_root"] + "/kairos_data"
    }
    for path, allowed in ((plan["release_root"], expected), (plan["release_root"] + "/kairos_data", package_entries)):
        try:
            descriptor = tree.directory(path, mode=0o555)
        except FileNotFoundError:
            continue
        try:
            if set(os.listdir(descriptor)) - allowed:
                raise ValueError("release contains unrecognized entries; owner review required")
        finally:
            os.close(descriptor)


def apply_setup(plan):
    require_owner_apply()
    service = pwd.getpwnam(plan["service_user"])
    training = pwd.getpwuid(plan["training_uid"])
    if service.pw_uid != plan["service_uid"] or service.pw_gid <= 0 or training.pw_uid == 0:
        raise ValueError("existing OS accounts do not match the reviewed identities")
    tree = RootTree()
    try:
        with owner_lock(tree):
            install_locked(tree, plan, service.pw_gid)
    finally:
        tree.close()
    return "Installed or verified exact sealed files. Nothing was enabled, started, or reloaded. " + PREREQUISITE


def install_locked(tree, plan, service_gid):
    for ancestor in ("/opt", "/etc", "/var/lib", "/etc/systemd/system"):
        descriptor = tree.directory(ancestor)
        os.close(descriptor)
    directories = setup_directories(plan, service_gid)
    for path, uid, gid, mode in directories:
        try:
            descriptor = tree.directory(path, uid=uid, gid=gid, mode=mode)
        except FileNotFoundError:
            continue
        os.close(descriptor)
    validate_dependencies(tree)
    check_release_entries(tree, plan)
    receipt_path = plan["release_root"] + "/receipt.json"
    receipt_exists = tree.verify_or_missing(receipt_path, plan["files"][receipt_path])
    unit_exists = tree.verify_or_missing(UNIT_PATH, plan["files"][UNIT_PATH])
    if unit_exists and not receipt_exists:
        raise ValueError("existing unit has no matching ownership receipt; refusing adoption")
    for path, content in plan["files"].items():
        tree.verify_or_missing(path, content)
    state = inspect_unit()
    if state["ActiveState"] not in ("inactive", "failed"):
        raise ValueError("setup refuses an active/transitioning service; owner must stop it first")
    if state["LoadState"] not in ("loaded", "not-found"):
        raise ValueError("setup refuses an unknown unit load state")
    if state["MainPID"] != "0" or state["ControlPID"] != "0":
        raise ValueError("setup refuses a unit with remaining processes")
    if state["UnitFileState"] not in ("disabled", ""):
        raise ValueError("setup requires an existing unit to be disabled")
    for path, uid, gid, mode in directories:
        descriptor = tree.directory(path, uid=uid, gid=gid, mode=mode, create=True)
        os.close(descriptor)
    for path, content in plan["files"].items():
        tree.publish(path, content)


def validate_receipt(content, release):
    receipt = json.loads(content)
    expected = {"kind", "release", "service_user", "service_uid", "training_uid", "sources", "config_sha256", "unit_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise ValueError("unrecognized installation receipt")
    if receipt["kind"] != "kairos-owner-install-v1" or receipt["release"] != release:
        raise ValueError("receipt does not match requested release")
    validate_release(release)
    validate_identity(receipt["service_user"], receipt["service_uid"], receipt["training_uid"])
    sources = receipt["sources"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_ALLOWLIST):
        raise ValueError("receipt source files do not match the explicit allowlist")
    for value in [*sources.values(), receipt["config_sha256"], receipt["unit_sha256"]]:
        if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("receipt contains an invalid digest")
    if canonical_json(receipt) != content:
        raise ValueError("receipt is not the canonical managed form")
    return receipt


def apply_rollback(plan):
    require_owner_apply()
    tree = RootTree()
    try:
        with owner_lock(tree):
            return rollback_locked(tree, plan)
    finally:
        tree.close()


def rollback_locked(tree, plan):
    receipt = validate_receipt(tree.read_file(plan["receipt"], maximum=MAX_RECEIPT_BYTES), plan["release"])
    try:
        current = tree.read_file(UNIT_PATH)
    except FileNotFoundError:
        current = None
    if current is not None and digest(current) != receipt["unit_sha256"]:
        raise ValueError("unit is not owned by this release; no rollback actions taken")
    state = inspect_unit()
    if (
        current is None and state["LoadState"] == "not-found"
        and state["ActiveState"] == "inactive" and state["UnitFileState"] in ("", "disabled")
        and state["MainPID"] == "0" and state["ControlPID"] == "0"
    ):
        return "Managed unit already absent. Identities, releases, config, packs and audit retained."
    if state["FragmentPath"] != UNIT_PATH or state["LoadState"] != "loaded":
        raise ValueError("cannot prove systemd is using the managed unit")
    run_systemctl("stop")
    if current is not None:
        run_systemctl("disable")
    state = inspect_unit()
    if (
        state["ActiveState"] not in ("inactive", "failed")
        or state["UnitFileState"] not in ("disabled", "")
        or state["MainPID"] != "0" or state["ControlPID"] != "0"
    ):
        raise ValueError("stop/disable not confirmed; managed unit retained")
    if current is not None:
        tree.remove_unit(receipt["unit_sha256"])
    run_systemctl("daemon-reload")
    return "Stopped and disabled the managed service; removed only its unit. All persistent state and identities retained."


def printable_plan(plan):
    result = dict(plan)
    if "files" in result:
        result["files"] = {
            path: {"sha256": digest(content), "bytes": len(content), "uid": 0, "gid": 0, "mode": "0444"}
            for path, content in plan["files"].items()
        }
        result["directories"] = [
            {"path": path, "uid": uid, "gid": "existing service account primary group" if uid else gid, "mode": f"{mode:04o}"}
            for path, uid, gid, mode in setup_directories(plan, 0)
        ]
        result["commands"] = [systemctl_arguments("show")]
    result["mode"] = "plan only; no production paths inspected or changed"
    result["apply_contract"] = (
        "--apply requires --approved-plan naming this exact saved plan, owner-reviewed "
        "and staged root:root mode 0444 below root-controlled directories. Apply rejects "
        "source-byte changes. Review and run the tooling itself with python3 -I -S -B. "
        "Setup requires the service inactive and disabled and never activates it. "
        "OS identities must already exist. Conflicting files, permissions, unit aliases "
        "or drop-ins require separate owner review; nothing is repaired or overwritten. "
        "A failed partial apply retains created files. Exact managed files are retryable; "
        "an interrupted temporary file requires owner review. No persistent state is removed."
    )
    return result


def verify_approved_plan(plan, approved_path):
    require_owner_apply()
    tree = RootTree()
    try:
        approved = json.loads(tree.read_file(str(approved_path), maximum=MAX_RECEIPT_BYTES))
    finally:
        tree.close()
    if approved != printable_plan(plan):
        raise ValueError("approved plan differs from captured sources or requested operation")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    setup = commands.add_parser("setup", help="plan copying reviewed allowlisted source; no implicit activation")
    setup.add_argument("--source", type=Path, required=True)
    setup.add_argument("--release", required=True)
    setup.add_argument("--service-user", required=True)
    setup.add_argument("--service-uid", type=int, required=True)
    setup.add_argument("--training-uid", type=int, required=True)
    setup.add_argument("--apply", action="store_true", help="explicit owner-only production provisioning")
    setup.add_argument("--approved-plan", type=Path, help="apply requires an owner-reviewed root:root 0444 saved plan in root-controlled directories")
    rollback = commands.add_parser("rollback", help="plan stopping/disabling and removing only the managed unit")
    rollback.add_argument("--release", required=True)
    rollback.add_argument("--apply", action="store_true", help="explicit owner-only production rollback")
    rollback.add_argument("--approved-plan", type=Path, help="apply requires an owner-reviewed root:root 0444 saved rollback plan")
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "setup":
            plan = build_setup_plan(
                arguments.source, arguments.release, arguments.service_user,
                arguments.service_uid, arguments.training_uid,
            )
            operation = apply_setup
        else:
            plan = build_rollback_plan(arguments.release)
            operation = apply_rollback
        if arguments.apply:
            if arguments.approved_plan is None:
                raise ValueError("--apply requires an owner-reviewed --approved-plan")
            verify_approved_plan(plan, arguments.approved_plan)
            print(operation(plan))
        else:
            print(json.dumps(printable_plan(plan), sort_keys=True, indent=2))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, "owner tooling refused: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
