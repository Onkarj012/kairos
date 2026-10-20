"""Mark each traded symbol as equity, fund or unknown from its ISIN, then its company names."""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

from ingest.daily_panel import replace_directory
from ingest.nse_reference import read_reference

EQUITY, FUND, UNKNOWN = "equity", "fund", "unknown"
TRADED_SERIES = ("EQ", "BE", "BZ")
REFERENCE_FILES = ("EQUITY_L.csv", "namechange.csv", "symbolchange.csv")
FUNDS_DIR = "daily-runner/funds-v1"
PANEL_COLUMNS = ["session_date", "symbol", "series", "isin", "traded_value"]
CLASSIFICATION_COLUMNS = ["symbol", "class", "basis", "isin", "name"]
FUND_NAME_RE = re.compile(r"\b(ETF|MF|MUTUAL FUND|FUND|BEES|FTF|FMP|SCHEME|NAV)\b", re.IGNORECASE)
FUND_SYMBOL_RE = re.compile(r"(ETF|BEES)$|IETF")
COMPANY_RE = re.compile(r"\b(LIMITED|LTD)\b", re.IGNORECASE)
OVERNIGHT_LEAKED_ETFS = (
    "BANKBEES",
    "GOLDBEES",
    "GOLDIETF",
    "HDFCGOLD",
    "HDFCSILVER",
    "LIQUIDBEES",
    "LIQUIDCASE",
    "NIFTYBEES",
    "SBISILVER",
    "SETFGOLD",
    "SILVER",
    "SILVERBEES",
    "SILVERIETF",
    "TATAGOLD",
    "TATSILV",
)


def read_csv(body: bytes) -> pd.DataFrame:
    """Parse a reference CSV, stripping padding from headers and values."""
    frame = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False)
    frame.columns = [column.strip() for column in frame.columns]
    return frame.apply(lambda column: column.str.strip())


def _isin_verdict(panel_isin: str, listed_isin: str) -> tuple[str, str, str] | None:
    """Class, basis and ISIN from the UDiFF and EQUITY_L ISINs; None when neither has one."""
    known = [(source, isin) for source, isin in (("udiff", panel_isin), ("equity_l", listed_isin)) if isin]
    if not known:
        return None
    source, isin = known[0]
    classes = {FUND if value.startswith("INF") else EQUITY for _, value in known}
    if len(classes) > 1:
        return UNKNOWN, "isin_conflict", isin
    cls = classes.pop()
    prefix = "isin_inf_" if cls == FUND else "isin_"
    return cls, f"{prefix}{source}", isin


def _name_verdict(symbol: str, names: list[str]) -> tuple[str, str]:
    """Class and basis from company names, then from the symbol text."""
    if any(FUND_NAME_RE.search(name) for name in names):
        return FUND, "name_fund_marker"
    if FUND_SYMBOL_RE.search(symbol):
        return FUND, "symbol_fund_marker"
    if any(COMPANY_RE.search(name) for name in names):
        return EQUITY, "name_company"
    return UNKNOWN, "no_isin_no_name"


def classify_symbols(panel: pd.DataFrame, equity_l: bytes, namechange: bytes, symbolchange: bytes) -> pd.DataFrame:
    """One row per traded symbol with its class, basis, ISIN used and first company name."""
    traded = panel[panel["series"].isin(TRADED_SERIES)]
    listing = read_csv(equity_l)
    renames = read_csv(namechange)
    symbol_changes = read_csv(symbolchange)

    isins = traded["isin"].fillna("").str.strip()
    panel_isins = isins[isins != ""].groupby(traded["symbol"]).min().to_dict()
    unique_listing = listing.drop_duplicates("SYMBOL")
    listed_isins = dict(zip(unique_listing["SYMBOL"], unique_listing["ISIN NUMBER"]))

    names: defaultdict[str, list[str]] = defaultdict(list)
    for symbol, name in zip(listing["SYMBOL"], listing["NAME OF COMPANY"]):
        names[symbol].append(name)
    for symbol, old, new in zip(renames["SYMBOL"], renames["OLD NAME OF THE COMPANY"], renames["NEW NAME OF THE COMPANY"]):
        names[symbol].extend((old, new))
    for name, old, new in zip(symbol_changes["NAME OF THE COMPANY"], symbol_changes["OLD SYMBOL"], symbol_changes["NEW SYMBOL"]):
        names[old].append(name)
        names[new].append(name)

    rows = []
    for symbol in sorted(traded["symbol"].unique()):
        company_names = [name for name in names.get(symbol, []) if name]
        verdict = _isin_verdict(panel_isins.get(symbol, ""), listed_isins.get(symbol, ""))
        if verdict is None:
            cls, basis = _name_verdict(symbol, company_names)
            isin = ""
        else:
            cls, basis, isin = verdict
        rows.append(
            {
                "symbol": symbol,
                "class": cls,
                "basis": basis,
                "isin": isin,
                "name": company_names[0] if company_names else "",
            }
        )
    return pd.DataFrame(rows, columns=CLASSIFICATION_COLUMNS)


def review_frame(panel: pd.DataFrame, classification: pd.DataFrame) -> pd.DataFrame:
    """Unknown symbols with their session span and median traded value, largest first."""
    unknown = classification[classification["class"] == UNKNOWN]
    traded = panel[panel["series"].isin(TRADED_SERIES) & panel["symbol"].isin(unknown["symbol"])]
    stats = (
        traded.groupby("symbol")
        .agg(
            first_session=("session_date", "min"),
            last_session=("session_date", "max"),
            sessions=("session_date", "nunique"),
            median_traded_value=("traded_value", "median"),
        )
        .reset_index()
    )
    for column in ("first_session", "last_session"):
        stats[column] = stats[column].map(lambda day: day.isoformat())
    review = unknown[["symbol", "basis", "name"]].merge(stats, on="symbol")
    return review.sort_values(["median_traded_value", "symbol"], ascending=[False, True]).reset_index(drop=True)


def _counts(values: pd.Series) -> dict[str, int]:
    """Row count per distinct value, keys sorted."""
    return {str(key): int(count) for key, count in values.value_counts().sort_index().items()}


def _write_dir(out_dir: Path, files: dict[str, str]) -> None:
    """Write files into a staging dir beside out_dir, then swap it into place."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        for name, text in files.items():
            (staging / name).write_text(text, encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def write_fund_classification(panel_path: Path, reference_root: Path | None, out_dir: Path) -> dict[str, object]:
    """Write classification.csv, review.csv and summary.json to out_dir and return the summary."""
    bodies = {name: read_reference(name, reference_root) for name in REFERENCE_FILES}
    panel = pd.read_parquet(panel_path, columns=PANEL_COLUMNS)
    classification = classify_symbols(panel, bodies["EQUITY_L.csv"], bodies["namechange.csv"], bodies["symbolchange.csv"])
    review = review_frame(panel, classification)
    class_by_symbol = dict(zip(classification["symbol"], classification["class"]))
    summary: dict[str, object] = {
        "counts_by_class": _counts(classification["class"]),
        "counts_by_basis": _counts(classification["basis"]),
        "reference_sha256": {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()},
        "panel_sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
        "overnight_leaked_etfs": {symbol: class_by_symbol.get(symbol, "absent") for symbol in OVERNIGHT_LEAKED_ETFS},
    }
    _write_dir(
        out_dir,
        {
            "classification.csv": classification.to_csv(index=False),
            "review.csv": review.to_csv(index=False),
            "summary.json": json.dumps(summary, indent=2, sort_keys=True) + "\n",
        },
    )
    return summary
