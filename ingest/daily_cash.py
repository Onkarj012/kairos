"""Bounded, evidence-preserving collection of an NSE Full Bhavdata session.

This module intentionally owns price retention only.  It does not admit security
identity, infer publication time, or use the older cash-history transport, whose
transport fallback would multiply an attempt budget.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import math
import os
import tempfile
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

import pandas as pd

from ingest.nse_bhavcopy import (
    nse_sec_bhavdata_full_url,
    parse_cm_bhavcopy_zip,
    parse_sec_bhavdata_full_schema,
)


PARSER_VERSION = "daily_cash.full_bhavdata.v1"
PARSER_VERSION_UDIFF = "daily_cash.udiff_zip.v1"
MAX_ATTEMPTS_PER_URL = 3
MIN_REQUEST_SPACING_SECONDS = 0.5
RETRY_WAITS_SECONDS = (0.0, 5.0, 30.0)
MAX_ELAPSED = timedelta(hours=8)
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
READ_CHUNK_BYTES = 1 << 16
# A short, plain User-Agent is required: NSE's edge stalls a request whose
# User-Agent carries a URL-style contact suffix, even for an otherwise valid
# official file.  The collection path must not manufacture a transport stall.
USER_AGENT = "Mozilla/5.0 (compatible; Kairos/0.1)"
EXCHANGE_TIMEZONE = ZoneInfo("Asia/Kolkata")
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
TRANSIENT_TRANSPORT_ERRORS = (URLError, TimeoutError, ConnectionError, OSError)
EXPECTED_VALIDATION_ERRORS = (ValueError, UnicodeError, csv.Error, pd.errors.ParserError, OverflowError)
TRANSPORT_STATE_FILE = "transport-state.json"
RESPONSE_METADATA_FILE = "response-metadata.json"

# Explicit official-host routes for the *same* Full Bhavdata filename.  A route
# owns its own durable attempt budget (its own request directory and manifest),
# so an exhausted host is never re-used to evade the three-attempt cap.
ARCHIVES_HOST = "archives.nseindia.com"
NSEARCHIVES_HOST = "nsearchives.nseindia.com"
OFFICIAL_CASH_HOSTS = frozenset({ARCHIVES_HOST, NSEARCHIVES_HOST})


def nsearchives_sec_bhavdata_full_url(trade_date: date) -> str:
    """The documented Full Bhavdata filename served by the archive file host."""
    return (
        "https://nsearchives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{trade_date:%d%m%Y}.csv"
    )


def nsearchives_udiff_cash_zip_url(trade_date: date) -> str:
    """The official same-day UDiFF cash ZIP (no LAST field; identity-bearing)."""
    return (
        "https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip"
    )


@dataclass(frozen=True)
class CashUrlRoute:
    name: str
    url_builder: Callable[[date], str]
    parser_kind: str
    parser_version: str


CASH_URL_ROUTES: dict[str, CashUrlRoute] = {
    "archives_full_bhavdata": CashUrlRoute(
        "archives_full_bhavdata", nse_sec_bhavdata_full_url, "full_bhavdata", PARSER_VERSION
    ),
    "nsearchives_full_bhavdata": CashUrlRoute(
        "nsearchives_full_bhavdata", nsearchives_sec_bhavdata_full_url, "full_bhavdata", PARSER_VERSION
    ),
    "nsearchives_udiff_zip": CashUrlRoute(
        "nsearchives_udiff_zip", nsearchives_udiff_cash_zip_url, "udiff_zip", PARSER_VERSION_UDIFF
    ),
}
DEFAULT_ROUTE = "archives_full_bhavdata"
ACCEPTED_CSV_MEDIA_TYPES = frozenset({
    "text/csv", "application/csv", "application/octet-stream", "text/plain", "application/zip",
})


class _TransientHttpError(Exception):
    pass


class Transport(Protocol):
    def __call__(self, url: str, connect_timeout: float, read_timeout: float) -> "HttpResponse": ...


Now = Callable[[], datetime]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class HttpResponse:
    """One HTTP exchange.  A transport supplied here must never retry."""

    status: int
    headers: Mapping[str, Any]
    body: bytes
    final_url: str
    retrieved_at: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    requested_date: date
    status: str
    attempts: int
    accepted_path: Path | None
    pointer_updated: bool
    reason: str | None = None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OPENER = build_opener(_NoRedirectHandler())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sleep(seconds: float) -> None:
    time_module.sleep(seconds)


def _set_read_timeout(response: Any, timeout: float) -> None:
    file_pointer = getattr(response, "fp", None)
    raw = getattr(file_pointer, "raw", None)
    connection = getattr(raw, "_sock", None)
    if connection is not None:
        connection.settimeout(timeout)


def _read_bounded(response: Any, read_timeout: float, deadline: datetime | None) -> bytes:
    """Read a response body without ever exceeding an absolute wall-clock deadline.

    With no deadline this is a plain ``response.read()`` bounded only by the
    socket read timeout.  With a deadline the body is read in chunks, the
    per-chunk socket inactivity timeout is clamped to the time that remains, and
    the loop stops (raising ``TimeoutError``) before a chunk can begin past the
    deadline.  Time already spent opening the connection therefore subtracts
    directly from the read budget.  No threads or helper processes are spawned,
    so a stalled peer can overrun the deadline by at most one clamped inactivity
    interval, never indefinitely.
    """
    if deadline is None:
        _set_read_timeout(response, read_timeout)
        return response.read()
    absolute = _as_utc(deadline)
    chunks: list[bytes] = []
    while True:
        remaining = (absolute - _utc_now()).total_seconds()
        if remaining <= 0:
            raise TimeoutError("absolute transport deadline exceeded while reading response")
        _set_read_timeout(response, min(read_timeout, remaining))
        chunk = response.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def single_http_transport(
    url: str,
    connect_timeout: float,
    read_timeout: float,
    deadline: datetime | None = None,
) -> HttpResponse:
    """Perform exactly one request and expose HTTP error bodies to the collector.

    ``deadline`` (when supplied by the shared-budget guard) is an absolute
    wall-clock instant: the connect phase is bounded by ``connect_timeout`` and
    the body read by both ``read_timeout`` and the remaining time to ``deadline``.
    """
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*", "Accept-Encoding": "identity"},
    )
    try:
        response_context = _OPENER.open(request, timeout=connect_timeout)
    except HTTPError as response:
        try:
            body = _read_bounded(response, read_timeout, deadline)
            return HttpResponse(
                status=response.code,
                headers=dict(response.headers.items()),
                body=body,
                final_url=response.geturl(),
                retrieved_at=_utc_now().isoformat(),
            )
        finally:
            response.close()
    with response_context as response:
        body = _read_bounded(response, read_timeout, deadline)
        return HttpResponse(
            status=int(response.status),
            headers=dict(response.headers.items()),
            body=body,
            final_url=response.geturl(),
            retrieved_at=_utc_now().isoformat(),
        )


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise TypeError("requested date must not include a time")
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_timestamp(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item())
    return str(value)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as output:
        temporary = Path(output.name)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object at {path}")
    return value


def _header(headers: Mapping[str, Any], name: str) -> Any:
    matches = [value for key, value in headers.items() if str(key).casefold() == name.casefold()]
    if len(matches) > 1:
        raise ValueError(f"duplicate HTTP {name} header")
    return matches[0] if matches else None


def _header_map(headers: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in headers.items()}


def _embedded_dates(body: bytes) -> tuple[str, ...]:
    """Best-effort metadata extraction; parser validation remains authoritative."""
    try:
        text = body.decode("utf-8-sig")
        rows = csv.DictReader(io.StringIO(text))
        if rows.fieldnames is None:
            return ()
        names = {name.strip().casefold(): name for name in rows.fieldnames if name is not None}
        field = next((names[name] for name in ("date1", "tradtdt", "bizdt", "timestamp") if name in names), None)
        if field is None:
            return ()
        found: set[str] = set()
        for row in rows:
            raw = str(row.get(field, "")).strip()
            for pattern in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
                try:
                    found.add(datetime.strptime(raw.upper(), pattern).date().isoformat())
                    break
                except ValueError:
                    continue
        return tuple(sorted(found))
    except (UnicodeDecodeError, csv.Error):
        return ()


def _content_type(headers: Mapping[str, Any]) -> str | None:
    value = _header(headers, "Content-Type")
    if value is None:
        return None
    return str(value).split(";", 1)[0].strip().casefold()


def _validate_last_modified(headers: Mapping[str, Any], requested: date, retrieved_at: str | None) -> str | None:
    value = _header(headers, "Last-Modified")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Last-Modified header is malformed")
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise ValueError("Last-Modified header is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Last-Modified header must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc).isoformat()
    exchange_date = parsed.astimezone(EXCHANGE_TIMEZONE).date()
    if exchange_date < requested:
        raise ValueError(f"stale Last-Modified date {exchange_date.isoformat()}")
    if retrieved_at is not None:
        retrieved = _parse_timestamp(retrieved_at)
        if parsed.astimezone(timezone.utc) > retrieved:
            raise ValueError("Last-Modified is later than actual retrieval time")
    return normalized


def _number(frame: pd.DataFrame, column: str, *, positive: bool = False) -> pd.Series:
    values = pd.to_numeric(frame[column].astype("string").str.strip().str.replace(",", "", regex=False), errors="coerce")
    finite = values.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    if values.isna().any() or not finite.all():
        raise ValueError(f"Full Bhavdata contains invalid {column}")
    if positive and (values <= 0).any():
        raise ValueError(f"Full Bhavdata contains non-positive {column}")
    if (values < 0).any():
        raise ValueError(f"Full Bhavdata contains negative {column}")
    return values


def _normalize_price_rows(body: bytes, requested: date) -> list[dict[str, Any]]:
    frame = parse_sec_bhavdata_full_schema(body, requested)
    if frame.empty:
        raise ValueError("Full Bhavdata contains no EQ rows")
    required = ("SYMBOL", "SERIES", "DATE1", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE", "PREV_CLOSE", "TTL_TRD_QNTY", "TURNOVER_LACS")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"Full Bhavdata is incomplete: missing {', '.join(missing)}")
    symbol = frame["SYMBOL"].astype("string").str.strip().str.upper()
    if symbol.isna().any() or symbol.eq("").any() or symbol.duplicated().any():
        raise ValueError("Full Bhavdata has blank or duplicate EQ symbols")
    open_price = _number(frame, "OPEN_PRICE", positive=True)
    high = _number(frame, "HIGH_PRICE", positive=True)
    low = _number(frame, "LOW_PRICE", positive=True)
    last = _number(frame, "LAST_PRICE", positive=True)
    close = _number(frame, "CLOSE_PRICE", positive=True)
    prev_close = _number(frame, "PREV_CLOSE", positive=True)
    volume = _number(frame, "TTL_TRD_QNTY")
    turnover_lacs = _number(frame, "TURNOVER_LACS")
    if (volume % 1 != 0).any():
        raise ValueError("Full Bhavdata contains non-integer TTL_TRD_QNTY")
    if (high < low).any() or (high < open_price).any() or (high < close).any() or (low > open_price).any() or (low > close).any():
        raise ValueError("Full Bhavdata has inconsistent OHLC bounds")
    rows: list[dict[str, Any]] = []
    for index in frame.index:
        traded_value = float(turnover_lacs.loc[index]) * 100000.0
        if not math.isfinite(traded_value):
            raise ValueError("Full Bhavdata contains non-finite traded_value")
        rows.append(
            {
                "symbol": str(symbol.loc[index]),
                "series": "EQ",
                "session_date": requested.isoformat(),
                "open": float(open_price.loc[index]),
                "high": float(high.loc[index]),
                "low": float(low.loc[index]),
                "last": float(last.loc[index]),
                "close": float(close.loc[index]),
                "prev_close": float(prev_close.loc[index]),
                "volume": int(volume.loc[index]),
                "traded_value": traded_value,
            }
        )
    return rows


def _normalize_udiff_rows(body: bytes, requested: date) -> list[dict[str, Any]]:
    """Retain the UDiFF cash ZIP's exact raw fields, leaving LAST explicitly absent."""
    frame = parse_cm_bhavcopy_zip(body, requested)
    if frame.empty:
        raise ValueError("UDiFF cash bhavcopy contains no EQ rows")
    required = ("symbol", "series", "open", "high", "low", "close", "prev_close", "volume", "turnover")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"UDiFF cash bhavcopy is incomplete: missing {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for index in frame.index:
        last_value = frame.at[index, "last"] if "last" in frame.columns else pd.NA
        last_available = bool(pd.notna(last_value))
        rows.append(
            {
                "symbol": str(frame.at[index, "symbol"]),
                "series": str(frame.at[index, "series"]),
                "session_date": requested.isoformat(),
                "open": float(frame.at[index, "open"]),
                "high": float(frame.at[index, "high"]),
                "low": float(frame.at[index, "low"]),
                "last": float(last_value) if last_available else None,
                "last_available": last_available,
                "close": float(frame.at[index, "close"]),
                "prev_close": float(frame.at[index, "prev_close"]),
                "volume": int(frame.at[index, "volume"]),
                "traded_value": float(frame.at[index, "turnover"]),
                "isin": None if pd.isna(frame.at[index, "isin"]) else str(frame.at[index, "isin"]),
                "instrument_id": None
                if pd.isna(frame.at[index, "instrument_id"])
                else str(frame.at[index, "instrument_id"]),
            }
        )
    return rows


