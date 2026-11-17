# Kairos AI execution ledger

## Coordinator reconciliation, 2026-11-17 IST

This continuation starts from verified clean main
`f444b2de312431bc119427fabae29a920e716011`. PRs 9 through 18 are merged;
the older pending-review entries below are historical checkpoints, not live
blockers. PR18's final `0d302f0f5ea0f268dbe0f312e9ff055197d822c4` had independent
Sol HIGH approval, 44 tests and 45 subtests, zero safety refusals, Greptile
5/5 SUCCESS, CodeRabbit SKIPPED and resolved threads; its watch is off.
This reconciles the prior coordinator's durable uncommitted history without
copying stale files over current main. That checkout and the owner root are
untouched. All existing worktrees remain retained.

Accepted CODE scopes: 01/02/03/08. Partial: 04/14. Blocked: 05/06/07/09/10/11/12/
13/15/16/17/18/19/20/21/22/23/24/25/26/27/28/29/31 (24 tickets).
Owner-controlled: 30/32/33/34. Ticket 04 still blocks 05 and 17. No operational,
training, performance, paper-duration, real-data or deployment claim is added.

## Service-software candidate, 2026-11-17 IST

The new coordinator owns core lifecycle, protocol, descriptor-pinned storage,
bootstrap/client and metadata. The bounded tooling worker
<t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A7138691d-2e9d-4fb8-b785-bf4acd48594a%3Adelegate-task%3A52dfe84a-c7d0-4a3c-9ae8-807b57745874>
adds only owner setup/rollback tooling, inactive unit and synthetic plan tests;
the coordinator audits and integrates them. See `docs/agents/owner-service.md`
for the exact activation, canary-denial, rollback and remaining-evidence list.

The root-only protocol supplies no training/live endpoint. Final testing is
disabled in generated configuration. All selected snapshots must verify after
one durable style audit and before exposure. Code/config/dependencies cannot
come from a mutable training checkout at runtime. Production paths are pinned
with no-follow opens, private owners/modes and singly linked regular files.
Management applies only exact owner-approved root-sealed plans; no activation
is implicit. No production provisioning, installation, service activation,
opening entry point, blind fixture/content, actual migration, Mac transfer,
training, broker or account call is performed in this continuation.

Before review, the original guarded six-file selection passed 66 tests and
62 subtests with zero safety refusals. This is a historical candidate checkpoint;
subsequent combined verification and exact-head review/publication determine
software acceptance, not this paragraph. Harness SHA256 remains
`1d6c48051d01417b385ca0233dc413f31333a096f4a513d0cd4f284c4c49a0e3`.
Kernel framing tests use temporary unprivileged socket pairs, not listeners or
either prohibited opening function. Owner filesystem opening wiring and
provisioning are static-review-only; fake lifecycle handles are not evidence
of deployed isolation. Ticket 04 stays incomplete and 05/17 blocked until
separately authorized actual isolation/migration evidence is accepted.

Combined pre-publication verification, 2026-11-17 IST: the seven explicit
audited files (the prior four plus owner_runtime, owner_protocol and
owner_service_tooling) passed 86 tests and 85 subtests with zero refusals,
including the bootstrap-predicate regression. Shell syntax and
`git diff --check` passed. systemd unit verification passed against a completely
synthetic private alternate root, generators disabled, with a copied benign
executable and synthetic target units; no production service paths or manager
were inspected or activated. Read-only identity checks confirm owner
Onkarj012/ADMIN. Disk remains approximately 6.8 GiB free; no installs or large
copies were made. Final exact-head test/review evidence is recorded separately.

## PR19 writer-limit correction, 2026-11-17 IST

