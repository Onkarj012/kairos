# 20: Same-day model

**What to build:** The table model and the best deep model so far (from 09 or 10), trained on the same-day dataset. Each gives a chance and size for both "rises by the close" and "falls by the close", plus a range and confidence, using the shared confidence check on 2024. Same-day yardsticks are written first: always buy at the open; follow the first 15 minutes; random. Plan: `../spec.md` sections 4 and 8.

**Blocked by:** 19, 07, 09

**Status:** ready-for-agent

- [ ] The same-day yardsticks are in the results table before any model run.
- [ ] Both models are trained with three random starts and scored on 2023 against the yardsticks.
- [ ] Confidence is checked and the cut-off fixed on 2024 only.
