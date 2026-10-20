"""Focused Phase 02b corporate-action contract and PIT tests."""
from __future__ import annotations

import dataclasses
import errno
import json
import os
from pathlib import Path

import pytest

from scripts.data import fetch_nse_corporate_actions as ca_collector
from scripts.data import fetch_cohort_corporate_actions as cohort_collector

from contracts.corporate_actions import (
    CORPORATE_ACTION_SCHEMA,
    CorporateActionRecord,
    action_visible_at,
    adjust_price,
    adjustment_factor_at,
    corporate_actions_available_at,
)
from contracts.security_master import SecurityMasterRecord
from ingest.corporate_actions import ingest_corporate_actions


APPLE_SPLIT_PROVENANCE = {
    "source": "Apple Q3 2020 results, July 30, 2020",
    "source_url": "https://www.apple.com/newsroom/2020/07/apple-reports-third-quarter-results/",
    "evidence": "Apple approved a four-for-one split; record date August 24, 2020; split-adjusted trading August 31, 2020",
}


def _master(*, security_id: str = "sec_apple") -> tuple[SecurityMasterRecord, ...]:
    return (
        SecurityMasterRecord(
            security_id,
            "US0378331005",
            (("identity_key", "apple-common"),),
            "AAPL",
            "EQ",
            "NASDAQ",
            "1980-12-12",
            None,
            "1980-12-12",
            None,
            0.01,
            None,
            None,
            "2020-01-01T00:00:00+00:00",
            "fixture-security-master",
            "security-master-source-1",
            None,
        ),
    )


def _split_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "security_id": "sec_apple",
        "symbol": "AAPL",
        "action_type": "split",
        "announcement_date": "2020-07-30",
        "ex_date": "2020-08-31",
        "record_date": "2020-08-24",
        "adjustment_factor": 0.25,
        "factor_provenance": APPLE_SPLIT_PROVENANCE["source_url"],
        "available_at": "2020-07-30T23:00:00+00:00",
        "source": APPLE_SPLIT_PROVENANCE["source"],
        "source_hash": "apple-newsroom-fixture-2020-07-30",
    }
    row.update(overrides)
    return row


