# 22: Same-day paper record

**What to build:** The same-day style runs every trading day on its own paper account, with its own record and scorecard. It goes through the same stage 1 shakedown and stage 2 record as the swing style (tickets 15 and 16), and one style failing does not stop the other. Paper fills need live minute prices during the day. Use the Zerodha data subscription (approved 2026-11-14) through the market-data side only, with no order calls. Plan: `../spec.md` sections 8 and 10.

**Blocked by:** 21, 15

**Status:** ready-for-agent

- [ ] Same-day picks are published before 09:00, separately from swing picks.
- [ ] The paper account fills from live minute prices and closes everything before the close.
- [ ] Its scorecard and drop-back rules run separately from the swing account.
- [ ] Only market-data calls are made, never order calls.
- [ ] Acceptance requires this style's own 3–4-week stage 1 shakedown and about 100 completed trades with its own passed scorecard; paper code by itself does not pass.
