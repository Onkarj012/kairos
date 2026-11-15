# 26: Retrain with longer history and richer inputs

**What to build:** Retrain the table model, the five-encoder model and the one-reader model on the extended history (from 2015) and the step 2 market inputs. The news reader is conditional on ticket 25's accepted coverage evidence; do not enable it before that evidence is accepted. Missing news never blocks the core richer-input run. Each addition is a change written down before it runs, with the improvement it is expected to bring. It is kept only if it improves the replay result on 2023 by more than the spread between random starts, and the gain does not come from a handful of stocks or one month. Otherwise it is dropped and logged. Plan: `../spec.md` sections 7 and 12 (phase 4).

**Blocked by:** 09, 10, 18, 23

**Status:** ready-for-agent

- [ ] Every change has a pre-written expectation in the results table before its run.
- [ ] Old and new are run on the same data with the same random starts.
- [ ] A short kept/dropped list, each with its numbers.
- [ ] The best swing model so far is named for the picks engine.
- [ ] News reader activation, if any, cites accepted ticket 25 coverage evidence; otherwise the core run proceeds with news off.
