"""Versioned, bounded, root-authenticated local IPC; no dynamic dispatch."""

from __future__ import annotations

import array
from dataclasses import dataclass
import json
import os
import socket
import struct
import time

from .owner_boundary import PeerCredentials, canonical_peer_requester, peer_credentials
from .owner_service import OpeningRequest, validate_opening_request
from .owner_session import PackExpectation, freeze_pack_selection
from .policy import AuthorizationError
from .storage import PackKind

MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SESSION_WIRE_BYTES = 9 * 1024 * 1024 * 1024
REQUEST_SECONDS = 5.0
SESSION_SECONDS = 900.0
_HEADER = struct.Struct("!I")
_CREDENTIALS = struct.Struct("=iII")


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON field")
        result[name] = value
    return result


def decode_json(payload: bytes) -> dict:
    def refuse_constant(value):
        raise ValueError("non-finite JSON number")

    value = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=refuse_constant)
    if not isinstance(value, dict):
        raise ValueError("protocol message must be a JSON object")
    return value


def encode_frame(value: dict, *, maximum: int = MAX_RESPONSE_BYTES) -> bytes:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not payload or len(payload) > maximum:
        raise ValueError("protocol frame exceeds its size limit")
    return _HEADER.pack(len(payload)) + payload


def _timeout(connection, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("protocol deadline exceeded")
    connection.settimeout(remaining)


def _receive(connection, maximum: int, peer: PeerCredentials, deadline: float) -> bytes:
    _timeout(connection, deadline)
    payload, ancillary, flags, _ = connection.recvmsg(
        maximum, socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(16 * array.array("i").itemsize),
        socket.MSG_CMSG_CLOEXEC,
    )
    credentials = []
    unexpected = False
    for level, kind, raw in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array("i")
            descriptors.frombytes(raw[:len(raw) - len(raw) % descriptors.itemsize])
            for descriptor in descriptors:
                os.close(descriptor)
            unexpected = True
        elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(raw) == _CREDENTIALS.size:
            credentials.append(PeerCredentials(*_CREDENTIALS.unpack(raw)))
        else:
            unexpected = True
    if unexpected or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC):
        raise AuthorizationError("ancillary handles or truncated messages are forbidden")
    if payload and credentials != [peer]:
        raise AuthorizationError("message credentials do not match the connected peer")
    return payload


def receive_request(connection, peer: PeerCredentials, *, deadline: float) -> dict:
    def exact(count):
        parts = bytearray()
        while len(parts) < count:
            part = _receive(connection, count - len(parts), peer, deadline)
            if not part:
                raise ValueError("incomplete protocol frame")
            parts.extend(part)
        return bytes(parts)

    length, = _HEADER.unpack(exact(_HEADER.size))
    if length < 1 or length > MAX_REQUEST_BYTES:
        raise ValueError("request frame exceeds its size limit")
    payload = exact(length)
    if _receive(connection, 1, peer, deadline):
        raise ValueError("one request per connection; client must half-close its write side")
    return decode_json(payload)


@dataclass(frozen=True)
class Command:
    operation: str
    opening: OpeningRequest | None = None


def parse_command(value: dict, peer: PeerCredentials) -> Command:
    if type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("unsupported protocol version")
    operation = value.get("operation")
    if operation in {"health", "canary"} and set(value) == {"version", "operation"}:
        return Command(operation)
    if operation != "final-test" or set(value) != {"version", "operation", "request"}:
        raise ValueError("unknown operation or fields")
    request = value["request"]
    fields = {"requested_at", "frozen_style", "model_fingerprint", "reason", "pack_selection"}
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("opening request has unknown or missing fields")
    if any(not isinstance(request[name], str) for name in fields - {"pack_selection"}):
        raise ValueError("opening metadata must be text")
    if request["frozen_style"] not in {"swing", "same-day"}:
        raise ValueError("only the frozen swing and same-day styles are supported")
    selection = request["pack_selection"]
    if not isinstance(selection, list) or not 1 <= len(selection) <= 256:
        raise ValueError("opening requires a bounded explicit selection")
    packs = []
    for entry in selection:
        if not isinstance(entry, dict) or set(entry) != {
            "identifier", "kind", "manifest_sha256", "content_sha256"
        }:
            raise ValueError("malformed pack selection")
        if any(not isinstance(item, str) for item in entry.values()):
            raise ValueError("pack selection values must be text")
        if len(entry["identifier"].encode("utf-8")) > 1024:
            raise ValueError("pack identifier is too long")
        packs.append(PackExpectation(
            entry["identifier"], PackKind(entry["kind"]), entry["manifest_sha256"], entry["content_sha256"]
        ))
    opening = OpeningRequest(
        requested_at=request["requested_at"], frozen_style=request["frozen_style"],
        model_fingerprint=request["model_fingerprint"], requester=canonical_peer_requester(peer),
        reason=request["reason"], pack_selection=freeze_pack_selection(packs).packs,
    )
    validate_opening_request(opening)
    return Command(operation, opening)


def serve_connection(connection, backend) -> None:
    """One accepted socket, one root peer, one request, one bounded response stream."""
    deadline = time.monotonic() + SESSION_SECONDS
    sent = 0

    def send(value):
        nonlocal sent
        frame = encode_frame(value)
        if sent + len(frame) > MAX_SESSION_WIRE_BYTES:
            raise ValueError("session exceeds its wire byte limit")
        _timeout(connection, deadline)
        connection.sendall(frame)
        sent += len(frame)

    try:
        peer = peer_credentials(connection)
        if peer.uid != 0:
            raise AuthorizationError("root peer required")
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        value = receive_request(connection, peer, deadline=time.monotonic() + REQUEST_SECONDS)
        command = parse_command(value, peer)
        if command.operation == "health":
            send({"version": 1, "type": "health", "final_test_enabled": backend.final_test_enabled})
        elif command.operation == "canary":
            send({"version": 1, "type": "canary", "sha256": backend.canary()})
        else:
            if not backend.final_test_enabled:
                raise AuthorizationError("final test disabled until separate ticket30 acceptance")
            with backend.open_session(command.opening, connection) as session:
                send({"version": 1, "type": "verified", "request_sha256": session.audit.request_sha256,
                      "selection_sha256": session.audit.selection_sha256, "packs": len(session.packs)})
                for pack in session.packs:
                    send({"version": 1, "type": "pack", "identifier": pack.identifier, "kind": pack.kind.value})
                    for row in pack.rows:
                        send({"version": 1, "type": "row", "row": row})
                send({"version": 1, "type": "complete"})
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        try:
            send({"version": 1, "type": "error", "code": "request-failed",
                  "retry": "no-automatic-retry; authorization-may-already-be-consumed"})
        except (OSError, ValueError):
            pass
