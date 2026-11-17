"""Synthetic planning and static owner-tooling checks only.

The loader extracts an explicit allowlist of pure/source-capture definitions.
It never imports the service, loads apply code, inspects production paths,
launches a subprocess, creates a socket, or opens any pack. Coordinator audit
is required before running this new file under the focused safety harness.
"""

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import unittest
from unittest.mock import patch


TOOL_DIRECTORY = Path(__file__).parent.parent / "ops" / "owner-service"
MANAGE_PATH = TOOL_DIRECTORY / "manage.py"
PURE_FUNCTIONS = {
    "digest", "canonical_json", "validate_release", "validate_identity", "render_unit",
    "file_signature", "read_regular", "open_source_directory", "read_source",
    "build_setup_plan", "build_rollback_plan", "systemctl_arguments",
    "setup_directories", "printable_plan", "validate_receipt",
}


def load_planning_definitions():
    tree = ast.parse(MANAGE_PATH.read_text(), filename=str(MANAGE_PATH))
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    nodes += [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        or isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCTIONS
    ]
    namespace = {
        "hashlib": hashlib, "json": json, "os": os, "Path": Path,
        "re": re, "stat": stat,
    }
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(MANAGE_PATH), "exec"), namespace)
    return namespace


class OwnerServiceToolingTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_planning_definitions()
        self.temporary = tempfile.TemporaryDirectory(prefix="kairos-owner-tooling-")
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name).resolve()
        for destination, relative in self.tool["REQUIRED_SOURCE_FILES"].items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic source for " + destination + "\n").encode("ascii"))
        template = self.source / self.tool["TEMPLATE_SOURCE"]
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_bytes((TOOL_DIRECTORY / "kairos-owner.service.in").read_bytes())

    def plan(self, **changes):
        arguments = {
            "source": self.source, "release": "reviewed_01", "service_user": "kairos_owner",
            "service_uid": 1201, "training_uid": 1202,
        }
        arguments.update(changes)
        return self.tool["build_setup_plan"](**arguments)

    def test_exact_config_and_no_implicit_activation(self):
        plan = self.plan()
        self.assertEqual(plan["config"], {
            "version": 1, "service_uid": 1201, "training_uid": 1202,
            "vault_root": "/var/lib/kairos-owner/packs",
            "audit_dir": "/var/lib/kairos-owner/audit",
            "snapshot_dir": "/var/lib/kairos-owner/snapshots",
            "runtime_dir": "/run/kairos-owner", "final_test_enabled": False,
        })
        self.assertIn("none", plan["activation"])
        self.assertIn("pyarrow", plan["prerequisite"])
        printable = self.tool["printable_plan"](plan)
        self.assertEqual(printable["commands"], [self.tool["systemctl_arguments"]("show")])
        self.assertTrue(all(file["mode"] == "0444" for file in printable["files"].values()))
        for name in ("packs", "audit", "snapshots"):
            directory = next(item for item in printable["directories"] if item["path"].endswith("/" + name))
            self.assertEqual(directory["uid"], 1201)
            self.assertEqual(directory["mode"], "0700")
        self.assertNotIn("/run/kairos-owner", {item["path"] for item in printable["directories"]})

    def test_plans_capture_bytes_without_reopening_mutable_source(self):
        plan = self.plan()
        path = self.source / "ops/owner-service/bootstrap.py"
        captured = plan["files"][plan["release_root"] + "/bootstrap.py"]
        first_printable = self.tool["printable_plan"](plan)
        path.write_bytes(b"replaced synthetic source\n")
        self.assertEqual(plan["files"][plan["release_root"] + "/bootstrap.py"], captured)
        self.assertEqual(self.tool["printable_plan"](plan), first_printable)
        self.assertNotEqual(self.tool["printable_plan"](self.plan()), first_printable)

    def test_missing_required_bootstrap_fails_closed(self):
        (self.source / "ops/owner-service/bootstrap.py").unlink()
        with self.assertRaises(FileNotFoundError):
            self.plan()

    def test_explicit_allowlist_ignores_unlisted_files(self):
        before = self.tool["printable_plan"](self.plan())
        (self.source / "kairos_data/unreviewed.py").write_bytes(b"not executable by packaging\n")
        self.assertEqual(self.tool["printable_plan"](self.plan()), before)
        for name in ("owner_daemon", "owner_client", "owner_protocol", "protected_io"):
            self.assertIn(self.plan()["release_root"] + "/kairos_data/" + name + ".py", self.plan()["files"])

    def test_source_symlink_is_refused(self):
        path = self.source / "ops/owner-service/bootstrap.py"
        target = self.source / "synthetic-alternate.py"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            self.plan()

    def test_source_hardlink_is_refused(self):
        path = self.source / "ops/owner-service/bootstrap.py"
        os.link(path, self.source / "synthetic-hardlink.py")
        with self.assertRaises(ValueError):
            self.plan()

    def test_oversize_source_is_refused_before_copying(self):
        path = self.source / "ops/owner-service/bootstrap.py"
        with path.open("wb") as output:
            output.truncate(self.tool["MAX_SOURCE_BYTES"] + 1)
        with self.assertRaises(ValueError):
            self.plan()

    def test_source_directory_symlink_is_refused(self):
        original = self.source / "kairos_data"
        replacement = self.source / "synthetic-module-directory"
        original.rename(replacement)
        original.symlink_to(replacement, target_is_directory=True)
        with self.assertRaises(OSError):
            self.plan()

    def test_dangling_module_symlinks_are_not_treated_as_missing(self):
        path = self.source / "kairos_data/owner_protocol.py"
        path.unlink()
        path.symlink_to(self.source / "absent-synthetic-file")
        with self.assertRaises(OSError):
            self.plan()

    def test_source_replacement_during_read_is_refused(self):
        target = self.source / "ops/owner-service/bootstrap.py"
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        self.addCleanup(os.close, descriptor)
        real_read = os.read
        replaced = False

        def replace_then_read(open_descriptor, maximum):
            nonlocal replaced
            if not replaced:
                replaced = True
                target.unlink()
                target.write_bytes(b"replacement\n")
            return real_read(open_descriptor, maximum)

        with patch.object(os, "read", side_effect=replace_then_read):
            with self.assertRaises(ValueError):
                self.tool["read_regular"](descriptor, maximum=1024)

    def test_release_username_and_uid_injection_refused(self):
        for release in ("../escape", "a/b", "one.two", "release\nUser=root", "café", "@RELEASE@", ""):
            with self.subTest(release=release), self.assertRaises(ValueError):
                self.plan(release=release)
        for user in ("root\nExecStart=/bin/sh", "owner%u", "owner name", "owner@host", "1201", "øwner"):
            with self.subTest(user=user), self.assertRaises(ValueError):
                self.plan(service_user=user)
        for uid in (0, -1, True, "1201", 2**31, 1202):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                self.plan(service_uid=uid)

    def test_template_replacements_fail_closed(self):
        render = self.tool["render_unit"]
        for template in (
            b"User=@SERVICE_USER@\n", b"@RELEASE@ @SERVICE_USER@ @OTHER@",
            b"@RELEASE@ @SERVICE_USER@ stray@", b"@RELEASE@ @SERVICE_USER@ \xff",
        ):
            with self.subTest(template=template), self.assertRaises((ValueError, UnicodeError)):
                render(template, "reviewed_01", "kairos_owner")

    def test_root_sealed_allowlisted_receipt(self):
        plan = self.plan()
        content = plan["files"][plan["release_root"] + "/receipt.json"]
        receipt = self.tool["validate_receipt"](content, "reviewed_01")
        self.assertEqual(set(receipt["sources"]), set(self.tool["REQUIRED_SOURCE_FILES"]))
        self.assertEqual(receipt["unit_sha256"], hashlib.sha256(plan["files"][self.tool["UNIT_PATH"]]).hexdigest())
        receipt["sources"]["kairos_data/unreviewed.py"] = "a" * 64
        with self.assertRaises(ValueError):
            self.tool["validate_receipt"](self.tool["canonical_json"](receipt), "reviewed_01")

    def test_rollback_planning_never_opens_anything(self):
        with patch.object(os, "open", side_effect=AssertionError("rollback planning inspected a path")):
            plan = self.tool["build_rollback_plan"]("reviewed_01")
        self.assertEqual(plan["remove_only"], ["/etc/systemd/system/kairos-owner.service"])
        self.assertEqual([command[3] for command in plan["commands_before_removal"]], ["stop", "disable"])
        self.assertIn("/var/lib/kairos-owner", plan["preserve"])
        self.assertIn("all OS identities", plan["preserve"])
        self.assertEqual(plan["reload_after_removal"][3], "daemon-reload")

    def test_no_apply_code_or_subprocess_is_loaded(self):
        for forbidden in ("subprocess", "pwd", "apply_setup", "apply_rollback", "RootTree", "main"):
            self.assertNotIn(forbidden, self.tool)
        for operation in ("enable", "start", "restart", "reload", "kill"):
            with self.assertRaises(ValueError):
                self.tool["systemctl_arguments"](operation)

    def test_hardened_unit_entry_and_write_allowlist(self):
        plan = self.plan()
        text = plan["files"][self.tool["UNIT_PATH"]].decode("ascii")
        properties = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        self.assertEqual(shlex.split(properties["ExecStart"]), [
            "/usr/bin/python3", "-I", "-S", "-B",
            "/opt/kairos-owner/releases/reviewed_01/bootstrap.py", "/etc/kairos-owner/config.json",
        ])
        for key, value in {
            "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
            "PrivateTmp": "yes", "RestrictAddressFamilies": "AF_UNIX", "IPAddressDeny": "any",
            "RuntimeDirectory": "kairos-owner", "RuntimeDirectoryMode": "0700",
            "MemoryMax": "1G", "LimitNOFILE": "1024", "TasksMax": "16",
            "TimeoutStopSec": "30s", "KillMode": "control-group", "Restart": "no",
        }.items():
            self.assertEqual(properties[key], value)
        self.assertEqual(set(shlex.split(properties["ReadWritePaths"])), {
            "/var/lib/kairos-owner/audit", "/var/lib/kairos-owner/snapshots", "/run/kairos-owner",
        })
        self.assertEqual(set(shlex.split(properties["Environment"])), {
            "ARROW_NUM_THREADS=2", "OMP_NUM_THREADS=1", "OPENBLAS_NUM_THREADS=1",
        })
        self.assertNotIn("EnvironmentFile", properties)
        self.assertNotIn("Sockets", properties)
        self.assertNotIn("ExecStartPre", properties)
        unset = set()
        for line in text.splitlines():
            if line.startswith("UnsetEnvironment="):
                unset.update(shlex.split(line.split("=", 1)[1]))
        self.assertTrue({"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"} <= unset)

    def test_wrapper_uses_isolated_python_and_forwards_explicit_arguments(self):
        for name, operation in (("setup.sh", "setup"), ("rollback.sh", "rollback")):
            lines = (TOOL_DIRECTORY / name).read_text().splitlines()
            command = shlex.split(next(line for line in lines if line.startswith("exec ")))
            self.assertEqual(command, [
                "exec", "/usr/bin/python3", "-I", "-S", "-B", "$tool_dir/manage.py", operation, "$@",
            ])

    def test_static_apply_gating_and_rollback_order(self):
        tree = ast.parse(MANAGE_PATH.read_text())
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        main_calls = [
            (node.lineno, node.func.id) for node in ast.walk(functions["main"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        approved_line = next(line for line, name in main_calls if name == "verify_approved_plan")
        operation_line = next(line for line, name in main_calls if name == "operation")
        self.assertLess(approved_line, operation_line)
        rollback = functions["rollback_locked"]
        actions = sorted(
            (node.lineno, node.args[0].value)
            for node in ast.walk(rollback)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "run_systemctl"
        )
        removal = next(
            node.lineno for node in ast.walk(rollback)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remove_unit"
        )
        self.assertEqual([operation for _, operation in actions], ["stop", "disable", "daemon-reload"])
        self.assertLess(actions[1][0], removal)
        self.assertLess(removal, actions[2][0])
        for name in ("apply_setup", "apply_rollback"):
            calls = {node.func.id for node in ast.walk(functions[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            self.assertIn("require_owner_apply", calls)
            self.assertIn("owner_lock", calls)
        self.assertNotIn("apply_setup", PURE_FUNCTIONS)


if __name__ == "__main__":
    unittest.main()
