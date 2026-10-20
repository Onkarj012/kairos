from __future__ import annotations

import gzip
import io
import json
import threading
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError

import pandas as pd
import pytest

from ingest import daily_cash as daily_cash_module
from ingest.daily_cash import (
    CashUrlRoute,
    HttpResponse,
    collect_daily_cash,
    nsearchives_udiff_cash_zip_url,
    route_url,
)
from ingest.nse_bhavcopy import nse_sec_bhavdata_full_url


SESSION = date(2026, 9, 14)
OTHER_SESSION = date(2026, 9, 15)


def _body(session: date = SESSION, *, close: str = "104", volume: str = "1200", incomplete: bool = False) -> bytes:
    row = {
        "SYMBOL": "AAA",
        "SERIES": "EQ",
        "DATE1": session.strftime("%d-%b-%Y"),
        "PREV_CLOSE": "98",
        "OPEN_PRICE": "100",
        "HIGH_PRICE": "105",
        "LOW_PRICE": "99",
        "LAST_PRICE": "103",
        "CLOSE_PRICE": close,
        "AVG_PRICE": "102",
        "TTL_TRD_QNTY": volume,
        "TURNOVER_LACS": "12.345",
        "NO_OF_TRADES": "42",
        "DELIV_QTY": "600",
        "DELIV_PER": "50",
    }
    if incomplete:
        row.pop("LAST_PRICE")
    return pd.DataFrame([row]).to_csv(index=False).encode()


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 15, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


def _response(
    url: str,
    body: bytes,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    retrieved_at: str = "2026-09-15T18:00:00+00:00",
) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"Content-Type": "text/csv", "Last-Modified": "Mon, 14 Sep 2026 12:00:00 GMT", **(headers or {})},
        body=body,
        final_url=url,
        retrieved_at=retrieved_at,
    )


def test_accepts_price_only_rows_retains_bytes_and_updates_pointer(tmp_path: Path) -> None:
    clock = Clock()
    url = nse_sec_bhavdata_full_url(SESSION)

    result = collect_daily_cash(SESSION, tmp_path, transport=lambda u, _c, _r: _response(u, _body()), now=clock.now, sleep=clock.sleep)

    assert result.status == "accepted"
    assert result.pointer_updated is True
    attempt = tmp_path / "requests" / SESSION.isoformat() / "attempt-001"
    receipt = json.loads((attempt / "receipt.json").read_text())
    assert (attempt / "response.bin").read_bytes() == _body()
    assert receipt["requested_date"] == SESSION.isoformat()
    assert receipt["embedded_date"] == SESSION.isoformat()
    assert receipt["final_url"] == url
    assert receipt["validation_result"]["status"] == "accepted"
    normalized = json.loads((attempt / "normalized.json").read_text())
    assert normalized["rows"][0]["traded_value"] == 1234500.0
    assert "available_at" not in normalized
    pointer = json.loads((tmp_path / "current.json").read_text())
    assert pointer["session_date"] == SESSION.isoformat()


def test_wrong_date_is_retained_rejected_and_resume_consumes_attempt_budget(tmp_path: Path) -> None:
    clock = Clock()
    url = nse_sec_bhavdata_full_url(SESSION)
    calls = 0

    def transport(requested_url: str, _connect: float, _read: float) -> HttpResponse:
        nonlocal calls
        calls += 1
        return _response(requested_url, _body(OTHER_SESSION))

    first = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    assert first.status == "rejected"
    first_receipt = json.loads((tmp_path / "requests" / SESSION.isoformat() / "attempt-001" / "receipt.json").read_text())
    assert first_receipt["embedded_date"] == OTHER_SESSION.isoformat()
    assert first_receipt["validation_result"]["status"] == "rejected"
    assert (tmp_path / "requests" / SESSION.isoformat() / "attempt-001" / "response.bin").exists()

    second = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep, resume=True)
    assert second.status == "rejected"
    assert calls == 2
    assert clock.sleeps == [5.0]
    third = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep, resume=True)
    assert third.status == "rejected"
    assert calls == 3
    assert clock.sleeps == [5.0, 30.0]
    exhausted = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep, resume=True)
    assert exhausted.status == "exhausted"
    assert calls == 3
    assert url in json.loads((tmp_path / "requests" / SESSION.isoformat() / "manifest.json").read_text())["requested_url"]


