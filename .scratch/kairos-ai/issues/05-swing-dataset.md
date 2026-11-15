# 05: Swing dataset from the daily prices, with the three checks

**What to build:** The training rows for multi-day (swing) trades: one row per stock per day, using only what was known by that evening. Inputs are step 1 from the spec: the last 60 days of open, high, low, close, volume and value traded; simple measures (recent returns, how jumpy the stock is, distance from its recent high and low, volume against normal); day of week and days to month end; and market mood (average move of all stocks, share of stocks rising, overnight overseas markets from the seven daily overseas series). Answers are computed from the following days: next-day direction and move, 5- and 10-day move, the highs and lows reached, and whether the target came before the stop-loss (the price where a losing trade is sold). The scoring code uses the split by time with 10-trading-day gaps: training to the end of 2022, tuning 2023, checking 2024. Plan: `../spec.md` sections 5 and 6.

The three checks, each a count, running in minutes:
1. Can every date and price be read, and are prices positive?
2. Does every row use only information from that day or earlier?
3. How many rows were dropped, and why?

Gaps never stop work: a missing input is marked as missing and the model is told. Only two things stop work: an answer that cannot be computed correctly, and anything that would let 2025 or 2026 in. This replaces the old panel checker, which always reported success; delete that checker in this ticket.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] The dataset builds from the existing panel through the vault-safe reader, and no row is dated 2025 or later.
- [ ] Answers near a period edge never cross into the next period (the 10-day gap is enforced and tested).
- [ ] Stock traits and scaling are worked out from past data only, and gaps are never filled from later dates. A test plants a future value and fails if it leaks.
- [ ] Each of the three checks prints a count, the full run takes minutes, and none of them stops the build over gaps.
- [ ] Stocks with too little history are left out and counted. Delisted stocks stay in.
- [ ] Rows with zero traded value, and the one missing session, are marked rather than silently dropped.
