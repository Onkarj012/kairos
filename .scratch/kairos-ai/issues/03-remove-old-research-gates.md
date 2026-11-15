# 03: Remove the old research gates and the sealed-year machinery

**What to build:** Delete the evidence layer built around the old rules: fixed yearly test windows, pass/fail gates, the trial counter, the old single sealed year (2025-10 to 2026-09), and the calendar-proof chain that tried to prove every trading day's legal record. That chain stopped every earlier attempt before a model was trained (decisions.md 2026-11-14: data checks are now three counts). The blind vault (ticket 04) and the honest replay (ticket 12) replace it.

What is removed, and why:
- Old backtest, gates, verdicts, statistics, walk-forward windows and trial registry (`evidence/daily_backtest.py`, `evidence/backtest.py`, `evidence/daily_verdict.py`, `evidence/gates.py`, `evidence/stats.py`, `evidence/walkforward.py`, `evidence/trials.py`, `evidence/trials.json`, `scripts/research/run_daily_backtest.py`): built for the two dropped rules. The new replay is ticket 12, and the new run log is ticket 06.
- The old sealed-year module (`evidence/sealed_test.py`) and the old research log (`evidence/research_ledger.py`): replaced by the blind vault (04) and the results table (06). Do not run the sealed-year module before deleting it.
- The two old rules (`models/daily_baselines.py`): "last year's winners" comes back as a yardstick in ticket 06.
- The calendar-proof chain (`ingest/daily_calendar_evidence.py`, `evidence/daily_session_ledger.py`, `evidence/daily_acquisition_checkpoint.py` and the scripts that run them): replaced by the three counts in ticket 05.

Kept: daily price collection from NSE files must still work. Anything the daily collector or panel build truly needs is kept or cut loose, not deleted.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Every removed file is listed in the PR, each with its one-line reason.
- [ ] The daily price panel still builds from the stored NSE files, and the daily collector still runs (checked with a dry run that makes no network call).
- [ ] Focused isolated tests pass only after the static import audit and under `../safe-validation.md`; do not run a blanket suite while old modules remain.
- [ ] No code path anywhere opens the old sealed-year module.

## Execution note — ticket 03 implementation

Date: 2026-11-15 IST. Base: `6223a371124b30422909b7ad75631f4b3670b369`. Branch: `t3/kairos-ai-03-remove-research-gates`.

### Removed files

- `evidence/backtest.py` — old strategy evaluation engine superseded by the new picks replay.
- `evidence/daily_acquisition_checkpoint.py` — calendar-denominator recovery gate removed; the cash collector's durable retry controls remain in `ingest.daily_cash`.
- `evidence/daily_backtest.py` — backtest for the dropped reversal and momentum rules.
- `evidence/daily_session_ledger.py` — legal calendar-proof and qualified-denominator chain removed.
- `evidence/daily_verdict.py` — old fixed-fold pass/fail verdict builder.
- `evidence/gates.py` — old promotion and sealed-year pass/fail gates.
- `evidence/research_ledger.py` — old research trial/verdict event log replaced by future ticket 06 records.
- `evidence/sealed_test.py` — old single sealed-year loader and evaluator; never imported or executed.
- `evidence/stats.py` — old gate-specific economic and trial-adjusted statistics.
- `evidence/trials.json` — persisted global counter for the removed trial registry.
- `evidence/trials.py` — old trial counter backed by the removed research log.
- `evidence/walkforward.py` — fixed yearly walk-forward windows for the dropped evaluation protocol.
- `ingest/daily_calendar_evidence.py` — source-retention and legal calendar-proof machinery replaced by the short data checks.
- `models/daily_baselines.py` — implementations of the two dropped hand-built trading rules.
- `scripts/data/build_daily_session_ledger.py` — CLI for the removed qualified session-ledger chain.
- `scripts/data/collect_daily_calendar.py` — CLI for the removed calendar-proof evidence store.
- `scripts/research/run_daily_backtest.py` — CLI for the removed backtest, verdict, gate, trial, and sealed-year flow.
- `tests/test_daily_acquisition_checkpoint.py` — removed denominator/recovery cases; retained summary-validation and CLI-budget refusal cases move to `tests/test_research_cleanup.py`.
- `tests/test_daily_baselines.py` — tests only the removed daily-rule baselines.
- `tests/test_daily_calendar_evidence.py` — tests only the removed calendar-proof source store.
- `tests/test_daily_rules_backtest.py` — old rule/backtest tests; the kept cost-model tests remain in `tests/test_costs.py`.
- `tests/test_daily_sealed_and_gates.py` — tests only the removed gate, trial, and sealed-year behavior.
- `tests/test_daily_session_ledger.py` — tests only the removed qualified session-ledger chain.
- `tests/test_daily_verdict.py` — tests only the removed fixed-fold verdict and trial flow.
- `tests/test_research_ledger.py` — removed compatibility-module imports; all eight hash-chain/idempotence/conflict/tamper/restart/concurrency cases move to direct `ops.event_log` tests in `tests/test_event_log.py`.

### Kept collector, panel, and dashboard

Moved the collector's `CheckpointError` and `aggregate_seed_counts` (including validation of prior audit counts and retained attempt reservations, merged with `max` to avoid double-counting) into `ingest/acquisition_budget.py`. `scripts/data/collect_daily_cash.py` now imports that neutral helper. The existing per-URL attempt cap, durable reservations, resume semantics, spacing, deadlines, request timeouts, and byte/integrity validation remain in `ingest.daily_cash`; only the calendar-denominator checkpoint was removed.