def _normalize_for_route(route: CashUrlRoute, body: bytes, requested: date) -> list[dict[str, Any]]:
    if route.parser_kind == "udiff_zip":
        return _normalize_udiff_rows(body, requested)
    return _normalize_price_rows(body, requested)


def _validation(
    status: str,
    reason: str | None = None,
    *,
    row_count: int = 0,
    retryable: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "row_count": row_count}
    if reason is not None:
        result["reason"] = reason
    if retryable:
        result["retryable"] = True
    return result


def _lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _resolve_route(route: str) -> CashUrlRoute:
    try:
        return CASH_URL_ROUTES[route]
    except KeyError as exc:
        raise ValueError(f"unknown cash URL route: {route!r}") from exc


def _route_for_url(url: str) -> CashUrlRoute:
    """Recover the route a retained receipt was fetched through, from its URL."""
    if "/content/cm/BhavCopy_NSE_CM_" in url:
        return CASH_URL_ROUTES["nsearchives_udiff_zip"]
    if urlsplit(url).hostname == NSEARCHIVES_HOST:
        return CASH_URL_ROUTES["nsearchives_full_bhavdata"]
    return CASH_URL_ROUTES["archives_full_bhavdata"]


def route_url(route: CashUrlRoute, trade_date: date) -> str:
    """Build the route URL and refuse any non-allowlisted official host."""
    url = route.url_builder(trade_date)
    if urlsplit(url).hostname not in OFFICIAL_CASH_HOSTS:
        raise ValueError(f"cash URL route {route.name!r} is not on an allowlisted official host")
    return url


