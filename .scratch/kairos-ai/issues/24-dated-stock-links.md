# 24: Dated stock links

**What to build:** The links the ripple model needs, each with the date it became known, so a model never sees a link before it existed:
- the industry label for every stock (today, labels cover only 23 stocks);
- membership of Nifty 50, Nifty 500 and the sector indices (today there are monthly snapshots with a gap between eras).

Sources are NSE and niftyindices.com. Delivered-volume share (the part of the day's volume actually taken as delivery) is added to the panel here too, because it comes from the same NSE files. Plan: `../spec.md` sections 4 (model 4) and 5.

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] Every stock in the dataset has an industry label with effective and known-at dates, or is counted as unlabelled.
- [ ] Index membership history is continuous from the start of the data, with any remaining gaps counted.
- [ ] Delivered-volume share is a column in the dataset, marked missing where unavailable.
- [ ] Old links are never back-dated. A test checks one known index change.
