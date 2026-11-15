# 11: Swing picks engine

**What to build:** Plain, readable rules that turn one evening's model outputs into the swing picks list (buy signals only, since shares cannot be sold short overnight in the ordinary share market). Steps:
1. Shortlist: actively traded, not under exchange restrictions, confidence above the fixed cut-off.
2. Rank by expected gain after costs, and drop anything that does not clearly beat its costs. Some days there are no picks.
3. Buy range: from the predicted low to a little above yesterday's close. Skip the pick if the stock opens above the range.
4. Target and stop-loss: from the predicted range and how jumpy the stock normally is.
5. Holding time: whichever of 1, 5 or 10 days the model is most confident about. Sell if neither target nor stop is hit by then.
6. Quantity: sized so that hitting the stop loses 1% of the account, capped per stock.
7. Held stocks: hold, tighten the stop, or sell tomorrow in a stated range.

At Rs 10,000 it holds one or two positions; the rules are the same at every account size and only the number of positions changes. The engine works with whichever model is currently best. Plan: `../spec.md` section 8.

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] For a given evening in 2023 or 2024, it prints a picks list with buy range, quantity, target, stop-loss and expected holding days, plus hold/sell lines for held stocks.
- [ ] Costs come from the kept cost model, including the fixed Rs 15.34 charge each time shares held overnight are sold (the DP charge).
- [ ] Tested at Rs 10,000, Rs 1 lakh and Rs 10 lakh: whole shares only, risk per trade at or under 1%, and the position count grows with the account.
- [ ] Swapping in a different model needs no change to the engine.