def request_directory_for_route(root: Path, requested: date, route_name: str) -> Path:
    """Route-aware request directory.

    The default route keeps the original ``requests/<date>`` layout so retained
    receipts stay byte-compatible; every other route gets its own subdirectory
    and therefore its own durable attempt budget.
    """
    base = Path(root) / "requests" / requested.isoformat()
    if route_name == DEFAULT_ROUTE:
        return base
    return base / route_name


def _attempt_directories(request_dir: Path) -> list[Path]:
    return sorted(request_dir.glob("attempt-*"), key=lambda path: path.name)


def _receipt_paths(request_dir: Path) -> list[Path]:
    return [path / "receipt.json" for path in _attempt_directories(request_dir) if (path / "receipt.json").exists()]


def _accepted_receipt(request_dir: Path) -> tuple[dict[str, Any], Path] | None:
    for path in _receipt_paths(request_dir):
        receipt = _read_json(path)
        result = receipt.get("validation_result")
        if isinstance(result, dict) and result.get("status") == "accepted":
            return receipt, path
    return None


def _latest_receipt(request_dir: Path) -> tuple[dict[str, Any], Path] | None:
    receipts = _receipt_paths(request_dir)
    if not receipts:
        return None
    path = receipts[-1]
    return _read_json(path), path


