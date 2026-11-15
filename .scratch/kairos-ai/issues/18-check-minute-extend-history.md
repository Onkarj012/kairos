# 18: Check the minute prices against the daily panel; extend daily history back to 2015

**What to build:** First, find out whether the minute prices are adjusted for splits and bonuses. Rebuild daily bars from the minutes and compare them with the existing daily panel over the 2019–2024 overlap, around the known split and bonus dates. Report counts of matches and mismatches. If they are unadjusted, apply the kept corporate-action factors. Then add daily rows for 2015 to September 2019 for those stocks to the swing dataset. This nearly doubles the training history and adds more kinds of market (the 2015–16 fall, 2018). The tuning and checking years do not change. Plan: `../spec.md` sections 5 and 12 (phase 3).

**Blocked by:** 17, 05

**Status:** ready-for-agent

- [ ] A short note says whether the minute prices are adjusted, with match counts around the known split and bonus dates.
- [ ] The swing dataset gains 2015–2019 rows, which pass the three checks with their counts reported.
- [ ] Stocks that appear only in the minute data and are missing from the panel are listed and counted.
- [ ] Nothing on or after 2025-01-01 is read from the training side.
