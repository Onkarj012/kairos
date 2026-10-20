from __future__ import annotations

import hashlib
import gzip
import io
import warnings
import zipfile
from datetime import date

import pandas as pd
import pytest

from ingest.nse_bhavcopy import (
    cash_bhavcopy_url_candidates,
    nse_archives_mii_security_url,
    parse_sec_bhavdata_full,
    parse_cm_bhavcopy_zip,
)


TRADE_DATE = date(2025, 1, 31)


def _zip_csv(frame: pd.DataFrame, name: str = "bhavcopy.csv") -> bytes:
    csv_bytes = frame.to_csv(index=False).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, csv_bytes)
    return output.getvalue()


def _legacy_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SYMBOL": " AAA ",
                "SERIES": "EQ",
                "OPEN": "100.0",
                "HIGH": "105.0",
                "LOW": "99.0",
                "CLOSE": "104.0",
                "LAST": "103.5",
                "PREVCLOSE": "98.0",
                "TOTTRDQTY": "1,200",
                "TOTTRDVAL": "123456.50",
                "TIMESTAMP": "31-JAN-2025",
                "TOTALTRADES": "42",
                "ISIN": "INE000A01000",
            },
            {
                "SYMBOL": " BBB ",
                "SERIES": "BE",
                "OPEN": "bad",
                "HIGH": "bad",
                "LOW": "bad",
                "CLOSE": "bad",
                "LAST": "bad",
                "PREVCLOSE": "bad",
                "TOTTRDQTY": "bad",
                "TOTTRDVAL": "bad",
                "TIMESTAMP": "31-JAN-2025",
                "TOTALTRADES": "bad",
                "ISIN": "",
            },
        ]
    )


def _udiff_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "TradDt": "2025-01-31",
                "BizDt": "2025-01-31",
                "Sgmt": "CM",
                "Src": "NSE",
                "FinInstrmTp": "STK",
                "FinInstrmId": "12345",
                "ISIN": "INE000A01000",
                "TckrSymb": "AAA",
                "SctySrs": "EQ",
                "OpnPric": "100",
                "HghPric": "105",
                "LwPric": "99",
                "ClsPric": "104",
                "PrvsClsgPric": "98",
                "TtlTradgVol": "1200",
                "TtlTrfVal": "123456.5",
                "TtlNbOfTxsExctd": "42",
                "NewBrdLotQty": "1",
            },
            {
                "TradDt": "2025-01-31",
                "BizDt": "2025-01-31",
                "Sgmt": "CM",
                "Src": "NSE",
                "FinInstrmTp": "STK",
                "FinInstrmId": "99999",
                "ISIN": "INE000B01000",
                "TckrSymb": "BBB",
                "SctySrs": "BE",
                "OpnPric": "bad",
                "HghPric": "bad",
                "LwPric": "bad",
                "ClsPric": "bad",
                "PrvsClsgPric": "bad",
                "TtlTradgVol": "bad",
                "TtlTrfVal": "bad",
                "TtlNbOfTxsExctd": "bad",
                "NewBrdLotQty": "bad",
            },
        ]
    )


def _sec_bhavdata_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SYMBOL": " AAA ",
                "SERIES": " EQ",
                "DATE1": " 31-Jan-2025",
                "PREV_CLOSE": " 98.0",
                "OPEN_PRICE": " 100.0",
                "HIGH_PRICE": " 105.0",
                "LOW_PRICE": " 99.0",
                "LAST_PRICE": " 103.5",
                "CLOSE_PRICE": " 104.0",
                "AVG_PRICE": " 102.0",
                "TTL_TRD_QNTY": " 1200",
                "TURNOVER_LACS": " 1234.565",
                "NO_OF_TRADES": " 42",
                "DELIV_QTY": " 600",
                "DELIV_PER": " 50.0",
            }
        ]
    )


def _mii_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [{"FinInstrmId": "12345", "TckrSymb": "AAA", "SctySrs": "EQ", "ISIN": "INE000A01000", "NewBrdLotQty": "1"}]
    )


def test_url_candidates_are_deterministic_and_prefer_udiff() -> None:
    urls = cash_bhavcopy_url_candidates(TRADE_DATE)
    assert urls == (
        "https://nsearchives.nseindia.com/content/cm/"
        "BhavCopy_NSE_CM_0_0_0_20250131_F_0000.csv.zip",
        "https://archives.nseindia.com/content/historical/EQUITIES/2025/JAN/"
        "cm31JAN2025bhav.csv.zip",
    )


