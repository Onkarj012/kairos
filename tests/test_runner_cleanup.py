from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ops.kite_login_support import DEFAULT_TOKEN_PATH, KiteError, save_token
from ops.account_core import AccountEvent
from ops.daily_account import PAPER_ACCOUNTS, DailyAccount
from serve.api import create_app
from scripts.kite_login import prepare_login_url


def test_login_url_uses_only_injected_credentials():
    assert prepare_login_url({
        "KITE_API_KEY": "synthetic key/1",
        "KITE_API_SECRET": "synthetic-secret",
    }) == "https://kite.zerodha.com/connect/login?v=3&api_key=synthetic%20key/1"


def test_login_url_requires_both_credentials_without_reading_env():
    with pytest.raises(KiteError, match="KITE_API_KEY and KITE_API_SECRET"):
        prepare_login_url({"KITE_API_KEY": "synthetic-key"})


def test_login_token_keeps_legacy_location_format_and_permissions(tmp_path: Path):
    path = tmp_path / "config" / "kite-token.json"
    login_date = date(2024, 12, 31)

    save_token(path, "synthetic-access-token", "synthetic-user", login_date)

    assert DEFAULT_TOKEN_PATH == Path.home() / ".config/kairos/kite-token.json"
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {
        "access_token": "synthetic-access-token",
        "user_id": "synthetic-user",
        "login_date": "2024-12-31",
    }


def test_dashboard_accounts_route_reads_synthetic_legacy_account(tmp_path: Path):
    accounts = tmp_path / "accounts"
    runner = tmp_path / "runner"
    spec = PAPER_ACCOUNTS[0]
    account = DailyAccount(accounts, spec)
    account.open(session="2024-01-02", verdict="synthetic")
    account.append([AccountEvent("SIGNAL", "2024-01-03", {"count": 0})])
    app = create_app(accounts_root=accounts, runner_root=runner)

    html = next(route.endpoint() for route in app.routes if getattr(route, "path", None) == "/accounts")

    assert "Paper accounts" in html
    assert spec.account_id in html
    assert "scripts/run_daily.py" not in html