def test_http_error_and_incomplete_response_are_retained_without_pointer(tmp_path: Path) -> None:
    clock = Clock()
    url = nse_sec_bhavdata_full_url(SESSION)
    responses = iter([
        _response(url, b"not found", status=404, headers={"Content-Type": "text/html"}),
        _response(url, _body(incomplete=True)),
    ])

    def transport(_url: str, _connect: float, _read: float) -> HttpResponse:
        return next(responses)

    first = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    second = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep, resume=True)
    assert first.status == "rejected"
    assert second.status == "rejected"
    assert not (tmp_path / "current.json").exists()
    first_receipt = json.loads((tmp_path / "requests" / SESSION.isoformat() / "attempt-001" / "receipt.json").read_text())
    assert first_receipt["http_status"] == 404
    assert first_receipt["body_byte_count"] == len(b"not found")
    second_receipt = json.loads((tmp_path / "requests" / SESSION.isoformat() / "attempt-002" / "receipt.json").read_text())
    assert second_receipt["validation_result"]["status"] == "rejected"


def test_pointer_is_monotonic_and_accepted_session_is_not_fetched_again(tmp_path: Path) -> None:
    clock = Clock()
    calls: list[str] = []

    def transport(url: str, _connect: float, _read: float) -> HttpResponse:
        calls.append(url)
        requested = OTHER_SESSION if url.endswith("15092026.csv") else SESSION
        modified = "Tue, 15 Sep 2026 12:00:00 GMT" if requested == OTHER_SESSION else "Mon, 14 Sep 2026 12:00:00 GMT"
        return _response(url, _body(requested), headers={"Last-Modified": modified})

    newer = collect_daily_cash(OTHER_SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    older = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    resumed_newer = collect_daily_cash(OTHER_SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    assert newer.status == "accepted"
    assert older.status == "accepted"
    assert resumed_newer.reason == "resumed accepted session"
    assert len(calls) == 2
    assert json.loads((tmp_path / "current.json").read_text())["session_date"] == OTHER_SESSION.isoformat()


def test_different_dates_are_one_request_in_flight_and_spacing_is_global(tmp_path: Path) -> None:
    clock = Clock()
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []
    results: dict[str, object] = {}

    def transport(url: str, _connect: float, _read: float) -> HttpResponse:
        calls.append(url)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(2)
        requested = OTHER_SESSION if url.endswith("15092026.csv") else SESSION
        modified = "Tue, 15 Sep 2026 12:00:00 GMT" if requested == OTHER_SESSION else "Mon, 14 Sep 2026 12:00:00 GMT"
        return _response(url, _body(requested), headers={"Last-Modified": modified})

    first = threading.Thread(
        target=lambda: results.setdefault("first", collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)),
    )
    second = threading.Thread(
        target=lambda: results.setdefault("second", collect_daily_cash(OTHER_SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)),
    )
    first.start()
    assert first_started.wait(2)
    second.start()
    assert not any(url.endswith("15092026.csv") for url in calls)
    release_first.set()
    first.join(2)
    second.join(2)

    assert results["first"].status == "accepted"
    assert results["second"].status == "accepted"
    assert len(calls) == 2
    assert clock.sleeps == [0.5]


def test_transient_timeout_retries_three_times_and_restart_cannot_exceed_budget(tmp_path: Path) -> None:
    clock = Clock()
    calls = 0

    def transport(_url: str, _connect: float, _read: float) -> HttpResponse:
        nonlocal calls
        calls += 1
        raise URLError("read timed out")

    result = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    restarted = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)

    assert result.status == "exhausted"
    assert result.attempts == 3
    assert restarted.status == "exhausted"
    assert calls == 3
    assert clock.sleeps == [5.0, 30.0]
    for attempt in (1, 2, 3):
        receipt = json.loads((tmp_path / "requests" / SESSION.isoformat() / f"attempt-{attempt:03d}" / "receipt.json").read_text())
        assert receipt["validation_result"]["retryable"] is True


def test_budget_does_not_start_request_at_exact_deadline(tmp_path: Path) -> None:
    clock = Clock()
    calls = 0

    def transport(_url: str, _connect: float, _read: float) -> HttpResponse:
        nonlocal calls
        calls += 1
        clock.value += timedelta(hours=8)
        raise URLError("connect timed out")

    first = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep)
    second = collect_daily_cash(SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep, resume=True)

    assert first.status == "exhausted"
    assert second.status == "exhausted"
    assert calls == 1
    assert "before next request" in (second.reason or "")