Initial head `61a6746ddfe810cf0c65b055ad452cca7e4edd14` received independent
gpt-6.1-sol HIGH APPROVE with independently reproduced 86 tests and 85 subtests,
zero safety refusals. PR19 Greptile then passed its check but scored 4/5 and
correctly identified that the JSONL writer could publish rows above the reader's
1 MiB byte limit. The writer now enforces the identical serialized-byte bound
before writing/publishing. Synthetic pre-2025 boundary tests cover limit-1,
limit, limit+1, escaped Unicode, identical retries, staging cleanup and preservation
of an existing pack. The unchanged guarded seven-file selection passes 88 tests
and 93 subtests, zero safety refusals. Changed-head independent recheck and
current-head remote approval remain required before publication/merge. No
opening entry point, protected path, provisioning or activation was exercised;
04 remains incomplete and 05/17 blocked.

## Historical checkpoints

PR18 portability fix, 2026-11-16 IST: Greptile P2 thread
PRRT_kwDOVQuzD863xr7X correctly identified an unpatched requester assertion and
missing SO_PEERCRED on non-Linux test hosts. Peer-test setup now mocks Linux
and the socket constant for the complete test lifecycle. A regression reruns
both peer cases under simulated darwin with SO_PEERCRED absent, requires no
errors/failures/skips, and checks constant cleanup. Before the setup fix it
failed with both reported errors. Guarded four-file selection passes44tests
and45subtests, zero safety refusals. Actual macOS execution is unverified.
Only tests and explanatory metadata change; production and decisions unchanged.
Independent exact-head recheck and remote acceptance pending;04incomplete,
05/17blocked14incompletepaper15blocked. No real socket/opening/deployment.

Owner-boundary candidate checkpoint, 2026-11-16 IST: worker9c5d466 on exact
base00e3e89 adds only owner_boundary.py, its synthetic test, README and ticket04
evidence. Coordinator inspected source/tests and removed an unused duplicate
authorizer protocol. Distinct non-root service/training UID checks, root-peer
SO_PEERCRED authorization and supplied layout metadata checks are helper-only.
Documentation now states connect-time credentials and trusted handle/user-
namespace assumptions. Guarded four-file selection passed43tests+45subtests,
zero safety refusals, with the existing unchanged harness and read-only installed
dependencies. No real sockets, protected paths or opening functions were used.
Independent exact-head review pending; no push/PR.04incomplete05/17blocked,
14incompletepaper15blocked. No daemon/listener, provisioning or migration.

PR17 merged checkpoint, 2026-11-16 IST: squash00e3e89004da4be5f968ae0129bc6d36b9b178c2
is the base of this branch. Finalhead08e3afe had independent Sol high approval,
39tests+22subtests, Greptile5/5SUCCESS, CodeRabbitSKIPPED and resolvedthreads.
SHA-guarded squash merge confirmed; watch removed and worktrees retained.
This accepts code only, not owner filesystem opening or OS/IPC enforcement.

PR 17 review follow-up, 2026-11-16 IST: exact `9bac62b` received independent
Sol high approval, Greptile SUCCESS with 4/5 confidence, and CodeRabbit SKIPPED.
Greptile's P2 unread-snapshot finding is valid. The coordinator added an
explicitly closable/context-managed session and idempotently closable row
iterators, preserving the single-pack return tuple. Synthetic empty-memory
stream tests cover partial and unread cleanup, caller exceptions, exhaustion,
iteration failure, and cleanup continuation after a close error. Guarded
three-file validation: 39 tests and 22 subtests passed, zero safety refusals,
ten audited local files. Neither opening function was invoked. Fresh exact-head
review and remote acceptance of this fix remain pending. Ticket 04 remains
incomplete; 05/17 and paper 15 remain blocked; 14 remains incomplete.

