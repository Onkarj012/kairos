"""Synthetic pre-2025 filesystem, policy and static lifecycle checks only."""

import ast
import hashlib
import io
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kairos_data.owner_daemon import CANARY, PATHS, ServiceBackend, ServiceConfig, check_runtime_dependencies, run
from kairos_data.owner_service import OpeningRequest, authorize_and_audit, _SessionSnapshot
from kairos_data.owner_session import PackExpectation
from kairos_data.policy import AuthorizationError
from kairos_data.protected_io import (
    open_absolute_directory, open_child_directory, open_regular, read_bounded, verify_selected_pack,
)
from kairos_data.storage import PackKind, _manifest_bytes, write_pack


class OwnerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.descriptor)

    def expected(self, identifier="pack", encoding="jsonl"):
        manifest = write_pack(
            self.root / identifier, [{"date": "2024-12-31", "symbol": "SYN", "close": 100.0}],
            kind="daily", partition="pre2025", encoding=encoding,
        )
        for name in ("manifest.json", "rows.jsonl" if encoding == "jsonl" else "rows.parquet"):
            (self.root / identifier / name).chmod(0o600)
        return PackExpectation(identifier, PackKind.DAILY, hashlib.sha256(_manifest_bytes(manifest)).hexdigest(), manifest.sha256)

    def test_pinned_pre2025_snapshot_for_both_encodings_and_byte_cap(self):
        for encoding in ("jsonl", "parquet"):
            with self.subTest(encoding=encoding):
                expected = self.expected(encoding, encoding)
                snapshot = io.BytesIO()
                manifest = verify_selected_pack(
                    self.descriptor, expected, snapshot, owner=os.getuid(), expected_partition="pre2025"
                )
                self.assertEqual(manifest.rows, 1)
                self.assertTrue(snapshot.getvalue())
                with self.assertRaisesRegex(ValueError, "snapshot limit"):
                    verify_selected_pack(
                        self.descriptor, expected, _SessionSnapshot(io.BytesIO(), 1),
                        owner=os.getuid(), expected_partition="pre2025",
                    )

    def test_pinned_pack_rejects_identity_and_payload_mutation_before_exposure(self):
        expected = self.expected()
        altered = PackExpectation(expected.identifier, PackKind.MINUTE, expected.manifest_sha256, expected.content_sha256)
        snapshot = io.BytesIO()
        with self.assertRaisesRegex(ValueError, "frozen identity"):
            verify_selected_pack(self.descriptor, altered, snapshot, owner=os.getuid(), expected_partition="pre2025")
        self.assertEqual(snapshot.getvalue(), b"")
        with (self.root / "pack" / "rows.jsonl").open("ab") as output:
            output.write(b'{}\n')
        with self.assertRaises(ValueError):
            verify_selected_pack(self.descriptor, expected, io.BytesIO(), owner=os.getuid(), expected_partition="pre2025")

    def test_symlink_hardlink_fifo_and_wrong_owner_refusal(self):
        (self.root / "plain").write_bytes(b"synthetic")
        (self.root / "plain").chmod(0o600)
        (self.root / "link").symlink_to("plain")
        with self.assertRaises(OSError):
            open_regular(self.descriptor, "link", owner=os.getuid())
        os.link(self.root / "plain", self.root / "hard")
        with self.assertRaises(PermissionError):
            open_regular(self.descriptor, "plain", owner=os.getuid())
        os.mkfifo(self.root / "pipe", 0o600)
        with self.assertRaises(PermissionError):
            open_regular(self.descriptor, "pipe", owner=os.getuid())
        (self.root / "child").mkdir(mode=0o700)
        with self.assertRaises(PermissionError):
            open_child_directory(self.descriptor, "child", owner=os.getuid() + 1, private=True)
        for name in ("../plain", "/plain", ".", ""):
            with self.assertRaises(ValueError):
                open_regular(self.descriptor, name, owner=os.getuid())

    def test_renamed_parent_does_not_redirect_pinned_descriptor(self):
        (self.root / "original").mkdir(mode=0o700)
        descriptor = open_child_directory(self.descriptor, "original", owner=os.getuid(), private=True)
        self.addCleanup(os.close, descriptor)
        (self.root / "original" / "note").write_bytes(b"original")
        (self.root / "original" / "note").chmod(0o600)
        (self.root / "original").rename(self.root / "moved")
        (self.root / "original").mkdir(mode=0o700)
        (self.root / "original" / "note").write_bytes(b"replacement")
        note = open_regular(descriptor, "note", owner=os.getuid())
        try:
            self.assertEqual(read_bounded(note, 8), b"original")
            with self.assertRaises(ValueError):
                read_bounded(note, 1)
        finally:
            os.close(note)

    def test_absolute_production_walker_refuses_temporary_writable_ancestor(self):
        with self.assertRaises(PermissionError):
            open_absolute_directory(str(self.root), owner=os.getuid(), private=True)

    def test_descriptor_relative_policy_audit_is_durable_once_and_fail_closed(self):
        class Authorizer:
            def authorize(self, requester, reason):
                return True

        request = OpeningRequest("2026-11-17T10:00:00+05:30", "swing", "a" * 64, "synthetic-owner", "policy only")
        with patch("kairos_data.owner_service.os.fsync", wraps=os.fsync) as sync:
            audit = authorize_and_audit(
                request, authorizer=Authorizer(), audit_log="openings.jsonl", audit_directory_fd=self.descriptor
            )
        self.assertGreaterEqual(sync.call_count, 2)
        self.assertEqual(json.loads((self.root / "openings.jsonl").read_text())["request_sha256"], audit.request_sha256)
        with self.assertRaises(AuthorizationError):
            authorize_and_audit(request, authorizer=Authorizer(), audit_log="openings.jsonl", audit_directory_fd=self.descriptor)
        (self.root / "openings.jsonl").write_text('{"frozen_style":"other"}')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            authorize_and_audit(request, authorizer=Authorizer(), audit_log="openings.jsonl", audit_directory_fd=self.descriptor)

    def test_configuration_refuses_unknown_paths_identities_and_truthy_flags(self):
        value = {"version": 1, "service_uid": 1201, "training_uid": 1202, "final_test_enabled": False, **PATHS}
        self.assertFalse(ServiceConfig.from_value(value).final_test_enabled)
        for changes in ({"service_uid": 0}, {"training_uid": 1201}, {"final_test_enabled": "false"},
                        {"vault_root": "/tmp/replacement"}, {"requester": "root"}, {"version": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ServiceConfig.from_value({**value, **changes})

    def test_parquet_runtime_check_uses_only_an_in_memory_pre2025_canary(self):
        self.assertEqual(check_runtime_dependencies()["synthetic_parquet"], "passed")

    def test_real_canary_method_on_only_a_synthetic_directory_is_idempotent(self):
        backend = object.__new__(ServiceBackend)
        backend.config = ServiceConfig(os.getuid(), os.getuid() + 1)
        backend.directories = {"audit_dir": self.descriptor}
        self.assertEqual(backend.canary(), hashlib.sha256(CANARY).hexdigest())
        self.assertEqual(backend.canary(), hashlib.sha256(CANARY).hexdigest())
        (self.root / "isolation-canary").write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            backend.canary()

    def test_lifecycle_fake_listener_serializes_and_cleans_without_binding(self):
        events = []

        class Backend:
            directories = {"runtime_dir": self.descriptor}

            def close(self):
                events.append("backend-close")

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                events.append("connection-close")

        class Listener:
            def setsockopt(self, *arguments):
                events.append("credentials-enabled")

            def bind(self, path):
                events.append("bind-synthetic")
                self.assertion = path

            def listen(self, count):
                events.append("listen")

            def settimeout(self, timeout):
                pass

            def accept(self):
                if "handled" in events:
                    raise SystemExit(0)
                return Connection(), None

            def close(self):
                events.append("listener-close")

        listener = Listener()
        with patch("kairos_data.owner_daemon.ServiceBackend", return_value=Backend()), \
             patch("kairos_data.owner_daemon.socket.socket", return_value=listener), \
             patch("kairos_data.owner_daemon._remove_socket", side_effect=lambda *args, **kwargs: events.append("remove")), \
             patch("kairos_data.owner_daemon.os.stat", return_value=SimpleNamespace(st_ino=123)), \
             patch("kairos_data.owner_daemon.os.umask"), \
             patch("kairos_data.owner_daemon.serve_connection", side_effect=lambda *args: events.append("handled")):
            with self.assertRaises(SystemExit):
                run(ServiceConfig(os.getuid(), os.getuid() + 1))
        self.assertLess(events.index("credentials-enabled"), events.index("bind-synthetic"))
        self.assertLess(events.index("handled"), events.index("connection-close"))
        self.assertEqual(events[-1], "backend-close")
        self.assertEqual(events.count("remove"), 2)

    def test_audit_digest_damage_refuses_without_new_authorization(self):
        class Authorizer:
            def authorize(self, requester, reason):
                return True

        request = OpeningRequest("2026-11-17T10:00:00+05:30", "swing", "a" * 64, "synthetic-owner", "policy only")
        authorize_and_audit(request, authorizer=Authorizer(), audit_log="openings.jsonl", audit_directory_fd=self.descriptor)
        path = self.root / "openings.jsonl"
        record = json.loads(path.read_text())
        record["frozen_style"] = "same-day"
        path.write_text(json.dumps(record) + "\n")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "digest"):
            authorize_and_audit(request, authorizer=Authorizer(), audit_log="openings.jsonl", audit_directory_fd=self.descriptor)
        self.assertEqual(path.read_bytes(), before)

    def test_opening_wiring_is_static_only_and_preserves_required_order(self):
        checkout = Path(__file__).resolve().parents[1]
        module = ast.parse((checkout / "kairos_data/owner_service.py").read_text())
        function = next(item for item in module.body if isinstance(item, ast.FunctionDef) and item.name == "open_final_test_session")
        calls = sorted((item.lineno, getattr(item.func, "id", getattr(item.func, "attr", "")))
                       for item in ast.walk(function) if isinstance(item, ast.Call))
        names = [name for _, name in calls]
        self.assertLess(names.index("authorize_and_audit"), names.index("verify_selected_pack"))
        self.assertLess(names.index("verify_selected_pack"), names.index("OpenedSession"))
        self.assertIn("close", names)
        daemon = ast.parse((checkout / "kairos_data/owner_daemon.py").read_text())
        self.assertTrue(any(isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                            and item.func.attr == "setitimer" for item in ast.walk(daemon)))

    def test_bootstrap_validates_trust_before_local_imports_and_rejects_bad_metadata(self):
        checkout = Path(__file__).resolve().parents[1]
        tree = ast.parse((checkout / "ops/owner-service/bootstrap.py").read_text())
        functions = {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef)}
        main = functions["main"]
        validation = min(item.lineno for item in ast.walk(main)
                         if isinstance(item, ast.Call) and getattr(item.func, "id", "") == "_verify_tree")
        local_import = min(item.lineno for item in ast.walk(main)
                           if isinstance(item, ast.ImportFrom) and item.module.startswith("kairos_data"))
        self.assertLess(validation, local_import)
        namespace = {"stat": stat}
        predicates = ast.Module(body=[functions["_directory"], functions["_file"]], type_ignores=[])
        exec(compile(ast.fix_missing_locations(predicates), "synthetic-bootstrap-predicates", "exec"), namespace)
        namespace["_directory"](SimpleNamespace(st_mode=0o40755, st_uid=0))
        namespace["_file"](SimpleNamespace(st_mode=0o100444, st_uid=0, st_nlink=1))
        for metadata in (SimpleNamespace(st_mode=0o100644, st_uid=0, st_nlink=1),
                         SimpleNamespace(st_mode=0o100444, st_uid=1202, st_nlink=1),
                         SimpleNamespace(st_mode=0o120444, st_uid=0, st_nlink=1),
                         SimpleNamespace(st_mode=0o100444, st_uid=0, st_nlink=2)):
            with self.assertRaises(PermissionError):
                namespace["_file"](metadata)
        with self.assertRaises(PermissionError):
            namespace["_directory"](SimpleNamespace(st_mode=0o40777, st_uid=0))


if __name__ == "__main__":
    unittest.main()
