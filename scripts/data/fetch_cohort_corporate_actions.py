#!/usr/bin/env python3
"""One full-range NSE corporate-actions request per cohort ever-member symbol, with receipts."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from http.client import HTTPException
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.daily_panel import derived_root
from scripts.data.fetch_nse_corporate_actions import (
    API_BASE,
    _action_type,
    _parse_day,
    _query_url,
    _request,
    _revision_status,
    _safe_name,
    _sha256,
    _validate_response,
    _write_rows,
)

SCHEMA = "meridian.daily-runner.corporate-actions.v1"
RANGE_START = date(2019, 10, 1)
RANGE_END = date(2026, 10, 19)
REQUEST_CAP = 550
MIN_GAP_SECONDS = 1.0
MAX_CONSECUTIVE_FAILURES = 5
TRUNCATION_ROWS = 20
ACCEPTED_SERIES = frozenset({"EQ", "BE", "BZ"})
RECEIPT_SUFFIX = ".headers.json"
RESERVATION_SUFFIX = ".reservation.json"
FAILED_RESPONSE_PREFIXES = ("content_type_not_json", "response_json_is_not_an_array")
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Member:
    """One security id and its symbol chain, oldest first."""

    security_id: str
    chain: tuple[str, ...]


@dataclass(frozen=True)
class Attempt:
    """One retained request, judged against the cohort rules."""

    symbol: str
    name: str
    sha256: str
    byte_count: int
    accepted: bool
    failed: bool
    reasons: tuple[str, ...]
    row_count: int
    rows: tuple[Any, ...]


def _now() -> str:
    """Return the current UTC time as an ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _iso(value: object) -> str | None:
    """Return the ISO date for an NSE date field, or None when it is missing."""
    parsed = _parse_day(value)
    return parsed.isoformat() if parsed else None


def _write_durable(path: Path, payload: bytes, *, exclusive: bool = False) -> None:
    """Write bytes and fsync them so a crash keeps what was fetched."""
    with path.open("xb" if exclusive else "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _load_members(path: Path) -> dict[str, Member]:
    """Map every ever-member symbol to its security id and symbol chain."""
    members: dict[str, Member] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            member = Member(row["security_id"], tuple(row["symbols"].split("|")))
            for symbol in member.chain:
                if symbol in members:
                    raise ValueError(f"symbol maps to two security ids: {symbol}")
                members[symbol] = member
    return members


def _request_one(url: str, *, timeout: float, allowed: frozenset[str]) -> dict[str, Any]:
    """Request one URL; a truncated body becomes a transport failure instead of a crash."""
    try:
        return _request(url, accept="application/json", timeout=timeout, symbols=allowed)
    except HTTPException as exc:
        return {"requested_url": url, "final_url": None, "status": None, "headers": [], "content_type": None, "retrieved_at": _now(), "body": b"", "error": f"{type(exc).__name__}: {exc}"}


def _judge(result: Mapping[str, Any], *, symbol: str, chain: tuple[str, ...], allowed: frozenset[str]) -> tuple[bool, bool, list[str], list[Any]]:
    """Validate one response with cohort rules; report whether the response itself failed."""
    _, reasons, payload = _validate_response(result, expected_url=str(result["requested_url"]), symbol=symbol, start=RANGE_START, end=RANGE_END, symbols=allowed)
    # Symbol and series are re-checked below: rows may carry any chain symbol and BE/BZ series.
    reasons = [reason for reason in reasons if not reason.endswith(("_symbol_mismatch", "_series_mismatch"))]
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("symbol") or "").strip().upper() not in chain:
            reasons.append(f"row_{index}_symbol_not_in_chain")
        if raw.get("series") not in ACCEPTED_SERIES:
            reasons.append(f"row_{index}_series_not_accepted")
    if result.get("error") is not None:
        reasons = [str(result["error"])]
    failed = result.get("error") is not None or result.get("status") != 200 or any(reason.startswith(FAILED_RESPONSE_PREFIXES) for reason in reasons)
    return not reasons, failed, reasons, payload


