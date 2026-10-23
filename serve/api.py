"""FastAPI recommendations API."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from fastapi import Request

if TYPE_CHECKING:
    from ops.daily_runner import Calendar


def trusted_origin(headers: Mapping[str, str], port: int) -> bool:
    """Return whether a request came from this dashboard, judged by Origin or by Referer when Origin is absent."""
    origin = headers.get("origin")
    if origin is None and headers.get("referer"):
        referer = urlsplit(headers["referer"])
        origin = f"{referer.scheme}://{referer.netloc}"
    return origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def _latest_recommendations(root: str | Path | None = None, path: str | Path | None = None) -> list[dict]:
    target = Path(path) if path is not None else None
    if target is None and root is not None:
        rec_dir = Path(root) / "recommendations"
        files = sorted(rec_dir.glob("*.json")) if rec_dir.exists() else []
        target = files[-1] if files else None
    if target is None or not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return list(payload.get("recommendations") or payload.get("picks") or [])


def _read_evidence(root: str | Path | None, token: str) -> dict:
    if root is None:
        return {"found": False, "error": "evidence root not configured"}
    safe = unquote(token)
    base = Path(root).resolve()
    candidates = [base / "evidence" / safe, base / "evidence" / f"{safe}.json"]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(base.resolve())):
                continue
            if resolved.exists() and resolved.is_file():
                if resolved.suffix == ".json":
                    return {"found": True, "path": str(resolved), "evidence": json.loads(resolved.read_text(encoding="utf-8"))}
                return {"found": True, "path": str(resolved), "text": resolved.read_text(encoding="utf-8")}
        except OSError:
            continue
    return {"found": False, "token": token}


def create_app(recommendations=None, *args, root=None, recommendations_path=None,
               accounts_root: Path | None = None, runner_root: Path | None = None,
               port: int = 8765, calendar: "Calendar | None" = None, clock: Callable[[], datetime] | None = None, **kwargs):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, RedirectResponse

    from ops.daily_account import PAPER_ACCOUNTS, DailyAccount
    from ops.daily_runner import CalendarMissing, answer_cutoff, default_paths, load_calendar, now_ist, record_answer
    from serve.dashboard import (
        AccountPage,
        account_json,
        approval_groups,
        load_account,
        ready,
        render_account,
        render_accounts,
        render_approvals,
    )

    app = FastAPI(title="Kairos")
    static_data = list(recommendations or [])

    @app.middleware("http")
    async def refuse_framing(request: Request, call_next):
        """Stop other sites from framing the dashboard, which would let them trick an approval click."""
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    def roots() -> tuple[Path, Path]:
        """Return the accounts and runner roots, defaulting to the configured derived-data root."""
        paths = default_paths()
        return Path(accounts_root or paths.accounts), Path(runner_root or paths.runner)

    def page_for(account_id: str) -> AccountPage:
        """Load one opened account or answer 404."""
        spec = next((s for s in PAPER_ACCOUNTS if s.account_id == account_id), None)
        page = load_account(*roots(), spec) if spec else None
        if page is None:
            raise HTTPException(status_code=404, detail="unknown or unopened account")
        return page

    def current_calendar() -> "Calendar":
        """Return the session calendar; raise CalendarMissing when its retained files are unreadable."""
        return calendar if calendar is not None else load_calendar(default_paths())

    def current_time() -> datetime:
        """Return the time answers are judged against: the injected clock, or IST now."""
        return clock() if clock is not None else now_ist()

    def answer(account_id: str, intent_id: str, decision: str) -> str:
        """Record one dashboard answer while its signal's answers are open; return the notice to show."""
        page = page_for(account_id)
        intent = next((i for i in page.view.state.intents if i.intent_id == intent_id and not i.sent), None)
        if intent is None:
            return f"Too late for {intent_id}; nothing changed"
        if not ready(page):
            return f"{account_id} is halted, kill-switched or unknown; nothing changed"
        signal = date.fromisoformat(intent.signal_session)
        try:
            cutoff = answer_cutoff(current_calendar(), signal)
        except CalendarMissing as exc:
            return f"Calendar unavailable, so answers are closed: {exc}; nothing changed"
        now = current_time()
        if now >= cutoff:
            return f"Answers closed at {cutoff:%H:%M} IST on {cutoff:%Y-%m-%d}; nothing changed"
        accounts, _runner = roots()
        return record_answer(DailyAccount(accounts, page.spec), account_id, intent_id, decision, cutoff.date(), signal, via="dashboard")

    def current_recommendations():
        if recommendations_path is not None or root is not None:
            return _latest_recommendations(root=root, path=recommendations_path)
        return static_data

    @app.get("/recommendations")
    def list_recommendations():
        data = current_recommendations()
        return {
            "recommendations": data,
            "picks": [item for item in data if item.get("status") == "PICK" or item.get("side") in {"LONG", "SHORT"}],
            "no_trade": [item for item in data if item.get("status") == "NO_TRADE" or item.get("side") == "NO_TRADE"],
        }

    @app.get("/health")
    def health():
        return {"ok": True, "service": "Kairos", "recommendations": len(current_recommendations())}

    @app.get("/evidence/{token:path}")
    def evidence(token: str):
        payload = _read_evidence(root, token)
        return payload

    @app.get("/accounts", response_class=HTMLResponse)
    def list_accounts():
        accounts, runner = roots()
        pages = {s.account_id: page for s in PAPER_ACCOUNTS if (page := load_account(accounts, runner, s)) is not None}
        return render_accounts(list(PAPER_ACCOUNTS), pages)

    @app.get("/accounts/{account_id}", response_class=HTMLResponse)
    def show_account(account_id: str):
        return render_account(page_for(account_id))

    @app.get("/api/accounts/{account_id}")
    def get_account(account_id: str):
        return account_json(page_for(account_id))

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(notice: str = ""):
        accounts, runner = roots()
        pages = [page for s in PAPER_ACCOUNTS if (page := load_account(accounts, runner, s)) is not None]
        try:
            groups = approval_groups(pages, current_calendar())
        except CalendarMissing as exc:
            return render_approvals([], current_time(), f"Calendar unavailable, so answers are closed: {exc}")
        return render_approvals(groups, current_time(), notice)

    @app.post("/approvals/{account_id}/{intent_id}")
    async def decide(account_id: str, intent_id: str, request: Request):
        if not trusted_origin(request.headers, port):
            raise HTTPException(status_code=403, detail="approvals take posts from this dashboard only")
        decision = parse_qs((await request.body()).decode("utf-8", errors="replace")).get("decision", [""])[0]
        if decision not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="decision must be approved or rejected")
        return RedirectResponse(f"/approvals?{urlencode({'notice': answer(account_id, intent_id, decision)})}", status_code=303)

    return app
