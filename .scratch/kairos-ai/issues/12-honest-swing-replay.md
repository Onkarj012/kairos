# 12: Honest replay of swing picks on 2023 and 2024

**What to build:** A day-by-day replay (backtest) of the picks engine over the tuning and checking years. Each evening the model sees only the past and makes its picks; the next days' real prices then fill or miss each order. To keep it honest:
- A buy fills only if the price actually traded through it, never on a day the stock was locked at its price band (the largest move allowed in a day). Use a high-equals-low day as the lock sign until real band data exists.
- Full costs: the tax on each trade (STT), stamp duty, exchange fees, the DP charge, and an allowance for getting a slightly worse price than planned (slippage). Tax on profits is included.
- Delisted stocks stay in.
- Results sit next to holding a Nifty index fund and a Nifty200 Momentum 30 fund over the same days, using those funds' own prices from the daily panel.

The report shows accuracy at each confidence level with the count kept; average gain per trade after costs; share of winning trades; profit factor (money won divided by money lost); yearly return; the worst fall from a peak (drawdown), also for the funds; and the number of trades. Under about 100 trades a result is a hint, not proof. Plan: `../spec.md` section 7.

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] Replays 2023 and 2024 with the table model and writes the report to the results table.
- [ ] A test order inside the day's range that the price never traded through stays unfilled. A locked day never fills.
- [ ] Reuse ticket 05's daily-bar ambiguity classification. For an open position whose bar opens between target and stop and touches both, assume stop-first, report the affected count and assumption, and test it synthetically. Never count target-first without event-order evidence. An observed opening gap through a stop uses the opening price plus applicable costs/slippage, not a better stop price. This rule never creates an otherwise unconfirmed entry fill.
- [ ] Every cost line appears separately in the report. A doubled-cost run is included as a stress case.
- [ ] Refuses any date on or after 2025-01-01. The blind run in ticket 30 goes through the vault instead.
