"""Log in to Kite on this Mac.

Set the Kite app redirect URL to http://127.0.0.1:5010/kite/callback.
This login runs on the Mac. Phone login waits for the stage 5 server.
"""
from __future__ import annotations

import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

REPO = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO))

from ops.daily_runner import IST
from ops.kite_broker import DEFAULT_TOKEN_PATH, KiteError, save_token
from ops.telegram import read_env


def login(env_path: Path = REPO / ".env", token_path: Path = DEFAULT_TOKEN_PATH) -> None:
    """Accept one local callback and save today's Kite access token."""
    env = read_env(env_path)
    api_key, api_secret = env.get("KITE_API_KEY", ""), env.get("KITE_API_SECRET", "")
    if not api_key or not api_secret:
        raise KiteError("KITE_API_KEY and KITE_API_SECRET must be set in .env")
    print(f"https://kite.zerodha.com/connect/login?v=3&api_key={quote(api_key)}")

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            """Exchange one callback request token and save it for today."""
            parts = urlsplit(self.path)
            request_token = parse_qs(parts.query).get("request_token", [""])[0]
            if parts.path != "/kite/callback" or not request_token:
                self.send_error(400, "Kite callback requires request_token")
                self.server.server_close()
                return
            try:
                from kiteconnect import KiteConnect

                result = KiteConnect(api_key=api_key).generate_session(request_token, api_secret=api_secret)
                token, user_id = str(result["access_token"]), str(result["user_id"])
                save_token(token_path, token, user_id, datetime.now(IST).date())
            except Exception as exc:
                self.send_error(500, f"Kite login failed: {type(exc).__name__}")
                self.server.server_close()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Kite login complete. You can close this tab.")
            print(f"logged in as {user_id}")
            self.server.server_close()

        def log_message(self, _format: str, *_args: object) -> None:
            """Suppress request logging because callback URLs carry a request token."""

    server = HTTPServer(("127.0.0.1", 5010), CallbackHandler)
    server.handle_request()
    server.server_close()


if __name__ == "__main__":
    login()
