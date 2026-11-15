# 17: Pack the minute prices on the Mac and copy them here

**What to build:** The Mac holds one-minute stock prices at `/Users/onkarj012/Projects/market/intranet_optinet/data/nifty500`: 536 CSV files, 22 GB, 531 stocks, February 2015 to June 2026. Nearby are the Nifty 50 minute file (`../nifty_intraday`) and a daily India VIX file. India VIX is an index of how large a move traders expect in the Nifty over the next month. This machine has only 11 GB free, so the files are packed into a compact columnar format on the Mac first, through the linked T3 environment, and then copied. The five duplicate or alternate files are resolved by comparing their rows, not by guessing. Everything dated 2025-01-01 or later goes into its own pack, which lands directly in the blind vault from ticket 04. Plan: `../spec.md` section 5 (minute data).

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Packing runs on the Mac and leaves the original CSVs untouched.
- [ ] Each pack has a checksum and a row count per stock that matches the source count.
- [ ] The duplicate files are resolved, and the choice for each is written down.
- [ ] The pre-2025 pack and the vault pack are copied here and fit in the free space, with room to spare reported.
- [ ] Only counts and date coverage are computed. No returns or scores on any date.
