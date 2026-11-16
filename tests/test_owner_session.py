"""Synthetic owner selection metadata and audit policy tests only.

These tests must not call either final-test opening entry point or create packs.
"""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from kairos_data.owner_session import (
    PackExpectation,
    freeze_pack_selection,
    selection_value,
)
from kairos_data.owner_service import (
    OpeningRequest,
    _request_hash,
    authorize_and_audit,
    model_fingerprint,
    validate_opening_request,
)
from kairos_data.policy import AuthorizationError
from kairos_data.storage import PackKind


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def pack(identifier: str, kind: PackKind = PackKind.DAILY) -> PackExpectation:
    return PackExpectation(identifier, kind, _DIGEST_A, _DIGEST_B)


def request(selection: tuple[PackExpectation, ...]) -> OpeningRequest:
    return OpeningRequest(
        requested_at="2026-11-16T12:00:00+05:30",
        frozen_style="swing",
        model_fingerprint=model_fingerprint(b"synthetic weights", {"seed": 7}),
        requester="synthetic-owner",
        reason="metadata and audit policy test",
        pack_selection=selection,
    )


class _AllowOwner:
    def authorize(self, requester: str, reason: str) -> bool:
        return requester == "synthetic-owner" and bool(reason)


class _DenyOwner:
    def authorize(self, requester: str, reason: str) -> bool:
        return False


class OwnerSessionMetadataTests(unittest.TestCase):
    def test_freezes_ordered_daily_and_minute_metadata(self):
        selection = freeze_pack_selection(
            (pack("2025/daily-01"), pack("2025/minute-01", PackKind.MINUTE))
        )
        self.assertEqual(
            [item["kind"] for item in selection_value(selection)], ["daily", "minute"]
        )
        self.assertEqual(selection.packs[0].identifier, "2025/daily-01")
        self.assertNotEqual(
            selection.sha256,
            freeze_pack_selection(tuple(reversed(selection.packs))).sha256,
        )

    def test_rejects_duplicate_and_unsafe_identifiers(self):
        for identifier in ("", "/absolute", "../escape", "a/../escape", "a//b", "a\\b", "."):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                freeze_pack_selection((pack(identifier),))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            freeze_pack_selection((pack("daily/one"), pack("daily/one", PackKind.MINUTE)))

    def test_rejects_malformed_kind_digest_and_unbounded_collection(self):
        with self.assertRaisesRegex(ValueError, "kind"):
            freeze_pack_selection((PackExpectation("daily/one", "daily", _DIGEST_A, _DIGEST_B),))
        with self.assertRaisesRegex(ValueError, "digest"):
            freeze_pack_selection((PackExpectation("daily/one", PackKind.DAILY, "bad", _DIGEST_B),))
        with self.assertRaisesRegex(ValueError, "pack count"):
            freeze_pack_selection(tuple(pack(f"daily/{index}") for index in range(257)))
        with self.assertRaisesRegex(ValueError, "immutable tuple"):
            validate_opening_request(request([pack("daily/one")]))

    def test_audit_binds_canonical_selection_and_rejects_second_style_opening(self):
        selected = (pack("2025/daily-01"), pack("2025/minute-01", PackKind.MINUTE))
        opening = request(selected)
        validate_opening_request(opening)
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            audit = authorize_and_audit(
                opening, authorizer=_AllowOwner(), audit_log=audit_path
            )
            record = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(record["selection_sha256"], audit.selection_sha256)
            self.assertEqual(record["pack_selection"], selection_value(freeze_pack_selection(selected)))
            self.assertEqual(record["pack_selection"][1]["kind"], "minute")
            self.assertEqual(len(record["pack_selection"]), 2)
            with self.assertRaisesRegex(AuthorizationError, "already has"):
                authorize_and_audit(
                    opening, authorizer=_AllowOwner(), audit_log=audit_path
                )

    def test_request_hash_binds_every_selection_field_and_order(self):
        selected = (pack("daily/one"), pack("minute/one", PackKind.MINUTE))
        original = request(selected)
        variants = (
            tuple(reversed(selected)),
            (replace(selected[0], identifier="daily/two"), selected[1]),
            (replace(selected[0], kind=PackKind.MINUTE), selected[1]),
            (replace(selected[0], manifest_sha256="c" * 64), selected[1]),
            (replace(selected[0], content_sha256="d" * 64), selected[1]),
        )
        original_hash = _request_hash(original)
        for selection in variants:
            with self.subTest(selection=selection):
                self.assertNotEqual(original_hash, _request_hash(replace(original, pack_selection=selection)))

    def test_denial_writes_no_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            with self.assertRaises(AuthorizationError):
                authorize_and_audit(
                    request((pack("2025/daily-01"),)),
                    authorizer=_DenyOwner(),
                    audit_log=audit_path,
                )
            self.assertFalse(audit_path.exists())


if __name__ == "__main__":
    unittest.main()