def _create_manifest(
    request_dir: Path,
    requested: date,
    url: str,
    now: Now,
    parser_version: str = PARSER_VERSION,
    *,
    connect_timeout_seconds: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: float = READ_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    manifest_path = request_dir / "manifest.json"
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        if manifest.get("requested_date") != requested.isoformat() or manifest.get("requested_url") != url:
            raise ValueError("resume manifest does not match requested date or URL")
        return manifest
    started = _timestamp(now())
    manifest = {
        "schema_version": 1,
        "requested_date": requested.isoformat(),
        "requested_url": url,
        "started_at": started,
        "max_elapsed_seconds": int(MAX_ELAPSED.total_seconds()),
        "max_attempts_per_url": MAX_ATTEMPTS_PER_URL,
        "min_request_spacing_seconds": MIN_REQUEST_SPACING_SECONDS,
        "retry_waits_seconds": list(RETRY_WAITS_SECONDS),
        "connect_timeout_seconds": connect_timeout_seconds,
        "read_timeout_seconds": read_timeout_seconds,
        "parser_version": parser_version,
    }
    try:
        _write_new(manifest_path, _json_bytes(manifest))
    except FileExistsError:
        manifest = _read_json(manifest_path)
    if manifest.get("requested_date") != requested.isoformat() or manifest.get("requested_url") != url:
        raise ValueError("resume manifest does not match requested date or URL")
    return manifest


def _reservation(request_dir: Path, attempt_number: int, url: str, now: Now) -> tuple[Path, dict[str, Any]]:
    attempt_dir = request_dir / f"attempt-{attempt_number:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    reservation = {
        "schema_version": 1,
        "attempt": attempt_number,
        "requested_url": url,
        "reserved_at": _timestamp(now()),
        "request_started_at": _timestamp(now()),
    }
    try:
        _write_new(attempt_dir / "reservation.json", _json_bytes(reservation))
    except BaseException:
        # The directory and reservation are intentionally left as durable evidence
        # if a write fails after creation; no attempt slot is silently reused.
        raise
    return attempt_dir, reservation


def _last_request_start(root: Path) -> datetime | None:
    starts: list[datetime] = []
    state_path = root / TRANSPORT_STATE_FILE
    if state_path.exists():
        state = _read_json(state_path)
        value = state.get("last_request_start")
        if isinstance(value, str):
            starts.append(_parse_timestamp(value))
    requests_root = root / "requests"
    if requests_root.exists():
        for reservation_path in sorted(requests_root.glob("**/attempt-*/reservation.json")):
            reservation = _read_json(reservation_path)
            value = reservation.get("request_started_at")
            if isinstance(value, str):
                starts.append(_parse_timestamp(value))
    return max(starts) if starts else None


def _elapsed(manifest: Mapping[str, Any], now: datetime) -> float:
    started = _parse_timestamp(str(manifest["started_at"]))
    return (_as_utc(now) - started).total_seconds()


def _wait_before_attempt(root: Path, attempt: int, manifest: Mapping[str, Any], now: Now, sleep: Sleep) -> str | None:
    remaining = MAX_ELAPSED.total_seconds() - _elapsed(manifest, now())
    if remaining <= 0:
        return "eight-hour elapsed budget exhausted before next request"
    last_start = _last_request_start(root)
    spacing = 0.0
    if last_start is not None:
        spacing = max(0.0, MIN_REQUEST_SPACING_SECONDS - (_as_utc(now()) - last_start).total_seconds())
    required = max(RETRY_WAITS_SECONDS[attempt - 1], spacing)
    if required >= remaining:
        return "eight-hour elapsed budget exhausted before next request"
    if required > 0:
        sleep(required)
    if _elapsed(manifest, now()) >= MAX_ELAPSED.total_seconds():
        return "eight-hour elapsed budget exhausted before next request"
    return None


