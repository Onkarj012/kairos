# 15: Swing paper trading, stage 1 (shakedown)

**What to build:** The swing picks system runs every trading day on paper. Picks appear before 09:00 on the dashboard and as a phone alert. The swing paper account (its own record, separate from same-day) fills or misses each order from that day's real prices, using the same fill rules as the replay. An evening report shows what filled, what was sold, profit and loss, and how results compare with what the model expected. The frozen model is loaded only; nothing trains during daily use. This stage lasts 3 to 4 weeks. Its goal is that nothing breaks, not profit. Plan: `../spec.md` section 10, stage 1.

**Blocked by:** 13, 14

**Status:** ready-for-agent

- [ ] Picks are published before 09:00 on every trading day of the run, and any late or missed day raises an alert.
- [ ] Paper fills match the day's real prices under the replay's fill rules, checked by a script each evening.
- [ ] The loss budget from 13 is live on the account.
- [ ] The evening report arrives every trading day.
- [ ] No broker order call exists anywhere in the path.
