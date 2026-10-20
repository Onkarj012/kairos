from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ingest.daily_panel import build_panel, parse_body, replace_directory
from scripts.data.check_daily_panel import _adjustments

SESSION = date(2022, 8, 8)
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
FULL_HEADER = [" SYMBOL", " SERIES", " DATE1", " OPEN_PRICE", " HIGH_PRICE", " LOW_PRICE", " CLOSE_PRICE", " PREV_CLOSE", " TTL_TRD_QNTY", " TURNOVER_LACS"]


def _xlsx(rows: list[list[str]]) -> bytes:
    """A minimal XLSX: one worksheet, every cell a shared string."""
    strings: list[str] = []
    sheet_rows = []
    for number, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            if value not in strings:
                strings.append(value)
            cells.append(f'<c r="{chr(65 + column)}{number}" t="s"><v>{strings.index(value)}</v></c>')
        sheet_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}">{shared}</sst>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{NS}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buffer.getvalue()


def _udiff_zip(rows: list[dict[str, str]]) -> bytes:
    """A UDiFF cash ZIP with one CSV member holding the given rows."""
    header = ["TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "NewBrdLotQty"]
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(row.get(column, "2022-08-08" if column in ("TradDt", "BizDt") else "1") for column in header))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20220808_F_0000.csv", "\n".join(lines) + "\n")
    return buffer.getvalue()


def test_xlsx_body_parses_kept_series_with_stripped_headers() -> None:
    body = _xlsx([
        FULL_HEADER,
        ["AAA", "EQ", "08-Aug-2022", "100", "105", "99", "104", "98", "1200", "12.5"],
        ["BBB", "BE", "08-Aug-2022", "50", "51", "49", "50", "49", "300", "1.5"],
        ["CCC", "N1", "08-Aug-2022", "10", "11", "9", "10", "10", "5", "0.1"],
    ])

    frame = parse_body(body, SESSION)

    assert list(frame["symbol"]) == ["AAA", "BBB"]
    assert list(frame["series"]) == ["EQ", "BE"]
    assert frame["traded_value"].tolist() == [1250000.0, 150000.0]
    assert frame["volume"].tolist() == [1200, 300]
    assert frame["isin"].isna().all()


def test_udiff_zip_keeps_be_and_sm_rows_with_isin() -> None:
    body = _udiff_zip([
        {"TckrSymb": "20MICRONS", "SctySrs": "EQ", "ISIN": "INE144J01027", "ClsPric": "201.29", "PrvsClsgPric": "207.86", "TtlTrfVal": "22530502.12", "TtlTradgVol": "111270"},
        {"TckrSymb": "SMALLCO", "SctySrs": "SM", "ISIN": "INE000000001", "TtlTrfVal": "5000.5", "TtlTradgVol": "40"},
        {"TckrSymb": "BETA", "SctySrs": "BE", "ISIN": "INE000000002", "TtlTrfVal": "900", "TtlTradgVol": "9"},
        {"TckrSymb": "GAMMA", "SctySrs": "BL", "ISIN": "INE000000003", "TtlTrfVal": "1", "TtlTradgVol": "1"},
    ])

    frame = parse_body(body, SESSION)

    assert sorted(frame["series"]) == ["BE", "EQ", "SM"]
    assert set(frame["symbol"]) == {"20MICRONS", "SMALLCO", "BETA"}
    assert frame.set_index("symbol").loc["20MICRONS", "isin"] == "INE144J01027"
    assert frame.set_index("symbol").loc["SMALLCO", "traded_value"] == 5000.5


def test_receipt_body_with_wrong_sha256_raises(tmp_path: Path) -> None:
    session = date(2024, 1, 2)
    root = tmp_path / "evidence"
    receipt_dir = root / "requests" / session.isoformat() / "attempt-001"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "response.bin").write_bytes(b"not the recorded bytes")
    (receipt_dir / "receipt.json").write_text(json.dumps({"body_path": f"requests/{session.isoformat()}/attempt-001/response.bin"}))
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"source_inventory": {"price_recovery": {"by_root_and_receipt": [{
        "root": str(root),
        "receipt": f"requests/{session.isoformat()}/attempt-001/receipt.json",
        "body_sha256": hashlib.sha256(b"something else").hexdigest(),
        "session_date": session.isoformat(),
        "source_url": "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_02012024.csv",
    }]}}}))
    ledger = tmp_path / "sessions.jsonl"
    ledger.write_text(json.dumps({"session_date": session.isoformat(), "state": "expected"}) + "\n")

    with pytest.raises(ValueError, match=f"{session.isoformat()}.*sha256"):
        build_panel(readiness, ledger)


def test_replace_directory_restores_previous_output_when_swap_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "old.txt").write_text("old", encoding="utf-8")
    staging = tmp_path / ".out.staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    real_rename = os.rename

    def failing_rename(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        """Fail only the swap of staging into out_dir."""
        if Path(src) == staging:
            raise OSError("swap failed")
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", failing_rename)
    with pytest.raises(OSError, match="swap failed"):
        replace_directory(staging, out_dir)

    assert (out_dir / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / ".out.retired").exists()

    # A run that died after moving out_dir aside leaves the old output only in .out.retired.
    os.rename(out_dir, tmp_path / ".out.retired")
    with pytest.raises(OSError, match="swap failed"):
        replace_directory(staging, out_dir)
    assert (out_dir / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / ".out.retired").exists()

    monkeypatch.undo()
    replace_directory(staging, out_dir)
    assert (out_dir / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (out_dir / "old.txt").exists()
    assert not (tmp_path / ".out.retired").exists()


def test_adjustment_status_reviews_aborted_fetch_and_failures(tmp_path: Path) -> None:
    """An aborted corporate-action run or a failed symbol keeps adjustment status at review."""
    import csv

    def write_csv(name: str, columns: list[str], rows: list[list[str]]) -> None:
        """Write a small CSV fixture for the adjustment section."""
        with (tmp_path / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)

    write_csv("events.csv", ["kind", "nse_prev_close_adjusted"], [])
    write_csv("purposes.csv", ["reason"], [])
    write_csv("moves.csv", ["reason"], [])
    write_csv("failures.csv", ["symbol"], [])
    (tmp_path / "summary.json").write_text(json.dumps({"tdpowersys_2026_08_24_adjusted_r": 0.0}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"run_status": "aborted"}), encoding="utf-8")
    paths = {
        "events": tmp_path / "events.csv",
        "review_purposes": tmp_path / "purposes.csv",
        "review_moves": tmp_path / "moves.csv",
        "adjustments_summary": tmp_path / "summary.json",
        "corporate_actions_manifest": tmp_path / "manifest.json",
        "failures": tmp_path / "failures.csv",
    }

    assert _adjustments(paths).status == "review"
    write_csv("failures.csv", ["symbol"], [["FAILED"]])
    (tmp_path / "manifest.json").write_text(json.dumps({"run_status": "finished"}), encoding="utf-8")
    assert _adjustments(paths).status == "review"