Multi-pack checkpoint, 2026-11-16 IST: PR16 merged as
`c9462bf77ae67c789d99897f3ecbf56eec330897` after exactfda065e Sol high approval,
Greptile5/5/SUCCESS, CodeRabbitSKIPPED and resolvedthread; watch removed.
Compact implementation evidence accepted, not real capacity/migration evidence.
The next04worker
`t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3Ae296ab3f-15d2-43a7-9614-a078802328c5%3Adelegate-task%3Akairos-ai-ticket04-multipack-20261116-01`
committed8446af0 from exactc9462bf in its T3isolatedcheckout. Five-path scope
adds pure frozen-selection metadata and a one-audit multi-pack owner contract,
without changing storage/ordinary readers/init. Coordinator inspected sources,
audited10localimports and startup, added request-hash sensitivity for every
selection field/order plus exact audit-selection equality. Guarded three-file
selection:35passed+19subtests, zero safety refusals. No opening entry point was
invoked, not even with stubs; owner filesystem integration remains UNVERIFIED.
Explicit selection limits are implementation bounds, not actual corpus coverage
or capacity proof. Legacy single-pack callers now need matching frozen metadata.
Fresh exact-head review pending; no push/PR. 04incomplete and05/17blocked until
accepted owner-controlled isolation/opening and actualmigration/count/checksum
evidence. No OS provisioning, service/IPC deployment, realdata, Mac, training or
owner-root writes;14incomplete,paper15blocked.

PR16 schema-coverage checkpoint, 2026-11-16 IST: independent Sol high approved
d750e20 and the branch was published as https://github.com/Onkarj012/kairos/pull/16.
Greptile SUCCESS at4/5 correctly found that the malformed-schema regression
stopped at manifest parsing instead of exercising schema comparisons. Added
direct pre-first-yield failures for valid manifest metadata with incorrect names
and types, and incorrect embedded kairos.schema metadata with an updated byte
checksum. No production code changes. Guarded focused selection now passes
29 tests plus7subtests, zero safety refusals, eight audited local files. Fresh
exact-head review is required before pushing these fixes; current remote remains
d750e20. 04 incomplete,05/17 blocked; no real data, vault or final-test invocation,
Mac, installation, deployment or owner-root writes.

Compact-pack checkpoint, 2026-11-15 IST: PR 15 merged as
`4e858ffaaacc1e206b1a40c750efd9dbaa00c148` after exact cc51000 Sol high approval,
Greptile 5/5/SUCCESS, CodeRabbit SKIPPED and no unresolved threads. Watch removed;
cleanup evidence accepted within scope, not actual stored-panel/calendar proof.
Ticket 04 compact follow-up worker
`t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket04-compact-20261115-01`
committed 6062dbd from that main in its provided isolated checkout. Coordinator
inspected all five paths and the eight-file local import closure, reproduced an
all-null Parquet column metadata failure, and fixed it with a regression that
also refuses type drift after schema freezing. Exact focused selection now has
27 passed plus 5 subtests, zero safety refusals; diff checks pass. Existing
synthetic live-window and owner policy tests were retained; no final-test entry
point was called. Independent exact-head review is pending; no push or PR yet.
JSONL remains the default and old manifests remain readable. Compact evidence
is synthetic only, not a real capacity estimate. OS-isolated owner service,
authenticated IPC, multi-pack opening and real migration/count/checksum evidence
remain absent: 04 incomplete, 05/17 blocked. Ticket 14 remains incomplete and
paper-ticket 15 blocked. No installs, Mac/data access, host service action or
owner-root writes occurred.

Current-main integration checkpoint, 2026-11-15 IST: PR 14 squash-merged as
`b86f709a36f651dc5526f5ea64d5ca2f3b3ac67a` after exact cea2ce7 Sol high approval,
Greptile 5/5/SUCCESS, CodeRabbit SKIPPED, resolved threads and owner ADMIN check.
Watch removed after merge; checkout retained, no activation. Ticket 03 c91e4d3
round-two review approved all three restored-coverage findings. Integrated the
new main in its isolated checkout; only ledger/decisions conflicted and both
intents were retained. Ticket 03 production/test changes remain unchanged from
approved c91e4d3; scheduler/data/model trees match new main. Integrated guarded
selection: 45 passed in 1.60s, zero safety refusals, 29 audited local files;
fake-manager/path-refusal shell scenarios also pass. Fresh exact integrated-head
approval is required before opening the cleanup PR. Stored-data panel rebuild,
calendar provisioning and live behavior remain unverified. No owner-root writes.

