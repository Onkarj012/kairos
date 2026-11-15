# 02: Remove the old daily rules runner and the live-trading stubs

**What to build:** Delete the machinery that ran the two old ranking rules (5-day reversal and 12-1 momentum) as daily paper trades. Both rules failed every test and were dropped (decisions.md 2026-11-14), and the new picks system replaces this runner. After this ticket the repo has no scheduled job trading the old rules, and the kept parts from ticket 01 still work.

What is removed, and why:
- The old daily runner, its daily actions and its trading rules (`ops/daily_runner.py`, `ops/daily_actions.py`, `ops/daily_rules.py`, `scripts/run_daily.py`): they run only the dropped rules.
- The four-account Kite dry run (`ops/kite_broker.py`) and the refusing broker stub (`ops/broker.py`): the dry run sized a sell to the broker's whole holding rather than the account's own quantity. The real order path is rebuilt in ticket 31.
- The old risk governor, halt files, graduation and live-eligibility helpers (`ops/risk_governor.py`, `ops/halts.py`, `ops/graduation.py`, `ops/live_eligibility.py`): only tests ever called them, and a second halt mechanism disagreed with the event log. Ticket 13 replaces them with one risk state.
- The macOS scheduled jobs (`ops/launchd/`) and the runner ledger script: ticket 14 replaces them with timers on this Linux machine.
- The tests that cover only the removed code.

Kept: the morning Kite login script, which keeps working without the runner (the one-tap login is a standing decision), the Telegram and macOS notification channels, the dashboard, the paper accounts and the event log.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Every removed file is listed in the PR, each with its one-line reason.
- [ ] The Kite login script still runs to the point of opening the login page, with no broker order calls.
- [ ] The dashboard starts and shows the existing paper accounts.
- [ ] Focused isolated tests pass only after the static import audit and under `../safe-validation.md`; do not run a blanket suite while old modules remain.
