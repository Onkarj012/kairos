"""Synthetic handlers and socket pairs only; no listener or production backend."""

from contextlib import contextmanager
import hashlib
import json
import os
import socket
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kairos_data.owner_boundary import PeerCredentials, peer_credentials
from kairos_data.owner_protocol import (
    MAX_REQUEST_BYTES, decode_json, encode_frame, parse_command, receive_request, serve_connection,
)
from kairos_data.policy import AuthorizationError
from kairos_data.storage import PackKind

ROOT_PEER = PeerCredentials(123, 0, 0)


def request():
    return {"version": 1, "operation": "final-test", "request": {
        "requested_at": "2026-11-17T10:00:00+05:30", "frozen_style": "swing",
        "model_fingerprint": "a" * 64, "reason": "synthetic protocol only",
        "pack_selection": [{"identifier": "synthetic/daily", "kind": "daily",
                            "manifest_sha256": "b" * 64, "content_sha256": "c" * 64}],
    }}


class SyntheticConnection:
    def __init__(self, value, peer=ROOT_PEER):
        self.payload = bytearray(encode_frame(value, maximum=MAX_REQUEST_BYTES))
        self.peer = peer
        self.frames = []

    def settimeout(self, timeout):
        pass

    def setsockopt(self, *arguments):
        pass

    def recvmsg(self, maximum, *arguments):
        payload = bytes(self.payload[:maximum])
        del self.payload[:maximum]
        ancillary = [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                      struct.pack("=iII", self.peer.pid, self.peer.uid, self.peer.gid))] if payload else []
        return payload, ancillary, 0, None

    def sendall(self, frame):
        self.frames.append(decode_json(frame[4:]))


class SyntheticBackend:
    final_test_enabled = False

    def __init__(self):
        self.closed = False
        self.opened = False

    def canary(self):
        return hashlib.sha256(b"synthetic handler only").hexdigest()

    @contextmanager
    def open_session(self, opening, connection):
        self.opened = True
        try:
            yield SimpleNamespace(
                audit=SimpleNamespace(request_sha256="a" * 64, selection_sha256="b" * 64),
                packs=(SimpleNamespace(identifier="synthetic/daily", kind=PackKind.DAILY,
                                       rows=iter(({"date": "2024-12-31", "value": 1},))),),
            )
        finally:
            self.closed = True