Ticket 03 round-one coverage checkpoint, 2026-11-15 IST: restored direct retained
event-log safeguards, dashboard route/no-write/trade-rendering regressions and
actual CLI exhausted-budget refusal with malformed-summary cases. Only tests
and explanatory metadata change from reviewed bfd141b. No production failure
was demonstrated or production fix added. Fresh exact-head recheck required.
Current restored selection: 37 passed in 1.52s, zero safety refusals, 27 local
files audited. This is a different selection from the earlier 37-test run.
Ticket 14's scoped fixes are independently approved at c10cefc and are now PR
https://github.com/Onkarj012/kairos/pull/14, awaiting current-head remote review.
14 remains incomplete, 15 blocked; 04/05/17 evidence gaps remain unchanged.

Research cleanup checkpoint, 2026-11-15 IST: ticket 02 PR13 merged6223a37 with scoped cleanup evidence accepted; ticket 03 worker committed `2dccb71` from that base. Coordinator inspected the 31-path deletion/extraction scope and reproduced 37 explicit synthetic tests in1.81s after auditing26localfiles, zero safety refusals. Collector CLI `--help` separately passed isolated startup with only its audited script source exempted from the generic path-component guard; no acquisition/data access occurred. Actual stored-panel rebuild, calendar provisioning and live behavior remain unverified. Current-head independent review is next. Ticket14 infrastructure is paused at bf4e40d in its own checkout for independent review: six synthetic checks, unconfigured held CLI, no installation/activation; external alert/reminder/real handlers absent, so14incomplete15blocked. Ticket04 remains incomplete and05/17blocked.
PR 14 bot-fix checkpoint, 2026-11-15 IST: c10cefc received Sol high approval and
opened https://github.com/Onkarj012/kairos/pull/14. Greptile 4/5 identified differing
lock defaults and unsafe installer paths. Coordinator reproduced/fixed both;
eight guarded tests plus fake-manager/path-refusal shell checks pass. Changes
await fresh exact-head review before push. No merge, activation or delivery
evidence is claimed; 14 incomplete and 15 blocked. Ticket 03 coverage fixes are
committed c91e4d3, awaiting independent round-two review.

Ticket 14 review-fix checkpoint, 2026-11-15 IST: round one blocked three P2s;
coordinator reproduced and fixed log-before-heartbeat ordering, non-object JSON
refusal and fail-closed uninstall of timers plus active services. Eight isolated
synthetic Python tests and three fake-manager shell scenarios pass. No host
systemd action occurred. Fresh exact-head round-two review is pending. Ticket 14
remains incomplete and 15 blocked. Ticket 03 separately received three retained
coverage findings; no PR has opened for either branch.

Scheduler checkpoint, 2026-11-15 IST: ticket 02 PR https://github.com/Onkarj012/kairos/pull/13 merged as `6223a371124b30422909b7ad75631f4b3670b369` and unwatched after exact-head Sol high approval, 19 synthetic checks, Greptile 5/5/SUCCESS, CodeRabbit SKIPPED and no review threads. Cleanup evidence accepted. Tickets 03 and 14 dispatched from that base with disjoint scopes. Ticket 14 worker committed faef77e; coordinator fixed entry-point imports and standalone lock location, reproduced 6 focused synthetic checks with no safety refusals plus an isolated temporary-state held CLI invocation. No timers installed/enabled or handlers deployed. Current-head independent review remains required before PR; outside-host alert/reminder evidence is absent, so 14 is incomplete and 15 blocked. Ticket 03 research/collector cleanup continues independently; ticket 04 remains incomplete and 05/17 blocked.

