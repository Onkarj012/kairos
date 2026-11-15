# 28: Ripple test at minute speed

**What to build:** The same three-way link test (true, none, shuffled) on the minute data, where information spreading between linked stocks should be fastest and strongest. It reuses the ripple model from 27 on the same-day dataset and is judged on the same-day replay. Plan: `../spec.md` sections 5 (minute data) and 12 (phase 4).

**Blocked by:** 27, 19

**Status:** ready-for-agent

- [ ] Three versions trained on the same-day dataset with the same random starts.
- [ ] The pass rule is written first. The verdict is recorded with numbers.
- [ ] Nothing on or after 2025-01-01 is read.