class OwnerProtocolTests(unittest.TestCase):
    def test_strict_json_and_explicit_protocol_fields(self):
        for payload in (b'{"version":1,"version":1}', b'{"value":NaN}', b'[]', b'{"value":Infinity}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_json(payload)
        for value in ({"version": True, "operation": "health"},
                      {"version": 1, "operation": "health", "path": "/tmp/anything"},
                      {"version": 1, "operation": "execute"}):
            with self.assertRaises(ValueError):
                parse_command(value, ROOT_PEER)

    def test_order_digests_and_kernel_identity_are_bound(self):
        value = request()
        value["request"]["pack_selection"].append({
            "identifier": "synthetic/minute", "kind": "minute", "manifest_sha256": "d" * 64,
            "content_sha256": "e" * 64,
        })
        opening = parse_command(value, ROOT_PEER).opening
        self.assertEqual(opening.requester, "unix-peer:pid=123:uid=0:gid=0")
        self.assertEqual([pack.kind for pack in opening.pack_selection], [PackKind.DAILY, PackKind.MINUTE])
        value["request"]["requester"] = "trainer-claimed-root"
        with self.assertRaises(ValueError):
            parse_command(value, ROOT_PEER)

    def test_malformed_selection_unknown_style_and_duplicate_pack_refused(self):
        variants = []
        for field, replacement in (("pack_selection", []), ("frozen_style", "arbitrary-new-style"),
                                   ("model_fingerprint", "invalid"), ("reason", "")):
            value = request()
            value["request"][field] = replacement
            variants.append(value)
        value = request()
        value["request"]["pack_selection"] *= 2
        variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_command(value, ROOT_PEER)

    def test_health_canary_and_disabled_final_use_only_synthetic_handler(self):
        backend = SyntheticBackend()
        for operation in ("health", "canary", "final-test"):
            value = request() if operation == "final-test" else {"version": 1, "operation": operation}
            connection = SyntheticConnection(value)
            with patch("kairos_data.owner_protocol.peer_credentials", return_value=ROOT_PEER):
                serve_connection(connection, backend)
            self.assertEqual(connection.frames[0]["type"], "error" if operation == "final-test" else operation)
        self.assertFalse(backend.opened)

    def test_synthetic_session_closes_on_success_and_disconnect(self):
        for fail_send in (False, True):
            backend = SyntheticBackend()
            backend.final_test_enabled = True
            connection = SyntheticConnection(request())
            if fail_send:
                connection.sendall = lambda frame: (_ for _ in ()).throw(BrokenPipeError())
            with patch("kairos_data.owner_protocol.peer_credentials", return_value=ROOT_PEER):
                serve_connection(connection, backend)
            self.assertTrue(backend.closed)
            if not fail_send:
                self.assertEqual([frame["type"] for frame in connection.frames], ["verified", "pack", "row", "complete"])

    def test_nonroot_peer_denied_before_handler(self):
        backend = SyntheticBackend()
        connection = SyntheticConnection({"version": 1, "operation": "health"})
        with patch("kairos_data.owner_protocol.peer_credentials", return_value=PeerCredentials(123, 1202, 1202)):
            serve_connection(connection, backend)
        self.assertEqual(connection.frames[0]["type"], "error")
        self.assertFalse(backend.opened)

    def test_credential_change_and_oversized_header_refused(self):
        connection = SyntheticConnection({"version": 1, "operation": "health"}, PeerCredentials(123, 1202, 1202))
        with self.assertRaises(AuthorizationError):
            receive_request(connection, ROOT_PEER, deadline=time.monotonic() + 1)
        connection = SyntheticConnection({"version": 1, "operation": "health"})
        connection.payload = bytearray(struct.pack("!I", MAX_REQUEST_BYTES + 1))
        with self.assertRaises(ValueError):
            receive_request(connection, ROOT_PEER, deadline=time.monotonic() + 1)


@unittest.skipUnless(hasattr(socket, "SO_PASSCRED") and hasattr(socket, "SCM_CREDENTIALS"), "Linux IPC only")
class OwnerKernelIPCTests(unittest.TestCase):
    def pair(self):
        receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(receiver.close)
        self.addCleanup(sender.close)
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        return receiver, sender

    def test_unprivileged_fragmented_frame_uses_real_per_message_credentials(self):
        receiver, sender = self.pair()
        frame = encode_frame({"version": 1, "operation": "canary"}, maximum=MAX_REQUEST_BYTES)
        failures = []

        def send():
            try:
                for part in (frame[:2], frame[2:5], frame[5:]):
                    sender.sendall(part)
                sender.shutdown(socket.SHUT_WR)
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=send)
        worker.start()
        try:
            value = receive_request(receiver, peer_credentials(receiver), deadline=time.monotonic() + 2)
        finally:
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(value, {"version": 1, "operation": "canary"})

    def test_actual_nonroot_connection_is_refused_and_slow_peer_times_out(self):
        receiver, sender = self.pair()
        if os.getuid() != 0:
            serve_connection(receiver, SyntheticBackend())
            self.assertEqual(decode_json(sender.recv(4096)[4:])["type"], "error")
        receiver, sender = self.pair()
        with self.assertRaises(TimeoutError):
            receive_request(receiver, peer_credentials(receiver), deadline=time.monotonic() + 0.02)

    def test_passed_handle_is_closed_and_refused(self):
        import array
        import tempfile

        receiver, sender = self.pair()
        with tempfile.TemporaryFile() as handle:
            before = len(os.listdir("/proc/self/fd"))
            sender.sendmsg([b"x"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [handle.fileno()]))])
            with self.assertRaises(AuthorizationError):
                receive_request(receiver, peer_credentials(receiver), deadline=time.monotonic() + 1)
            self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_extra_request_and_missing_half_close_are_refused(self):
        for suffix in (b"extra", None):
            receiver, sender = self.pair()
            sender.sendall(encode_frame({"version": 1, "operation": "health"}))
            if suffix is not None:
                sender.sendall(suffix)
                sender.shutdown(socket.SHUT_WR)
            with self.assertRaises((ValueError, TimeoutError)):
                receive_request(receiver, peer_credentials(receiver), deadline=time.monotonic() + 0.02)


if __name__ == "__main__":
    unittest.main()
