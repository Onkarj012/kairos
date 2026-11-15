# 07: Table model (gradient-boosted trees)

**What to build:** The first learned model, and the bar the deep models must clear. Gradient-boosted trees (many small yes/no decision charts, each correcting the last; LightGBM is the usual tool) are fed a table of about 40 hand-made measures per stock per day. The model produces the five outputs every model must give: direction (chance of closing higher tomorrow, over 5 days and over 10 days), size (expected move against the market), range (expected high and low), target-before-stop, and confidence. It is trained three times from different random starts. This ticket also builds the shared confidence check that all later models reuse: on the checking year (2024), confirm that "70% sure" is right about 70% of the time (calibration), then fix the confidence cut-off there. Plan: `../spec.md` sections 4, 6 and 7.

**Blocked by:** 06

**Status:** ready-for-agent

- [ ] Trains in minutes on this machine and logs three random starts to the results table, with the spread between them.
- [ ] Scored on 2023 against every yardstick, with a list of which inputs mattered most.
- [ ] The calibration check and cut-off are set on 2024 only, are reusable by other models, and report how many predictions survive the cut-off.
- [ ] The saved model carries a fingerprint that the picks engine can load.
