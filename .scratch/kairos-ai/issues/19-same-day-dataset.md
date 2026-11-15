# 19: Same-day dataset and same-day costs

**What to build:** Training rows for same-day trades: enter in the morning and close before the market shuts. One row per stock per morning, built from the minute prices known by the entry time: the prior days, the overnight overseas moves, and the first minutes of the session if the entry is after the open. Answers cover both directions: how far the stock rose or fell by the close, the highs and lows reached during the day, and whether a target came before a stop for a buy, and for a short. A short is selling shares you do not own in the morning and buying them back before the close; Indian brokers allow this only within one day. Also add a same-day cost schedule to the cost model (about 0.1 to 0.2% per trade, with no DP charge). Uses the same date split, gaps, three checks and vault. Plan: `../spec.md` sections 5 and 8.

**Blocked by:** 18

**Status:** ready-for-agent

- [ ] The dataset builds from the pre-2025 minute pack and passes the three checks with counts.
- [ ] A test proves no input uses a minute after the row's entry time.
- [ ] The cost model has a same-day schedule with its own test, and the multi-day schedule is unchanged.
- [ ] Sessions with missing minutes are marked and counted, never silently filled.
