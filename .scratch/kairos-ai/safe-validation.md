# Kairos AI safe validation

This guide governs checks for Kairos AI setup and the early cleanup tickets. It does not authorize real data access, training, broker/account access, deployment, or vault access.

## Never run

- Never import, collect, or execute `evidence/sealed_test.py`, directly or transitively. `pytest --ignore evidence/sealed_test.py` is not safe: collection or another test may import it.
- Do not run pytest, `pytest --collect-only`, blanket package imports, or `compileall` over `evidence/` while the old sealed module remains.
- Do not calculate historical scores, returns, or charts for any date on or after 2025-01-01. Do not open the vault or access the linked Mac.
- Do not read market data, train on real data, access accounts or brokers, deploy jobs, or write raw data or large weights to Git.

## Setup-only checks

For documentation and ignore-rule changes, use only:

1. `git diff --check`.
2. Verify the staged paths against the setup allowlist and confirm there are exactly 34 issue files and that the full glossary is present.
3. Check included and excluded sample paths with `git check-ignore -v --no-index`.
4. Check planning completeness and dependency references with static text parsing only.
5. Perform static import-reference inspection only; do not import Python modules.

These checks make no claim that the untouched test suite passes.

## Runtime checks for later tickets

Before any runtime test, recursively inspect its imports and every transitive import. Reject a test if it can reach `evidence/sealed_test.py`, broker/network calls, account reads, market data, vault content, or other real artifacts. Select individual synthetic/offline test files only. If pytest is eventually used, disable third-party plugin autoload (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`) and explicitly name the audited test paths. Never use blanket discovery or collection. Ticket 01 must first create or refactor focused isolated tests, document the import audit, and run only the approved selection. Tickets 02 and 03 must not request a blanket suite while old modules remain.

Synthetic refusal tests may check the 2025-01-01 boundary, but must not load, score, chart, or derive results from historical data.
