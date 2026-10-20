"""NSE bhavcopy parsing and point-in-time availability stamps."""
from __future__ import annotations

import io
import hashlib
import gzip
import math
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from ingest.kite import data_root


LEGACY_CASH_COLUMNS = (
    "SYMBOL",
    "SERIES",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "LAST",
    "PREVCLOSE",
    "TOTTRDQTY",
    "TOTTRDVAL",
    "TIMESTAMP",
    "TOTALTRADES",
    "ISIN",
)
UDIFF_CASH_COLUMNS = (
    "TradDt",
    "BizDt",
    "Sgmt",
    "Src",
    "FinInstrmTp",
    "FinInstrmId",
    "ISIN",
    "TckrSymb",
    "SctySrs",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "PrvsClsgPric",
    "TtlTradgVol",
    "TtlTrfVal",
    "TtlNbOfTxsExctd",
    "NewBrdLotQty",
)
SEC_BHAVDATA_COLUMNS = (
    "SYMBOL",
    "SERIES",
    "DATE1",
    "PREV_CLOSE",
    "OPEN_PRICE",
    "HIGH_PRICE",
    "LOW_PRICE",
    "LAST_PRICE",
    "CLOSE_PRICE",
    "AVG_PRICE",
    "TTL_TRD_QNTY",
    "TURNOVER_LACS",
    "NO_OF_TRADES",
    "DELIV_QTY",
    "DELIV_PER",
)
MII_SECURITY_COLUMNS = ("FinInstrmId", "TckrSymb", "SctySrs", "ISIN")
CASH_BHAVCOPY_COLUMNS = (
    "symbol",
    "isin",
    "instrument_id",
    "series",
    "date",
    "open",
    "high",
    "low",
    "close",
    "last",
    "prev_close",
    "volume",
    "turnover",
    "n_trades",
    "lot_size",
    "source",
    "source_format",
    "source_hash",
    "available_at",
    "usable_from",
)


def nse_cash_bhavcopy_url_candidates(trade_date: date) -> tuple[str, ...]:
    """Return official UDiFF-first and legacy NSE cash-bhavcopy URLs."""
    month = trade_date.strftime("%b").upper()
    return (
        "https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip",
        "https://archives.nseindia.com/content/historical/EQUITIES/"
        f"{trade_date:%Y}/{month}/cm{trade_date:%d}{month}{trade_date:%Y}bhav.csv.zip",
    )


def nse_sec_bhavdata_full_url(trade_date: date) -> str:
    """Return the documented official full cash bhavcopy URL."""
    return (
        "https://archives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{trade_date:%d%m%Y}.csv"
    )


def nse_mii_security_url(trade_date: date) -> str:
    """Return the documented official daily CM MII security-file URL."""
    return (
        "https://nsearchives.nseindia.com/content/cm/"
        f"NSE_CM_security_{trade_date:%d%m%Y}.csv.gz"
    )


def nse_archives_mii_security_url(trade_date: date) -> str:
    """Return the qualified official archive-host CM MII security-file URL."""
    return (
        "https://archives.nseindia.com/content/cm/"
        f"NSE_CM_security_{trade_date:%d%m%Y}.csv.gz"
    )