def test_corrupt_accepted_receipt_never_reuses_changed_artifacts(tmp_path: Path) -> None:
    clock = Clock()
    result = collect_daily_cash(SESSION, tmp_path, transport=lambda u, _c, _r: _response(u, _body()), now=clock.now, sleep=clock.sleep)
    assert result.status == "accepted"
    attempt = tmp_path / "requests" / SESSION.isoformat() / "attempt-001"
    normalized_path = attempt / "normalized.json"
    normalized = json.loads(normalized_path.read_text())
    normalized["rows"][0]["close"] = 999.0
    normalized_path.write_text(json.dumps(normalized), encoding="utf-8")
    (attempt / "response.bin").unlink()

    reused = collect_daily_cash(SESSION, tmp_path, transport=lambda *_args: (_ for _ in ()).throw(AssertionError("must not refetch")), now=clock.now, sleep=clock.sleep)

    assert reused.status == "rejected"
    assert reused.reason == "accepted receipt corruption: original body is missing"
    assert reused.pointer_updated is False


def test_corrupt_normalized_hash_is_rejected_even_when_body_is_intact(tmp_path: Path) -> None:
    clock = Clock()
    result = collect_daily_cash(SESSION, tmp_path, transport=lambda u, _c, _r: _response(u, _body()), now=clock.now, sleep=clock.sleep)
    assert result.status == "accepted"
    normalized_path = tmp_path / "requests" / SESSION.isoformat() / "attempt-001" / "normalized.json"
    normalized = json.loads(normalized_path.read_text())
    normalized["rows"][0]["close"] = 999.0
    normalized_path.write_text(json.dumps(normalized), encoding="utf-8")

    reused = collect_daily_cash(SESSION, tmp_path, transport=lambda *_args: (_ for _ in ()).throw(AssertionError("must not refetch")), now=clock.now, sleep=clock.sleep)

    assert reused.reason == "accepted receipt corruption: normalized file SHA-256 mismatch"


def test_last_modified_uses_exchange_date_and_retrieval_time(tmp_path: Path) -> None:
    clock = Clock()
    later = collect_daily_cash(
        SESSION,
        tmp_path / "later",
        transport=lambda u, _c, _r: _response(u, _body(), headers={"Last-Modified": "Tue, 15 Sep 2026 12:00:00 GMT"}),
        now=clock.now,
        sleep=clock.sleep,
    )
    earlier = collect_daily_cash(
        SESSION,
        tmp_path / "earlier",
        transport=lambda u, _c, _r: _response(u, _body(), headers={"Last-Modified": "Sun, 13 Sep 2026 12:00:00 GMT"}),
        now=clock.now,
        sleep=clock.sleep,
    )
    future = collect_daily_cash(
        SESSION,
        tmp_path / "future",
        transport=lambda u, _c, _r: _response(
            u,
            _body(),
            headers={"Last-Modified": "Wed, 16 Sep 2026 12:00:00 GMT"},
            retrieved_at="2026-09-15T18:00:00+00:00",
        ),
        now=clock.now,
        sleep=clock.sleep,
    )

    assert later.status == "accepted"
    assert earlier.reason == "stale Last-Modified date 2026-09-13"
    assert future.reason == "Last-Modified is later than actual retrieval time"


def test_fractional_share_volume_is_rejected(tmp_path: Path) -> None:
    clock = Clock()
    result = collect_daily_cash(
        SESSION,
        tmp_path,
        transport=lambda u, _c, _r: _response(u, _body(volume="1200.5")),
        now=clock.now,
        sleep=clock.sleep,
    )

    assert result.status == "rejected"
    assert result.reason == "Full Bhavdata contains non-integer TTL_TRD_QNTY"


