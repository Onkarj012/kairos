# 06: Yardsticks and the results table

**What to build:** The numbers every model must beat, and one place where every run is written down. Yardsticks: always say "up"; copy yesterday's direction; buy last year's winners (12-month move skipping the last month); random picks. They are scored on the tuning year (2023) for accuracy, how well the ranking of stocks matches what happened, and accuracy at each confidence level together with how many predictions are kept. The results table gets one plain line per run: date, data version, code version, settings, random start, scores, and the pass marks written before the run. Plan: `../spec.md` sections 4 (model 0), 6 and 7.

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] All four yardsticks run on the swing dataset and land in the results table.
- [ ] A run cannot be recorded unless its pass marks were written down before it started.
- [ ] The table is one file anyone can read: no database, no special tool.
- [ ] Scoring refuses any date on or after 2025-01-01.
