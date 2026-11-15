# Kairos AI safe validation

This guide governs checks for Kairos AI setup and the early cleanup tickets. It does not authorize real data access, training, broker/account access, deployment, or vault access.

## Never run

- Never import, collect, or execute `evidence/sealed_test.py`, directly or transitively. `pytest --ignore evidence/sealed_test.py` is not safe: collection or another test may import it.
- Do not use blanket pytest discovery or collection, blanket package imports, or `compileall` over `evidence/`. Only the explicitly audited synthetic test paths described below may run, even while the old sealed module remains.
- Do not calculate historical scores, returns, or charts for any date on or after 2025-01-01. No agent opens the vault.
- Do not make live broker/account calls, move money, send real orders, deploy jobs, or write raw data or large weights to Git. Owner-controlled tickets remain owner-controlled.

## Scope of setup and cleanup restrictions

Setup and early cleanup checks must not read market data, train on real data, or access the linked Mac. These restrictions apply to these checks, not to all future implementation. Later tickets may use real pre-2025 data and train models within their authorized scope after prerequisites and evidence are accepted. Permit one heavy training job at a time and serialize shared external writes. Ticket 08 permits read-only Mac source inspection and copying. Ticket 17 permits creating compact output packs on the Mac while retaining source CSVs untouched, followed by a storage-checked transfer. Held-out packs land directly in the protected vault path without scoring or opening them. This guide does not itself authorize those later operations.

## Setup-only checks

For documentation and ignore-rule changes, use only:

1. `git diff --check`.
2. Verify the staged paths against the setup allowlist and confirm there are exactly 34 issue files and that the full glossary is present.
3. Check included and excluded sample paths with `git check-ignore -v --no-index`, including the bare `derived_data` symlink and representative children.
4. Check planning completeness and dependency references with static text parsing only.
5. Perform static import-reference inspection only; do not import Python modules.

These checks make no claim that the untouched test suite passes.

## Runtime checks for later tickets

Before any runtime test, recursively inspect its imports and every transitive import, including package initializers, startup hooks, applicable `conftest.py` files, and explicit plugins. Reject a test if it can reach `evidence/sealed_test.py`, broker/network calls, real account reads, market data, vault content, or other real artifacts. Select individual synthetic/offline test files only. Audited explicit test paths may run before ticket 03 removes the old sealed module. Disable third-party plugin autoload with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, audit startup configuration and inherited `PYTEST_ADDOPTS`, and explicitly name the approved test paths. Never use blanket discovery or collection. Ticket 01 must first create or refactor focused isolated tests, document the import audit, and run only the approved selection. Tickets 02 and 03 must not request a blanket suite while old modules remain.

Synthetic refusal tests may check the 2025-01-01 boundary, but must not load, score, chart, or derive results from historical data.
