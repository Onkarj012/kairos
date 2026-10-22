"""Dashboard read model and server-rendered, read-only account pages."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from evidence.research_ledger import read_events
from ops.daily_account import AccountSpec, AccountView, DailyAccount
from ops.daily_rules import TRADE_COLUMNS
from ops.halts import kill_switch_path

STYLE = """<style>
body{font:15px/1.45 system-ui,-apple-system,sans-serif;color:#17212b;margin:0;background:#f4f6f8}
main{max-width:1120px;margin:auto;padding:24px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
a{color:#1769aa}.muted{color:#61707d}
section{background:#fff;border:1px solid #dbe1e6;border-radius:10px;padding:16px;margin:14px 0}
.scroll{overflow-x:auto}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.fact{padding:8px 10px;background:#f6f8fa;border-radius:6px}.fact div{color:#526170;font-size:12px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid #e4e9ed}th{background:#f6f8fa;font-weight:600;font-size:13px}
td.num,th.num{text-align:right}
.badge{display:inline-block;background:#b42318;color:#fff;border-radius:12px;padding:1px 8px;font-size:13px;margin:1px 2px 1px 0}
.ok{color:#137333}.bad{color:#b42318}
svg{width:100%;height:auto;display:block}
@media(max-width:600px){main{padding:12px}section{padding:12px}.facts{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>"""


@dataclass(frozen=True)
class AccountPage:
    """Everything one account page shows, read from the account log."""

    spec: AccountSpec
    opened: str
    mode: str
    verdict: str
    view: AccountView
    trades: pd.DataFrame
    equity: pd.DataFrame
    kill_switch: bool

    @property
    def pnl(self) -> float:
        """Return equity minus starting capital, after costs."""
        return self.view.state.equity - self.view.state.capital


def load_account(accounts_root: Path, runner_root: Path, spec: AccountSpec) -> AccountPage | None:
    """Read one account, or None when it has not been opened."""
    account = DailyAccount(accounts_root, spec)
    if not account.exists():
        return None
    opening = read_events(account.path)[0].payload
    return AccountPage(
        spec=spec,
        opened=str(opening.get("session", "")),
        mode=str(opening.get("mode", "paper")),
        verdict=str(opening.get("stage1_verdict", "unknown")),
        view=account.view(),
        trades=account.trades(),
        equity=account.equity(),
        kill_switch=kill_switch_path(runner_root, spec.account_id).exists(),
    )


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return a frame's rows as JSON-safe dicts."""
    return [{k: (None if pd.isna(v) else v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


def account_json(page: AccountPage) -> dict[str, Any]:
    """Return one account page as JSON."""
    state = page.view.state
    return {
        "account": page.spec.account_id,
        "strategy": page.spec.strategy,
        "mode": page.mode,
        "approval": page.spec.approval,
        "verdict": page.verdict,
        "opened": page.opened,
        "capital": state.capital,
        "cash": state.cash,
        "positions_value": state.positions_value,
        "equity": state.equity,
        "pnl": page.pnl,
        "last_settled": state.last_session,
        "kill_switch": page.kill_switch,
        "halts": page.view.halts,
        "positions": [
            {"security_id": h.security_id, "qty": h.qty, "entry_session": h.entry_session, "entry_price": h.entry_price,
             "last_close": h.last_close, "exit_due": h.exit_due}
            for h in state.holdings
        ],
        "intents": [
            {"intent_id": i.intent_id, "security_id": i.security_id, "qty": i.qty, "reserved": i.reserved,
             "sent": i.sent, "answer": page.view.decisions.get(i.intent_id, "no answer")}
            for i in state.intents
        ],
        "trades": _records(page.trades),
        "equity_curve": _records(page.equity),
    }


def _e(value: Any) -> str:
    """Escape a value before placing it in HTML."""
    return escape("" if value is None else str(value), quote=True)


def _money(value: float) -> str:
    """Format rupees with two decimals and a leading minus for losses."""
    return f"-₹{-value:,.2f}" if value < 0 else f"₹{value:,.2f}"


def _signed(value: float, text: str) -> str:
    """Colour a value green when non-negative and red when negative."""
    return f'<span class="{"ok" if value >= 0 else "bad"}">{_e(text)}</span>'


def _page(title: str, body: str) -> str:
    """Wrap content in the shared responsive page."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)} · Kairos</title>{STYLE}</head><body><main>{body}</main></body></html>"
    )


def _table(headers: list[tuple[str, bool]], rows: list[list[str]], empty: str) -> str:
    """Render a table from pre-escaped cells; each header says whether its column is numeric."""
    if not rows:
        return f'<p class="muted">{_e(empty)}</p>'
    head = "".join(f'<th class="num">{_e(h)}</th>' if num else f"<th>{_e(h)}</th>" for h, num in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="num">{c}</td>' if headers[i][1] else f"<td>{c}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _halts(view: AccountView) -> str:
    """Render halt badges, or a plain none."""
    return "".join(f'<span class="badge">{_e(h["reason"])}</span>' for h in view.halts) or '<span class="ok">none</span>'


def render_accounts(specs: list[AccountSpec], pages: dict[str, AccountPage]) -> str:
    """Render the summary of every account."""
    headers = [("Account", False), ("Strategy", False), ("Mode", False), ("Stage 1", False), ("Equity", True),
               ("P&L", True), ("Positions", True), ("Halts", False), ("Kill switch", False), ("Last settled", False)]
    rows: list[list[str]] = []
    for spec in specs:
        page = pages.get(spec.account_id)
        if page is None:
            rows.append([_e(spec.account_id), _e(spec.strategy), '<span class="muted">not opened</span>', "", "", "", "", "", "", ""])
            continue
        state = page.view.state
        share = page.pnl / state.capital * 100 if state.capital else 0.0
        rows.append([
            f'<a href="/accounts/{_e(spec.account_id)}">{_e(spec.account_id)}</a>', _e(spec.strategy), _e(page.mode), _e(page.verdict),
            _money(state.equity), _signed(page.pnl, f"{_money(page.pnl)} ({share:+.2f}%)"),
            str(len(state.holdings)), _halts(page.view), '<span class="bad">on</span>' if page.kill_switch else "off",
            _e(state.last_session or "none"),
        ])
    body = (
        '<h1>Paper accounts</h1><p class="muted">Read-only. Clear halts with <code>scripts/run_daily.py clear --note</code>.</p>'
        f"<section>{_table(headers, rows, 'No accounts.')}</section>"
    )
    return _page("Paper accounts", body)


def render_account(page: AccountPage) -> str:
    """Render one account: facts, halts, equity curve, positions, intents and closed trades."""
    state, view = page.view.state, page.view
    facts = {
        "Strategy": page.spec.strategy, "Mode": f"{page.mode}, approval {page.spec.approval}", "Stage 1 verdict": page.verdict,
        "Opened": page.opened, "Capital": _money(state.capital), "Cash": _money(state.cash), "Equity": _money(state.equity),
        "P&L after costs": _money(page.pnl), "Last settled": state.last_session or "none",
    }
    fact_html = "".join(f'<div class="fact"><div>{_e(k)}</div><strong>{_e(v)}</strong></div>' for k, v in facts.items())
    halt_html = "".join(
        f'<p><span class="badge">{_e(h["reason"])}</span> {_e(h["session"])}: {_e(h["detail"])}</p>' for h in view.halts
    ) or '<p class="ok">No open halts.</p>'
    switch = '<p class="bad">Kill switch is on.</p>' if page.kill_switch else '<p class="muted">Kill switch is off.</p>'
    positions = _table(
        [("Security", False), ("Qty", True), ("Entry", False), ("Entry price", True), ("Last close", True), ("Exit due", False)],
        [[_e(h.security_id), str(h.qty), _e(h.entry_session), f"{h.entry_price:,.2f}", f"{h.last_close:,.2f}", _e(h.exit_due)] for h in state.holdings],
        "No open positions.",
    )
    intents = _table(
        [("Signal", False), ("Security", False), ("Qty", True), ("Reserved", True), ("Answer", False), ("Sent", False)],
        [[_e(i.signal_session), _e(i.security_id), str(i.qty), _money(i.reserved), _e(view.decisions.get(i.intent_id, "no answer")),
          "yes" if i.sent else "no"] for i in state.intents],
        "No open intents.",
    )
    numeric = {"qty", "entry_price", "exit_price", "buy_costs", "sell_costs", "net_pnl", "net_bps"}
    trade_rows: list[list[str]] = []
    for trade in _records(page.trades):
        cells = []
        for column in TRADE_COLUMNS:
            value = trade.get(column)
            if column in {"net_pnl", "net_bps"} and value is not None:
                cells.append(_signed(float(value), f"{float(value):,.2f}"))
            elif column in numeric and isinstance(value, float):
                cells.append(f"{value:,.2f}")
            else:
                cells.append(_e(value))
        trade_rows.append(cells)
    trades = _table([(c.replace("_", " "), c in numeric) for c in TRADE_COLUMNS], trade_rows, "No closed trades yet.")
    curve = [(str(r["session_date"]), float(r["equity"])) for r in _records(page.equity)]
    body = (
        f'<p><a href="/accounts">All accounts</a></p><h1>{_e(page.spec.account_id)}</h1>'
        f'<section class="facts">{fact_html}</section>'
        f"<section><h2>Halts</h2>{halt_html}{switch}</section>"
        f"<section><h2>Equity after costs</h2>{equity_svg(curve, state.capital)}</section>"
        f"<section><h2>Open positions</h2>{positions}</section>"
        f"<section><h2>Open intents</h2>{intents}</section>"
        f"<section><h2>Closed trades</h2>{trades}</section>"
    )
    return _page(page.spec.account_id, body)


def equity_svg(points: list[tuple[str, float]], capital: float) -> str:
    """Draw the equity curve with a dashed capital line and labelled ends."""
    if not points:
        return '<p class="muted">No settled sessions yet.</p>'
    width, height, left, right, top, bottom = 800, 260, 64, 12, 14, 30
    values = [v for _, v in points]
    low, high = min(min(values), capital), max(max(values), capital)
    span = high - low or 1.0
    step = (width - left - right) / max(1, len(points) - 1)

    def y(value: float) -> float:
        """Map a rupee value to a pixel row."""
        return top + (high - value) / span * (height - top - bottom)

    line = " ".join(f"{left + i * step:.1f},{y(v):.1f}" for i, v in enumerate(values))
    colour = "#137333" if values[-1] >= capital else "#b42318"
    label = 'font-size="14" fill="#61707d"'
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Equity curve from {_e(points[0][0])} to {_e(points[-1][0])}">'
        f'<line x1="{left}" x2="{width - right}" y1="{y(capital):.1f}" y2="{y(capital):.1f}" stroke="#8a96a3" stroke-dasharray="5 5"/>'
        f'<text x="{left - 6}" y="{y(high) + 4:.1f}" text-anchor="end" {label}>{high:,.0f}</text>'
        f'<text x="{left - 6}" y="{y(low) + 4:.1f}" text-anchor="end" {label}>{low:,.0f}</text>'
        f'<text x="{left}" y="{height - 8}" {label}>{_e(points[0][0])}</text>'
        f'<text x="{width - right}" y="{height - 8}" text-anchor="end" {label}>{_e(points[-1][0])}</text>'
        f'<polyline fill="none" stroke="{colour}" stroke-width="2.5" points="{line}"/>'
        "</svg>"
    )


def build_dashboard_payload(recommendations=None, evidence=None, halt=None, drift=None, ledger=None, graduation=None, **kwargs):
    recommendations = list(recommendations or [])
    picks = [item for item in recommendations if item.get("status") == "PICK" or item.get("side") in {"LONG", "SHORT"}]
    no_trade = [item for item in recommendations if item.get("status") == "NO_TRADE" or item.get("side") == "NO_TRADE"]
    return {
        "recommendations": recommendations,
        "picks": picks,
        "no_trade": no_trade,
        "halt": halt or {},
        "drift": drift or {},
        "ledger": ledger or {},
        "graduation": graduation or {},
        "evidence": evidence or {},
        **kwargs,
    }
