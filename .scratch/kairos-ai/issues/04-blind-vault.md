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
