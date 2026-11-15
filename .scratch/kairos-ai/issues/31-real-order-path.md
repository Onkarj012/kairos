# 31: Real order path, tested against a fake broker

**What to build:** Everything needed to send real orders to Zerodha, built and fault-tested against a fake broker, with nothing sent live. Pieces:
- one broker account, with the swing and same-day records as labels within it;
- every order tracked through its states: planned, risk-approved, sending, acknowledged, part-filled, filled, cancelled, rejected, unknown;
- an "unknown" order is settled before anything else is sent;
- the order's own ID is derived from the pick, so a restart never sends it twice;
- the broker's order IDs and fills are saved;
- limit orders only, with bounded repricing and an expiry;
- before any new trade, holdings, sellable quantity, open orders, cash and charges are matched against Kite;
- limits at the point of each order: per stock, total, order size, daily turnover, order count;
- the loss budget from ticket 13 is checked at the same point.

Plan: `../retained-system-requirements.md`.

**Blocked by:** 13, 02

**Status:** ready-for-agent

- [ ] All tests run against a fake broker. The real adapter exists but is switched off and makes no calls in tests.
- [ ] Fault tests: a restart mid-send creates no duplicate order; a timeout lands in "unknown" and is settled; a dead login token places nothing.
- [ ] A mismatch between account and broker blocks new trades but never blocks exits.
