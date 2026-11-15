# 08: Bring the paper's five-encoder code over from the Mac and settle the leak question

**What to build:** The published model (HybridSwingNet) lives on the Mac at `/Users/onkarj012/Projects/major_pro/StockXpert`, which is reachable as a linked T3 environment. The model code is under `src/stockxpert` (the five readers, the attention step that combines them, the sample builder, the date splits, the scaling). Copy the parts the rebuild needs into this repo and make them run here on dummy data. Then settle the open question from the paper review (`/tmp/kairos-research/out-major-pro.md`): the paper's target was a future move smoothed with a window centred on the day. Did that window include the move that had already happened today? Answer it by reading the code and running a small synthetic test with no market data. Plan: `../spec.md` section 2.

**Blocked by:** Setup

**Status:** ready-for-agent

- [ ] The model builds and does one forward and backward pass on dummy data on this machine's graphics card (GTX 1650, 4 GB), or on the CPU if the card is unavailable.
- [ ] A one-page note says yes or no on the smoothing leak, with the synthetic test that shows it.
- [ ] The note also lists the paper's two smaller leaks (stock traits from the whole history, gaps filled from later dates) and where each lives in the copied code.
- [ ] Nothing on the Mac is changed. No training is run on real data, and nothing is computed on dates on or after 2025-01-01.
