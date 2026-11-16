# 04: Blind vault for 2025 and 2026

**What to build:** The blind years are the main proof that a model works, so the code must make peeking impossible, not just discouraged. Every price row dated 2025-01-01 or later, up to the day the vault is built, moves into a vault file. Training, tuning and scoring code cannot open it. The only way in is a "final test" call that records the date, the model's fingerprint (a hash of its weights and settings) and who asked. Plan: `../spec.md` section 6; decisions.md 2026-11-14 (two blind years).

Live use (agreed 2026-11-15): the daily picks job may read recent prices, but only to build that morning's input rows, never to score anything. Paper results from their start date count as the live record, not as blind-test evidence.

**Blocked by:** Setup

**Status:** ready-for-agent

- [ ] The training-side price reader returns no row dated on or after 2025-01-01. A test asks for 2025 rows and is refused.
- [ ] The vault holds the moved rows, stored with a checksum. Its row count is reported. No returns or scores are computed on it.
- [ ] Opening the vault through the final-test call writes a log line (date, model fingerprint, reason). Opening it any other way fails.
- [ ] The live input reader can get the last 60 sessions for building today's rows, but cannot get future answers or scores. A test proves this.
- [ ] The minute prices (ticket 17) can be added to the same vault later without code changes.

## Implementation note (2026-11-15 IST)

Independent review round 1 blocked the initial head on five guarantees. Coordinator fixes require a durably pre-provisioned audit directory and sync its file entry before returning authorization; exclude the morning request's own session; enforce flat scalar live values at write and read; accept an identical publication winner after a rename collision; and consume a verified private byte snapshot rather than reopen mutable source bytes. Five targeted synthetic regressions plus the original checks pass, **16 tests total**. No final-test entry point or vault was opened. Physical crash recovery remains untested. The multi-pack daily/minute opening session and compact encoding integration remain outstanding design work before any owner test or minute migration; one JSONL pack is not evidence of either.

Added `kairos_data/` with immutable checksummed JSONL pack storage, separate pre-2025 and live readers, a generic daily/minute pack kind, and a separate owner-service policy/opening interface. Synthetic tests cover partition boundaries, pre-2025 row counts and checksums, idempotent retries, live input projection and the 60-session bound, minute-pack compatibility, and audit policy. No held-out file was opened and no source-data migration was run.

The final-test Python entry point is not sufficient security by itself. A production owner service must run under a separate OS identity and accept requests through OS-authenticated IPC; same-user arbitrary code can bypass Python interfaces and file modes. The deployment adapter and OS isolation are not implemented here. Real source partition/count/checksum evidence is also outstanding, so this infrastructure commit does not complete ticket 04 or unlock tickets 05/17. The coordinator should integrate the package design choices into `decisions.md` before independent review.

## Implementation update (2026-11-15 IST)

Added explicit version-1 manifest encoding metadata. Missing encoding remains `jsonl` for compatibility; compact packs declare `parquet` and carry a validated scalar schema. Both kinds retain the same daily/minute and partition labels and use the existing training/live readers through the verified snapshot path. The bounded writer accepts one-pass iterables and writes configurable Parquet row groups; the reader scans bounded Arrow batches to validate bytes, schema, row count, dates, and partition before yielding from the same 8 MiB-spooled private snapshot.

Focused offline regressions cover legacy JSONL, daily/minute compact training reads, request boundary refusal, checksum/count/schema refusal before row yield, one-pass batches, schema drift, retry/collision behavior, live input/date guards, and a compact-versus-JSONL synthetic size comparison. No real data was read or migrated. This implementation does not finish ticket 04: multi-pack owner opening and enforced OS/IPC isolation remain outstanding. Tickets 05/17 remain blocked pending those prerequisites and accepted real migration evidence. No final-test entry point was invoked.

This encoding update supersedes the earlier provisional JSONL-only choice: JSONL remains the default and backward-compatible encoding, while explicit Parquet is available because `pyarrow` is already a runtime dependency. It does not change the per-session live layout or the separate owner-service boundary.

Coordinator inspection reproduced a nullable-column metadata failure: an all-null column reached schema serialization as `None` rather than the null type. Fixed metadata normalization and added a synthetic roundtrip plus frozen-null schema-drift refusal. The focused compact and existing vault-infrastructure files now pass **27 tests and 5 subtests**, with zero safety refusals, after an eight-local-file import audit and startup/plugin inspection. No final-test entry point was called. Independent exact-head review remains pending.

Original design choices, retained as history: use stdlib JSONL with one manifest per immutable pack rather than Parquet or a database, to avoid new dependencies and keep checksums/counts explicit. The JSONL-only encoding choice is superseded by the update above. Per-session live inputs, window selection before opening packs, and the separate OS owner-service boundary remain active; Python names/flags are not authorization. Alternatives were rejected for dependency/setup cost, unbounded reads of later sessions, and same-user Python bypass respectively. Source: implementation thread on ticket 04, 2026-11-15 IST.

## PR16 coverage follow-up (2026-11-16 IST)

Greptile correctly identified a committed coverage gap: malformed manifest schema fields failed parsing without testing the Parquet schema comparisons. Added direct well-formed-but-incorrect manifest column-name/type cases and a mismatched embedded `kairos.schema` case. The embedded case updates the exact byte checksum so it does not pass merely by detecting payload tampering. Every case asserts its specific schema error on the first `next`, before any row is returned. No production changes were needed. The guarded compact and existing vault-infrastructure selection passes **29 tests and 7 subtests**, zero safety refusals, after the eight-file local import audit. Fresh exact-head review remains required before push. Ticket04 remains incomplete and05/17 blocked; no final-test entry point, real data or vault was opened.