def test_archive_mii_url_uses_the_exact_official_path() -> None:
    assert nse_archives_mii_security_url(TRADE_DATE) == (
        "https://archives.nseindia.com/content/cm/NSE_CM_security_31012025.csv.gz"
    )


def test_legacy_cash_zip_is_normalized_and_filters_non_eq() -> None:
    content = _zip_csv(_legacy_frame())
    result = parse_cm_bhavcopy_zip(content, TRADE_DATE)

    assert result["symbol"].tolist() == ["AAA"]
    assert result.loc[0, "series"] == "EQ"
    assert result.loc[0, "isin"] == "INE000A01000"
    assert result.loc[0, "instrument_id"] is pd.NA
    assert result.loc[0, "open"] == 100.0
    assert result.loc[0, "volume"] == 1200
    assert result.loc[0, "turnover"] == 123456.5
    assert result.loc[0, "n_trades"] == 42
    assert pd.isna(result.loc[0, "lot_size"])
    assert result.loc[0, "source_format"] == "legacy"
    assert result.loc[0, "source_hash"] == hashlib.sha256(content).hexdigest()
    assert result.loc[0, "usable_from"] == pd.Timestamp("2025-02-01")


def test_udiff_cash_zip_is_preferred_by_schema_and_preserves_instrument_id() -> None:
    result = parse_cm_bhavcopy_zip(_zip_csv(_udiff_frame()), TRADE_DATE)

    assert result["symbol"].tolist() == ["AAA"]
    assert result.loc[0, "series"] == "EQ"
    assert result.loc[0, "instrument_id"] == "12345"
    assert result.loc[0, "isin"] == "INE000A01000"
    assert result.loc[0, "close"] == 104.0
    assert result.loc[0, "turnover"] == 123456.5
    assert result.loc[0, "lot_size"] == 1
    assert result.loc[0, "source_format"] == "udiff"


def test_sec_bhavdata_full_joins_same_day_mii_identity() -> None:
    result = parse_sec_bhavdata_full(
        _sec_bhavdata_frame().to_csv(index=False).encode(),
        gzip.compress(_mii_frame().to_csv(index=False).encode()),
        TRADE_DATE,
        selected_symbols=("AAA",),
    )

    assert result[["symbol", "isin", "instrument_id", "series"]].iloc[0].tolist() == [
        "AAA",
        "INE000A01000",
        "12345",
        "EQ",
    ]
    assert result.loc[0, "close"] == 104.0
    assert result.loc[0, "prev_close"] == 98.0
    assert result.loc[0, "source_format"] == "sec_bhavdata_full+mii"


def test_cash_parser_uses_explicit_date_formats_without_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parse_cm_bhavcopy_zip(_zip_csv(_legacy_frame()), TRADE_DATE)
        parse_cm_bhavcopy_zip(_zip_csv(_udiff_frame()), TRADE_DATE)

    assert not [warning for warning in caught if issubclass(warning.category, UserWarning)]


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (_legacy_frame().assign(TIMESTAMP="30-JAN-2025"), "trade date"),
        (_legacy_frame().assign(ISIN=["", ""]), "blank ISIN"),
        (_legacy_frame().assign(CLOSE=["bad", "bad"]), "invalid CLOSE"),
        (_legacy_frame().iloc[[0, 0]].assign(SYMBOL=["AAA", "BBB"], SERIES=["EQ", "EQ"]), "duplicate"),
        (_legacy_frame().iloc[[0, 0]].assign(SYMBOL=["AAA", "AAA"], SERIES=["EQ", "EQ"], ISIN=["INE000A01000", "INE000B01000"]), "duplicate"),
    ],
)
def test_cash_parser_rejects_invalid_rows(frame: pd.DataFrame, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_cm_bhavcopy_zip(_zip_csv(frame), TRADE_DATE)


def test_cash_parser_rejects_malformed_archive_and_unsupported_schema() -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_cm_bhavcopy_zip(b"not a zip", TRADE_DATE)
    with pytest.raises(ValueError, match="unsupported"):
        parse_cm_bhavcopy_zip(_zip_csv(pd.DataFrame({"wrong": ["schema"]})), TRADE_DATE)