PR 13 feedback checkpoint, 2026-11-15 IST: Greptile reviewed remote `2f19c9d` at 4/5 and correctly found that deleting the mixed runner test file also removed three standalone kept safeguards. Coordinator restored them in isolated `tests/test_kept_safeguards.py`: truncated Telegram reply handling, synthetic-token-safe errors/repr, and synthetic legacy-holdout account refusal with no log write. Telegram calls are intercepted with a fake opener; no network/account/vault data is used. Audited 44 local transitive files and reran exactly five approved files: 19 passed in 2.90s, zero safety refusals. The narrower integration-only review at 6c62c81 was canceled before this superseding test change; a fresh combined integration/fix review must pass before push. No production code changed for this finding. PR 13 remains watched; no merge on its older head.

Merge/integration checkpoint, 2026-11-15 IST: ticket 08 PR https://github.com/Onkarj012/kairos/pull/12 merged as `52a6d2d4886c94a8f0fffe21d87b6e7b002d3103` and unwatched. Exact e341dfb had independent Sol high approval, 2 passing guarded synthetic tests, Greptile 5/5/SUCCESS, CodeRabbit SKIPPED and no review threads; owner ADMIN authority and unchanged base were confirmed. Ticket 08 source-port/audit evidence is accepted, not full feature/scaler training-pipeline or model-quality evidence. Ticket 02 PR https://github.com/Onkarj012/kairos/pull/13 opened at independently approved 2f19c9d with 14 synthetic tests, and remains watched. Coordinator integrated current main into its provided checkout without conflict; only coordinator metadata/evidence records are being added. A fresh exact integrated-head recheck must pass before push; do not infer approval from its older head. No deletion/train/data prerequisite beyond 08 itself is unlocked; 09 still waits for 06/07, and 03 is queued until 02 merges.

Coordinator checkpoint, 2026-11-15 IST: ticket 01 PR https://github.com/Onkarj012/kairos/pull/11 merged as `b99363464fd145807b7e3670831ba917a8d18ea5` after exact-head Sol high approval, 10 focused synthetic tests, Greptile 5/5/SUCCESS, CodeRabbit SKIPPED, resolved threads and confirmed owner ADMIN authority; it is unwatched. Cleanup evidence is accepted; actual calendar provisioning/history integration remain unverified. Ticket 02 implementation `2f19c9d` is committed from b993634 in its isolated checkout, awaiting independent review. Ticket 03 is queued behind 02's deletion/caller scope, not a changed canonical prerequisite. Ticket 08 source port `a55956b` passed independent Sol high review; current-main integration and two small review improvements require exact-head validation/recheck before PR. Ticket 04 remains incomplete and 05/17 blocked. No agent has read the vault, run real-data training or deployed trading jobs.

Canonical ticket `Status:` labels remain triage state as defined in `../../docs/agents/triage-labels.md`. Execution state below is separate. Only the coordinator edits this shared ledger.

The default prerequisite is **merged code plus accepted evidence**. Code merged by itself does not satisfy evidence gates. Evidence is tracked separately as (1) code merged, (2) actual training artifacts and accepted research evidence, and (3) elapsed paper records with their scorecards. No model or trading claim follows merely from a code merge. No stacked PRs: a dependent ticket starts from the merged current head.

Shared-resource status on 2026-11-15: no heavy training, real-data migration, registry, or paper-account writer is authorized or running. Ticket 08 uses a private CPU-only dummy-check runtime, with at most 2 GB new disk use and at least 8 GB left free; no GPU stack or shared environment writes. Permit one heavy training job at a time. Serialize external data writes, model-registry writes, GPU use, and paper-account writes. Keep raw data and large weights out of Git.

Setup merged as `31cd294937636777641fb02c46bc24b6488edac3`. Its final head `88673c8` received independent Sol high approval and Greptile 5/5, the applicable check passed, CodeRabbit explicitly skipped automatic review, and all review threads were resolved. Setup evidence is accepted. Coordinator and new workers use this updated-main base. The owner root checkout and its local changes remain untouched. This coordinator-only working ledger update will accompany a later independently reviewed implementation PR, not a direct main commit.

