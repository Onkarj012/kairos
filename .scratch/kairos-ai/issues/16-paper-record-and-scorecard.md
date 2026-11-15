# 16: Paper record (stage 2) and the scorecard

**What to build:** The record that decides whether a style may touch real money, plus the checks that keep it honest. Paper trading continues until about 100 completed trades. The scorecard shows:
- results against the range the replay predicted;
- return above the index fund after costs and tax;
- the gap between paper fills and real prices;
- a clean match between the account and its event log.

Win rate is shown but never decides anything. A weekly health check compares recent accuracy and confidence with the expected figures. Warning signs pause new buys: accuracy on confident picks falls to chance; confidence stops being honest; picks cluster in one industry; a data source goes missing. The system drops back a stage automatically if results fall outside the predicted range for 30 trades, if a loss limit is hit, or if the account and the broker disagree about holdings. Each style is scored on its own. Plan: `../spec.md` sections 10 and 11.

**Blocked by:** 15

**Status:** ready-for-agent

- [ ] The scorecard is on the dashboard and in the weekly report, per style.
- [ ] Each warning sign is tested and pauses new buys while exits continue.
- [ ] Each drop-back trigger is tested and moves the account back a stage, with an event-log entry.
- [ ] Stage 2 counts as passed only at about 100 completed trades with results inside the predicted range.