def _udiff_zip(session: date = SESSION) -> bytes:
    row = {
        "TradDt": session.isoformat(),
        "BizDt": session.isoformat(),
        "Sgmt": "CM",
        "Src": "NSE",
        "FinInstrmTp": "STK",
        "FinInstrmId": "16921",
        "ISIN": "INE144J01027",
        "TckrSymb": "20MICRONS",
        "SctySrs": "EQ",
        "OpnPric": "207.98",
        "HghPric": "208.77",
        "LwPric": "198.51",
        "ClsPric": "201.29",
        "PrvsClsgPric": "207.86",
        "TtlTradgVol": "111270",
        "TtlTrfVal": "22530502.12",
        "TtlNbOfTxsExctd": "1000",
        "NewBrdLotQty": "1",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20260914_F_0000.csv", pd.DataFrame([row]).to_csv(index=False))
    return buffer.getvalue()


class _FakeReadResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.reads = 0

    def read(self, _amount: int | None = None) -> bytes:
        self.reads += 1
        return self._chunks.pop(0) if self._chunks else b""


def test_read_bounded_without_deadline_reads_the_whole_body() -> None:
    response = _FakeReadResponse([b"abc"])
    assert daily_cash_module._read_bounded(response, 30.0, None) == b"abc"
    assert response.reads == 1


def test_read_bounded_stops_once_the_absolute_deadline_passes(monkeypatch) -> None:
    clock = {"now": datetime(2026, 9, 15, tzinfo=timezone.utc)}
    monkeypatch.setattr(daily_cash_module, "_utc_now", lambda: clock["now"])
    response = _FakeReadResponse([b"first", b"second"])

    def slow_read(_amount: int | None = None) -> bytes:
        value = response._chunks.pop(0) if response._chunks else b""
        response.reads += 1
        clock["now"] = clock["now"] + timedelta(seconds=100)
        return value

    response.read = slow_read  # type: ignore[method-assign]
    deadline = datetime(2026, 9, 15, 0, 0, 30, tzinfo=timezone.utc)
    with pytest.raises(TimeoutError):
        daily_cash_module._read_bounded(response, 30.0, deadline)
    assert response.reads == 1


def test_read_bounded_fails_closed_when_the_deadline_already_passed(monkeypatch) -> None:
    monkeypatch.setattr(
        daily_cash_module, "_utc_now", lambda: datetime(2026, 9, 15, tzinfo=timezone.utc)
    )
    response = _FakeReadResponse([b"abc"])
    deadline = datetime(2026, 9, 14, tzinfo=timezone.utc)
    with pytest.raises(TimeoutError):
        daily_cash_module._read_bounded(response, 30.0, deadline)
    assert response.reads == 0


def test_route_url_rejects_non_allowlisted_host() -> None:
    bogus = CashUrlRoute("bogus", lambda day: "https://example.invalid/x.csv", "full_bhavdata", "v1")
    with pytest.raises(ValueError):
        route_url(bogus, SESSION)


def test_alternate_route_has_its_own_budget_and_preserves_udiff_without_last(tmp_path: Path) -> None:
    clock = Clock()

    def failing(_url: str, _connect: float, _read: float) -> HttpResponse:
        raise URLError("read timed out")

    exhausted = collect_daily_cash(SESSION, tmp_path, transport=failing, now=clock.now, sleep=clock.sleep)
    assert exhausted.status == "exhausted"
    assert list((tmp_path / "requests" / SESSION.isoformat()).glob("attempt-*"))

    expected_url = nsearchives_udiff_cash_zip_url(SESSION)
    body = _udiff_zip(SESSION)

    def transport(url: str, _connect: float, _read: float) -> HttpResponse:
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/zip"},
            body=body,
            final_url=url,
            retrieved_at="2026-09-15T18:00:00+00:00",
        )

    result = collect_daily_cash(
        SESSION, tmp_path, transport=transport, now=clock.now, sleep=clock.sleep, route="nsearchives_udiff_zip"
    )

    assert result.status == "accepted"
    assert result.pointer_updated is True
    attempt = tmp_path / "requests" / SESSION.isoformat() / "nsearchives_udiff_zip" / "attempt-001"
    receipt = json.loads((attempt / "receipt.json").read_text())
    assert receipt["final_url"] == expected_url
    assert receipt["parser_version"] == "daily_cash.udiff_zip.v1"
    assert receipt["validation_result"]["status"] == "accepted"
    normalized = json.loads((attempt / "normalized.json").read_text())
    row = normalized["rows"][0]
    assert row["last"] is None
    assert row["last_available"] is False
    assert row["isin"] == "INE144J01027"
    assert row["instrument_id"] == "16921"
    assert row["traded_value"] == 22530502.12