def _persist_request_start(root: Path, reservation: Mapping[str, Any]) -> None:
    state = {
        "schema_version": 1,
        "last_request_start": reservation["request_started_at"],
        "requested_url": reservation["requested_url"],
        "attempt": reservation["attempt"],
    }
    _write_atomic(root / TRANSPORT_STATE_FILE, _json_bytes(state))


def _response_metadata(response: HttpResponse | None) -> tuple[str | None, int | None, str | None, dict[str, Any]]:
    if response is None:
        return None, None, None, {}
    digest = hashlib.sha256(response.body).hexdigest()
    return response.final_url, response.status, digest, _header_map(response.headers)


def _persist_response_evidence(root: Path, attempt_dir: Path, response: HttpResponse) -> None:
    final_url, status, digest, headers = _response_metadata(response)
    body_path = attempt_dir / "response.bin"
    metadata_path = attempt_dir / RESPONSE_METADATA_FILE
    if body_path.exists():
        if body_path.read_bytes() != response.body:
            raise ValueError("attempt response evidence changed before receipt")
    else:
        _write_new(body_path, response.body)
    metadata = {
        "schema_version": 1,
        "final_url": final_url,
        "http_status": status,
        "response_headers": headers,
        "retrieved_at": response.retrieved_at,
        "body_byte_count": len(response.body),
        "body_sha256": digest,
    }
    if metadata_path.exists():
        if metadata_path.read_bytes() != _json_bytes(metadata):
            raise ValueError("attempt response metadata changed before receipt")
    else:
        _write_new(metadata_path, _json_bytes(metadata))


def _write_attempt_receipt(
    attempt_dir: Path,
    *,
    root: Path,
    requested: date,
    reservation: Mapping[str, Any],
    response: HttpResponse | None,
    finished_at: str,
    error: str | None,
    rows: list[dict[str, Any]] | None,
    validation_result: Mapping[str, Any],
    embedded_dates: tuple[str, ...],
    last_modified: str | None,
    parser_version: str = PARSER_VERSION,
    source_kind: str = "official_nse_full_bhavdata_price_only",
) -> Path:
    body_path: str | None = None
    body_sha256: str | None = None
    byte_count: int | None = None
    final_url: str | None = None
    status: int | None = None
    headers: dict[str, Any] = {}
    retrieved_at: str | None = None
    if response is not None:
        _persist_response_evidence(root, attempt_dir, response)
        body_path = str((attempt_dir / "response.bin").relative_to(root))
        body_sha256 = hashlib.sha256(response.body).hexdigest()
        byte_count = len(response.body)
        final_url, status, _, headers = _response_metadata(response)
        retrieved_at = response.retrieved_at or finished_at
    normalized_path: str | None = None
    normalized_sha256: str | None = None
    if rows is not None:
        normalized = {
            "schema_version": 1,
            "session_date": requested.isoformat(),
            "requested_date": requested.isoformat(),
            "source": source_kind,
            "source_url": final_url,
            "source_sha256": body_sha256,
            "parser_version": parser_version,
            "rows": rows,
        }
        normalized_bytes = _json_bytes(normalized)
        normalized_path = str((attempt_dir / "normalized.json").relative_to(root))
        _write_new(attempt_dir / "normalized.json", normalized_bytes)
        normalized_sha256 = hashlib.sha256(normalized_bytes).hexdigest()
    receipt = {
        "schema_version": 1,
        "requested_date": requested.isoformat(),
        "embedded_dates": list(embedded_dates),
        "embedded_date": embedded_dates[0] if len(embedded_dates) == 1 else None,
        "requested_url": reservation["requested_url"],
        "final_url": final_url,
        "http_status": status,
        "response_headers": headers,
        "reserved_at": reservation["reserved_at"],
        "request_started_at": reservation["request_started_at"],
        "response_received_at": finished_at,
        "retrieved_at": retrieved_at,
        "last_modified": last_modified,
        "body_path": body_path,
        "body_byte_count": byte_count,
        "body_sha256": body_sha256,
        "normalized_path": normalized_path,
        "normalized_sha256": normalized_sha256,
        "parser_version": parser_version,
        "validation_result": dict(validation_result),
    }
    if error is not None:
        receipt["transport_error"] = error
    receipt_path = attempt_dir / "receipt.json"
    _write_new(receipt_path, _json_bytes(receipt))
    return receipt_path