def test_contract_schema_is_frozen_and_contains_identity_and_provenance() -> None:
    assert tuple(field.name for field in dataclasses.fields(CorporateActionRecord)) == tuple(
        name for name, _ in CORPORATE_ACTION_SCHEMA
    )
    action = CorporateActionRecord(
        "sec-1", "merger", "2024-01-02", "2024-02-01", None, None, 1.0,
        "official filing", "2024-01-02T12:00:00+00:00", "source", "source-hash", "ca-row", "row-hash",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        action.security_id = "changed"  # type: ignore[misc]


def test_identity_join_uses_canonical_id_and_symbol_is_only_a_check() -> None:
    result = ingest_corporate_actions([_split_row()], _master())
    assert result.corporate_action[0].security_id == "sec_apple"
    assert result.corporate_action[0].row_identity.startswith("ca_")
    assert result.corporate_action[0].row_hash

    with pytest.raises(ValueError, match="canonical or explicit source identity"):
        ingest_corporate_actions([_split_row(security_id=None, symbol="AAPL")], _master())


def test_explicit_identity_key_joins_to_master_without_ticker_resolution() -> None:
    result = ingest_corporate_actions(
        [_split_row(security_id=None, identity_key="apple-common")],
        _master(),
    )
    assert result.corporate_action[0].security_id == "sec_apple"


def test_visibility_requires_source_availability_and_announcement_date() -> None:
    action = ingest_corporate_actions([_split_row()], _master()).corporate_action[0]
    assert not action_visible_at(action, "2020-07-29T23:59:59+00:00")
    assert corporate_actions_available_at((action,), "2020-08-01T00:00:00+00:00") == (action,)
    assert corporate_actions_available_at((action,), "2020-07-30T22:59:59+00:00") == ()


def test_known_apple_four_for_one_split_adjusts_only_pre_ex_prices() -> None:
    action = ingest_corporate_actions([_split_row()], _master()).corporate_action[0]
    assert action.factor_provenance == APPLE_SPLIT_PROVENANCE["source_url"]
    assert adjustment_factor_at((action,), "sec_apple", "2020-08-28") == pytest.approx(0.25)
    assert adjust_price(400.0, "sec_apple", "2020-08-28", (action,)) == pytest.approx(100.0)
    assert adjustment_factor_at((action,), "sec_apple", "2020-08-31") == pytest.approx(1.0)


def test_merger_and_other_actions_use_the_same_pit_factor_rule() -> None:
    rows = [
        _split_row(
            action_type="merger",
            announcement_date="2021-01-01",
            ex_date="2021-02-01",
            record_date=None,
            adjustment_factor=0.5,
            source="merger-fixture",
            source_hash="merger-source-1",
        ),
        _split_row(
            action_type="other",
            announcement_date="2022-01-01",
            ex_date="2022-02-01",
            record_date=None,
            adjustment_factor=2.0,
            source="other-fixture",
            source_hash="other-source-1",
        ),
    ]
    actions = ingest_corporate_actions(rows, _master()).corporate_action
    assert adjustment_factor_at(actions, "sec_apple", "2021-01-31") == pytest.approx(0.5)
    assert adjustment_factor_at(actions, "sec_apple", "2022-01-01") == pytest.approx(2.0)
    assert adjustment_factor_at(actions, "sec_apple", "2022-02-01") == pytest.approx(1.0)


def test_conflicting_same_identity_rows_fail_closed() -> None:
    with pytest.raises(ValueError, match="conflicting corporate action"):
        ingest_corporate_actions(
            [_split_row(adjustment_factor=0.25), _split_row(adjustment_factor=0.2)],
            _master(),
        )


def test_invalid_factor_and_unknown_identity_fail_closed() -> None:
    with pytest.raises(ValueError, match="positive"):
        ingest_corporate_actions([_split_row(adjustment_factor=0.0)], _master())
    with pytest.raises(ValueError, match="unknown canonical"):
        ingest_corporate_actions([_split_row(security_id="sec_missing")], _master())


def test_public_corporate_action_url_rejects_percent_encoding_and_unapproved_query() -> None:
    with pytest.raises(ValueError):
        ca_collector._validate_url(
            "https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol=SBIN&foo=bar"
        )
    canonical = ca_collector._query_url("M&M", ca_collector.START_DATE, ca_collector.END_DATE)
    assert "symbol=M%26M" in canonical
    assert ca_collector._validate_url(canonical) == canonical
    for variant in ("M&M", "M%26m", "M%2526M"):
        with pytest.raises(ValueError):
            ca_collector._validate_url(canonical.replace("M%26M", variant))


def test_demerger_is_classified_before_merger() -> None:
    assert ca_collector._action_type("Scheme of demerger and merger") == "demerger"


def test_dividend_is_classified_explicitly() -> None:
    assert ca_collector._action_type("Final dividend 2024-25") == "dividend"


def test_distinct_report_root_is_rejected_before_network_or_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ca_collector, "_request", lambda *args, **kwargs: pytest.fail("network must not start"))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="report_root must be omitted or equal"):
        ca_collector.acquire(output_root=output, report_root=tmp_path / "report")
    assert not output.exists()
    assert not (tmp_path / "report").exists()