Cleared old gate/backtest lazy exports from `evidence/__init__.py`, since the initializer itself was in the transitive import closure of kept `evidence.costs` imports. `ingest/calendar.py` remains as the normalized sessions/manifest producer used by the kept paper-calendar reader. No legal-proof gate or claim that calendar artifacts are provisioned was added. As ticket 01 records, normalized calendar provisioning remains unverified; missing or invalid calendar data continues to close approval timing while history remains readable.

Replaced the mixed `tests/test_dashboard_accounts.py` legacy-runner cases with retained account listing, account event-history/halt, and unknown-account checks over synthetic pre-2025 events. Added `tests/test_research_cleanup.py` for the collector's injected fake fetch and budget preservation, synthetic retained-byte panel build, and dashboard-origin behavior. Historical account date enforcement remains a handoff: `ops/daily_account.py` still has its inherited 2025-10-01 through 2026-09-30 guard; no blanket post-2024 guard was introduced because it would block live paper logging. Ticket 04 must handle historical reader/evaluation policy at its boundary while preserving live paper records.

### Validation and limits

Round-one review found lost coverage for kept behavior, not demonstrated
production failures. Restored all eight old event-log cases against the retained
writer; standalone dashboard anti-framing, 08:40 cutoff/no-write, origin/referrer,
zero-share closed-trade, open/skipped trade separation, API/cost/rendering checks;
and corrupt/non-object/negative budget summaries plus the actual collector CLI's
exhaustion refusal before collection. All fixtures use pre-2025 synthetic events,
except the existing explicit held-out refusal test. No production fixes were
needed. The exact audited CLI source is permitted read-only in the local guard;
its cached bytecode is refused to force source execution. No general `data`
directory exception or network permission was introduced. Initial collection
refused the CLI cache; no real artifact was read. Independent round-two recheck
is required before PR. Actual stored-panel rebuild and calendar provisioning
remain unverified.

Latest fix selection: **37 passed in 1.52s**, zero safety refusals, with a private
temporary directory and the 27-local-file audited import closure. Exact files:
`tests/test_event_log.py tests/test_research_cleanup.py tests/test_dashboard_accounts.py tests/test_costs.py tests/test_kept_parts.py tests/test_kept_safeguards.py`.
This excludes `tests/test_daily_cash.py`, whose older accepted synthetic fixtures
include 2026 dates; those dates were outside the first review's narrower brief.
No current-head full-suite claim is made.

Coordinator independently reproduced the six-file selection: **37 passed in 1.81s**, zero safety refusals, after re-auditing the 26-local-file import closure. The neutral budget functions match the prior count-validation/overlap semantics; actual stored data was not read. The collector CLI's direct-file startup was subsequently exercised with `--help` under isolated Python and network/artifact refusals, allowing only the audited script source through the path-component `data` guard: it exited 0 with zero safety refusals. This supersedes the earlier inability to invoke the CLI reported below; it proves startup/imports only. Actual acquisition, stored-panel rebuild, calendar provisioning and live behavior remain unverified. Independent current-head Sol high review is required before PR.

Recursive AST import/startup audit covered 26 local Python files across the explicit selection `tests/test_research_cleanup.py tests/test_dashboard_accounts.py tests/test_daily_cash.py tests/test_costs.py tests/test_kept_parts.py tests/test_kept_safeguards.py`. It found no import closure to `evidence.sealed_test`, removed research modules, or old calendar-proof modules. There are no root or tests `conftest.py`; no explicit pytest plugins or inherited `PYTEST_ADDOPTS` were found; `pyproject.toml` settings were overridden by the approved harness. A first harness attempt included `tests/test_daily_panel.py` and imported `scripts/data/check_daily_panel.py`; the artifact guard refused that path because its path contains the protected component `data`. That selection was stopped. The focused alternative below exercises `build_panel` directly using synthetic retained bytes and did not bypass the guard. The `scripts/data/collect_daily_cash.py` CLI itself was statically inspected but not invoked under this harness for the same protected-path reason; its underlying collector was exercised with an injected fake transport.

Passed: `TMPDIR=/tmp/kairos-ticket03-d7afb111 /usr/bin/python3 -I -S -B /tmp/kairos-safe-focused-check.py "$PWD" tests/test_research_cleanup.py tests/test_dashboard_accounts.py tests/test_daily_cash.py tests/test_costs.py tests/test_kept_parts.py tests/test_kept_safeguards.py` — **37 passed**, zero safety refusals. The harness disabled plugin autoload, cleared `PYTEST_ADDOPTS`, selected only explicit files, used `/dev/null` config and a private temporary directory, and blocked network/subprocess and real artifact paths. The collector test injected a fake fetch; no socket, stored market data, account data, vault, model weights, historical scores, or post-2024 return was accessed. Actual stored-data panel rebuild was not authorized and remains unverified; actual acquisition was not started. `git diff --check` passed and the exact deletion/status review found no out-of-scope changes before commit.

Architecture note for the coordinator's decisions log (not edited here): preserve durable collector safety semantics by extracting only seed-count reading/validation into the neutral ingest helper; do not keep the checkpoint module or silently drop its remaining collector controls. Keep normalized calendar generation separate from the removed legal-proof chain. The historical-reader/evaluation date cutoff belongs to the authorized reader boundary; do not make a blanket account-event cutoff.
