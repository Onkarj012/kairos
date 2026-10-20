#!/usr/bin/env python3
"""Bounded qualification and source acquisition for NSE corporate actions."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import errno
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sys
import stat
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

COHORT: tuple[str, ...] = (
    "ASIANPAINT", "BAJAJ-AUTO", "BHARTIARTL", "CIPLA", "COALINDIA",
    "GRASIM", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INFY", "ITC",
    "JSWSTEEL", "LT", "M&M", "MARUTI", "NTPC", "ONGC", "SBIN",
    "SUNPHARMA", "TCS", "TECHM", "TITAN", "ULTRACEMCO",
)

START_DATE = date(2019, 9, 30)
END_DATE = date(2025, 12, 2)
DEFAULT_OUTPUT_ROOT = Path("derived_data/phase02/raw/nse_corporate_actions/public-risk23-v3-probe/live-20260901a")
DEFAULT_REPORT_ROOT: Path | None = None
API_BASE = "https://www.nseindia.com/api/corporates-corporateActions"
ALLOWED_HOST = "www.nseindia.com"
ALLOWED_PATH = "/api/corporates-corporateActions"
QUERY_ORDER = ("index", "symbol", "series", "from_date", "to_date", "page", "limit", "offset")
QUALIFICATION_QUERY_KEYS = frozenset(QUERY_ORDER)
PAGINATION_KEYS = ("page", "limit", "offset")
PROBE_SYMBOLS = ("SBIN", "BAJAJ-AUTO")
PROBE_WINDOWS: tuple[tuple[str, date, date], ...] = (
    ("early", date(2019, 9, 30), date(2019, 10, 31)),
    ("middle", date(2022, 1, 1), date(2022, 1, 31)),
    ("late", date(2025, 11, 1), END_DATE),
)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S")
_DATE_FIELDS = ("exDate", "recDate", "bcStartDate", "bcEndDate")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_name(value: str) -> str:
    return _SAFE_NAME.sub("_", value).strip("._") or "item"


def _canonical_symbol_value(symbol: str) -> str:
    if symbol == "M&M":
        return "M%26M"
    return symbol


def _file_entry(name: str, payload: bytes, media_type: str) -> dict[str, Any]:
    return {"path": name, "media_type": media_type, "byte_count": len(payload), "sha256": _sha256(payload)}


def _headers(response: Any) -> list[list[str]]:
    return [[str(key), str(value)] for key, value in response.headers.items()]


def _parse_day(value: object) -> date | None:
    if value in (None, "", "-"):
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _validate_url(url: str, *, allow_pagination_probe: bool = False) -> str:
    """Validate one canonical request or response URL before trusting it."""
    if not isinstance(url, str) or url != url.strip() or not url.isascii():
        raise ValueError(f"URL is not canonical ASCII HTTPS: {url}")
    parts = urlsplit(url)
    if (
        parts.scheme != "https" or parts.netloc != ALLOWED_HOST or parts.hostname != ALLOWED_HOST
        or parts.username is not None or parts.password is not None or parts.port is not None
        or parts.path != ALLOWED_PATH or "//" in parts.path or "#" in url
    ):
        raise ValueError(f"URL is outside the approved official NSE route: {url}")
    if not parts.query:
        raise ValueError(f"corporate-actions URL must have a canonical query: {url}")
    fields = parts.query.split("&")
    if any(field.count("=") != 1 for field in fields):
        raise ValueError(f"query serialization is not canonical: {url}")
    pairs = [field.split("=", 1) for field in fields]
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)) or any(key not in QUALIFICATION_QUERY_KEYS for key in keys):
        raise ValueError(f"query contains duplicate or unapproved keys: {url}")
    if not allow_pagination_probe and any(key in PAGINATION_KEYS for key in keys):
        raise ValueError(f"pagination key is only permitted in the qualification matrix: {url}")
    if keys != sorted(keys, key=QUERY_ORDER.index):
        raise ValueError(f"query key order is not canonical: {url}")
    values = dict(pairs)
    if values.get("index") != "equities" or values.get("series") != "EQ":
        raise ValueError(f"query has unsupported fixed values: {url}")
    encoded_symbol = values.get("symbol", "")
    symbol = "M&M" if encoded_symbol == "M%26M" else encoded_symbol
    if symbol not in COHORT or symbol != symbol.upper() or not re.fullmatch(r"[A-Z0-9&-]+", symbol) or encoded_symbol != _canonical_symbol_value(symbol):
        raise ValueError(f"query has unsupported symbol serialization: {url}")
    date_keys = {"from_date", "to_date"}
    if bool(date_keys & values.keys()) != bool(date_keys <= values.keys()):
        raise ValueError(f"date range must contain both canonical bounds: {url}")
    for key in date_keys & values.keys():
        if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", values[key]):
            raise ValueError(f"date query value is not canonical: {url}")
    from_date = _parse_day(values.get("from_date"))
    to_date = _parse_day(values.get("to_date"))
    if date_keys <= values.keys() and (from_date is None or to_date is None or from_date > to_date):
        raise ValueError(f"date range is inverted: {url}")
    for key in PAGINATION_KEYS & values.keys():
        if not re.fullmatch(r"[1-9]\d*", values[key]):
            raise ValueError(f"pagination value is not canonical: {url}")
    return url


def _in_range(value: object, start: date = START_DATE, end: date = END_DATE) -> bool:
    parsed = _parse_day(value)
    return parsed is not None and start <= parsed <= end


def _query_url(symbol: str, start: date, end: date, **extra: str) -> str:
    query = f"index=equities&symbol={_canonical_symbol_value(symbol)}&series=EQ&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
    if extra:
        query += "&" + "&".join(f"{key}={value}" for key, value in extra.items())
    return _validate_url(f"{API_BASE}?{query}", allow_pagination_probe=bool(extra))


def _request(url: str, *, accept: str, timeout: float = 30.0, allow_pagination_probe: bool = False) -> dict[str, Any]:
    _validate_url(url, allow_pagination_probe=allow_pagination_probe)
    retrieved_at = _now()
    request = Request(url, headers={"Accept": accept, "Accept-Encoding": "identity", "User-Agent": "Kairos/2.0"})
    try:
        response = build_opener(_NoRedirect).open(request, timeout=timeout)
        with response:
            body = response.read()
            return {"requested_url": url, "final_url": str(response.geturl()), "status": int(response.status), "headers": _headers(response), "content_type": response.headers.get("Content-Type"), "retrieved_at": retrieved_at, "body": body, "error": None}
    except HTTPError as exc:
        body = exc.read()
        return {"requested_url": url, "final_url": str(exc.geturl()), "status": int(exc.code), "headers": _headers(exc), "content_type": exc.headers.get("Content-Type"), "retrieved_at": retrieved_at, "body": body, "error": f"HTTPError: {exc.code}"}
    except (OSError, URLError, TimeoutError) as exc:
        return {"requested_url": url, "final_url": None, "status": None, "headers": [], "content_type": None, "retrieved_at": retrieved_at, "body": b"", "error": f"{type(exc).__name__}: {exc}"}


def _json_array(body: bytes) -> tuple[bool, list[Any]]:
    try:
        value = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, []
    return isinstance(value, list), value if isinstance(value, list) else []


def _validate_response(result: Mapping[str, Any], *, expected_url: str, symbol: str, start: date, end: date, allow_pagination_probe: bool = False) -> tuple[bool, list[str], list[Any]]:
    reasons: list[str] = []
    try:
        _validate_url(expected_url, allow_pagination_probe=allow_pagination_probe)
    except ValueError as exc:
        reasons.append(f"requested_url_invalid:{exc}")
    final_url = result.get("final_url")
    try:
        if not isinstance(final_url, str):
            raise ValueError("missing final URL")
        _validate_url(final_url, allow_pagination_probe=allow_pagination_probe)
    except ValueError as exc:
        reasons.append(f"final_url_invalid:{exc}")
    if result.get("status") != 200:
        reasons.append(f"status_not_200:{result.get('status')}")
    if final_url != expected_url:
        reasons.append("final_url_does_not_equal_requested_url")
    content_type = str(result.get("content_type") or "").split(";", 1)[0].strip().casefold()
    if content_type != "application/json":
        reasons.append(f"content_type_not_json:{result.get('content_type')}")
    body = result.get("body", b"")
    if not isinstance(body, bytes):
        reasons.append("response_body_is_not_bytes")
        array, payload = False, []
    else:
        array, payload = _json_array(body)
    if not array:
        reasons.append("response_json_is_not_an_array")
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            reasons.append(f"row_{index}_is_not_an_object")
            continue
        if str(raw.get("symbol") or "").strip().upper() != symbol:
            reasons.append(f"row_{index}_symbol_mismatch")
        if raw.get("series") != "EQ":
            reasons.append(f"row_{index}_series_mismatch")
        isin = raw.get("isin")
        if not isinstance(isin, str) or not re.fullmatch(r"IN[A-Z0-9]{10}", isin):
            reasons.append(f"row_{index}_isin_missing_or_invalid")
        observed = [raw.get(key) for key in _DATE_FIELDS if raw.get(key) not in (None, "", "-")]
        if not any(_in_range(value, start, end) for value in observed):
            reasons.append(f"row_{index}_has_no_event_date_in_requested_range")
    return not reasons, reasons, payload


def _action_type(subject: str) -> str:
    text = subject.casefold()
    if "reverse split" in text:
        return "reverse_split"
    if "split" in text:
        return "split"
    if "bonus" in text:
        return "bonus"
    if "rights" in text:
        return "rights"
    if "demerger" in text:
        return "demerger"
    if "merger" in text:
        return "merger"
    if "dividend" in text:
        return "dividend"
    if "acquisition" in text or "takeover" in text:
        return "acquisition"
    return "other"


def _revision_status(subject: str) -> str:
    text = subject.casefold()
    if "cancel" in text:
        return "cancelled"
    if "revis" in text:
        return "revised"
    return "original_or_unspecified"


def _source_ref(name: str, header_name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    body = result.get("body", b"")
    return {"body_path": name, "headers_path": header_name, "requested_url": result.get("requested_url"), "final_url": result.get("final_url"), "status": result.get("status"), "content_type": result.get("content_type"), "retrieved_at": result.get("retrieved_at"), "byte_count": len(body), "sha256": _sha256(body)}


def _write_rows(rows: list[dict[str, Any]]) -> bytes:
    return b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows)


class NSEPublicationError(OSError):
    """Publication failed after the output root was already committed."""

    def __init__(self, *, phase: str, output_root: Path, cause: BaseException, committed: bool) -> None:
        self.phase = phase
        self.output_root = output_root
        self.cause = cause
        self.committed = committed
        self.durability_confirmed = False
        super().__init__(f"{phase} publication failed for {output_root}; committed={committed}; durability_confirmed=False: {cause}")


def _lexical_absolute(path: Path) -> Path:
    raw = os.fspath(Path(path))
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("//") or raw.endswith(os.sep):
        raise ValueError(f"unsafe candidate path: {path}")
    raw_parts = raw.split(os.sep)
    if any(part in {".", ".."} for part in raw_parts):
        raise ValueError(f"unsafe candidate path: {path}")
    return Path(os.path.abspath(raw))


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _close_fds(fds: tuple[int, ...], *, active: BaseException | None = None) -> None:
    primary = active
    for fd in fds:
        if fd < 0:
            continue
        try:
            os.close(fd)
        except BaseException as exc:
            if primary is None:
                primary = exc
            else:
                primary.add_note(f"close({fd}) failed after the primary error: {exc!r}")
    if active is None and primary is not None:
        raise primary


def _open_directory_no_symlinks(path: Path) -> int:
    absolute = _lexical_absolute(path)
    fd = os.open(os.sep, _directory_flags())
    active: BaseException | None = None
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            try:
                os.close(fd)
            except BaseException as exc:
                fd = -1
                _close_fds((next_fd,), active=exc)
                raise
            fd = next_fd
        result = fd
        fd = -1
        return result
    except BaseException as exc:
        active = exc
        raise
    finally:
        _close_fds((fd,), active=active)


def _ensure_directory_no_symlinks(path: Path) -> None:
    absolute = _lexical_absolute(path)
    fd = os.open(os.sep, _directory_flags())
    active: BaseException | None = None
    try:
        for part in absolute.parts[1:]:
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=fd)
                next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            try:
                os.close(fd)
            except BaseException as exc:
                fd = -1
                _close_fds((next_fd,), active=exc)
                raise
            fd = next_fd
    except BaseException as exc:
        active = exc
        raise
    finally:
        _close_fds((fd,), active=active)


def _safe_relative_parts(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name or ntpath.splitdrive(name)[0]:
        raise ValueError(f"unsafe staging path: {name}")
    if name.startswith("/") or name.endswith("/") or "//" in name:
        raise ValueError(f"unsafe staging path: {name}")
    parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe staging path: {name}")
    return parts


def _open_relative_directory(parent_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    fd = os.dup(parent_fd)
    active: BaseException | None = None
    try:
        for part in parts:
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                next_fd = os.open(part, _directory_flags(), dir_fd=fd)
            try:
                os.close(fd)
            except BaseException as exc:
                fd = -1
                _close_fds((next_fd,), active=exc)
                raise
            fd = next_fd
        result = fd
        fd = -1
        return result
    except BaseException as exc:
        active = exc
        raise
    finally:
        _close_fds((fd,), active=active)


def _fsync_tree(directory_fd: int) -> None:
    """Fsync all regular files and directories without following links."""
    for entry in os.scandir(directory_fd):
        if entry.name.startswith(".") and entry.name != ".owner":
            raise ValueError(f"unlisted control file in staging: {entry.name}")
        if entry.is_dir(follow_symlinks=False):
            child_fd = os.open(entry.name, _directory_flags(), dir_fd=directory_fd)
            active: BaseException | None = None
            try:
                _fsync_tree(child_fd)
            except BaseException as exc:
                active = exc
                raise
            finally:
                _close_fds((child_fd,), active=active)
        elif entry.is_file(follow_symlinks=False):
            file_fd = os.open(entry.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            active = None
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError(f"non-regular staged file: {entry.name}")
                os.fsync(file_fd)
            except BaseException as exc:
                active = exc
                raise
            finally:
                _close_fds((file_fd,), active=active)
        else:
            raise ValueError(f"unsafe staged entry: {entry.name}")
    os.fsync(directory_fd)


def _stage_bytes(staging: Path, name: str, payload: bytes) -> None:
    parts = _safe_relative_parts(name)
    staging = _lexical_absolute(staging)
    staging_parent_fd = _open_directory_no_symlinks(staging.parent)
    directory_fd = -1
    parent_fd = -1
    file_fd = -1
    active: BaseException | None = None
    try:
        directory_fd = _open_relative_directory(staging_parent_fd, (staging.name,), create=False)
        parent_fd = _open_relative_directory(directory_fd, parts[:-1], create=True)
        file_fd = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(file_fd, "wb") as handle:
            file_fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException as exc:
        active = exc
        raise
    finally:
        _close_fds((parent_fd, file_fd, directory_fd, staging_parent_fd), active=active)


def _create_staging(root: Path) -> tuple[Path, str, os.stat_result]:
    root = _lexical_absolute(root)
    _ensure_directory_no_symlinks(root.parent)
    parent_fd = _open_directory_no_symlinks(root.parent)
    candidate_created = False
    staging_name = ""
    staging_fd = -1
    staging_fd_opened = False
    owner_setup_started = False
    active: BaseException | None = None
    try:
        for _ in range(100):
            candidate = f".{root.name}.{os.urandom(12).hex()}"
            try:
                os.mkdir(candidate, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            candidate_created = True
            staging_name = candidate
            try:
                staging_fd = os.open(candidate, _directory_flags(), dir_fd=parent_fd)
                staging_fd_opened = True
                staging_stat = os.fstat(staging_fd)
                owner = hashlib.sha256(os.urandom(32)).hexdigest()
                owner_fd = os.open(
                    ".owner",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=staging_fd,
                )
                owner_setup_started = True
                owner_error: BaseException | None = None
                try:
                    owner_bytes = owner.encode("ascii")
                    written = 0
                    while written < len(owner_bytes):
                        count = os.write(owner_fd, owner_bytes[written:])
                        if count <= 0:
                            raise OSError(errno.EIO, "owner-token write made no progress")
                        written += count
                    os.fsync(owner_fd)
                except BaseException as exc:
                    owner_error = exc
                    raise
                finally:
                    try:
                        _close_fds((owner_fd,), active=owner_error)
                    finally:
                        owner_fd = -1
                if owner_error is not None:
                    raise owner_error
                os.fsync(staging_fd)
                staging = root.parent / staging_name
                return staging, owner, staging_stat
            except BaseException as exc:
                if staging_fd_opened:
                    _close_fds((staging_fd,), active=exc)
                    staging_fd = -1
                    staging_fd_opened = False
                if owner_setup_started:
                    try:
                        _cleanup_owned_staging_at(parent_fd, staging_name, owner, staging_stat)
                    except BaseException:
                        pass
                elif candidate_created:
                    try:
                        os.rmdir(staging_name, dir_fd=parent_fd)
                    except BaseException:
                        pass
                raise
        raise FileExistsError(f"could not create unique staging directory beside {root}")
    except BaseException as exc:
        active = exc
        raise
    finally:
        _close_fds((staging_fd if staging_fd_opened else -1, parent_fd), active=active)


def _remove_tree_at(directory_fd: int) -> None:
    for entry in os.scandir(directory_fd):
        if entry.is_dir(follow_symlinks=False):
            child_fd = os.open(entry.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                _remove_tree_at(child_fd)
            finally:
                try:
                    os.close(child_fd)
                except BaseException:
                    pass
            os.rmdir(entry.name, dir_fd=directory_fd)
        else:
            os.unlink(entry.name, dir_fd=directory_fd)


def _cleanup_owned_staging_at(parent_fd: int, name: str, owner: str, expected_stat: os.stat_result) -> None:
    flags = _directory_flags()
    child_fd = owner_fd = -1
    try:
        child_fd = os.open(name, flags, dir_fd=parent_fd)
        current = os.fstat(child_fd)
        if current.st_dev != expected_stat.st_dev or current.st_ino != expected_stat.st_ino:
            return
        owner_fd = os.open(".owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=child_fd)
        supplied = os.read(owner_fd, len(owner.encode("ascii")) + 1).decode("ascii")
        if supplied != owner:
            return
        _remove_tree_at(child_fd)
        try:
            replacement = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if replacement.st_dev == current.st_dev and replacement.st_ino == current.st_ino:
            os.rmdir(name, dir_fd=parent_fd)
    except (OSError, UnicodeError):
        return
    finally:
        for fd in (owner_fd, child_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except BaseException:
                    pass


def _cleanup_owned_staging(path: Path, owner: str, expected_stat: os.stat_result) -> None:
    path = _lexical_absolute(path)
    parent_fd = -1
    try:
        parent_fd = _open_directory_no_symlinks(path.parent)
        _cleanup_owned_staging_at(parent_fd, path.name, owner, expected_stat)
    except (OSError, UnicodeError):
        return
    finally:
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except BaseException:
                pass


def _atomic_noreplace_directory(staging: Path, root: Path) -> None:
    import ctypes
    staging = _lexical_absolute(staging)
    root = _lexical_absolute(root)
    if staging.parent != root.parent:
        raise ValueError(f"staging and output root must share one lexical parent: {staging}, {root}")
    parent_fd = _open_directory_no_symlinks(root.parent)
    libc = ctypes.CDLL(None, use_errno=True)
    active: BaseException | None = None
    try:
        if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
            rename = libc.renameatx_np
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(parent_fd, os.fsencode(staging.name), parent_fd, os.fsencode(root.name), 0x00000004)
        elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(parent_fd, os.fsencode(staging.name), parent_fd, os.fsencode(root.name), 0x00000001)
        else:
            raise OSError("atomic no-replace directory publication is unavailable on this platform")
        if result != 0:
            number = ctypes.get_errno()
            raise FileExistsError(number, os.strerror(number), root) if number == errno.EEXIST else OSError(number, os.strerror(number))
    except BaseException as exc:
        active = exc
        raise
    finally:
        _close_fds((parent_fd,), active=active)


def _publish_staged(staging: Path, root: Path, *, owner: str | None = None, staging_stat: os.stat_result | None = None) -> dict[str, Any]:
    """Commit one complete bundle, including its report, with no replacement."""
    if owner is None or not owner:
        raise PermissionError("staging owner is required for publication")
    root = _lexical_absolute(root)
    staging = _lexical_absolute(staging)
    if staging.parent != root.parent:
        raise ValueError(f"staging and output root must share one lexical parent: {staging}, {root}")
    parent_fd = _open_directory_no_symlinks(root.parent)
    staging_fd = -1
    committed = False
    active: BaseException | None = None
    try:
        try:
            os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"refusing non-fresh publication target: {root}")
        staging_fd = os.open(staging.name, _directory_flags(), dir_fd=parent_fd)
        if staging_stat is None:
            raise PermissionError("original staging inode metadata is required for publication")
        current_stat = os.fstat(staging_fd)
        if (current_stat.st_dev, current_stat.st_ino) != (staging_stat.st_dev, staging_stat.st_ino):
            raise PermissionError(f"staging inode changed: {staging}")
        owner_fd = os.open(".owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=staging_fd)
        owner_error: BaseException | None = None
        try:
            supplied = os.read(owner_fd, len(owner.encode("ascii")) + 1).decode("ascii")
        except BaseException as exc:
            owner_error = exc
            raise
        finally:
            _close_fds((owner_fd,), active=owner_error)
        if supplied != owner:
            raise PermissionError(f"staging owner mismatch: {staging}")
        _fsync_tree(staging_fd)
        post_rename_error: BaseException | None = None
        try:
            _atomic_noreplace_directory(staging, root)
        except BaseException as exc:
            try:
                os.stat(staging.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                staging_present = False
            else:
                staging_present = True
            try:
                os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                root_present = False
            else:
                root_present = True
            if not (root_present and not staging_present):
                raise
            post_rename_error = exc
            committed = True
        else:
            try:
                os.stat(staging.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("atomic publication returned before moving staging directory")
            committed = True
        post_commit_error: BaseException | None = None
        try:
            root_fd = os.open(root.name, _directory_flags(), dir_fd=parent_fd)
            root_error: BaseException | None = None
            try:
                os.unlink(".owner", dir_fd=root_fd)
                os.fsync(root_fd)
            except BaseException as exc:
                root_error = exc
                raise
            finally:
                _close_fds((root_fd,), active=root_error)
            os.fsync(parent_fd)
        except BaseException as exc:
            post_commit_error = exc
        if post_commit_error is not None:
            if post_rename_error is not None:
                post_commit_error.add_note(f"earlier post-rename error: {post_rename_error!r}")
            raise NSEPublicationError(phase="post-commit", output_root=root, cause=post_commit_error, committed=True) from post_commit_error
        if post_rename_error is not None:
            raise NSEPublicationError(phase="post-commit", output_root=root, cause=post_rename_error, committed=True) from post_rename_error
        return {"committed": True, "idempotent": False, "report_co_published": True, "durability_confirmed": True}
    except BaseException as exc:
        active = exc
        raise
    finally:
        if active is not None:
            _close_fds((staging_fd, parent_fd), active=active)
        else:
            try:
                _close_fds((staging_fd, parent_fd))
            except BaseException as exc:
                if committed:
                    raise NSEPublicationError(phase="post-commit", output_root=root, cause=exc, committed=True) from exc
                raise


def _probe_records(*, timeout: float) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for symbol in PROBE_SYMBOLS:
        for label, start, end in (("full_range", START_DATE, END_DATE), *PROBE_WINDOWS):
            url = _query_url(symbol, start, end)
            result = _request(url, accept="application/json", timeout=timeout)
            accepted, reasons, payload = _validate_response(result, expected_url=url, symbol=symbol, start=start, end=end)
            records.append({"kind": label, "symbol": symbol, "start": start.isoformat(), "end": end.isoformat(), "requested_url": url, "accepted": accepted, "reasons": reasons, "row_count": len(payload) if accepted else 0, "body": result.get("body", b""), "result": result})
    for key in PAGINATION_KEYS:
        symbol = "SBIN"
        url = _query_url(symbol, START_DATE, END_DATE, **{key: "1"})
        result = _request(url, accept="application/json", timeout=timeout, allow_pagination_probe=True)
        accepted, reasons, payload = _validate_response(result, expected_url=url, symbol=symbol, start=START_DATE, end=END_DATE, allow_pagination_probe=True)
        records.append({"kind": f"pagination_{key}", "symbol": symbol, "start": START_DATE.isoformat(), "end": END_DATE.isoformat(), "requested_url": url, "accepted": accepted, "reasons": reasons, "row_count": len(payload) if accepted else 0, "body": result.get("body", b""), "result": result})
    return records


def _public_probe(records: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    for record in records:
        result = record["result"]
        body = record["body"]
        summaries.append({"kind": record["kind"], "symbol": record["symbol"], "requested_range": {"start": record["start"], "end": record["end"]}, "requested_url": record["requested_url"], "final_url": result.get("final_url"), "status": result.get("status"), "content_type": result.get("content_type"), "retrieved_at": result.get("retrieved_at"), "body_byte_count": len(body), "body_sha256": _sha256(body), "json_array": _json_array(body)[0], "row_count": record["row_count"], "accepted": record["accepted"], "rejection_reasons": record["reasons"]})
    full = [item for item in summaries if item["kind"] == "full_range"]
    windows = [item for item in summaries if item["kind"] in {"early", "middle", "late"}]
    date_qualified = bool(full) and all(item["accepted"] for item in full + windows) and all(item["row_count"] < 20 for item in full)
    pagination = [item for item in summaries if item["kind"].startswith("pagination_")]
    pagination_distinct = [item["kind"] for item in pagination if item["accepted"] and item["body_sha256"] not in {full_item["body_sha256"] for full_item in full}]
    return {"schema_version": "meridian.nse-corporate-actions-public-probe.v2", "retrieved_at": _now(), "requested_range": {"start": START_DATE.isoformat(), "end": END_DATE.isoformat(), "inclusive": True}, "probe_symbols": list(PROBE_SYMBOLS), "results": summaries, "date_range_parameters": {"parameters": ["from_date", "to_date"], "qualified": date_qualified, "qualification_scope": list(PROBE_SYMBOLS), "reason": "accepted bounded early/middle/late windows and full-range responses remained below the 20-row cap" if date_qualified else "bounded response validation did not establish date-range semantics"}, "full_range_history": {"verdict": "qualified_for_probe_scope_only" if date_qualified else "not_qualified", "without_20_row_truncation": date_qualified, "scope": list(PROBE_SYMBOLS), "proceed_full_cohort": False, "reason": "full-range endpoint returned 5 and 7 rows for the tested symbols; cohort-wide completeness remains unqualified"}, "pagination_parameters": {"candidates": list(PAGINATION_KEYS), "accepted_responses": [item["kind"] for item in pagination if item["accepted"]], "distinct_response_evidence": pagination_distinct, "qualified": False, "reason": "candidate keys returned the same 5-row payload as the unpaginated request; pagination remains unqualified"}, "historical_availability_qualified": False, "availability_reason": "public corporate-actions responses expose no authoritative publication or first-availability timestamp"}


def probe(*, timeout: float = 30.0) -> dict[str, Any]:
    return _public_probe(_probe_records(timeout=timeout))


def acquire(*, output_root: Path = DEFAULT_OUTPUT_ROOT, report_root: Path | None = DEFAULT_REPORT_ROOT, timeout: float = 30.0, pause_seconds: float = 0.05) -> dict[str, Any]:
    output_root = _lexical_absolute(Path(output_root))
    if report_root is not None and _lexical_absolute(Path(report_root)) != output_root:
        raise ValueError("report_root must be omitted or equal to output_root; report is co-published")
    del pause_seconds
    _ensure_directory_no_symlinks(output_root.parent)
    parent_fd = _open_directory_no_symlinks(output_root.parent)
    try:
        output_exists = True
        try:
            os.stat(output_root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            output_exists = False
    finally:
        os.close(parent_fd)
    if output_exists:
        raise FileExistsError(f"refusing to use existing immutable output root: {output_root}")
    staging, owner, staging_stat = _create_staging(output_root)
    committed = False
    try:
        records = []
        for symbol in COHORT:
            url = _query_url(symbol, START_DATE, END_DATE)
            result = _request(url, accept="application/json", timeout=timeout)
            accepted, reasons, payload = _validate_response(result, expected_url=url, symbol=symbol, start=START_DATE, end=END_DATE)
            records.append({"kind": "cohort_full_range", "symbol": symbol, "start": START_DATE.isoformat(), "end": END_DATE.isoformat(), "requested_url": url, "accepted": accepted, "reasons": reasons, "row_count": len(payload) if accepted else 0, "body": result.get("body", b""), "result": result})
        files: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        quarantines: list[dict[str, Any]] = []
        source_responses: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            body = record["body"]
            stem = f"{index:02d}_{record['kind']}_{_safe_name(record['symbol'])}"
            body_name = f"probes/{stem}.json"
            header_name = f"probes/{stem}.headers.json"
            _stage_bytes(staging, body_name, body)
            header_bytes = (json.dumps({key: value for key, value in record["result"].items() if key != "body"}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            _stage_bytes(staging, header_name, header_bytes)
            files.extend([_file_entry(body_name, body, "application/json"), _file_entry(header_name, header_bytes, "application/json")])
            source = _source_ref(body_name, header_name, record["result"])
            source.update({"probe_kind": record["kind"], "symbol": record["symbol"], "accepted": record["accepted"], "row_count": record["row_count"], "rejection_reasons": record["reasons"]})
            source_responses.append(source)
            if not record["accepted"]:
                quarantines.append({"symbol": record["symbol"], "probe_kind": record["kind"], "reason": "response failed strict status/final-URL/content-type/JSON/symbol/date validation", "details": record["reasons"], "source": source})
                continue
            if record["kind"].startswith("pagination_"):
                continue
            payload = _json_array(body)[1]
            for row_index, raw in enumerate(payload):
                subject = str(raw.get("subject") or "").strip()
                observed = [raw.get(key) for key in _DATE_FIELDS if raw.get(key) not in (None, "", "-")]
                row = {"observation_id": "caobs_" + _sha256(json.dumps({"requested_url": record["requested_url"], "row_index": row_index, "raw": raw}, sort_keys=True, default=str).encode())[:32], "probe_kind": record["kind"], "symbol": str(raw.get("symbol") or "").strip().upper(), "company_name": raw.get("comp"), "series": raw.get("series"), "isin": raw.get("isin"), "action_type_hint": _action_type(subject), "purpose": subject, "face_value": raw.get("faceVal"), "announcement_date": None, "ex_date": raw.get("exDate"), "record_date": raw.get("recDate"), "book_closure_start": raw.get("bcStartDate"), "book_closure_end": raw.get("bcEndDate"), "payable_date": None, "ca_broadcast_date": raw.get("caBroadcastDate"), "adjustment_factor": None, "factor_provenance": None, "published_at": None, "first_seen_at": None, "available_at": None, "availability_status": "unqualified_missing_publication_or_first_availability_timestamp", "revision_status": _revision_status(subject), "displayed_event_dates": [str(value) for value in observed], "in_requested_range": any(_in_range(value, date.fromisoformat(record["start"]), date.fromisoformat(record["end"])) for value in observed), "source": source, "parser_result": "parsed_public_json_row_strictly_validated_terms_preserved", "source_row": raw}
                rows.append(row)
        rows.sort(key=lambda item: (item["symbol"], item["probe_kind"], item["ex_date"] or "", item["observation_id"]))
        observations = _write_rows(rows)
        accepted_symbols = [source["symbol"] for source in source_responses if source["accepted"]]
        public_probe = {"schema_version": "meridian.nse-corporate-actions-public-probe.v3", "results": source_responses, "route_consistency": {"requested_symbol_count": len(COHORT), "accepted_symbol_count": len(accepted_symbols), "all_symbols_behaved_consistently": len(accepted_symbols) == len(COHORT)}, "truncation_pagination_evidence": {"pagination_requests": 0, "pagination_parameters_qualified": False, "reason": "exact cohort acquisition is one full-range request per symbol; no pagination request was added", "per_symbol_response_row_counts": {record["symbol"]: record["row_count"] for record in records}}}
        manifest: dict[str, Any] = {"schema_version": "meridian.nse-corporate-actions-public-risk23-v3-probe", "status": "bounded_cohort_source_evidence_unqualified_for_pit", "cohort": list(COHORT), "range": {"start": START_DATE.isoformat(), "end": END_DATE.isoformat(), "inclusive": True}, "source_route": API_BASE, "official_route_qualification": {"public_retrieval": all(source["accepted"] for source in source_responses), "date_filter": all(source["accepted"] for source in source_responses), "symbol_series_isin_validation": all(source["accepted"] for source in source_responses), "reason": "strict response validation required exact final URL, status, JSON array, source symbol, EQ series, valid source ISIN, and in-range event date"}, "bounded_probe": public_probe, "request_accounting": {"cohort_symbols": len(COHORT), "api_requests": len(records), "expected_api_requests": len(COHORT), "pagination_requests": 0, "attachment_requests": 0}, "rows": len(rows), "rows_in_requested_range": sum(bool(row["in_requested_range"]) for row in rows), "per_symbol_rows": {symbol: sum(row["symbol"] == symbol for row in rows) for symbol in COHORT}, "revisions_or_cancellations": sum(row["revision_status"] != "original_or_unspecified" for row in rows), "timestamp_completeness": {"published_at": 0, "first_seen_at": 0, "available_at": 0, "status": "unqualified"}, "quarantine_count": len(quarantines), "quarantines": quarantines, "source_responses": source_responses, "files": [_file_entry("observations.jsonl", observations, "application/x-ndjson"), *files], "observations_sha256": _sha256(observations), "do_not_canonicalize_identity": True, "pit_verdict": "not_qualified_missing_historical_first_availability", "report_is_co_published": True}
        _stage_bytes(staging, "observations.jsonl", observations)
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        _stage_bytes(staging, "manifest.json", manifest_bytes)
        report = {"schema_version": "meridian.nse-corporate-actions-public-report.v3", "output_root": str(output_root), "manifest": str(output_root / "manifest.json"), "report": manifest, "publication": {"committed": True, "report_co_published": True}}
        _stage_bytes(staging, "report.json", (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
        publication = _publish_staged(staging, output_root, owner=owner, staging_stat=staging_stat)
        committed = True
        report["publication"] = publication
        return report
    finally:
        if not committed:
            _cleanup_owned_staging(staging, owner, staging_stat)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--acquire", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = probe(timeout=args.timeout) if args.probe else acquire(output_root=args.output_root, report_root=args.report_root, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
