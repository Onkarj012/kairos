# 09: Five-encoder model rebuilt

**What to build:** The published design, re-tested fairly. Three readers of price history (7, 21 and 60 days), one for market mood and one for news, combined by an attention step that decides each day which reader to trust. In the first version the news reader is built but switched off, and the model must work with a reader missing. The paper's settings stay fixed, because they have already seen 2025 once. The three leaks are closed: answers are built only from future prices with no overlap with today; stock traits come from the training years only; nothing is filled from later dates. It is scored two ways: on the raw next-day move, and on a multi-day trend (5 and 10 days) built honestly from future prices. Fibonacci levels (prices at fixed fractions, such as 38.2% and 61.8%, of a recent rise or fall) are tested as inputs, with and without. It gives all five outputs and uses the shared confidence check from ticket 07. Plan: `../spec.md` sections 2 and 4 (model 2).

**Blocked by:** 06, 08

**Status:** ready-for-agent

- [ ] Trains on the swing dataset on this machine. Training time is measured and recorded. If it is too big, first use fewer stocks or a smaller model, then rent a card.
- [ ] Three random starts for each target (raw next-day, honest multi-day), all in the results table.
- [ ] Scored on 2023 against the yardsticks and the table model, with the with-Fibonacci and without-Fibonacci runs side by side.
- [ ] Runs with the news reader switched off and does not crash when it is missing.
- [ ] The shared training loop is reusable by tickets 10, 20, 26 and 27.