def _rooted_reference(root: Path, value: Any, label: str) -> Path:
    """Resolve a receipt file reference against the evidence root.

    Older named-route receipts stored their relative paths against
    ``<root>/requests`` while the default route stored them against ``<root>``.
    Both layouts are accepted so retained bytes stay reusable; the first
    candidate that exists wins.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"accepted receipt corruption: missing {label} reference")
    candidates: list[Path] = []
    for base in (Path(root).resolve(), (Path(root) / "requests").resolve()):
        candidate = (base / value).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        candidates.append(candidate)
    if not candidates:
        raise ValueError(f"accepted receipt corruption: {label} reference escapes output root")
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def _verify_accepted_receipt(root: Path, requested: date, url: str, receipt: Mapping[str, Any]) -> Path:
    requested_iso = requested.isoformat()
    if receipt.get("requested_date") != requested_iso:
        raise ValueError("accepted receipt corruption: requested date reference mismatch")
    if receipt.get("requested_url") != url or receipt.get("final_url") != url:
        raise ValueError("accepted receipt corruption: source URL reference mismatch")
    embedded_dates = receipt.get("embedded_dates")
    if embedded_dates:
        if receipt.get("embedded_date") != requested_iso or embedded_dates != [requested_iso]:
            raise ValueError("accepted receipt corruption: embedded session reference mismatch")
    body_path = _rooted_reference(root, receipt.get("body_path"), "original body")
    normalized_path = _rooted_reference(root, receipt.get("normalized_path"), "normalized file")
    if not body_path.is_file():
        raise ValueError("accepted receipt corruption: original body is missing")
    try:
        body = body_path.read_bytes()
    except OSError as exc:
        raise ValueError("accepted receipt corruption: original body is unreadable") from exc
    if receipt.get("body_byte_count") != len(body):
        raise ValueError("accepted receipt corruption: original body byte count mismatch")
    body_sha256 = hashlib.sha256(body).hexdigest()
    if receipt.get("body_sha256") != body_sha256:
        raise ValueError("accepted receipt corruption: original body SHA-256 mismatch")
    # Re-run the route parser so a ZIP (whose embedded date is inside the
    # archive) is date-validated rather than trusted from the status string.
    try:
        reparsed_rows = _normalize_for_route(_route_for_url(url), body, requested)
    except EXPECTED_VALIDATION_ERRORS as exc:
        raise ValueError("accepted receipt corruption: raw body fails re-validation") from exc
    if not normalized_path.is_file():
        raise ValueError("accepted receipt corruption: normalized file is missing")
    try:
        normalized_bytes = normalized_path.read_bytes()
    except OSError as exc:
        raise ValueError("accepted receipt corruption: normalized file is unreadable") from exc
    if receipt.get("normalized_sha256") != hashlib.sha256(normalized_bytes).hexdigest():
        raise ValueError("accepted receipt corruption: normalized file SHA-256 mismatch")
    try:
        normalized = _read_json(normalized_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("accepted receipt corruption: normalized file is unreadable") from exc
    if normalized.get("session_date") != requested_iso or normalized.get("requested_date") != requested_iso:
        raise ValueError("accepted receipt corruption: normalized session reference mismatch")
    if normalized.get("source_url") != receipt.get("final_url") or normalized.get("source_sha256") != body_sha256:
        raise ValueError("accepted receipt corruption: normalized source reference mismatch")
    rows = normalized.get("rows")
    validation_result = receipt.get("validation_result")
    if not isinstance(validation_result, dict):
        raise ValueError("accepted receipt corruption: validation result is missing")
    expected_count = validation_result.get("row_count")
    if not isinstance(rows, list) or not rows:
        raise ValueError("accepted receipt corruption: normalized rows are missing")
    if expected_count != len(rows):
        raise ValueError("accepted receipt corruption: normalized row count mismatch")
    if len(reparsed_rows) != len(rows):
        raise ValueError("accepted receipt corruption: raw body row count mismatch")
    for row in rows:
        if not isinstance(row, dict) or row.get("session_date") != requested_iso:
            raise ValueError("accepted receipt corruption: normalized row session reference mismatch")
    return normalized_path


def _accepted_receipts(request_dir: Path) -> list[tuple[dict[str, Any], Path]]:
    found: list[tuple[dict[str, Any], Path]] = []
    for path in _receipt_paths(request_dir):
        receipt = _read_json(path)
        result = receipt.get("validation_result")
        if isinstance(result, dict) and result.get("status") == "accepted":
            found.append((receipt, path))
    return found


def _request_dirs_for_date(root: Path, requested: date) -> list[Path]:
    """Every route's request directory for one session (default plus named routes)."""
    date_dir = Path(root) / "requests" / requested.isoformat()
    if not date_dir.is_dir():
        return []
    dirs: list[Path] = []
    if any(date_dir.glob("attempt-*")):
        dirs.append(date_dir)
    for child in sorted(date_dir.iterdir()):
        if child.is_dir() and any(child.glob("attempt-*")):
            dirs.append(child)
    return dirs


