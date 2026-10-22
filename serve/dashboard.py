"""Dashboard read-model and server-rendered account pages."""
from __future__ import annotations

from html import escape
from typing import Any


STYLE = """<style>body{font:16px system-ui,sans-serif;color:#17212b;margin:0;background:#f4f6f8}main{max-width:1120px;margin:auto;padding:24px}h1,h2{margin:0 0 14px}section,.card{background:white;border:1px solid #dbe1e6;border-radius:10px;padding:16px;margin:14px 0;overflow:auto}.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.fact{padding:8px;background:#f6f8fa;border-radius:6px}.label{color:#526170;font-size:13px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}th,td{text-align:left;padding:8px;border-bottom:1px solid #e4e9ed}th{background:#f6f8fa}td.num{text-align:right}.badge{background:#b42318;color:white;border-radius:12px;padding:3px 8px}.green{color:#137333}.red{color:#b42318}svg{width:100%;height:auto;max-height:300px}.muted{color:#61707d}@media(max-width:600px){main{padding:12px}h1{font-size:24px}.facts{grid-template-columns:repeat(2,minmax(0,1fr))}section,.card{padding:12px}}</style>"""


def _e(value: Any) -> str:
    """Escape a value before placing it in HTML."""
    return escape("" if value is None else str(value), quote=True)


def _page(title: str, body: str) -> str:
    """Wrap rendered dashboard content in the shared responsive page."""
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(title)}</title>{STYLE}</head><body><main>{body}</main></body></html>'


def render_accounts(rows: list[dict[str, Any]]) -> str:
    """Render the four paper account summaries as an HTML table."""
    headers = ("Account", "Strategy", "Mode", "Stage 1 verdict", "Capital", "Equity", "P&amp;L", "Positions", "Halts", "Kill switch", "Last settled")
    body = "".join(f'<tr><td><a href="/accounts/{_e(row["account"])}">{_e(row["account"])}</a></td><td>{_e(row["strategy"])}</td><td>{_e(row["mode"])}</td><td>{_e(row["verdict"])}</td><td class="num">{_e(row["capital"])}</td><td class="num">{_e(row["equity"])}</td><td class="num">{_e(row["pnl"])}</td><td>{_e(row["positions"])}</td><td>{row["halts_html"]}</td><td>{_e(row["kill_switch"])}</td><td>{_e(row["last_settled"])}</td></tr>' for row in rows)
    return _page("Paper accounts", f'<h1>Paper accounts</h1><section><table><thead><tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr></thead><tbody>{body}</tbody></table></section>')


def render_account(data: dict[str, Any]) -> str:
    """Render one account with its state, history and open risk."""
    facts = data["facts"]
    fact_html = "".join(f'<div class="fact"><div class="label">{_e(key)}</div><strong>{_e(value)}</strong></div>' for key, value in facts.items())
    halt_html = "".join(f'<p><span class="badge">{_e(h["reason"])}</span> {_e(h["session"])} · {_e(h["detail"])}</p>' for h in data["halts"]) or '<p class="muted">no halts</p>'
    position_html = _table(("Security", "Qty", "Entry price", "Last close", "Exit due"), data["positions"])
    intent_html = _table(("Signal session", "Security", "Qty", "Sent"), data["intents"])
    trade_rows = []
    for trade in data["trades"]:
        cells = "".join(f'<td class="{"red" if k == "net_pnl" and float(v or 0) < 0 else "green" if k == "net_pnl" else ""}">{_e(v)}</td>' for k, v in trade.items())
        trade_rows.append(f"<tr>{cells}</tr>")
    trade_html = f'<div class="card"><table><thead><tr>{"".join(f"<th>{_e(c)}</th>" for c in data["trade_columns"])}</tr></thead><tbody>{"".join(trade_rows)}</tbody></table></div>'
    return _page(data["account"], f'<h1>{_e(data["account"])}</h1><p><a href="/accounts">All accounts</a></p><section class="facts">{fact_html}</section><section><h2>Open halts</h2>{halt_html}<p>Kill switch: <strong>{_e(data["kill_switch"])}</strong></p></section><section><h2>Equity curve</h2>{data["chart"]}</section><section><h2>Open positions</h2>{position_html}</section><section><h2>Open intents</h2>{intent_html}</section><section><h2>Closed trades</h2>{trade_html}</section>')


def _table(headers: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    """Render a small escaped table from ordered row values."""
    if not rows:
        return '<p class="muted">None</p>'
    return f'<div class="card"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join("<tr>" + "".join(f"<td>{_e(v)}</td>" for v in row.values()) + "</tr>" for row in rows)}</tbody></table></div>'


def equity_svg(points: list[tuple[str, float]], capital: float) -> str:
    """Draw an inline SVG equity line and capital baseline."""
    width, height, pad = 800, 260, 28
    values = [value for _, value in points] or [capital]
    low, high = min(min(values), capital), max(max(values), capital)
    span = high - low or 1
    xstep = (width - 2 * pad) / max(1, len(points) - 1)
    coords = [f'{pad + i*xstep:.1f},{height-pad-(v-low)/span*(height-2*pad):.1f}' for i, (_, v) in enumerate(points)]
    baseline = height-pad-(capital-low)/span*(height-2*pad)
    polyline = " ".join(coords)
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Account equity curve"><line x1="{pad}" y1="{baseline:.1f}" x2="{width-pad}" y2="{baseline:.1f}" stroke="#b42318" stroke-dasharray="6 5"/><polyline fill="none" stroke="#1769aa" stroke-width="3" points="{polyline}"/></svg>'

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
