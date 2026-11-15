# 10: One-reader model

**What to build:** A single small network reading the last 60 days, trained exactly like ticket 09. It answers whether five readers really beat one. It gives the same five outputs and uses the same scoring and confidence check. Plan: `../spec.md` section 4 (model 3).

**Blocked by:** 09

**Status:** ready-for-agent

- [ ] Uses the shared training loop from 09 with the same random starts and data version.
- [ ] Three runs in the results table, with a side-by-side line against the five-encoder model on 2023.
- [ ] It counts as better or worse only if the gap is larger than the spread between random starts.
