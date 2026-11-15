# Kairos AI execution ledger

Canonical ticket `Status:` labels remain triage state as defined in `../../docs/agents/triage-labels.md`. Execution state below is separate. Only the coordinator edits this shared ledger.

The default prerequisite is **merged code plus accepted evidence**. Code merged by itself does not satisfy evidence gates. Evidence is tracked separately as (1) code merged, (2) actual training artifacts and accepted research evidence, and (3) elapsed paper records with their scorecards. No model or trading claim follows merely from a code merge. No stacked PRs: a dependent ticket starts from the merged current head.

Shared-resource status on 2026-11-15: no heavy training, real-data migration, registry, or paper-account writer is authorized or running. Ticket 08 uses a private CPU-only dummy-check runtime, with at most 2 GB new disk use and at least 8 GB left free; no GPU stack or shared environment writes. Permit one heavy training job at a time. Serialize external data writes, model-registry writes, GPU use, and paper-account writes. Keep raw data and large weights out of Git.

Setup merged as `31cd294937636777641fb02c46bc24b6488edac3`. Its final head `88673c8` received independent Sol high approval and Greptile 5/5, the applicable check passed, CodeRabbit explicitly skipped automatic review, and all review threads were resolved. Setup evidence is accepted. Coordinator and new workers use this updated-main base. The owner root checkout and its local changes remain untouched. This coordinator-only working ledger update will accompany a later independently reviewed implementation PR, not a direct main commit.

Initial disjoint implementation scopes after setup: ticket 01 owns operations/account/dashboard/contracts and relevant tests; ticket 04 owns a new isolated vault, readers, tests, and docs without wiring unrelated modules; ticket 08 owns a new model-port namespace, tests, and audit only, with the source Mac read-only. Recheck actual file/import overlap before dispatch; do not presume the scopes remain disjoint.

| Ticket | Execution state | Thread link | Branch | PR | Required evidence | Blockers |
|---|---|---|---|---|---|---|
| Setup | merged, evidence accepted | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-setup-20261115-01` | `t3/kairos-ai-setup` | https://github.com/Onkarj012/kairos/pull/9 | Merge 31cd294; static checks; exact-head Sol approval; Greptile 5/5; no unresolved threads | none |
| 01 | implementation approved; latest-main integration review pending | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket01-20261115-01` | `t3/kairos-ai-01-kept-parts` | none | Sol high approved c0ed553; 25-file static audit; 9 focused synthetic tests; no deletions | Review integrated current head, PR checks and merge; real normalized-calendar provisioning unverified |
| 02 | blocked | — | — | — | Removed-file reasons; isolated behavior; no sealed-module path | 01 |
| 03 | blocked | — | — | — | Panel/collector behavior; old sealed module removed with audited references | 01 |
| 04 | infrastructure merged; ticket evidence incomplete | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket04-20261115-01` | `t3/kairos-ai-04-blind-vault` | https://github.com/Onkarj012/kairos/pull/10 | Merge 1e3d4fb; Sol high and Greptile 5/5 at exact head 08e07c7; 16 synthetic tests | Dedicated owner-service OS/IPC enforcement and real partition/count/checksum evidence absent; 05/17 remain blocked |
| 05 | blocked | — | — | — | Dataset rows/counts, three checks, masked ambiguous target/stop answers | 04 |
| 06 | blocked | — | — | — | Yardstick results table and run records | 05 |
| 07 | blocked | — | — | — | Table model artifact and tuning evidence | 06 |
| 08 | implementing from completed source exports | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket08-20261115-01` | `t3/kairos-ai-08-stockxpert-port` | none | Private CPU torch 2.11.0+cpu ready; existing helper supplied missing ResidualBlock/RegimeDetector source with full hash; no model result claimed | Verified faithful port, actual dummy forward/backward and synthetic leak proof still required |
| 09 | blocked | — | — | — | Five-encoder model artifact and tuning evidence | 06, 07, 08 |
| 10 | blocked | — | — | — | One-reader model artifact and tuning evidence | 09 |
| 11 | blocked | — | — | — | Swing picks output and focused tests | 07 |
| 12 | blocked | — | — | — | Honest swing replay, conservative ambiguous-bar counts, and date refusal | 11 |
| 13 | blocked | — | — | — | Risk-state tests and documented loss budget | 12 |
| 14 | blocked | — | — | — | Timers, lock, heartbeat, and missing-token evidence | 02 |
| 15 | blocked | — | — | — | Swing stage 1 elapsed paper record and acceptance evidence | 13, 14 |
| 16 | blocked | — | — | — | Swing paper scorecard and about 100 completed trades | 15 |
| 17 | blocked | — | — | — | Mac packing evidence, checksums/counts; output packs only, source CSVs untouched | 04 |
| 18 | blocked | — | — | — | Adjustment comparison counts and extended dataset checks | 17, 05 |
| 19 | blocked | — | — | — | Same-day dataset checks and entry-time test | 18 |
| 20 | blocked | — | — | — | Yardsticks and model artifacts/evidence | 19, 07, 09 |
| 21 | blocked | — | — | — | Same-day replay and no-overnight short evidence | 20, 12, 13 |
| 22 | blocked | — | — | — | Own 3–4-week shakedown, about 100 completed trades, own passed scorecard | 21, 15 |
| 23 | blocked | — | — | — | Source/checksum and coverage counts; known-at tests | 05 |
| 24 | blocked | — | — | — | Dated links, coverage counts, temporal test | 05 |
| 25 | blocked | — | — | — | Accepted news/announcement coverage evidence and timing tests | 05 |
| 26 | blocked | — | — | — | Paired model artifacts/results; news only if 25 coverage accepted | 09, 10, 12, 18, 23; 25 conditionally for news only |
| 27 | blocked | — | — | — | Daily true/no/shuffled link artifacts and verdict | 10, 12, 24 |
| 28 | blocked | — | — | — | Minute true/no/shuffled link artifacts and verdict | 27, 19, 21 |
| 29 | blocked | — | — | — | Both 27 daily and 28 minute verdicts; frozen style specs | 26, 27, 28, 21 |
| 30 | owner-controlled | — | — | — | Owner-run single blind opening per style against frozen marks | 29 |
| 31 | blocked | — | — | — | Fake-broker fault tests; real adapter off and no real calls | 13, 02 |
| 32 | owner-controlled | — | — | — | Owner-provisioned host/IP/DDPI and tested backup restore | 14 |
| 33 | owner-controlled | — | — | — | Common 30/31/32 plus own style paper evidence (swing 16 or same-day 22), own blind pass, owner approval | common 30, 31, 32; swing 16 or same-day 22 |
| 34 | owner-controlled | — | — | — | Owner sign-off, promotion and capital evidence, side-by-side model record | 33 |
