"""Synthetic-only checks for the isolated Kairos data package."""

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from kairos_data import PackKind, read_live_inputs, read_training_rows, write_pack
from kairos_data.policy import AuthorizationError, DateBoundaryError
from kairos_data.storage import partition_rows, read_verified_pack
from kairos_data.owner_service import (
    OpeningRequest,
    authorize_and_audit,
    model_fingerprint,
    validate_opening_request,
)


def _row(day: date, symbol: str = "SYN") -> dict[str, object]:
    return {"date": day.isoformat(), "symbol": symbol, "close": 100.0}


class BlindVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_partition_boundary_is_inclusive_without_loading_any_pack(self) -> None:
        rows = [_row(date(2024, 12, 31)), _row(date(2025, 1, 1)), _row(date(2025, 1, 2))]

        pre2025, blind = partition_rows(rows)

        self.assertEqual([row["date"] for row in pre2025], ["2024-12-31"])
        self.assertEqual([row["date"] for row in blind], ["2025-01-01", "2025-01-02"])

    def test_training_pack_count_checksum_and_repeat_write_are_stable(self) -> None:
        training_root = self.root / "training"
        training_root.mkdir()
        pack = training_root / "daily.pack"
        rows = [_row(date(2024, 12, 30)), _row(date(2024, 12, 31))]

        first = write_pack(pack, rows, kind=PackKind.DAILY, partition="pre2025")
        second = write_pack(pack, rows, kind=PackKind.DAILY, partition="pre2025")

        self.assertEqual(first, second)
        self.assertEqual(first.rows, 2)
        self.assertEqual(len(first.sha256), 64)
        self.assertEqual(list(read_training_rows(training_root, "daily.pack")), rows)

        with (pack / "rows.jsonl").open("ab") as output:
            output.write(b'{"date":"2024-12-31","symbol":"TAMPERED"}\n')
        with self.assertRaisesRegex(ValueError, "checksum"):
            list(read_verified_pack(pack, expected_partition="pre2025"))

    def test_training_refuses_boundary_before_opening_pack(self) -> None:
        with self.assertRaisesRegex(DateBoundaryError, "2025-01-01"):
            list(
                read_training_rows(
                    self.root / "does-not-exist",
                    "also-does-not-exist",
                    start=date(2025, 1, 1),
                )
            )

    def test_training_writer_rejects_boundary_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "pre2025"):
            write_pack(
                self.root / "training.pack",
                [_row(date(2025, 1, 1))],
                kind="daily",
                partition="pre2025",
            )

    def test_training_rejects_symlink_pack(self) -> None:
        training_root = self.root / "training"
        training_root.mkdir()
        real = training_root / "real.pack"
        write_pack(real, [_row(date(2024, 12, 31))], kind="daily", partition="pre2025")
        (training_root / "alias.pack").symlink_to(real, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlink"):
            list(read_training_rows(training_root, "alias.pack"))

    def test_live_reader_reads_only_last_60_known_sessions_and_projects_inputs(self) -> None:
        live_root = self.root / "live"
        live_root.mkdir()
        first_day = date(2026, 1, 1)
        for offset in range(61):
            day = first_day + timedelta(days=offset)
            write_pack(
                live_root / day.isoformat(),
                [{**_row(day), "volume": offset, "debug_note": offset}],
                kind="daily",
                partition="live",
            )
        # A future-dated symlink is listed but never resolved for this earlier query.
        (live_root / "2026-12-31").symlink_to(self.root / "missing-future-pack", target_is_directory=True)

        result = read_live_inputs(
            live_root,
            as_of=first_day + timedelta(days=60),
            input_columns=("close", "volume"),
        )

        self.assertEqual(len(result), 60)
        self.assertEqual(result[0]["date"], (first_day + timedelta(days=1)).isoformat())
        self.assertEqual(result[-1]["date"], (first_day + timedelta(days=60)).isoformat())
        self.assertEqual(set(result[-1]), {"date", "symbol", "close", "volume"})
        self.assertTrue(all("debug_note" not in row for row in result))
        with self.assertRaisesRegex(ValueError, "input_columns"):
            read_live_inputs(live_root, as_of=date(2026, 3, 1), input_columns=("private_score",))

    def test_live_pack_rejects_answer_and_future_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "live input packs"):
            write_pack(
                self.root / "2024-12-31",
                [{**_row(date(2024, 12, 31)), "future_return": 0.1}],
                kind="daily",
                partition="live",
            )

    def test_minute_pack_format_works_with_pre2025_rows(self) -> None:
        rows = [{"date": "2024-12-31T09:15:00", "symbol": "SYN", "close": 100.0}]
        manifest = write_pack(self.root / "minute.pack", rows, kind="minute", partition="pre2025")

        self.assertEqual(manifest.kind, PackKind.MINUTE)
        self.assertEqual(manifest.rows, 1)
        self.assertEqual(
            list(read_verified_pack(self.root / "minute.pack", expected_partition="pre2025")),
            rows,
        )

    def test_ordinary_reader_refuses_blind_partition_without_opening_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot open the blind partition"):
            list(read_verified_pack(self.root / "no-vault-access", expected_partition="blind"))

    def test_owner_policy_audits_once_per_frozen_style_without_opening_vault(self) -> None:
        request = self._request()
        validate_opening_request(request)

        audit = authorize_and_audit(request, authorizer=_AllowOwner(), audit_log=self.root / "audit.jsonl")

        self.assertEqual(audit.frozen_style, "swing")
        record = json.loads((self.root / "audit.jsonl").read_text())
        self.assertEqual(record["requested_at"], request.requested_at)
        self.assertEqual(record["model_fingerprint"], request.model_fingerprint)
        self.assertEqual(record["requester"], request.requester)
        self.assertEqual(record["reason"], request.reason)
        with self.assertRaisesRegex(AuthorizationError, "already has"):
            authorize_and_audit(request, authorizer=_AllowOwner(), audit_log=self.root / "audit.jsonl")

    def test_owner_policy_denies_without_audit_record(self) -> None:
        with self.assertRaises(AuthorizationError):
            authorize_and_audit(
                self._request(),
                authorizer=_DenyOwner(),
                audit_log=self.root / "audit.jsonl",
            )
        self.assertFalse((self.root / "audit.jsonl").exists())

    def _request(self) -> OpeningRequest:
        return OpeningRequest(
            requested_at="2026-11-15T12:00:00+05:30",
            frozen_style="swing",
            model_fingerprint=model_fingerprint(b"synthetic weights", {"seed": 7}),
            requester="owner",
            reason="synthetic authorization policy check",
        )


class _AllowOwner:
    def authorize(self, requester: str, reason: str) -> bool:
        return requester == "owner" and bool(reason)


class _DenyOwner:
    def authorize(self, requester: str, reason: str) -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
