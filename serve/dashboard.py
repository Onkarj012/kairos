"""Dashboard read-model helper."""

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
