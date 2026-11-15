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
