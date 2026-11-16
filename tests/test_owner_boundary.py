"""Synthetic regressions for the offline Linux owner boundary helper."""

from dataclasses import replace
import socket
import struct
import unittest
from unittest.mock import patch

from kairos_data.owner_boundary import (
    KernelPeerOwnerAuthorizer,
    PathMetadata,
    ProtectedLayout,
    peer_credentials,
    validate_protected_layout,
    validate_service_identities,
)


def fake_socket(credentials: bytes, *, family=socket.AF_UNIX, kind=socket.SOCK_STREAM):
    class ConnectedStream:
        def __init__(self):
            self.family = family
            self.type = kind

        def getpeername(self):
            return "synthetic-peer"

        def getsockopt(self, level, option, length):
            if (level, option, length) != (
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            ):
                raise AssertionError("unexpected socket option")
            return credentials

    return ConnectedStream()


def metadata(path, mode, uid, *, symlink=False):
    return PathMetadata(path=path, mode=mode, uid=uid, is_symlink=symlink)


class OwnerBoundaryIdentityTests(unittest.TestCase):
    def test_service_and_training_identities_are_distinct_valid_nonroot_uids(self):
        self.assertEqual(validate_service_identities(1201, 1202), (1201, 1202))
        for service_uid, training_uid in (
            (0, 1202),
            (1201, 0),
            (1201, 1201),
            (True, 1202),
            (1201, False),
            (-1, 1202),
            (2**32, 1202),
        ):
            with self.subTest(service_uid=service_uid, training_uid=training_uid):
                with self.assertRaises(ValueError):
                    validate_service_identities(service_uid, training_uid)


class OwnerBoundaryPeerTests(unittest.TestCase):
    def test_peer_identity_comes_from_connected_linux_unix_stream_credentials(self):
        valid = fake_socket(struct.pack("3i", 42, 0, 100))
        with patch("kairos_data.owner_boundary.sys.platform", "linux"):
            self.assertEqual(peer_credentials(valid).uid, 0)
            self.assertEqual(peer_credentials(valid).pid, 42)

        invalid_handles = (
            fake_socket(struct.pack("3i", 42, 1201, 100), family=socket.AF_INET),
            fake_socket(struct.pack("3i", 42, 1201, 100), kind=socket.SOCK_DGRAM),
            fake_socket(b"malformed"),
            fake_socket(struct.pack("3i", 0, 1201, 100)),
            fake_socket(struct.pack("3i", 42, -1, 100)),
            fake_socket(struct.pack("3i", 42, 1201, -1)),
        )
        unconnected = fake_socket(struct.pack("3i", 42, 1201, 100))
        unconnected.getpeername = lambda: None
        invalid_handles += (unconnected,)
        for handle in invalid_handles:
            with self.subTest(handle=handle):
                with patch("kairos_data.owner_boundary.sys.platform", "linux"):
                    with self.assertRaises((OSError, ValueError)):
                        peer_credentials(handle)
        with patch("kairos_data.owner_boundary.sys.platform", "darwin"):
            with self.assertRaises(OSError):
                peer_credentials(valid)

    def test_authorizer_binds_requester_to_root_peer_and_requires_reason(self):
        handle = fake_socket(struct.pack("3i", 42, 0, 100))
        authorizer = KernelPeerOwnerAuthorizer(
            handle, service_uid=1201, training_uid=1202
        )
        canonical = "unix-peer:pid=42:uid=0:gid=100"
        self.assertEqual(authorizer.requester, canonical)
        with patch("kairos_data.owner_boundary.sys.platform", "linux"), patch(
            "kairos_data.owner_boundary.os.geteuid", return_value=1201
        ):
            self.assertTrue(authorizer.authorize(canonical, "owner acceptance"))
            self.assertFalse(authorizer.authorize("owner", "owner acceptance"))
            self.assertFalse(authorizer.authorize(canonical, "  "))

        nonroot = KernelPeerOwnerAuthorizer(
            fake_socket(struct.pack("3i", 43, 1201, 100)),
            service_uid=1201,
            training_uid=1202,
        )
        with patch("kairos_data.owner_boundary.sys.platform", "linux"), patch(
            "kairos_data.owner_boundary.os.geteuid", return_value=1201
        ):
            self.assertFalse(
                nonroot.authorize("unix-peer:pid=43:uid=1201:gid=100", "reason")
            )
        with patch("kairos_data.owner_boundary.sys.platform", "linux"), patch(
            "kairos_data.owner_boundary.os.geteuid", return_value=1202
        ):
            self.assertFalse(authorizer.authorize(canonical, "owner acceptance"))


class ProtectedLayoutTests(unittest.TestCase):
    def test_layout_requires_root_protected_code_and_private_service_directories(self):
        good = ProtectedLayout(
            code_and_config=(
                metadata("/srv/kairos/service.py", 0o100444, 0),
                metadata("/srv/kairos/service.conf", 0o100444, 0),
            ),
            ancestors=(
                metadata("/", 0o040755, 0),
                metadata("/srv", 0o040755, 0),
                metadata("/srv/kairos", 0o040755, 0),
                metadata("/var", 0o040755, 0),
                metadata("/var/lib", 0o040755, 0),
                metadata("/var/log", 0o040755, 0),
                metadata("/run", 0o040755, 0),
            ),
            data_dir=metadata("/var/lib/kairos", 0o040700, 1201),
            audit_dir=metadata("/var/log/kairos", 0o040700, 1201),
            runtime_dir=metadata("/run/kairos", 0o040700, 1201),
        )
        self.assertTrue(validate_protected_layout(good, service_uid=1201))
        invalid_layouts = (
            replace(
                good,
                code_and_config=(
                    metadata("/srv/kairos/service.py", 0o100644, 0),
                    metadata("/srv/kairos/service.conf", 0o100444, 0),
                ),
            ),
            replace(
                good,
                code_and_config=(
                    metadata("/srv/kairos/service.py", 0o100444, 1201),
                    metadata("/srv/kairos/service.conf", 0o100444, 0),
                ),
            ),
            replace(
                good,
                code_and_config=(
                    metadata("/srv/kairos/service.py", 0o120777, 0, symlink=True),
                    metadata("/srv/kairos/service.conf", 0o100444, 0),
                ),
            ),
            replace(
                good,
                ancestors=(
                    metadata("/", 0o040755, 0),
                    metadata("/srv", 0o040777, 0),
                    *good.ancestors[2:],
                ),
            ),
            replace(
                good,
                ancestors=(
                    metadata("/", 0o040755, 0),
                    metadata("/srv", 0o100755, 0),
                    *good.ancestors[2:],
                ),
            ),
            replace(good, ancestors=good.ancestors[1:]),
            replace(good, data_dir=metadata("/var/lib/kairos", 0o040700, 1202)),
            replace(good, data_dir=metadata("/var/lib/kairos", 0o040770, 1201)),
            replace(good, data_dir=metadata("/var/lib/kairos", 0o100700, 1201)),
        )
        for layout in invalid_layouts:
            with self.subTest(layout=layout):
                with self.assertRaises(ValueError):
                    validate_protected_layout(layout, service_uid=1201)


if __name__ == "__main__":
    unittest.main()
