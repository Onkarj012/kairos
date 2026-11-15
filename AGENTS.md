# Agent instructions

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The repo uses `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

## Decisions log

When a choice between options changes what gets built or how (stack, architecture, scope cuts, naming, data model, a dropped approach), add it to the top of `decisions.md`. Include the date (IST), the decision in one line, what was rejected, why ("not stated" if no reason was given), the source thread, and a status. If it reverses an older entry, mark that entry superseded instead of deleting it.

## Kairos AI execution safety

Follow `.scratch/kairos-ai/safe-validation.md` before running checks. Never import, collect, or execute `evidence/sealed_test.py`, including through transitive imports. No agent opens the blind vault or calculates historical scores, returns, or charts for dates on or after 2025-01-01. Synthetic refusal tests may exercise date boundaries without reading held-out data. No live broker/account calls, money movement, real orders, or deployment are authorized. Tickets 30, 32, 33, and 34 remain owner-controlled.

Setup and cleanup use only audited synthetic/offline checks. Later tickets may use real pre-2025 data and train models only within their authorized scope and accepted prerequisites, with one heavy training job at a time. Ticket 08 may read and copy source code from the linked Mac without changing it. Ticket 17 may create compact output packs on the Mac while leaving source CSVs untouched, then transfer them after checking storage; held-out packs go directly to the protected vault path without scoring or opening them. Serialize writes to shared external resources. Raw data and large model weights stay out of Git.
