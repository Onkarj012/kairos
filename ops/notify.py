"""Local macOS notification banners through osascript, next to the Telegram channel in ops/telegram.py."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

APPROVALS_URL = "http://127.0.0.1:8765/approvals"
TIMEOUT_SECONDS = 10


class NotifyError(RuntimeError):
    """A banner could not be shown."""


class Notifier(Protocol):
    """What the runner needs from a notification channel."""

    def send(self, text: str) -> None: ...


def _applescript_text(text: str) -> str:
    """Flatten a message to one line and escape it for an AppleScript double-quoted string."""
    flat = text.replace("\n", " ")
    return flat.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True)
class Osascript:
    """Shows a banner titled Kairos through osascript."""

    def send(self, text: str) -> None:
        """Show one banner; a failed or hung osascript raises NotifyError."""
        script = f'display notification "{_applescript_text(text)}" with title "Kairos"'
        try:
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotifyError(f"osascript failed: {type(exc).__name__}") from None


def mac_notifier(env: dict[str, str]) -> Notifier | None:
    """Return the macOS channel, which is on unless KAIROS_MAC_NOTIFY is 0."""
    return None if env.get("KAIROS_MAC_NOTIFY") == "0" else Osascript()
