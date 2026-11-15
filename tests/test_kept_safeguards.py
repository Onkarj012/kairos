from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ops.daily_account import AccountSpec, DailyAccount
from ops.telegram import BotApi, TelegramError


def test_bot_turns_truncated_response_into_safe_error(monkeypatch):
    class Truncated:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"{")

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Truncated())
    with pytest.raises(TelegramError, match="IncompleteRead"):
        BotApi("123456:SYNTHETIC-TOKEN", "42").updates(None)


def test_bot_errors_and_repr_never_carry_token(monkeypatch):
    secret = "123456:SYNTHETIC-TOKEN"

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    bot = BotApi(secret, "42")
    with pytest.raises(TelegramError) as caught:
        bot.send("synthetic message")
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert secret not in repr(bot)


def test_account_refuses_legacy_holdout_session_without_writing(tmp_path: Path):
    account = DailyAccount(tmp_path, AccountSpec("paper-synthetic", "synthetic", 10_000))
    with pytest.raises(ValueError, match="sealed holdout"):
        account.open(session="2026-01-05", verdict="synthetic refusal")
    assert not account.path.exists()
