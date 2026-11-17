"""Synthetic regression tests for bounded JSONL and Parquet pack handling."""

from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kairos_data import PackKind, read_training_rows, write_pack
from kairos_data.policy import DateBoundaryError
from kairos_data.storage import MAX_JSONL_ROW_BYTES, read_verified_pack


def row(day: str, close: float = 100.0) -> dict[str, object]:
    return {"date": day, "symbol": "SYN", "close": close, "volume": 7}


class CompactPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_jsonl_writer_boundary_roundtrips_and_retries(self):
        for character in ("x", "é"):
            for size in (MAX_JSONL_ROW_BYTES - 1, MAX_JSONL_ROW_BYTES):
                with self.subTest(character=character, size=size):
                    record = {**row("2024-12-31"), "text": ""}
                    overhead = len((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
                    width = len(json.dumps(character)[1:-1].encode())
                    repeats, padding = divmod(size - overhead, width)
                    record["text"] = character * repeats + "x" * padding
                    pack = self.root / "boundary"
                    manifest = write_pack(pack, [record], kind="daily", partition="pre2025")
                    self.assertEqual((pack / "rows.jsonl").stat().st_size, size)
                    self.assertEqual(list(read_verified_pack(pack, expected_partition="pre2025")), [record])
                    self.assertEqual(write_pack(pack, [record], kind="daily", partition="pre2025"), manifest)
                    for path in pack.iterdir():
                        path.unlink()
                    pack.rmdir()

    def test_oversized_jsonl_write_cleans_staging_and_preserves_existing_pack(self):
        for existing in (False, True):
            for character in ("x", "é"):
                with self.subTest(existing=existing, character=character):
                    pack = self.root / "oversized"
                    original = row("2024-12-31")
                    if existing:
                        write_pack(pack, [original], kind="daily", partition="pre2025")
                    record = {**original, "text": ""}
                    overhead = len((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
                    width = len(json.dumps(character)[1:-1].encode())
                    repeats, padding = divmod(MAX_JSONL_ROW_BYTES + 1 - overhead, width)
                    record["text"] = character * repeats + "x" * padding
                    for _ in range(2):
                        with self.assertRaisesRegex(ValueError, "JSONL row exceeds the supported byte limit"):
                            write_pack(pack, [original, record], kind="daily", partition="pre2025")
                        self.assertEqual(list(self.root.iterdir()), [pack] if existing else [])
                        if existing:
                            self.assertEqual(list(read_verified_pack(pack, expected_partition="pre2025")), [original])
                    if existing:
                        for path in pack.iterdir():
                            path.unlink()
                        pack.rmdir()

    def test_legacy_manifest_without_encoding_remains_jsonl(self):
        pack = self.root / "legacy"
        pack.mkdir()
        raw = (json.dumps(row("2024-12-31"), sort_keys=True, separators=(",", ":")) + "\n").encode()
        (pack / "rows.jsonl").write_bytes(raw)
        (pack / "manifest.json").write_text(json.dumps({
            "format_version": 1, "kind": "daily", "partition": "pre2025", "rows": 1,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "first_date": "2024-12-31", "last_date": "2024-12-31",
        }))
        self.assertEqual(list(read_verified_pack(pack, expected_partition="pre2025")), [row("2024-12-31")])

    def test_parquet_roundtrips_daily_and_minute_through_training_reader(self):
        root = self.root / "training"
        root.mkdir()
        cases = (
            ("daily", [row("2024-12-30"), row("2024-12-31", 101.25)]),
            ("minute", [{"date": "2024-12-31T09:15:00.123456", "symbol": "SYN", "close": 100.25}]),
        )
        for kind, rows in cases:
            pack = root / (kind + ".pack")
            manifest = write_pack(pack, iter(rows), kind=kind, partition="pre2025", encoding="parquet", row_group_size=2)
            self.assertEqual(manifest.encoding, "parquet")
            self.assertEqual(list(read_training_rows(root, pack.name)), rows)

    def test_training_boundary_refusal_precedes_pack_open(self):
        with self.assertRaises(DateBoundaryError):
            list(read_training_rows(self.root / "missing", "missing", start=date(2025, 1, 1)))

    def test_tampered_parquet_bytes_or_manifest_count_yield_nothing(self):
        pack = self.root / "pack"
        write_pack(pack, [row("2024-12-31")], kind="daily", partition="pre2025", encoding="parquet")
        manifest_path = pack / "manifest.json"
        original = json.loads(manifest_path.read_text())
        original["rows"] = 2
        manifest_path.write_text(json.dumps(original))
        result = read_verified_pack(pack, expected_partition="pre2025")
        with self.assertRaises(ValueError):
            next(result)

    def test_parquet_checksum_tamper_yields_nothing(self):
        pack = self.root / "checksum"
        write_pack(pack, [row("2024-12-31")], kind="daily", partition="pre2025", encoding="parquet")
        payload = pack / "rows.parquet"
        raw = bytearray(payload.read_bytes())
        raw[-1] ^= 1
        payload.write_bytes(raw)
        result = read_verified_pack(pack, expected_partition="pre2025")
        with self.assertRaises(ValueError):
            next(result)

    def test_unknown_encoding_and_malformed_schema_metadata_are_rejected(self):
        pack = self.root / "metadata"
        write_pack(pack, [row("2024-12-31")], kind="daily", partition="pre2025", encoding="parquet")
        manifest_path = pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["encoding"] = "csv"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "invalid pack manifest"):
            next(read_verified_pack(pack, expected_partition="pre2025"))
        manifest["encoding"] = "parquet"
        manifest["schema"] = [{"name": "date"}]
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "invalid pack manifest"):
            next(read_verified_pack(pack, expected_partition="pre2025"))

    def test_well_formed_manifest_schema_mismatches_yield_nothing(self):
        for field, key, incorrect in ((1, "name", "wrong_symbol"), (2, "type", "int64")):
            with self.subTest(key=key):
                pack = self.root / ("schema-" + key)
                write_pack(pack, [row("2024-12-31")], kind="daily", partition="pre2025", encoding="parquet")
                manifest_path = pack / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["schema"][field][key] = incorrect
                manifest_path.write_text(json.dumps(manifest))
                result = read_verified_pack(pack, expected_partition="pre2025")
                with self.assertRaisesRegex(ValueError, "Parquet schema does not match manifest"):
                    next(result)

    def test_incorrect_embedded_schema_metadata_yields_nothing(self):
        import pyarrow.parquet as parquet

        pack = self.root / "embedded-schema"
        write_pack(pack, [row("2024-12-31")], kind="daily", partition="pre2025", encoding="parquet")
        payload = pack / "rows.parquet"
        reader = parquet.ParquetFile(payload)
        try:
            table = reader.read()
        finally:
            reader.close()
        metadata = dict(table.schema.metadata)
        embedded_schema = json.loads(metadata[b"kairos.schema"])
        embedded_schema[1]["name"] = "wrong_symbol"
        metadata[b"kairos.schema"] = json.dumps(embedded_schema, separators=(",", ":")).encode()
        parquet.write_table(table.replace_schema_metadata(metadata), payload)
        manifest_path = pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        result = read_verified_pack(pack, expected_partition="pre2025")
        with self.assertRaisesRegex(ValueError, "Parquet schema metadata is missing or malformed"):
            next(result)

    def test_one_pass_batches_and_schema_drift_refused(self):
        import pyarrow.parquet as parquet

        consumed = []
        def once():
            for item in (row("2024-12-30"), row("2024-12-31")):
                consumed.append(item)
                yield item
        written_batches = []
        real_write_table = parquet.ParquetWriter.write_table
        def record_batch(writer, table, **kwargs):
            written_batches.append(table.num_rows)
            return real_write_table(writer, table, **kwargs)
        with patch.object(parquet.ParquetWriter, "write_table", record_batch):
            manifest = write_pack(self.root / "ok", once(), kind="daily", partition="pre2025", encoding="parquet", row_group_size=1)
        self.assertEqual(manifest.rows, 2)
        self.assertEqual(len(consumed), 2)
        self.assertEqual(written_batches, [1, 1])
        for broken in (
            {"date": "2024-12-31", "symbol": "SYN", "other": 5},
            {"date": "2024-12-31", "symbol": "SYN"},
            {"date": "2024-12-31", "symbol": "SYN", "close": "not-a-number", "volume": 7},
        ):
            def drift():
                yield row("2024-12-30")
                yield broken
            with self.subTest(broken=broken), self.assertRaisesRegex(ValueError, "schema|type"):
                write_pack(self.root / "drift", drift(), kind="daily", partition="pre2025", encoding="parquet", row_group_size=1)

    def test_retry_identical_and_conflict_rejected(self):
        pack = self.root / "immutable"
        first = write_pack(pack, [row("2024-12-31")], kind=PackKind.DAILY, partition="pre2025", encoding="parquet")
        self.assertEqual(first, write_pack(pack, [row("2024-12-31")], kind="daily", partition="pre2025", encoding="parquet"))
        with self.assertRaises(FileExistsError):
            write_pack(pack, [row("2024-12-31", 200)], kind="daily", partition="pre2025", encoding="parquet")

    def test_nullable_columns_roundtrip_and_frozen_null_type_refuses_drift(self):
        rows = [{**row("2024-12-30"), "optional": None}, {**row("2024-12-31"), "optional": None}]
        pack = self.root / "nullable"
        write_pack(pack, rows, kind="daily", partition="pre2025", encoding="parquet", row_group_size=1)
        self.assertEqual(list(read_verified_pack(pack, expected_partition="pre2025")), rows)
        drifting = self.root / "nullable-drift"
        with self.assertRaisesRegex(ValueError, "type inconsistency"):
            write_pack(drifting, [rows[0], {**rows[1], "optional": 1}], kind="daily", partition="pre2025", encoding="parquet", row_group_size=1)
        self.assertFalse(drifting.exists())

    def test_live_parquet_schema_and_date_boundary(self):
        from kairos_data import read_live_inputs
        live = self.root / "live"
        live.mkdir()
        day = "2024-12-30"
        write_pack(live / day, [row(day)], kind="daily", partition="live", encoding="parquet")
        self.assertEqual(read_live_inputs(live, as_of=date(2024, 12, 31), input_columns=("close",))[0]["close"], 100.0)
        with self.assertRaisesRegex(ValueError, "live input packs"):
            write_pack(live / "bad", [{**row(day), "future_answer": 1}], kind="daily", partition="live", encoding="parquet")

    def test_compact_fixture_size_is_smaller_than_jsonl_fixture(self):
        rows = [row(f"2024-12-{(offset % 28) + 1:02d}") for offset in range(300)]
        json_pack, parquet_pack = self.root / "json", self.root / "parquet"
        write_pack(json_pack, rows, kind="daily", partition="pre2025")
        write_pack(parquet_pack, rows, kind="daily", partition="pre2025", encoding="parquet")
        compact_bytes = (parquet_pack / "rows.parquet").stat().st_size
        jsonl_bytes = (json_pack / "rows.jsonl").stat().st_size
        print(f"SYNTHETIC PACK BYTES: parquet={compact_bytes}, jsonl={jsonl_bytes}")
        self.assertLess(compact_bytes, jsonl_bytes)


if __name__ == "__main__":
    unittest.main()