def verify_retained_session(
    root: str | Path,
    requested: date | str,
    *,
    route: str | None = None,
) -> dict[str, Any] | None:
    """Re-validate an accepted session artifact; never trust a status string.

    The accepted receipt's original body and normalized file are re-read and
    re-hashed, and the requested/embedded date and row count are re-checked.  A
    missing or corrupt artifact returns ``None`` so the caller treats the
    session as unrecovered rather than reusing unverified bytes.
    """
    requested_date = _as_date(requested)
    root_path = Path(root).resolve()
    if route is not None:
        candidates = [request_directory_for_route(root_path, requested_date, route)]
    else:
        candidates = _request_dirs_for_date(root_path, requested_date)
    for request_dir in candidates:
        if not request_dir.is_dir():
            continue
        try:
            accepted = _accepted_receipts(request_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for receipt, receipt_path in accepted:
            url = receipt.get("requested_url")
            if not isinstance(url, str) or urlsplit(url).hostname not in OFFICIAL_CASH_HOSTS:
                continue
            try:
                normalized_path = _verify_accepted_receipt(root_path, requested_date, url, receipt)
            except ValueError:
                continue
            return {
                "session_date": requested_date.isoformat(),
                "root": str(root_path),
                "receipt_path": str(receipt_path.relative_to(root_path)),
                "normalized_path": str(normalized_path.relative_to(root_path)),
                "source_url": url,
                "body_sha256": receipt.get("body_sha256"),
                "row_count": (receipt.get("validation_result") or {}).get("row_count"),
            }
    return None


def tally_receipts(root: str | Path) -> dict[str, int]:
    """Count retained attempt receipts by outcome for an evidence root.

    ``rejected_responses`` counts attempts that received an HTTP response and
    failed validation; ``failures`` counts attempts with no usable response
    (transport errors).  A status string alone is never trusted, so the totals
    come from the retained receipts themselves.
    """
    root_path = Path(root)
    tally = {"attempts": 0, "accepted": 0, "rejected_responses": 0, "failures": 0}
    requests_root = root_path / "requests"
    if not requests_root.is_dir():
        return tally
    for path in sorted(requests_root.glob("**/receipt.json")):
        try:
            receipt = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"attempt receipt is unreadable: {path}") from exc
        tally["attempts"] += 1
        result = receipt.get("validation_result")
        status = result.get("status") if isinstance(result, Mapping) else None
        if status == "accepted":
            tally["accepted"] += 1
        elif status == "rejected" and receipt.get("http_status") is not None:
            tally["rejected_responses"] += 1
        else:
            tally["failures"] += 1
    return tally


