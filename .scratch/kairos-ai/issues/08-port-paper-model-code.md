# 08: Bring the paper's five-encoder code over from the Mac and settle the leak question

**What to build:** The published model (HybridSwingNet) lives on the Mac at `/Users/onkarj012/Projects/major_pro/StockXpert`, which is reachable as a linked T3 environment. The model code is under `src/stockxpert` (the five readers, the attention step that combines them, the sample builder, the date splits, the scaling). Copy the parts the rebuild needs into this repo and make them run here on dummy data. Then settle the open question from the paper review (`/tmp/kairos-research/out-major-pro.md`): the paper's target was a future move smoothed with a window centred on the day. Did that window include the move that had already happened today? Answer it by reading the code and running a small synthetic test with no market data. Plan: `../spec.md` section 2.

**Blocked by:** Setup

**Status:** ready-for-agent

- [x] The model builds and does one forward and backward pass on dummy data on this machine's graphics card (GTX 1650, 4 GB), or on the CPU if the card is unavailable.
- [x] A one-page note says yes or no on the smoothing leak, with the synthetic test that shows it.
- [x] The note also lists the paper's two smaller leaks (stock traits from the whole history, gaps filled from later dates) and where each lives in the copied code.
- [x] Nothing on the Mac is changed. No training is run on real data, and nothing is computed on dates on or after 2025-01-01.

## Implementation evidence

- Integrated onto merged main `b993634` and reran the implementation-linked target regression and dummy CPU forward/backward check under the audited isolated runner: **2 passed in 2.03s**, zero safety-hook refusals. The actual copied target AST was inspected without importing its missing feature dependencies. Main's kept-parts and vault code are unchanged by integration. Fresh independent integrated-head review remains required before PR.
- Independent Sol high approved source-preserving head `a55956b` and independently verified original/adapted hashes, synthetic model execution and the actual target method. Two non-blocking findings were addressed during integration onto merged main `b993634`: the committed leak test now executes the unchanged copied `_compute_targets` AST rather than duplicating its formula, and the audit note cites the copied fundamentals backfill separately from the unvendored market/macro backfills. The complete feature-builder package and sklearn scaler remain unverified; no training or model-quality claim follows from this source-port ticket. Integrated-head checks and independent recheck must pass before PR creation.
- Earlier coordinator validation audited 14 local transitive Python files and minimal initializers. Isolated `/usr/bin/python3 -I -S -B` startup used only the checkout, private CPU torch package directory and existing root dependencies read-only, disabled plugin autoload and caches, cleared `PYTEST_ADDOPTS` and bypassed pytest project config with `/dev/null`. A Python audit hook refused network/subprocess and real artifact access. Result at that earlier head: **2 passed in 1.98s**; diff check passed; disk had about 9.0 GB free.
- Ported the V3 five encoders, attention fusion, target builder, sample builder,
  date splits, scaler, and source model dependency into models/stockxpert_port.
  Exact upstream hashes and the import-path adapter are in provenance.md.
- For h=1, the centered target reads raw_dlog[t-1], which uses Close[t] and
  Close[t-1]; sample windows end at t. The synthetic test changes only
  Close[t-1] and observes a changed centered target while Close[t] and later
  closes remain fixed.
- Whole-history traits are calculated in exported Mac scripts/train_v3.py
  lines 209-246. Copied V3 sample code collapses those columns across each
  full symbol frame at source/stockxpert/v3/dataset/make_samples.py lines
  24-35 and 44-51. Later-value fills are at Mac script lines 139, 170, and 191.
- Focused runtime check used CPU PyTorch 2.11.0+cpu in private
  /tmp/kairos-ai-08-runtime, with audited existing dependencies read only.
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and empty PYTEST_ADDOPTS were set. Only
  tests/test_stockxpert_port.py ran. Result: 2 passed.
- Exported Mac source was read only. The dirty training script was used only
  to verify the listed lines. No data, checkpoints, training, market scores,
  held-out dates, broker calls, or Mac writes were involved.
