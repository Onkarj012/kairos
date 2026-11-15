# Kairos AI execution ledger

Canonical ticket `Status:` labels remain triage state as defined in `../../docs/agents/triage-labels.md`. Execution state below is separate. Only the coordinator edits this shared ledger.

The default prerequisite is **merged code plus accepted evidence**. Code merged by itself does not satisfy evidence gates. Evidence is tracked separately as (1) code merged, (2) actual training artifacts and accepted research evidence, and (3) elapsed paper records with their scorecards. No model or trading claim follows merely from a code merge. No stacked PRs: a dependent ticket starts from the merged current head.

Initial shared-resource status: all unclaimed. Permit one heavy training job at a time. Serialize external data writes, model-registry writes, GPU use, and paper-account writes. Keep raw data and large weights out of Git.

Initial disjoint implementation scopes after setup: ticket 01 owns operations/account/dashboard/contracts and relevant tests; ticket 04 owns a new isolated vault, readers, tests, and docs without wiring unrelated modules; ticket 08 owns a new model-port namespace, tests, and audit only, with the source Mac read-only. Recheck actual file/import overlap before dispatch; do not presume the scopes remain disjoint.

| Ticket | Execution state | Thread link | Branch | PR | Required evidence | Blockers |
|---|---|---|---|---|---|---|
| Setup | in progress | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80` | `t3/kairos-ai-setup` | none | Safe setup checks; exact scoped commit | none |
| 01 | awaiting setup | — | — | — | Focused audited tests; kept parts independent; no deletions | setup |
| 02 | blocked | — | — | — | Removed-file reasons; isolated behavior; no sealed-module path | 01 |
| 03 | blocked | — | — | — | Panel/collector behavior; old sealed module removed with audited references | 01 |
| 04 | awaiting setup | — | — | — | Vault checksum/count and synthetic refusal; no score computation | setup |
| 05 | blocked | — | — | — | Dataset rows/counts and three checks | 04 |
| 06 | blocked | — | — | — | Yardstick results table and run records | 05 |
| 07 | blocked | — | — | — | Table model artifact and tuning evidence | 06 |
| 08 | awaiting setup | — | — | — | Dummy forward/backward pass and code audit; source Mac read-only | setup |
| 09 | blocked | — | — | — | Five-encoder model artifact and tuning evidence | 06, 08 |
| 10 | blocked | — | — | — | One-reader model artifact and tuning evidence | 09 |
| 11 | blocked | — | — | — | Swing picks output and focused tests | 07 |
| 12 | blocked | — | — | — | Honest swing replay results and date refusal | 11 |
| 13 | blocked | — | — | — | Risk-state tests and documented loss budget | 12 |
| 14 | blocked | — | — | — | Timers, lock, heartbeat, and missing-token evidence | 02 |
| 15 | blocked | — | — | — | Swing stage 1 elapsed paper record and acceptance evidence | 13, 14 |
| 16 | blocked | — | — | — | Swing paper scorecard and about 100 completed trades | 15 |
| 17 | blocked | — | — | — | Mac packing evidence, checksums/counts; Mac read-only | 04 |
| 18 | blocked | — | — | — | Adjustment comparison counts and extended dataset checks | 17, 05 |
| 19 | blocked | — | — | — | Same-day dataset checks and entry-time test | 18 |
| 20 | blocked | — | — | — | Yardsticks and model artifacts/evidence | 19, 07, 09 |
| 21 | blocked | — | — | — | Same-day replay and no-overnight short evidence | 20, 12, 13 |
| 22 | blocked | — | — | — | Own 3–4-week shakedown, about 100 completed trades, own passed scorecard | 21, 15 |
| 23 | blocked | — | — | — | Source/checksum and coverage counts; known-at tests | 05 |
| 24 | blocked | — | — | — | Dated links, coverage counts, temporal test | 05 |
| 25 | blocked | — | — | — | Accepted news/announcement coverage evidence and timing tests | 05 |
| 26 | blocked | — | — | — | Paired model artifacts/results; news only if 25 coverage accepted | 09, 10, 18, 23; 25 conditionally for news only |
| 27 | blocked | — | — | — | Daily true/no/shuffled link artifacts and verdict | 10, 12, 24 |
| 28 | blocked | — | — | — | Minute true/no/shuffled link artifacts and verdict | 27, 19 |
| 29 | blocked | — | — | — | Both 27 daily and 28 minute verdicts; frozen style specs | 26, 27, 28, 21 |
| 30 | owner-controlled | — | — | — | Owner-run single blind opening per style against frozen marks | 29 |
| 31 | blocked | — | — | — | Fake-broker fault tests; real adapter off and no real calls | 13, 02 |
| 32 | owner-controlled | — | — | — | Owner-provisioned host/IP/DDPI and tested backup restore | 14 |
| 33 | owner-controlled | — | — | — | Common 30/31/32 plus own style paper evidence (swing 16 or same-day 22), own blind pass, owner approval | common 30, 31, 32; matching style evidence |
| 34 | owner-controlled | — | — | — | Owner sign-off, promotion and capital evidence, side-by-side model record | 33 |