def test_fetch_output_guard_allows_repo_derived_data_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented data directory is allowed while other repo paths stay blocked."""
    repo = tmp_path / "repo"
    (repo / "derived_data").mkdir(parents=True)
    monkeypatch.setattr(cohort_collector, "REPO_ROOT", repo)

    cohort_collector._validate_output_root(repo / "derived_data" / "corporate-actions")
    with pytest.raises(ValueError, match="outside the git tree"):
        cohort_collector._validate_output_root(repo / "scratch-output")


def test_acquire_cli_defaults_to_self_contained_report() -> None:
    args = ca_collector.parse_args(["--acquire"])
    assert args.output_root == ca_collector.DEFAULT_OUTPUT_ROOT
    assert args.report_root is None


def test_create_staging_cleans_candidate_when_open_fails_after_mkdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output"
    sibling = tmp_path / ".output.sibling"
    sibling.mkdir()
    (sibling / "sentinel").write_bytes(b"keep")
    real_mkdir = ca_collector.os.mkdir
    real_open = ca_collector.os.open
    real_close = ca_collector.os.close
    candidate: dict[str, object] = {}
    closed: list[int] = []
    injected = OSError("injected staging open failure")

    def record_mkdir(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)
        if path.startswith(".output."):
            candidate.update(name=path, parent_fd=dir_fd)

    def fail_staging_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if path == candidate.get("name") and dir_fd == candidate.get("parent_fd"):
            raise injected
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def record_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(ca_collector.os, "mkdir", record_mkdir)
    monkeypatch.setattr(ca_collector.os, "open", fail_staging_open)
    monkeypatch.setattr(ca_collector.os, "close", record_close)
    with pytest.raises(OSError) as error:
        ca_collector._create_staging(output)

    assert error.value is injected
    assert candidate["parent_fd"] in closed
    assert not (tmp_path / str(candidate["name"])).exists()
    assert (sibling / "sentinel").read_bytes() == b"keep"

    monkeypatch.undo()
    output = tmp_path / "output-fstat"
    sibling = tmp_path / ".output-fstat.sibling"
    sibling.mkdir()
    (sibling / "sentinel").write_bytes(b"keep")
    real_mkdir = ca_collector.os.mkdir
    real_open = ca_collector.os.open
    real_fstat = ca_collector.os.fstat
    real_close = ca_collector.os.close
    candidate: dict[str, object] = {}
    closed: list[int] = []
    injected = OSError("injected staging fstat failure")

    def record_mkdir(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)
        if path.startswith(".output-fstat."):
            candidate.update(name=path, parent_fd=dir_fd)

    def record_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == candidate.get("name") and dir_fd == candidate.get("parent_fd"):
            candidate["staging_fd"] = fd
        return fd

    def fail_staging_fstat(fd: int) -> os.stat_result:
        if fd == candidate.get("staging_fd"):
            raise injected
        return real_fstat(fd)

    def record_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(ca_collector.os, "mkdir", record_mkdir)
    monkeypatch.setattr(ca_collector.os, "open", record_open)
    monkeypatch.setattr(ca_collector.os, "fstat", fail_staging_fstat)
    monkeypatch.setattr(ca_collector.os, "close", record_close)
    with pytest.raises(OSError) as error:
        ca_collector._create_staging(output)

    assert error.value is injected
    assert candidate["staging_fd"] in closed
    assert candidate["parent_fd"] in closed
    assert not (tmp_path / str(candidate["name"])).exists()
    assert (sibling / "sentinel").read_bytes() == b"keep"


def test_owner_write_zero_progress_raises_once_and_runs_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[int] = []
    cleanup_calls: list[str] = []

    def no_progress(fd: int, payload: bytes) -> int:
        writes.append(fd)
        return 0

    def record_cleanup(parent_fd: int, name: str, owner: str, expected_stat: os.stat_result) -> None:
        del parent_fd, owner, expected_stat
        cleanup_calls.append(name)

    monkeypatch.setattr(ca_collector.os, "write", no_progress)
    monkeypatch.setattr(ca_collector, "_cleanup_owned_staging_at", record_cleanup)
    with pytest.raises(OSError, match="owner-token write made no progress") as error:
        ca_collector._create_staging(tmp_path / "output")

    assert error.value.errno == errno.EIO
    assert len(writes) == 1
    assert cleanup_calls
    assert not (tmp_path / "output").exists()


def test_owner_write_and_fsync_errors_remain_primary_when_close_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run_case(output: Path, *, fail_at: str) -> None:
        real_open = ca_collector.os.open
        real_close = ca_collector.os.close
        real_write = ca_collector.os.write
        real_fsync = ca_collector.os.fsync
        owner_fd: int | None = None
        close_calls: list[int] = []
        primary = OSError(f"injected owner {fail_at} failure")
        close_error = OSError("injected owner close failure")

        def record_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            nonlocal owner_fd
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == ".owner":
                owner_fd = fd
            return fd

        def fail_write(fd: int, payload: bytes) -> int:
            if fail_at == "write" and fd == owner_fd:
                raise primary
            return real_write(fd, payload)

        def fail_fsync(fd: int) -> None:
            if fail_at == "fsync" and fd == owner_fd:
                raise primary
            real_fsync(fd)

        def fail_owner_close(fd: int) -> None:
            close_calls.append(fd)
            if fd == owner_fd:
                try:
                    real_close(fd)
                finally:
                    raise close_error
            real_close(fd)

        monkeypatch.setattr(ca_collector.os, "open", record_open)
        monkeypatch.setattr(ca_collector.os, "write", fail_write)
        monkeypatch.setattr(ca_collector.os, "fsync", fail_fsync)
        monkeypatch.setattr(ca_collector.os, "close", fail_owner_close)
        with pytest.raises(OSError) as error:
            ca_collector._create_staging(output)
        assert error.value is primary
        assert any("close(" in note and "owner close failure" in note for note in error.value.__notes__)
        assert owner_fd is not None
        assert close_calls
        monkeypatch.undo()

    run_case(tmp_path / "write-output", fail_at="write")
    run_case(tmp_path / "fsync-output", fail_at="fsync")


def test_transition_close_failure_closes_new_fd_once_without_retrying_old_fd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "child").mkdir()
    parent_fd = os.open(tmp_path, ca_collector._directory_flags())
    real_dup = ca_collector.os.dup
    real_open = ca_collector.os.open
    real_close = ca_collector.os.close
    old_fd: int | None = None
    new_fd: int | None = None
    close_calls: list[int] = []
    injected = OSError("injected transition close failure")

    def record_dup(fd: int) -> int:
        nonlocal old_fd
        old_fd = real_dup(fd)
        return old_fd

    def record_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal new_fd
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "child":
            new_fd = fd
        return fd

    def fail_old_close(fd: int) -> None:
        close_calls.append(fd)
        if fd == old_fd:
            try:
                real_close(fd)
            finally:
                raise injected
        real_close(fd)

    monkeypatch.setattr(ca_collector.os, "dup", record_dup)
    monkeypatch.setattr(ca_collector.os, "open", record_open)
    monkeypatch.setattr(ca_collector.os, "close", fail_old_close)
    with pytest.raises(OSError) as error:
        ca_collector._open_relative_directory(parent_fd, ("child",), create=False)
    monkeypatch.undo()
    os.close(parent_fd)

    assert error.value is injected
    assert old_fd is not None and new_fd is not None
    assert close_calls == [old_fd, new_fd]


def test_cleanup_child_close_failure_still_removes_child_and_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staging, owner, staging_stat = ca_collector._create_staging(tmp_path / "output")
    (staging / "child").mkdir()
    (staging / "child" / "payload").write_bytes(b"fixture")
    real_open = ca_collector.os.open
    real_close = ca_collector.os.close
    child_fd: int | None = None
    close_calls: list[int] = []

    def record_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal child_fd
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "child":
            child_fd = fd
        return fd

    def fail_child_close(fd: int) -> None:
        close_calls.append(fd)
        if fd == child_fd:
            try:
                real_close(fd)
            finally:
                raise OSError("injected cleanup child close failure")
        real_close(fd)

    monkeypatch.setattr(ca_collector.os, "open", record_open)
    monkeypatch.setattr(ca_collector.os, "close", fail_child_close)
    ca_collector._cleanup_owned_staging(staging, owner, staging_stat)

    assert child_fd is not None
    assert close_calls.count(child_fd) == 1
    assert not staging.exists()


def test_close_failure_is_classified_by_rename_state_and_retains_committed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "post-output"
    staging, owner, staging_stat = ca_collector._create_staging(output)
    ca_collector._stage_bytes(staging, "payload", b"fixture")
    real_open = ca_collector.os.open
    real_close = ca_collector.os.close
    staging_fd: int | None = None
    failed = False

    def record_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal staging_fd
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == staging.name:
            staging_fd = fd
        return fd

    def fail_post_commit_close(fd: int) -> None:
        nonlocal failed
        if fd == staging_fd and output.exists() and not failed:
            failed = True
            try:
                real_close(fd)
            finally:
                raise OSError("injected post-rename staging close failure")
        real_close(fd)

    monkeypatch.setattr(ca_collector.os, "open", record_open)
    monkeypatch.setattr(ca_collector.os, "close", fail_post_commit_close)
    with pytest.raises(ca_collector.NSEPublicationError) as error:
        ca_collector._publish_staged(staging, output, owner=owner, staging_stat=staging_stat)
    assert error.value.committed is True
    assert (output / "payload").read_bytes() == b"fixture"
    assert not staging.exists()

    monkeypatch.undo()
    pre_output = tmp_path / "pre-output"
    pre_staging, pre_owner, pre_stat = ca_collector._create_staging(pre_output)
    ca_collector._stage_bytes(pre_staging, "payload", b"fixture")
    pre_staging_fd: int | None = None
    close_after_rename_attempt = False
    real_open = ca_collector.os.open
    real_close = ca_collector.os.close

    def record_pre_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal pre_staging_fd
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == pre_staging.name:
            pre_staging_fd = fd
        return fd

    def fail_pre_commit_close(fd: int) -> None:
        if fd == pre_staging_fd and close_after_rename_attempt:
            try:
                real_close(fd)
            finally:
                raise OSError("injected pre-rename staging close failure")
        real_close(fd)

    def fail_before_rename(source: Path, target: Path) -> None:
        nonlocal close_after_rename_attempt
        close_after_rename_attempt = True
        raise OSError("injected rename failure")

    monkeypatch.setattr(ca_collector.os, "open", record_pre_open)
    monkeypatch.setattr(ca_collector.os, "close", fail_pre_commit_close)
    monkeypatch.setattr(ca_collector, "_atomic_noreplace_directory", fail_before_rename)
    with pytest.raises(OSError, match="injected rename failure") as pre_error:
        ca_collector._publish_staged(pre_staging, pre_output, owner=pre_owner, staging_stat=pre_stat)
    assert not pre_output.exists()
    assert pre_staging.exists()
    assert pre_error.value.__notes__
    monkeypatch.undo()


def test_publication_cleanup_is_ownership_safe_and_post_rename_fault_retains_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output"
    staging, owner, staging_stat = ca_collector._create_staging(output)
    ca_collector._stage_bytes(staging, "payload", b"fixture")
    real_publish = ca_collector._atomic_noreplace_directory

    def rename_then_fail(source: Path, target: Path) -> None:
        real_publish(source, target)
        raise RuntimeError("injected after rename")

    monkeypatch.setattr(ca_collector, "_atomic_noreplace_directory", rename_then_fail)
    with pytest.raises(ca_collector.NSEPublicationError, match="committed=True") as error:
        ca_collector._publish_staged(staging, output, owner=owner, staging_stat=staging_stat)
    assert error.value.committed is True
    assert error.value.durability_confirmed is False
    assert (output / "payload").read_bytes() == b"fixture"
    assert not staging.exists()


def test_publication_cleanup_ignores_wrong_owner(tmp_path: Path) -> None:
    output = tmp_path / "output"
    staging, owner, staging_stat = ca_collector._create_staging(output)
    ca_collector._stage_bytes(staging, "sentinel", b"keep")
    ca_collector._cleanup_owned_staging(staging, owner + "wrong", staging_stat)
    assert (staging / "sentinel").read_bytes() == b"keep"


def test_publication_fsyncs_parent_and_reports_post_rename_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output"
    staging, owner, staging_stat = ca_collector._create_staging(output)
    ca_collector._stage_bytes(staging, "payload", b"fixture")
    real_fsync = ca_collector.os.fsync
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    parent_fsynced = False

    def record_parent(fd: int) -> None:
        nonlocal parent_fsynced
        try:
            identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        except OSError:
            identity = None
        if identity == parent_identity:
            parent_fsynced = True
        real_fsync(fd)

    monkeypatch.setattr(ca_collector.os, "fsync", record_parent)
    publication = ca_collector._publish_staged(staging, output, owner=owner, staging_stat=staging_stat)
    assert publication["committed"] is True
    assert publication["durability_confirmed"] is True
    assert parent_fsynced
    assert not (output / ".owner").exists()

    output_with_fault = tmp_path / "output-with-fault"
    staging, owner, staging_stat = ca_collector._create_staging(output_with_fault)
    ca_collector._stage_bytes(staging, "payload", b"fixture")

    def fail_parent(fd: int) -> None:
        try:
            identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        except OSError:
            identity = None
        if identity == parent_identity:
            raise OSError("injected parent fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(ca_collector.os, "fsync", fail_parent)
    with pytest.raises(ca_collector.NSEPublicationError, match="durability_confirmed=False") as error:
        ca_collector._publish_staged(staging, output_with_fault, owner=owner, staging_stat=staging_stat)
    assert error.value.committed is True
    assert error.value.durability_confirmed is False
    assert (output_with_fault / "payload").read_bytes() == b"fixture"
    assert not (output_with_fault / ".owner").exists()


def test_acquire_passes_exact_staging_ownership_to_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_request(url: str, *, accept: str, timeout: float = 30.0) -> dict[str, object]:
        symbol = url.split("symbol=", 1)[1].split("&", 1)[0].replace("%26", "&")
        body = json.dumps([{"symbol": symbol, "series": "EQ", "isin": "INE000A01001", "exDate": "01-Jan-2020"}]).encode()
        return {"requested_url": url, "final_url": url, "status": 200, "headers": [], "content_type": "application/json", "retrieved_at": "2026-09-01T00:00:00+00:00", "body": body, "error": None}

    def fake_publish(staging: Path, root: Path, *, owner: str | None = None, staging_stat: os.stat_result | None = None) -> dict[str, object]:
        seen.update(staging=staging, root=root, owner=owner, staging_stat=staging_stat)
        return {"committed": True, "idempotent": False, "report_co_published": True, "durability_confirmed": True}

    monkeypatch.setattr(ca_collector, "_request", fake_request)
    monkeypatch.setattr(ca_collector, "_publish_staged", fake_publish)
    ca_collector.acquire(output_root=tmp_path / "output", report_root=None)
    assert isinstance(seen["owner"], str) and seen["owner"]
    assert isinstance(seen["staging_stat"], os.stat_result)


def test_cleanup_requires_owner_marker(tmp_path: Path) -> None:
    staging, owner, staging_stat = ca_collector._create_staging(tmp_path / "output")
    (staging / ".owner").unlink()
    (staging / "sentinel").write_bytes(b"keep")
    ca_collector._cleanup_owned_staging(staging, owner, staging_stat)
    assert staging.exists()
    assert (staging / "sentinel").read_bytes() == b"keep"


def test_cleanup_rejects_replaced_staging_inode(tmp_path: Path) -> None:
    staging, owner, staging_stat = ca_collector._create_staging(tmp_path / "output")
    (staging / "sentinel").write_bytes(b"original")
    replacement = tmp_path / "original-staging"
    staging.rename(replacement)
    staging.mkdir()
    (staging / ".owner").write_text(owner, encoding="ascii")
    (staging / "sentinel").write_bytes(b"replacement")
    ca_collector._cleanup_owned_staging(staging, owner, staging_stat)
    assert (staging / "sentinel").read_bytes() == b"replacement"
    assert (replacement / "sentinel").read_bytes() == b"original"


def test_stage_bytes_rejects_unsafe_relative_names(tmp_path: Path) -> None:
    staging, _, _ = ca_collector._create_staging(tmp_path / "output")
    for name in ("/absolute", "../escape", "a/../escape", "a//b", "a\\b", "C:/escape", ".", "a/."):
        with pytest.raises(ValueError):
            ca_collector._stage_bytes(staging, name, b"x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (staging / "parent-link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        ca_collector._stage_bytes(staging, "parent-link/file", b"x")
    (staging / "final-link").symlink_to(outside / "final")
    with pytest.raises(OSError):
        ca_collector._stage_bytes(staging, "final-link", b"x")


def test_stage_and_root_symlinks_are_rejected_without_resolution(tmp_path: Path) -> None:
    output = tmp_path / "output"
    staging, owner, staging_stat = ca_collector._create_staging(output)
    root_target = tmp_path / "root-target"
    root_target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(root_target, target_is_directory=True)
    with pytest.raises(FileExistsError):
        ca_collector._publish_staged(staging, root_link, owner=owner, staging_stat=staging_stat)
    assert staging.exists()

    output_two = tmp_path / "output-two"
    staging_two, owner_two, staging_stat_two = ca_collector._create_staging(output_two)
    staging_link = tmp_path / "staging-link"
    staging_link.symlink_to(staging_two, target_is_directory=True)
    with pytest.raises(OSError):
        ca_collector._publish_staged(staging_link, output_two, owner=owner_two, staging_stat=staging_stat_two)
    assert staging_two.exists()


def test_collision_preserves_existing_root_and_owned_staging(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "sentinel").write_bytes(b"existing")
    staging, owner, staging_stat = ca_collector._create_staging(output)
    ca_collector._stage_bytes(staging, "sentinel", b"staged")
    with pytest.raises(FileExistsError):
        ca_collector._publish_staged(staging, output, owner=owner, staging_stat=staging_stat)
    assert (output / "sentinel").read_bytes() == b"existing"
    assert (staging / "sentinel").read_bytes() == b"staged"


def test_failure_before_rename_preserves_owned_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output"
    staging, owner, staging_stat = ca_collector._create_staging(output)
    ca_collector._stage_bytes(staging, "sentinel", b"staged")

    def fail_before_rename(source: Path, target: Path) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(ca_collector, "_atomic_noreplace_directory", fail_before_rename)
    with pytest.raises(OSError, match="injected rename failure"):
        ca_collector._publish_staged(staging, output, owner=owner, staging_stat=staging_stat)
    assert not output.exists()
    assert (staging / ".owner").read_text(encoding="ascii") == owner
    assert (staging / "sentinel").read_bytes() == b"staged"


def test_public_corporate_action_response_requires_exact_semantics() -> None:
    url = ca_collector._query_url("SBIN", ca_collector.START_DATE, ca_collector.END_DATE)
    accepted, reasons, rows = ca_collector._validate_response(
        {
            "status": 200,
            "final_url": url,
            "content_type": "text/html",
            "body": b'[{"symbol":"SBIN","exDate":"16-May-2025"}]',
        },
        expected_url=url,
        symbol="SBIN",
        start=ca_collector.START_DATE,
        end=ca_collector.END_DATE,
    )
    assert not accepted
    assert "content_type_not_json:text/html" in reasons
    assert rows
