# Agent instructions

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The repo uses `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

## Decisions log

When a choice between options changes what gets built or how (stack, architecture, scope cuts, naming, data model, a dropped approach), add it to the top of `decisions.md`. Include the date (IST), the decision in one line, what was rejected, why ("not stated" if no reason was given), the source thread, and a status. If it reverses an older entry, mark that entry superseded instead of deleting it.

## Kairos AI execution safety

Follow `.scratch/kairos-ai/safe-validation.md` before running checks. Never import, collect, or execute `evidence/sealed_test.py`, including through transitive imports. Do not calculate historical scores, returns, or charts for dates on or after 2025-01-01, open the blind vault, run real-data training, access accounts or brokers, deploy jobs, or write to the linked Mac. Only synthetic refusal tests may exercise date boundaries. Raw data and large model weights stay out of Git.
