# 23: Market-wide daily inputs

**What to build:** Step 2 market inputs from the spec, collected from NSE daily files with their originals kept:
- Nifty and sector index levels;
- India VIX;
- daily futures and options figures, including open interest (the number of contracts currently open; rising open interest means new money is entering);
- daily foreign and domestic institution buying.

Collect the history back as far as the sources go, and keep them current every evening. Missing values are marked as missing and never stop work. Each value carries the time it became known. Plan: `../spec.md` section 5 (step 2).

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] Each series is collected with source files and checksums kept, and its coverage is reported as counts.
- [ ] Each value has a known-at time. The dataset joins them only after that time, and a test proves it.
- [ ] The evening collector adds the new day. A failed download marks that day missing and does not stop the run.
