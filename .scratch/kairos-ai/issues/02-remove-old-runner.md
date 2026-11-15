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

## Execution evidence

Base: `b99363464fd145807b7e3670831ba917a8d18ea5`; branch: `t3/kairos-ai-02-remove-old-runner`.

### Deletions

- `ops/daily_runner.py` — ran the dropped reversal and momentum paper rules.
- `ops/daily_actions.py` — runner-only corporate-action fetching and merge path.
- `ops/daily_rules.py` — implementation of dropped daily trading rules; research callers remain for ticket 03.
- `ops/kite_broker.py` — four-account holdings reader and dry-run order path; login token support moved separately.
- `ops/broker.py` — legacy refusing broker stub and live-order gate.
- `ops/risk_governor.py` — obsolete risk governor only used by the removed gate.
- `ops/halts.py` — obsolete sleeve halt and kill-switch helper, distinct from account event halts.
- `ops/graduation.py` — old paper-graduation/live-eligibility thresholds.
- `ops/live_eligibility.py` — old live eligibility gate replaced by future authorized tickets.
- `ops/launchd/com.kairos.daily.place.plist` — scheduled the removed daily place job.
- `ops/launchd/com.kairos.daily.reconcile.plist` — scheduled the removed daily reconciliation job.
- `ops/launchd/com.kairos.daily.signal.plist` — scheduled the removed daily signal job.
- `ops/launchd/com.kairos.dashboard.plist` — obsolete macOS dashboard launch agent; retained manual dashboard entry point.
- `ops/launchd/install.sh` — installed launch agents removed by this ticket.
- `ops/launchd/uninstall.sh` — managed launch agents removed by this ticket.
- `scripts/run_daily.py` — CLI for the removed old runner.
- `scripts/data/build_daily_runner_ledger.py` — built frozen inputs for the removed runner.
- `tests/test_daily_actions.py` — exercised only runner-bound corporate actions.
- `tests/test_daily_paper_runner.py` — exercised only the removed runner.
- `tests/test_kite_broker.py` — exercised only the removed broker reader and dry-run order path.

`tests/test_ops_ledger_halts_broker.py` now covers only the retained ledger; obsolete halt and broker cases were removed. `tests/test_phase5_phase6_ops_api.py` retains unrelated ledger/API/DAG cases while obsolete graduation and eligibility cases were removed.

### Kept behavior

`scripts/kite_login.py` now depends only on strategy-neutral `ops.kite_login_support` for IST, `.env` parsing, the unchanged token path/JSON/mode, and the credential error type. Its URL builder accepts an injected mapping; no login, browser, callback, network or real `.env` access was used. The dashboard account page no longer points to the deleted runner clear command; halts and recovery notes remain event-log based. Account/event log, dashboard, Telegram, macOS notification, price panel, corporate-action adjustment and cost modules remain.

### Validation and handoff

Independent Sol high approved original head `2f19c9d` and independently reproduced **14 passed in 1.16s**, zero safety refusals. Its recursive static audit covered **28 local Python files**, broader than the worker's initial 19-file accounting below. After source-port PR 12 merged, coordinator integrated current main `52a6d2d` without conflict. No ticket 02 production/test behavior changed; coordinator execution records were updated. A fresh exact integrated-head review and current-head checks are required before merging PR 13. Actual login/callback, real account history, calendar provisioning and notification delivery remain unverified.

Static AST audit of the three explicit test roots plus local transitive imports covered 19 Python files. No removed runner/broker/risk module or sealed module is imported. No root/tests `conftest.py`, `pytest_plugins`, startup customizer, or explicit plugin exists; `pyproject.toml` only sets `testpaths` and `pythonpath`, overridden by `-c /dev/null`. Inherited `PYTEST_ADDOPTS` was empty. The exact selection `tests/test_runner_cleanup.py tests/test_kept_parts.py tests/test_costs.py` passed: **14 passed**. Runtime used `/usr/bin/python3 -I -S -B`, manually added this checkout and `/home/onkarj012/Projects/market/kairos/.venv/lib/python3.12/site-packages`, set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS=`, and ran `pytest.main(['-q','-p','no:cacheprovider','-c','/dev/null','--confcutdir', '<checkout>/tests','-o','addopts=', ...])`. An audit hook rejected socket connect/name lookup, subprocess/system/spawn, reads of `.env`, the real token path, sealed/vault/derived-data paths, and non-code evidence artifacts. Tests used temporary synthetic accounts/tokens only. The first overly broad hook attempt blocked temp token writes and asyncio's internal socket construction; after narrowing the guard, all 14 passed. No sealed module, real data or real account was read.

Ticket 03 handoff: direct imports of removed runner/rule modules remain in `evidence/daily_backtest.py` (`ops.daily_rules` and the sealed module), `tests/test_daily_rules_backtest.py` (`ops.daily_rules`), and the mixed dashboard/research file `tests/test_dashboard_accounts.py` (`ops.daily_runner`, `ops.daily_rules`, and the research ledger). Leave those research and mixed tests for ticket 03; none were imported or collected here. Calendar-proof callers still include `scripts/data/collect_daily_calendar.py`, `tests/test_daily_calendar_evidence.py`, `scripts/data/build_daily_session_ledger.py`, `tests/test_daily_session_ledger.py`, and `evidence/daily_session_ledger.py`, which imports `ingest.daily_calendar_evidence`. Other research callers of the sealed/research modules include `scripts/research/run_daily_backtest.py`, `tests/test_daily_sealed_and_gates.py`, `tests/test_daily_verdict.py`, `tests/test_research_ledger.py`, and `evidence/trials.py`. These are ticket 03 scope and were not imported or collected. Historical account compatibility beyond synthetic pre-2025 records remains unverified.