def _valid_session_for_pointer(path: Path, requested: date) -> bool:
    if not path.exists():
        return False
    try:
        session = _read_json(path)
        return session.get("session_date") == requested.isoformat() and isinstance(session.get("rows"), list) and bool(session["rows"])
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _update_current_pointer(root: Path, requested: date, session_path: Path, now: Now) -> bool:
    if not _valid_session_for_pointer(session_path, requested):
        return False
    pointer_path = root / "current.json"
    lock_handle = _lock(root / "current.lock")
    try:
        if pointer_path.exists():
            current = _read_json(pointer_path)
            current_date = date.fromisoformat(str(current["session_date"]))
            if current_date >= requested:
                return False
        pointer = {
            "schema_version": 1,
            "session_date": requested.isoformat(),
            "session_path": str(session_path.relative_to(root)),
            "session_sha256": hashlib.sha256(session_path.read_bytes()).hexdigest(),
            "pointer_updated_at": _timestamp(now()),
        }
        _write_atomic(pointer_path, _json_bytes(pointer))
        return True
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def collect_daily_cash(
    requested_date: date | str,
    output_root: Path,
    *,
    transport: Transport = single_http_transport,
    now: Now = _utc_now,
    sleep: Sleep = _sleep,
    resume: bool = False,
    route: str = DEFAULT_ROUTE,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = READ_TIMEOUT_SECONDS,
) -> CollectionResult:
    """Collect one session; transient failures retry, validation failures need resume=True.

    ``route`` selects an allowlisted official source URL (the same Full
    Bhavdata filename on an official host, or the official UDiFF cash ZIP).
    Each route keeps its own request directory, manifest and three-attempt
    budget; switching hosts never resets an exhausted budget.

    ``connect_timeout``/``read_timeout`` are the effective transport settings;
    the caller's values are forwarded unchanged to the transport (and recorded in
    the run manifest), so a report never claims a timeout the socket never saw.
    """
    requested = _as_date(requested_date)
    root = Path(output_root)
    resolved_route = _resolve_route(route)
    url = route_url(resolved_route, requested)
    request_dir = request_directory_for_route(root, requested, resolved_route.name)
    root.mkdir(parents=True, exist_ok=True)
    lock_handle = _lock(root / "collector.lock")
    try:
        manifest = _create_manifest(
            request_dir,
            requested,
            url,
            now,
            resolved_route.parser_version,
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )
        accepted = _accepted_receipt(request_dir)
        if accepted is not None:
            receipt, _receipt_path = accepted
            try:
                normalized_path = _verify_accepted_receipt(root, requested, url, receipt)
            except ValueError as exc:
                return CollectionResult(requested, "rejected", len(_attempt_directories(request_dir)), None, False, str(exc))
            pointer_updated = _update_current_pointer(root, requested, normalized_path, now)
            return CollectionResult(requested, "accepted", len(_attempt_directories(request_dir)), normalized_path, pointer_updated, "resumed accepted session")

        attempts = _attempt_directories(request_dir)
        latest = _latest_receipt(request_dir)
        if latest is not None and not resume:
            latest_receipt, _latest_path = latest
            latest_result = latest_receipt.get("validation_result")
            if isinstance(latest_result, dict) and latest_result.get("status") == "rejected" and not latest_result.get("retryable", False):
                return CollectionResult(
                    requested,
                    "rejected",
                    len(attempts),
                    None,
                    False,
                    f"terminal rejection requires resume=True: {latest_result.get('reason', 'validation failed')}",
                )
        if len(attempts) >= MAX_ATTEMPTS_PER_URL:
            return CollectionResult(requested, "exhausted", len(attempts), None, False, "maximum three attempts for URL already reserved")
        while True:
            attempts = _attempt_directories(request_dir)
            if len(attempts) >= MAX_ATTEMPTS_PER_URL:
                return CollectionResult(requested, "exhausted", len(attempts), None, False, "maximum three attempts for URL already reserved")
            attempt_number = len(attempts) + 1
            wait_reason = _wait_before_attempt(root, attempt_number, manifest, now, sleep)
            if wait_reason is not None:
                return CollectionResult(requested, "exhausted", len(attempts), None, False, wait_reason)
            if _elapsed(manifest, now()) >= MAX_ELAPSED.total_seconds():
                return CollectionResult(requested, "exhausted", len(attempts), None, False, "eight-hour elapsed budget exhausted before next request")
            attempt_dir, reservation = _reservation(request_dir, attempt_number, url, now)
            _persist_request_start(root, reservation)
            response: HttpResponse | None = None
            transport_error: str | None = None
            rows: list[dict[str, Any]] | None = None
            embedded_dates: tuple[str, ...] = ()
            last_modified: str | None = None
            validation_result: dict[str, Any]
            try:
                response = transport(url, connect_timeout, read_timeout)
                if not isinstance(response, HttpResponse):
                    raise TypeError("transport must return HttpResponse")
                _persist_response_evidence(root, attempt_dir, response)
                embedded_dates = _embedded_dates(response.body)
                if response.status in TRANSIENT_HTTP_STATUSES:
                    raise _TransientHttpError(f"transient HTTP status {response.status}")
                if response.status != 200:
                    raise ValueError(f"HTTP status {response.status}")
                if response.final_url != url:
                    raise ValueError("final URL did not exactly match requested URL")
                media_type = _content_type(response.headers)
                if media_type not in ACCEPTED_CSV_MEDIA_TYPES:
                    raise ValueError("response Content-Type is not an accepted cash media type")
                if not response.body.strip() or response.body.lstrip().lower().startswith((b"<html", b"<!doctype", b"<head", b"<form")):
                    raise ValueError("response body is empty or HTML")
                retrieval_timestamp = response.retrieved_at or _timestamp(now())
                last_modified = _validate_last_modified(response.headers, requested, retrieval_timestamp)
                rows = _normalize_for_route(resolved_route, response.body, requested)
                validation_result = _validation("accepted", row_count=len(rows))
            except (_TransientHttpError, *TRANSIENT_TRANSPORT_ERRORS) as exc:
                transport_error = str(exc) or exc.__class__.__name__
                validation_result = _validation("rejected", transport_error, retryable=True)
            except EXPECTED_VALIDATION_ERRORS as exc:
                transport_error = str(exc) or exc.__class__.__name__
                validation_result = _validation("rejected", transport_error)
            finished_at = _timestamp(now())
            _write_attempt_receipt(
                attempt_dir,
                root=root,
                requested=requested,
                reservation=reservation,
                response=response,
                finished_at=finished_at,
                error=transport_error,
                rows=rows,
                validation_result=validation_result,
                embedded_dates=embedded_dates,
                last_modified=last_modified,
                parser_version=resolved_route.parser_version,
                source_kind=(
                    "official_nse_udiff_cash_zip_price_only"
                    if resolved_route.parser_kind == "udiff_zip"
                    else "official_nse_full_bhavdata_price_only"
                ),
            )
            if validation_result["status"] == "accepted":
                normalized_path = attempt_dir / "normalized.json"
                pointer_updated = _update_current_pointer(root, requested, normalized_path, now)
                return CollectionResult(requested, "accepted", attempt_number, normalized_path, pointer_updated)
            if not validation_result.get("retryable", False):
                return CollectionResult(requested, "rejected", attempt_number, None, False, str(validation_result.get("reason")))
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


__all__ = [
    "CASH_URL_ROUTES",
    "CollectionResult",
    "DEFAULT_ROUTE",
    "HttpResponse",
    "OFFICIAL_CASH_HOSTS",
    "PARSER_VERSION",
    "PARSER_VERSION_UDIFF",
    "collect_daily_cash",
    "nsearchives_sec_bhavdata_full_url",
    "nsearchives_udiff_cash_zip_url",
    "request_directory_for_route",
    "route_url",
    "single_http_transport",
    "tally_receipts",
    "verify_retained_session",
]
