"""Owner-only client. No retries, discovery, arbitrary paths or commands on wire."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time

from .owner_boundary import peer_credentials
from .owner_daemon import PATHS, ServiceConfig
from .owner_protocol import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MAX_SESSION_WIRE_BYTES, SESSION_SECONDS,
    decode_json, encode_frame, parse_command,
)
from .owner_boundary import PeerCredentials
from .protected_io import open_absolute_directory


def main(config: ServiceConfig, arguments: list[str]) -> None:
    if os.geteuid() != 0:
        raise PermissionError("owner client must run as root")
    if arguments in (["health"], ["canary"]):
        value = {"version": 1, "operation": arguments[0]}
    elif arguments == ["final-test", "--consume-final-style-opening"]:
        if not config.final_test_enabled:
            raise PermissionError("final test remains disabled; ticket30 acceptance is required")
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request is too large")
        value = decode_json(raw)
        if value.get("operation") != "final-test":
            raise ValueError("expected a final-test request")
    else:
        raise ValueError("use health, canary, or later: final-test --consume-final-style-opening < request.json")
    parse_command(value, PeerCredentials(os.getpid(), os.geteuid(), os.getegid()))
    frame = encode_frame(value, maximum=MAX_REQUEST_BYTES)
    directory_fd = open_absolute_directory(PATHS["runtime_dir"], owner=config.service_uid, private=True)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            deadline = time.monotonic() + SESSION_SECONDS
            connection.settimeout(5)
            connection.connect(f"/proc/self/fd/{directory_fd}/owner.sock")
            if peer_credentials(connection).uid != config.service_uid:
                raise PermissionError("server identity differs from protected configuration")
            connection.sendall(frame)
            connection.shutdown(socket.SHUT_WR)
            total = 0
            complete = False

            def exact(count):
                result = bytearray()
                while len(result) < count:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("response deadline exceeded")
                    connection.settimeout(remaining)
                    part = connection.recv(count - len(result))
                    if not part:
                        raise ValueError("truncated response; do not automatically retry")
                    result.extend(part)
                return bytes(result)

            while not complete:
                length, = struct.unpack("!I", exact(4))
                total += length + 4
                if not 1 <= length <= MAX_RESPONSE_BYTES or total > MAX_SESSION_WIRE_BYTES:
                    raise ValueError("response exceeds protocol bounds")
                result = decode_json(exact(length))
                if result.get("version") != 1 or result.get("type") == "error":
                    raise ValueError("service refused or failed; authorization may already be consumed; no retry")
                print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
                complete = result.get("type") in {"health", "canary", "complete"}
    finally:
        os.close(directory_fd)