Initial disjoint implementation scopes after setup: ticket 01 owns operations/account/dashboard/contracts and relevant tests; ticket 04 owns a new isolated vault, readers, tests, and docs without wiring unrelated modules; ticket 08 owns a new model-port namespace, tests, and audit only, with the source Mac read-only. Recheck actual file/import overlap before dispatch; do not presume the scopes remain disjoint.

| Ticket | Execution state | Thread link | Branch | PR | Required evidence | Blockers |
|---|---|---|---|---|---|---|
| Setup | merged, evidence accepted | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-setup-20261115-01` | `t3/kairos-ai-setup` | https://github.com/Onkarj012/kairos/pull/9 | Merge 31cd294; static checks; exact-head Sol approval; Greptile 5/5; no unresolved threads | none |
| 01 | merged; cleanup evidence accepted | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket01-20261115-01` | `t3/kairos-ai-01-kept-parts` | https://github.com/Onkarj012/kairos/pull/11 | Merge b993634; final head 7db9535 Sol high approved; 10 synthetic tests; Greptile 5/5/SUCCESS; no unresolved threads | Calendar provisioning and actual-history integration unverified; legacy cutoff handoff to 03/04 |
| 02 | merged; scoped cleanup evidence accepted | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket02-20261115-01` | t3/kairos-ai-02-remove-old-runner | https://github.com/Onkarj012/kairos/pull/13 | Merge6223a37; exact0e858e7 Sol high approved;19synthetic checks;Greptile5/5/SUCCESS;no threads | Actual login/calendar/notification/real-history behavior unverified;no deployment |
| 03 | merged; scoped cleanup evidence accepted | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket03-20261115-01` | t3/kairos-ai-03-remove-research-gates | https://github.com/Onkarj012/kairos/pull/15 | Merge 4e858ff; exact cc51000 Sol high approved; 45 guarded tests; Greptile 5/5/SUCCESS; all 25 deletions explained | Actual stored-data rebuild and calendar provisioning unverified |
| 04 | infrastructure merged; ticket evidence incomplete | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket04-20261115-01` | `t3/kairos-ai-04-blind-vault` | https://github.com/Onkarj012/kairos/pull/10 | Merge 1e3d4fb; Sol high and Greptile 5/5 at exact head 08e07c7; 16 synthetic tests | Dedicated owner-service OS/IPC enforcement and real partition/count/checksum evidence absent; 05/17 remain blocked |
| 05 | blocked | — | — | — | Dataset rows/counts, three checks, masked ambiguous target/stop answers | 04 |
| 06 | blocked | — | — | — | Yardstick results table and run records | 05 |
| 07 | blocked | — | — | — | Table model artifact and tuning evidence | 06 |
| 08 | merged; source-port/audit evidence accepted | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket08-20261115-01` | `t3/kairos-ai-08-stockxpert-port` | https://github.com/Onkarj012/kairos/pull/12 | Merge 52a6d2d; final e341dfb Sol high approved; 2 synthetic tests; source hashes checked; Greptile 5/5/SUCCESS; no unresolved threads | Full feature/scaler pipeline and model quality unverified; 09 still requires 06/07 |
| 09 | blocked | — | — | — | Five-encoder model artifact and tuning evidence | 06, 07, 08 |
| 10 | blocked | — | — | — | One-reader model artifact and tuning evidence | 09 |
| 11 | blocked | — | — | — | Swing picks output and focused tests | 07 |
| 12 | blocked | — | — | — | Honest swing replay, conservative ambiguous-bar counts, and date refusal | 11 |
| 13 | blocked | — | — | — | Risk-state tests and documented loss budget | 12 |
| 14 | infrastructure merged; ticket evidence incomplete | `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket14-20261115-01` | t3/kairos-ai-14-scheduled-jobs | https://github.com/Onkarj012/kairos/pull/14 | Merge b86f709; exact cea2ce7 Sol high approved; 8 guarded tests, shell/unit checks; Greptile 5/5/SUCCESS; no unresolved threads | External monitor kill-alert/reminder delivery and real handlers absent; 14 incomplete, 15 blocked; no activation |
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