def _record(symbol: str, name: str, result: Mapping[str, Any], chain: tuple[str, ...], allowed: frozenset[str]) -> Attempt:
    """Judge one retained response and summarize it."""
    accepted, failed, reasons, payload = _judge(result, symbol=symbol, chain=chain, allowed=allowed)
    body = result["body"]
    return Attempt(symbol=symbol, name=name, sha256=_sha256(body), byte_count=len(body), accepted=accepted, failed=failed, reasons=tuple(reasons), row_count=len(payload), rows=tuple(payload) if accepted else ())


def _attempt_name(sequence: int, symbol: str) -> str:
    """File stem for one request: its sequence number, then its symbol."""
    return f"{sequence:03d}_{_safe_name(symbol)}"


def _reserve(responses: Path, sequence: int, symbol: str, url: str) -> None:
    """Durably count one request before it is sent; a reservation with no receipt still counts."""
    record = {"sequence": sequence, "symbol": symbol, "url": url, "reserved_at": _now()}
    _write_durable(responses / f"{_attempt_name(sequence, symbol)}{RESERVATION_SUFFIX}", (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(), exclusive=True)


def _write_attempt(responses: Path, sequence: int, symbol: str, result: Mapping[str, Any]) -> str:
    """Retain one raw body and its receipt; the receipt, which records the body sha256, is written last."""
    name = _attempt_name(sequence, symbol)
    body = result["body"]
    _write_durable(responses / f"{name}.json", body, exclusive=True)
    receipt = {key: value for key, value in result.items() if key != "body"} | {"body_sha256": _sha256(body)}
    _write_durable(responses / f"{name}{RECEIPT_SUFFIX}", (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(), exclusive=True)
    return name


def _recorded_hashes(output_root: Path) -> dict[str, str]:
    """Body sha256 per response file as the last manifest recorded it; covers receipts written before body_sha256."""
    path = output_root / "manifest.json"
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {entry["file"]: entry["sha256"] for entry in manifest["symbols"] if entry["file"]}


def _load_attempts(responses: Path, members: Mapping[str, Member], allowed: frozenset[str], recorded: Mapping[str, str]) -> list[Attempt]:
    """Rebuild every retained attempt from its receipt and body, refusing a body whose sha256 differs from its recorded hash."""
    bodies = {path.stem for path in responses.glob("*.json") if not path.name.endswith((RECEIPT_SUFFIX, RESERVATION_SUFFIX))}
    receipts = {path.name.removesuffix(RECEIPT_SUFFIX) for path in responses.glob(f"*{RECEIPT_SUFFIX}")}
    reservations = {path.name.removesuffix(RESERVATION_SUFFIX) for path in responses.glob(f"*{RESERVATION_SUFFIX}")}
    # A body with no receipt is a crash after the body write; its reservation makes it an incomplete request.
    orphans = (receipts - bodies) | (bodies - receipts - reservations)
    if orphans:
        raise ValueError(f"responses without a matching receipt or receipt without a body: {sorted(orphans)}")
    attempts: list[Attempt] = []
    for receipt_path in sorted(responses.glob(f"*{RECEIPT_SUFFIX}")):
        name = receipt_path.name.removesuffix(RECEIPT_SUFFIX)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        body = (responses / f"{name}.json").read_bytes()
        expected = receipt.get("body_sha256") or recorded.get(f"responses/{name}.json")
        if expected is None:
            raise ValueError(f"{name}: retained body has no recorded sha256")
        if _sha256(body) != expected:
            raise ValueError(f"{name}: retained body sha256 does not match its recorded hash")
        symbol = parse_qs(urlsplit(receipt["requested_url"]).query)["symbol"][0]
        result = {**receipt, "body": body}
        attempts.append(_record(symbol, name, result, members[symbol].chain, allowed))
    return attempts


def _incomplete(responses: Path) -> list[dict[str, Any]]:
    """Reservations with no receipt: requests that were counted but never answered, noting any body kept without a receipt."""
    answered = {path.name.removesuffix(RECEIPT_SUFFIX) for path in responses.glob(f"*{RECEIPT_SUFFIX}")}
    records: list[dict[str, Any]] = []
    for path in sorted(responses.glob(f"*{RESERVATION_SUFFIX}")):
        name = path.name.removesuffix(RESERVATION_SUFFIX)
        if name in answered:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if (responses / f"{name}.json").exists():
            record["orphan_body"] = f"responses/{name}.json"
        records.append(record)
    return records


def _symbol_entries(members: Mapping[str, Member], attempts: list[Attempt], incomplete: list[dict[str, Any]], abort: str | None) -> list[dict[str, Any]]:
    """Return one manifest entry per member symbol, using its latest attempt."""
    latest = {attempt.symbol: attempt for attempt in attempts}
    unanswered = {record["symbol"]: record for record in incomplete}
    entries: list[dict[str, Any]] = []
    for symbol in sorted(members):
        security_id = members[symbol].security_id
        attempt = latest.get(symbol)
        if attempt is None and symbol in unanswered:
            reasons = ["request reserved but never answered; not retried automatically"]
            if "orphan_body" in unanswered[symbol]:
                reasons.append(f"body kept without receipt: {unanswered[symbol]['orphan_body']}")
            entries.append({"security_id": security_id, "symbol": symbol, "file": None, "sha256": None, "byte_count": None, "status": "incomplete", "accepted": False, "row_count": 0, "reasons": reasons, "possibly_truncated": False})
            continue
        if attempt is None:
            entries.append({"security_id": security_id, "symbol": symbol, "file": None, "sha256": None, "byte_count": None, "status": "not_requested", "accepted": False, "row_count": 0, "reasons": [abort or "not_requested"], "possibly_truncated": False})
            continue
        entries.append({"security_id": security_id, "symbol": symbol, "file": f"responses/{attempt.name}.json", "sha256": attempt.sha256, "byte_count": attempt.byte_count, "status": "accepted" if attempt.accepted else "failed", "accepted": attempt.accepted, "row_count": attempt.row_count, "reasons": list(attempt.reasons), "possibly_truncated": attempt.row_count >= TRUNCATION_ROWS})
    return entries


def _observations(members: Mapping[str, Member], attempts: list[Attempt]) -> list[dict[str, Any]]:
    """Flatten accepted rows into observation records, sorted deterministically."""
    observations: list[dict[str, Any]] = []
    for attempt in attempts:
        if not attempt.accepted:
            continue
        source_file = f"responses/{attempt.name}.json"
        for raw in attempt.rows:
            subject = str(raw.get("subject") or "").strip()
            observations.append({"security_id": members[attempt.symbol].security_id, "query_symbol": attempt.symbol, "symbol": str(raw.get("symbol") or "").strip().upper(), "series": raw.get("series"), "isin": raw.get("isin"), "company": raw.get("comp"), "subject": subject, "ex_date": _iso(raw.get("exDate")), "record_date": _iso(raw.get("recDate")), "action_type_hint": _action_type(subject), "revision_status": _revision_status(subject), "source_file": source_file})
    observations.sort(key=lambda row: (row["security_id"], row["query_symbol"], row["source_file"], row["ex_date"] or "", row["symbol"], row["subject"]))
    return observations


def _write_outputs(output_root: Path, members: Mapping[str, Member], attempts: list[Attempt], incomplete: list[dict[str, Any]], *, abort: str | None) -> dict[str, Any]:
    """Write manifest, observations, and failures from the retained attempts and unanswered reservations."""
    entries = _symbol_entries(members, attempts, incomplete, abort)
    observations = _observations(members, attempts)
    observations_bytes = _write_rows(observations)
    _write_durable(output_root / "observations.jsonl", observations_bytes)
    accounting = {"symbols": len(members), "requests": len(attempts) + len(incomplete), "accepted": sum(attempt.accepted for attempt in attempts), "failed": sum(not attempt.accepted for attempt in attempts), "incomplete": len(incomplete), "not_requested": sum(entry["status"] == "not_requested" for entry in entries), "request_cap": REQUEST_CAP}
    manifest = {"schema": SCHEMA, "run_status": "aborted" if abort else "finished", "abort_reason": abort, "range": {"from_date": RANGE_START.strftime("%d-%m-%Y"), "to_date": RANGE_END.strftime("%d-%m-%Y"), "start": RANGE_START.isoformat(), "end": RANGE_END.isoformat()}, "source_route": API_BASE, "request_accounting": accounting, "observations": {"file": "observations.jsonl", "row_count": len(observations), "sha256": _sha256(observations_bytes)}, "symbols": entries}
    _write_durable(output_root / "manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    failures = io.StringIO()
    writer = csv.writer(failures, lineterminator="\n")
    writer.writerow(["symbol", "security_id", "status", "reasons"])
    for entry in entries:
        if not entry["accepted"]:
            writer.writerow([entry["symbol"], entry["security_id"], entry["status"], "; ".join(entry["reasons"])])
    _write_durable(output_root / "failures.csv", failures.getvalue().encode())
    return manifest


def fetch(*, members: Mapping[str, Member], output_root: Path, resume: bool, timeout: float) -> dict[str, Any]:
    """Request each pending member symbol once, retaining each receipt as it arrives."""
    responses = output_root / "responses"
    responses.mkdir(parents=True, exist_ok=True)
    allowed = frozenset(members)
    recorded = _recorded_hashes(output_root) if resume else {}
    attempts = _load_attempts(responses, members, allowed, recorded) if resume else []
    incomplete = _incomplete(responses) if resume else []
    accepted = {attempt.symbol for attempt in attempts if attempt.accepted}
    unanswered = {record["symbol"] for record in incomplete}
    pending = [symbol for symbol in sorted(members) if symbol not in accepted and symbol not in unanswered]
    used = len(attempts) + len(incomplete)
    streak: list[Attempt] = []
    abort: str | None = None
    last_start: float | None = None
    for symbol in pending:
        if used >= REQUEST_CAP:
            abort = f"request cap {REQUEST_CAP} reached before all symbols were requested"
            break
        if last_start is not None:
            time.sleep(max(0.0, MIN_GAP_SECONDS - (time.monotonic() - last_start)))
        url = _query_url(symbol, RANGE_START, RANGE_END, symbols=allowed)
        used += 1
        _reserve(responses, used, symbol, url)
        last_start = time.monotonic()
        result = _request_one(url, timeout=timeout, allowed=allowed)
        name = _write_attempt(responses, used, symbol, result)
        attempt = _record(symbol, name, result, members[symbol].chain, allowed)
        attempts.append(attempt)
        print(f"{used:03d} {symbol} accepted={attempt.accepted} rows={attempt.row_count} reasons={'; '.join(attempt.reasons)}", flush=True)
        streak = [*streak, attempt] if attempt.failed else []
        if len(streak) >= MAX_CONSECUTIVE_FAILURES:
            abort = f"{MAX_CONSECUTIVE_FAILURES} consecutive failed responses: " + " | ".join(f"{item.symbol}: {'; '.join(item.reasons)}" for item in streak)
            break
    return _write_outputs(output_root, members, attempts, incomplete, abort=abort)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options; defaults come from the derived-data root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the cohort fetch and print the manifest request accounting."""
    args = parse_args(argv)
    root = derived_root()
    members_path = args.members or root / "daily-runner" / "cohort-v1" / "ever_members.csv"
    output_root = args.output_root or root / "daily-runner" / "corporate-actions-v1"
    if REPO_ROOT in output_root.resolve().parents or output_root.resolve() == REPO_ROOT:
        raise ValueError(f"output root must be outside the git tree: {output_root}")
    if output_root.exists() and not args.resume:
        raise FileExistsError(f"output root exists; pass --resume to continue: {output_root}")
    manifest = fetch(members=_load_members(members_path), output_root=output_root, resume=args.resume, timeout=args.timeout)
    print(json.dumps({"run_status": manifest["run_status"], "abort_reason": manifest["abort_reason"], "request_accounting": manifest["request_accounting"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
