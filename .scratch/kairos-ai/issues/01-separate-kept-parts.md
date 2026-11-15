# 01: Separate the kept parts from the old rules (prep work)

**What to build:** The parts we keep must stand on their own before anything is deleted. Kept parts: the daily price panel build, the corporate-action adjustment (splits and bonuses), the cost model, the hash-chained event log (each entry carries a fingerprint of the one before, so tampering shows), the paper accounts, and the local dashboard. Today the paper account code imports the old rules, the old sealed-year module and the old research log, and the dashboard reads through the old daily runner. Cut those links so tickets 02 and 03 can delete the old code without breaking anything kept. Plan: `../spec.md` section 9; owner direction 2026-11-15 in `decisions.md`.

**Blocked by:** Setup

**Status:** ready-for-agent

- [ ] The paper account and event log import nothing from the old rules, the old sealed-year module, the old research log or the old daily runner.
- [ ] The dashboard shows paper accounts and their event history without importing the old runner, rules or halt files.
- [ ] The cost model stands alone and has a test of its own.
- [ ] A short list in the PR names every module still depending on old code, for tickets 02 and 03 to remove.
- [ ] Nothing is deleted in this ticket. After a static import audit, the focused isolated tests pass using only the approved checks in `../safe-validation.md`; do not run the full suite.
