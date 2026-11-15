# 27: Ripple model with the three-way link test (daily)

**What to build:** The research idea: the market as a web. Each stock is read by the one-reader or five-encoder base. Then it receives information from the stocks it is linked to:
- same industry;
- same index;
- price links measured from the past only (which stocks tended to move together, and which tended to move first);
- overseas markets and the Nifty future as "market nodes" every stock connects to.

Every link is dated. The decisive test: three versions identical except for the links (true links, no links, randomly shuffled links). The idea passes only if true links beat both on the 2023 replay. If it passes, each kind of link is then tested alone. Plan: `../spec.md` sections 4 (model 4) and 7.

**Blocked by:** 10, 12, 24

**Status:** ready-for-agent

- [ ] The three versions are trained with the same random starts, and all are logged.
- [ ] The pass rule is written in the results table before the runs.
- [ ] Price links are recomputed from the past only at each date, and a test proves it.
- [ ] A plain verdict: supported or not supported on daily data, with numbers. If not supported, the picks system carries on with the best other model.
