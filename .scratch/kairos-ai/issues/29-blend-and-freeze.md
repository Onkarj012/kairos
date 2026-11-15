# 29: Blend, then freeze the final model for each style

**What to build:** For each style (swing and same-day), try a blend: the average of the best two or three models, which is usually steadier than any one. Phase 4's daily and minute ripple verdicts (tickets 27 and 28) must both be recorded before freeze. Then freeze the winner: model weights, fingerprint, settings, data version, code version, confidence cut-off, picks-engine settings, and the pass marks for the blind test. Once frozen, nothing changes before ticket 30. Plan: `../spec.md` sections 4 (model 5) and 12 (phases 4 and 5).

**Blocked by:** 26, 27, 28, 21

**Status:** ready-for-agent

- [ ] The blend is scored like any other model and kept only if it beats the best single model by more than the seed spread.
- [ ] A frozen spec file per style, with its fingerprint, is checked in.
- [ ] Pass and fail marks for the blind test are written in the frozen spec before anyone opens the vault.
