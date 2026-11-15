# 13: Loss budget enforcement

**What to build:** One risk state that works the same way in the replay and in every paper and real account. It is decided in decisions.md 2026-11-15:
- **10% fall from the account's highest value:** the system stops and returns to paper. Only the owner's review restarts it.
- **5% fall from the highest value:** position sizes are halved until the account recovers.
- **2% lost in one day:** no new trades for the rest of that day.

Stops and sell orders on held stocks keep working in every state. The risk state never blocks reducing risk. Each change of state is written to the hash-chained event log and shown on the dashboard. This replaces the old risk governor and halt files removed in ticket 02. Plan: `../spec.md` section 10.

**Blocked by:** 12

**Status:** ready-for-agent

- [ ] The replay applies the three rules. A forced test drawdown triggers each one at the right point.
- [ ] Paper accounts read the same risk state. After a 10% stop, the system returns to paper and stays there until an explicit owner restart is recorded.
- [ ] Selling and stop-loss exits are never blocked by the risk state.
- [ ] Every state change is an event-log entry and is visible on the dashboard.
