"""FastAPI recommendations API."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote


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


def create_app(recommendations=None, *args, root=None, recommendations_path=None, accounts_root: Path | None = None, runner_root: Path | None = None, **kwargs):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse
    from ops.daily_account import PAPER_ACCOUNTS, DailyAccount
    from ops.daily_rules import TRADE_COLUMNS
    from ops.halts import kill_switch_path
    from serve.dashboard import equity_svg, render_account, render_accounts
    from evidence.research_ledger import read_events
    app = FastAPI(title="Kairos")
    static_data = list(recommendations or [])
    def account_roots():
        from ops.daily_runner import default_paths
        paths = default_paths()
        return (Path(accounts_root) if accounts_root is not None else paths.accounts, Path(runner_root) if runner_root is not None else paths.runner)

    def account_data(account_id: str):
        from fastapi.encoders import jsonable_encoder
        spec = next((item for item in PAPER_ACCOUNTS if item.account_id == account_id), None)
        if spec is None:
            raise HTTPException(status_code=404, detail="Account not found")
        accounts, runner = account_roots()
        account = DailyAccount(accounts, spec)
        if not account.exists():
            raise HTTPException(status_code=404, detail="Account not opened")
        events = read_events(account.path)
        opening = events[0].payload
        view = account.view()
        state = view.state
        trades_df = account.trades()
        equity_df = account.equity()
        trades = jsonable_encoder(trades_df.replace({float("nan"): None}).to_dict(orient="records"))
        equity_rows = jsonable_encoder(equity_df.replace({float("nan"): None}).to_dict(orient="records"))
        points = [(str(row["session_date"]), float(row["equity"])) for row in equity_rows]
        halt_rows = [{"reason": h.get("reason", ""), "session": h.get("session", ""), "detail": h.get("detail", "")} for h in view.halts]
        pnl = round(state.equity - state.capital, 2)
        facts = {"Strategy": spec.strategy, "Mode": opening.get("mode", "paper"), "Verdict": opening.get("stage1_verdict", "unknown"), "Opened session": opening.get("session"), "Capital": f"₹{state.capital:,.2f}", "Cash": f"₹{state.cash:,.2f}", "Equity": f"₹{state.equity:,.2f}", "P&L after costs": f"₹{pnl:,.2f}", "Last settled": state.last_session or "none"}
        return {"account": account_id, "strategy": spec.strategy, "mode": opening.get("mode", "paper"), "verdict": opening.get("stage1_verdict", "unknown"), "opened_session": opening.get("session"), "state": {"cash": state.cash, "equity": state.equity, "positions_value": state.positions_value, "capital": state.capital, "last_session": state.last_session}, "pnl": pnl, "halts": halt_rows, "kill_switch": kill_switch_path(runner, account_id).exists(), "positions": [{"security": h.security_id, "qty": h.qty, "entry_price": h.entry_price, "last_close": h.last_close, "exit_due": h.exit_due} for h in state.holdings], "intents": [{"signal_session": i.signal_session, "security": i.security_id, "qty": i.qty, "sent": i.sent} for i in state.intents], "trades": trades, "trade_columns": TRADE_COLUMNS, "equity": equity_rows, "equity_svg": equity_svg(points, state.capital), "facts": facts}

    def account_summaries():
        from fastapi.encoders import jsonable_encoder
        rows = []
        accounts, _ = account_roots()
        for spec in PAPER_ACCOUNTS:
            account = DailyAccount(accounts, spec)
            if not account.exists():
                rows.append({"account": spec.account_id, "strategy": spec.strategy, "opened": False, "mode": "not opened", "verdict": "not opened", "capital": "not opened", "equity": "not opened", "pnl": "not opened", "positions": "not opened", "halts": [], "halts_html": '<span class="muted">not opened</span>', "kill_switch": "not opened", "last_settled": "not opened"})
                continue
            data = account_data(spec.account_id)
            state = data["state"]
            percentage = data["pnl"] / state["capital"] * 100 if state["capital"] else 0
            data.update({"opened": True, "capital": f'₹{state["capital"]:,.0f}', "equity": f'₹{state["equity"]:,.2f}', "pnl": f'₹{data["pnl"]:,.2f} ({percentage:.2f}%)', "positions": len(data["positions"]), "halts_html": " ".join(f'<span class="badge">{_safe(h["reason"])}</span>' for h in data["halts"]) or "none", "last_settled": state["last_session"] or "none", "kill_switch": "on" if data["kill_switch"] else "off"})
            rows.append(data)
        return jsonable_encoder(rows)

    def _safe(value):
        from html import escape
        return escape(str(value), quote=True)

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
        return render_accounts(account_summaries())

    @app.get("/accounts/{account_id}", response_class=HTMLResponse)
    def show_account(account_id: str):
        data = account_data(account_id)
        data["chart"] = data.pop("equity_svg")
        return render_account(data)

    @app.get("/api/accounts/{account_id}")
    def get_account(account_id: str):
        return account_data(account_id)

    return app
