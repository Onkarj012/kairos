# 25: News and announcements

**What to build:** News inputs for the five-encoder model's news reader. Reuse the paper's Google News and GDELT scripts from the Mac (StockXpert `scripts/`). Fix their timing: a headline published after the market close counts for the next trading day, and items without an exact time are not used. Start saving NSE company announcements (results, orders, board meetings) every day with their exact time, so a real history builds up from now on. Report coverage per stock and per year. The news reader is switched on in ticket 26 only if coverage is enough to matter. Plan: `../spec.md` sections 4 and 5 (step 3).

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] The headline scripts run here, and every item is stamped with the trading day it may first be used.
- [ ] A test puts an after-close headline on the next trading day.
- [ ] The daily announcement collector is saving with exact times, and its originals are kept.
- [ ] Coverage counts per stock per year are written down. No mood scores are computed on dates on or after 2025-01-01.