def _cash_zip_csv(content: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            csv_names = [
                name
                for name in archive.namelist()
                if not name.endswith("/") and name.lower().endswith(".csv")
            ]
            if len(csv_names) != 1:
                raise ValueError("cash bhavcopy ZIP must contain exactly one CSV")
            return archive.read(csv_names[0])
    except ValueError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError("malformed cash bhavcopy ZIP archive") from exc


def _cash_frame(content: bytes) -> pd.DataFrame:
    try:
        raw = pd.read_csv(io.BytesIO(_cash_zip_csv(content)), dtype=str)
    except ValueError:
        raise
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError("cash bhavcopy CSV could not be parsed") from exc
    raw.columns = [str(column).lstrip("\ufeff").strip() for column in raw.columns]
    columns = set(raw.columns)
    if set(UDIFF_CASH_COLUMNS).issubset(columns):
        return raw
    if set(LEGACY_CASH_COLUMNS).issubset(columns):
        return raw
    raise ValueError("unsupported NSE cash bhavcopy schema")


def _parse_cash_dates(values: pd.Series, formats: tuple[str, ...]) -> pd.Series:
    values = values.astype("string").str.strip()
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    for date_format in formats:
        remaining = parsed.isna() & values.notna()
        if not remaining.any():
            break

        def parse_one(value: str) -> datetime | None:
            try:
                return datetime.strptime(value, date_format)
            except ValueError:
                return None

        parsed.loc[remaining] = values.loc[remaining].map(parse_one)
    return parsed


def _validate_cash_dates(
    raw: pd.DataFrame,
    trade_date: date,
    columns: tuple[str, ...],
    formats: tuple[str, ...],
) -> None:
    for column in columns:
        parsed = _parse_cash_dates(raw[column], formats)
        if parsed.empty:
            continue
        if parsed.isna().any() or set(parsed.dt.date.dropna()) != {trade_date}:
            raise ValueError(f"cash bhavcopy {column} does not match trade date")


def _cash_numbers(raw: pd.Series, name: str, *, positive: bool = False, integer: bool = False) -> pd.Series:
    values = pd.to_numeric(raw.astype("string").str.strip().str.replace(",", "", regex=False), errors="coerce")
    finite = values.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    if values.isna().any() or not finite.all():
        raise ValueError(f"cash bhavcopy has invalid {name}")
    if positive and (values <= 0).any():
        raise ValueError(f"cash bhavcopy has non-positive {name}")
    if integer and (values % 1 != 0).any():
        raise ValueError(f"cash bhavcopy has non-integer {name}")
    return values


def _optional_cash_integer(raw: pd.Series, name: str) -> pd.Series:
    values = raw.astype("string").str.strip()
    available = values.notna() & values.ne("")
    result = pd.Series(pd.NA, index=raw.index, dtype="Int64")
    if available.any():
        parsed = _cash_numbers(values.loc[available], name, positive=True, integer=True)
        result.loc[available] = parsed.astype("Int64")
    return result


def parse_cm_bhavcopy_zip(content: bytes, trade_date: date) -> pd.DataFrame:
    """Parse a legacy or UDiFF NSE cash bhavcopy ZIP into normalized rows."""
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    raw = _cash_frame(content)
    is_udiff = set(UDIFF_CASH_COLUMNS).issubset(raw.columns)
    if is_udiff:
        _validate_cash_dates(raw, trade_date, ("TradDt", "BizDt"), ("%Y-%m-%d",))
        raw = raw[raw["SctySrs"].astype("string").str.strip() == "EQ"].copy()
        symbol = raw["TckrSymb"].astype("string").str.strip()
        series = raw["SctySrs"].astype("string").str.strip().str.upper()
        instrument_id = raw["FinInstrmId"].astype("string").str.strip()
        prices = {
            "open": _cash_numbers(raw["OpnPric"], "OpnPric", positive=True),
            "high": _cash_numbers(raw["HghPric"], "HghPric", positive=True),
            "low": _cash_numbers(raw["LwPric"], "LwPric", positive=True),
            "close": _cash_numbers(raw["ClsPric"], "ClsPric", positive=True),
            "prev_close": _cash_numbers(raw["PrvsClsgPric"], "PrvsClsgPric", positive=True),
        }
        volume = _cash_numbers(raw["TtlTradgVol"], "TtlTradgVol")
        turnover = _cash_numbers(raw["TtlTrfVal"], "TtlTrfVal")
        n_trades = _cash_numbers(raw["TtlNbOfTxsExctd"], "TtlNbOfTxsExctd")
        lot_size = _optional_cash_integer(raw["NewBrdLotQty"], "NewBrdLotQty")
        source_format = "udiff"
    else:
        _validate_cash_dates(raw, trade_date, ("TIMESTAMP",), ("%d-%b-%Y",))
        raw = raw[raw["SERIES"].astype("string").str.strip() == "EQ"].copy()
        symbol = raw["SYMBOL"].astype("string").str.strip()
        series = raw["SERIES"].astype("string").str.strip().str.upper()
        instrument_id = pd.Series(pd.NA, index=raw.index, dtype="string")
        prices = {
            "open": _cash_numbers(raw["OPEN"], "OPEN", positive=True),
            "high": _cash_numbers(raw["HIGH"], "HIGH", positive=True),
            "low": _cash_numbers(raw["LOW"], "LOW", positive=True),
            "close": _cash_numbers(raw["CLOSE"], "CLOSE", positive=True),
            "last": _cash_numbers(raw["LAST"], "LAST", positive=True),
            "prev_close": _cash_numbers(raw["PREVCLOSE"], "PREVCLOSE", positive=True),
        }
        volume = _cash_numbers(raw["TOTTRDQTY"], "TOTTRDQTY")
        turnover = _cash_numbers(raw["TOTTRDVAL"], "TOTTRDVAL")
        n_trades = _cash_numbers(raw["TOTALTRADES"], "TOTALTRADES")
        lot_size = pd.Series(pd.NA, index=raw.index, dtype="Int64")
        source_format = "legacy"

    if raw.empty:
        return pd.DataFrame(columns=CASH_BHAVCOPY_COLUMNS)
    isin = raw["ISIN"].astype("string").str.strip()
    if isin.isna().any() or isin.eq("").any():
        raise ValueError("cash bhavcopy contains a blank ISIN")
    if symbol.isna().any() or symbol.eq("").any():
        raise ValueError("cash bhavcopy contains a blank symbol")
    if is_udiff and (instrument_id.isna().any() or instrument_id.eq("").any()):
        raise ValueError("cash bhavcopy contains a blank instrument ID")
    duplicate_identity = is_udiff and instrument_id.duplicated().any()
    if symbol.duplicated().any() or isin.duplicated().any() or duplicate_identity:
        raise ValueError("cash bhavcopy contains duplicate instruments")

    source_hash = hashlib.sha256(content).hexdigest()
    result = pd.DataFrame(index=raw.index)
    result["symbol"] = symbol
    result["isin"] = isin
    result["instrument_id"] = instrument_id
    result["series"] = series
    result["date"] = pd.to_datetime(trade_date)
    for name, values in prices.items():
        result[name] = values
    if "last" not in result:
        result["last"] = pd.NA
    result["volume"] = volume
    result["turnover"] = turnover
    result["n_trades"] = n_trades
    result["lot_size"] = lot_size
    result["source"] = "nse_bhavcopy_cm"
    result["source_format"] = source_format
    result["source_hash"] = source_hash
    result["available_at"] = availability_timestamp(trade_date).isoformat()
    result["usable_from"] = pd.to_datetime(usable_from_date(trade_date))
    return result.loc[:, CASH_BHAVCOPY_COLUMNS].reset_index(drop=True)


def parse_sec_bhavdata_full(
    content: bytes,
    security_content: bytes,
    trade_date: date,
    selected_symbols: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Parse the official full cash file joined to the same-day MII security file."""
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    try:
        raw = pd.read_csv(io.BytesIO(content), dtype=str)
        security_raw = pd.read_csv(io.BytesIO(gzip.decompress(security_content)), dtype=str)
    except (OSError, EOFError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError("official NSE sec_bhavdata_full or MII file could not be parsed") from exc
    raw.columns = [str(column).lstrip("\ufeff").strip() for column in raw.columns]
    security_raw.columns = [str(column).lstrip("\ufeff").strip() for column in security_raw.columns]
    if not set(SEC_BHAVDATA_COLUMNS).issubset(raw.columns):
        raise ValueError("unsupported NSE sec_bhavdata_full schema")
    if not set(MII_SECURITY_COLUMNS).issubset(security_raw.columns):
        raise ValueError("unsupported NSE MII security schema")

    _validate_cash_dates(raw, trade_date, ("DATE1",), ("%d-%b-%Y",))
    raw = raw[raw["SERIES"].astype("string").str.strip().str.upper() == "EQ"].copy()
    raw["symbol"] = raw["SYMBOL"].astype("string").str.strip().str.upper()
    if selected_symbols is not None:
        required_symbols = tuple(str(symbol).strip().upper() for symbol in selected_symbols)
        raw = raw[raw["symbol"].isin(required_symbols)].copy()
        if sorted(raw["symbol"].tolist()) != sorted(required_symbols):
            missing = sorted(set(required_symbols).difference(raw["symbol"]))
            raise ValueError(f"sec_bhavdata_full is missing selected EQ symbols: {', '.join(missing)}")
    if raw["symbol"].isna().any() or raw["symbol"].eq("").any():
        raise ValueError("sec_bhavdata_full contains a blank symbol")
    if raw["symbol"].duplicated().any():
        raise ValueError("sec_bhavdata_full contains duplicate EQ symbols")

    security = security_raw[
        security_raw["SctySrs"].astype("string").str.strip().str.upper() == "EQ"
    ].copy()
    security["symbol"] = security["TckrSymb"].astype("string").str.strip().str.upper()
    if selected_symbols is not None:
        security = security[security["symbol"].isin(required_symbols)].copy()
    security["instrument_id"] = security["FinInstrmId"].astype("string").str.strip()
    security["isin"] = security["ISIN"].astype("string").str.strip().str.upper()
    if security["symbol"].isna().any() or security["symbol"].eq("").any():
        raise ValueError("MII security file contains a blank EQ symbol")
    if security["instrument_id"].isna().any() or security["instrument_id"].eq("").any():
        raise ValueError("MII security file contains a blank EQ instrument ID")
    if security["isin"].isna().any() or security["isin"].eq("").any():
        raise ValueError("MII security file contains a blank EQ ISIN")
    if security["symbol"].duplicated().any():
        raise ValueError("MII security file contains duplicate EQ symbols")
    if security["instrument_id"].duplicated().any():
        raise ValueError("MII security file contains non-unique EQ instrument ID")
    if selected_symbols is not None and security["isin"].duplicated().any():
        raise ValueError("MII security file contains non-unique selected EQ ISIN")

    joined = raw.merge(
        security[["symbol", "instrument_id", "isin"]],
        on="symbol",
        how="left",
        validate="one_to_one",
    )
    if joined["instrument_id"].isna().any() or joined["isin"].isna().any():
        raise ValueError("MII security file is missing an EQ identity for sec_bhavdata_full")
    result = pd.DataFrame({"symbol": joined["symbol"]})
    result["isin"] = joined["isin"]
    result["instrument_id"] = joined["instrument_id"]
    result["series"] = joined["SERIES"].astype("string").str.strip().str.upper()
    result["date"] = pd.to_datetime(trade_date)
    result["open"] = _cash_numbers(joined["OPEN_PRICE"], "OPEN_PRICE", positive=True)
    result["high"] = _cash_numbers(joined["HIGH_PRICE"], "HIGH_PRICE", positive=True)
    result["low"] = _cash_numbers(joined["LOW_PRICE"], "LOW_PRICE", positive=True)
    result["close"] = _cash_numbers(joined["CLOSE_PRICE"], "CLOSE_PRICE", positive=True)
    result["prev_close"] = _cash_numbers(joined["PREV_CLOSE"], "PREV_CLOSE", positive=True)
    result["last"] = _cash_numbers(joined["LAST_PRICE"], "LAST_PRICE", positive=True)
    result["volume"] = _cash_numbers(joined["TTL_TRD_QNTY"], "TTL_TRD_QNTY")
    result["turnover"] = _cash_numbers(joined["TURNOVER_LACS"], "TURNOVER_LACS") * 100000
    result["n_trades"] = _cash_numbers(joined["NO_OF_TRADES"], "NO_OF_TRADES")
    result["lot_size"] = (
        _optional_cash_integer(
            security.set_index("symbol").reindex(result["symbol"])["NewBrdLotQty"],
            "NewBrdLotQty",
        )
        if "NewBrdLotQty" in security.columns
        else pd.Series(pd.NA, index=result.index, dtype="Int64")
    )
    result["source"] = "nse_sec_bhavdata_full"
    result["source_format"] = "sec_bhavdata_full+mii"
    result["source_hash"] = hashlib.sha256(content).hexdigest()
    result["available_at"] = availability_timestamp(trade_date).isoformat()
    result["usable_from"] = pd.to_datetime(usable_from_date(trade_date))
    return result.loc[:, CASH_BHAVCOPY_COLUMNS].reset_index(drop=True)


def parse_mii_security(content: bytes, trade_date: date) -> pd.DataFrame:
    """Parse a same-day official MII gzip into identity-bearing EQ rows."""
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    if not content.startswith(b"\x1f\x8b"):
        raise ValueError("MII security response was not a gzip archive")
    try:
        raw = pd.read_csv(io.BytesIO(gzip.decompress(content)), dtype=str)
    except (OSError, EOFError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError("official NSE MII file could not be parsed") from exc
    raw.columns = [str(column).lstrip("\ufeff").strip() for column in raw.columns]
    if not set(MII_SECURITY_COLUMNS).issubset(raw.columns):
        raise ValueError("unsupported NSE MII security schema")
    raw = raw[raw["SctySrs"].astype("string").str.strip().str.upper() == "EQ"].copy()
    result = pd.DataFrame(
        {
            "symbol": raw["TckrSymb"].astype("string").str.strip().str.upper(),
            "instrument_id": raw["FinInstrmId"].astype("string").str.strip(),
            "series": raw["SctySrs"].astype("string").str.strip().str.upper(),
            "isin": raw["ISIN"].astype("string").str.strip().str.upper(),
            "date": pd.to_datetime(trade_date),
        }
    )
    for column in ("symbol", "instrument_id", "isin"):
        if result[column].isna().any() or result[column].eq("").any():
            raise ValueError(f"MII security file contains a blank {column}")
        if result[column].duplicated().any():
            raise ValueError(f"MII security file contains duplicate {column}")
    return result.reset_index(drop=True)


def parse_sec_bhavdata_full_schema(content: bytes, trade_date: date) -> pd.DataFrame:
    """Validate the price-only Full Bhavdata schema before an MII join."""
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    if content.lstrip().lower().startswith((b"<html", b"<!doctype", b"<head")):
        raise ValueError("Full Bhavdata response was HTML")
    try:
        raw = pd.read_csv(io.BytesIO(content), dtype=str)
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError("official NSE Full Bhavdata could not be parsed") from exc
    raw.columns = [str(column).lstrip("\ufeff").strip() for column in raw.columns]
    if not set(SEC_BHAVDATA_COLUMNS).issubset(raw.columns):
        raise ValueError("unsupported NSE Full Bhavdata schema")
    _validate_cash_dates(raw, trade_date, ("DATE1",), ("%d-%b-%Y",))
    raw = raw[raw["SERIES"].astype("string").str.strip().str.upper() == "EQ"].copy()
    raw["symbol"] = raw["SYMBOL"].astype("string").str.strip().str.upper()
    if raw["symbol"].isna().any() or raw["symbol"].eq("").any():
        raise ValueError("Full Bhavdata contains a blank EQ symbol")
    if raw["symbol"].duplicated().any():
        raise ValueError("Full Bhavdata contains duplicate EQ symbols")
    return raw.reset_index(drop=True)


parse_nse_cash_bhavcopy_zip = parse_cm_bhavcopy_zip
cash_bhavcopy_url_candidates = nse_cash_bhavcopy_url_candidates


def availability_timestamp(trade_date: date, release_time: time = time(18, 0)) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(trade_date, release_time), tz="Asia/Kolkata")


def usable_from_date(trade_date: date) -> date:
    return trade_date + timedelta(days=1)


def parse_cm_bhavcopy(content: bytes, trade_date: date) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(content), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip() == "EQ"].copy()
    df = pd.DataFrame()
    df["symbol"] = raw["SYMBOL"].str.strip()
    df["date"] = pd.to_datetime(trade_date)
    df["close"] = pd.to_numeric(raw["CLOSE_PRICE"], errors="coerce")
    df["prev_close"] = pd.to_numeric(raw["PREV_CLOSE"], errors="coerce")
    df["avg_price"] = pd.to_numeric(raw["AVG_PRICE"], errors="coerce")
    df["volume"] = pd.to_numeric(raw["TTL_TRD_QNTY"], errors="coerce")
    df["turnover_lacs"] = pd.to_numeric(raw["TURNOVER_LACS"], errors="coerce")
    df["n_trades"] = pd.to_numeric(raw["NO_OF_TRADES"], errors="coerce")
    df["delivery_qty"] = pd.to_numeric(raw["DELIV_QTY"], errors="coerce")
    df["delivery_pct"] = pd.to_numeric(raw["DELIV_PER"], errors="coerce")
    df["source"] = "nse_bhavcopy_cm"
    df["available_at"] = availability_timestamp(trade_date).isoformat()
    df["usable_from"] = pd.to_datetime(usable_from_date(trade_date))
    return df.reset_index(drop=True)


def parse_fo_bhavcopy_zip(content: bytes, trade_date: date) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), zf.namelist()[0])
        raw = pd.read_csv(zf.open(csv_name), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    raw = raw[(raw["FinInstrmTp"].str.strip() == "STF") & (raw["Sgmt"].str.strip() == "FO")].copy()
    if raw.empty:
        return pd.DataFrame()
    raw["_expiry"] = pd.to_datetime(raw["XpryDt"], errors="coerce")
    raw["_vol"] = pd.to_numeric(raw["TtlTradgVol"], errors="coerce").fillna(0)
    raw = raw[raw["_vol"] > 0].copy()
    raw = raw.loc[raw.groupby("TckrSymb")["_expiry"].idxmin()]
    df = pd.DataFrame()
    df["symbol"] = raw["TckrSymb"].str.strip()
    df["date"] = pd.to_datetime(trade_date)
    df["fut_oi"] = pd.to_numeric(raw["OpnIntrst"], errors="coerce")
    df["fut_oi_chg"] = pd.to_numeric(raw["ChngInOpnIntrst"], errors="coerce")
    df["fut_volume"] = raw["_vol"].values
    df["fut_close"] = pd.to_numeric(raw["ClsPric"], errors="coerce")
    df["underlying_price"] = pd.to_numeric(raw["UndrlygPric"], errors="coerce")
    df["source"] = "nse_bhavcopy_fo"
    df["available_at"] = availability_timestamp(trade_date).isoformat()
    df["usable_from"] = pd.to_datetime(usable_from_date(trade_date))
    return df.reset_index(drop=True)


def bhavcopy_path(segment: str, trade_date: date) -> Path:
    return data_root() / "bhavcopy" / segment / f"{segment}_{trade_date.isoformat()}.parquet"


def save_bhavcopy(df: pd.DataFrame, segment: str, trade_date: date) -> Path:
    out = bhavcopy_path(segment, trade_date)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out
