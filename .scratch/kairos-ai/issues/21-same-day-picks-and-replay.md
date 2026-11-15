# 21: Same-day picks (buy and short) and honest replay

**What to build:** A separate picks list for the same-day style. It gives buy signals (stock expected to rise) and sell signals (a short: sell in the morning, buy back before the close). Each pick carries an entry range, quantity, target and stop, and every trade is closed before the market closes. The replay uses minute prices: a fill happens only when the price traded through the order, nothing fills at a price band lock, and same-day costs plus slippage are charged. The loss budget from ticket 13 applies to this style's own account. The report has the same measures as ticket 12, next to the index fund. Plan: `../spec.md` sections 7 and 8.

**Blocked by:** 20, 12, 13

**Status:** ready-for-agent

- [ ] For one 2023 morning it prints a same-day picks list with both buys and shorts.
- [ ] Replays 2023 and 2024 on minute prices. No trade is still open at the close.
- [ ] Shorts are never carried overnight, and a test proves it.
- [ ] Report written to the results table. Refuses dates on or after 2025-01-01.
