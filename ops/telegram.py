"""Telegram Bot API over stdlib HTTPS: intent messages with Approve and Reject buttons, their answers, and halt posts.

There is no always-on process. The signal job sends, the place job reads button
answers through ``getUpdates``, and every job posts the halts it opened. The bot
token is read from ``.env`` and never logged or put in an error message.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

API = "https://api.telegram.org"
TIMEOUT_SECONDS = 20
CALLBACK_LIMIT = 64
ACTIONS = {"a": "approved", "r": "rejected"}


class TelegramError(RuntimeError):
    """A Bot API call failed; the message never carries the token."""


@dataclass(frozen=True)
class Button:
    """One inline keyboard button."""

    text: str
    data: str


@dataclass(frozen=True)
class Callback:
    """A pressed Approve or Reject button."""

    update_id: int
    callback_id: str
    decision: str
    account_id: str
    intent_id: str


class Telegram(Protocol):
    """What the runner needs from a bot."""

    chat_id: str

    def send(self, text: str, rows: list[list[Button]] | None = None) -> None: ...
    def updates(self, offset: int | None) -> list[dict[str, object]]: ...
    def answer(self, callback_id: str, text: str) -> None: ...


def read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file; process environment values win."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


@dataclass(frozen=True)
class BotApi:
    """The real bot, posting to one chat."""

    token: str
    chat_id: str

    def __repr__(self) -> str:
        return f"BotApi(chat_id={self.chat_id!r})"

    def _call(self, method: str, payload: dict[str, object]) -> object:
        """POST one Bot API method and return its result."""
        request = urllib.request.Request(
            f"{API}/bot{self.token}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TelegramError(f"{method} failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
        if not isinstance(body, dict) or not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else None
            raise TelegramError(f"{method} was refused: {description or 'no description'}")
        return body.get("result")

    def send(self, text: str, rows: list[list[Button]] | None = None) -> None:
        """Send a message, with inline buttons when given."""
        payload: dict[str, object] = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if rows:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": b.text, "callback_data": b.data} for b in row] for row in rows]}
        self._call("sendMessage", payload)

    def updates(self, offset: int | None) -> list[dict[str, object]]:
        """Return pending callback updates; an offset confirms everything before it."""
        payload: dict[str, object] = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        return [u for u in result if isinstance(u, dict)] if isinstance(result, list) else []

    def answer(self, callback_id: str, text: str) -> None:
        """Show a short reply on the pressed button."""
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


def load_bot(env_path: Path) -> BotApi | None:
    """Return the configured bot, or None when the token or chat id is missing."""
    env = read_env(env_path)
    token, chat_id = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    return BotApi(token, chat_id) if token and chat_id else None


def callback_data(decision: str, account_id: str, intent_id: str) -> str | None:
    """Encode a button press, or None when it would pass Telegram's 64-byte limit."""
    data = f"{decision}:{account_id}:{intent_id}"
    return data if len(data.encode("utf-8")) <= CALLBACK_LIMIT else None


def parse_callback(update: dict[str, object]) -> Callback | None:
    """Decode an Approve or Reject press from the configured chat; anything else is None."""
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return None
    parts = str(query.get("data", "")).split(":", 2)
    if len(parts) != 3 or parts[0] not in ACTIONS:
        return None
    return Callback(int(str(update["update_id"])), str(query["id"]), ACTIONS[parts[0]], parts[1], parts[2])


def callback_chat(update: dict[str, object]) -> str | None:
    """Return the chat id a button press came from."""
    query = update.get("callback_query")
    message = query.get("message") if isinstance(query, dict) else None
    chat = message.get("chat") if isinstance(message, dict) else None
    return str(chat["id"]) if isinstance(chat, dict) and "id" in chat else None


def read_offset(path: Path) -> int | None:
    """Return the next update id to ask for, or None before the first read."""
    return int(json.loads(path.read_text(encoding="utf-8"))["offset"]) if path.exists() else None


def write_offset(path: Path, offset: int) -> None:
    """Remember the next update id so answered presses are confirmed and not read again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"offset": offset}) + "\n", encoding="utf-8")
    temporary.replace(path)
