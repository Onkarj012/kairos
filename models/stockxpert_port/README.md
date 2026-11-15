# StockXpert V3 source port

This namespace contains the source needed to instantiate the paper's V3
five-encoder model and to audit its sample, scaling, and split behavior. The
model files were exported as text from the linked Mac project, not rewritten.
The manifest records their Mac HEAD and SHA-256 values. The Mac training script
was dirty at export time; the manifest records its separate working-copy hash.

The exported source imports the top-level `stockxpert` package. To keep this
port isolated from any unseen package startup code, its files live below
`source/stockxpert` and each package initializer here is a minimal docstring.
The V3 model and V3 dataset helpers have only their absolute import paths
adjusted to the isolated package path. Their class bodies are otherwise copied
from the byte-verified source. Optional data and news clients are not imported.

## Target leak audit

The target builder is copied at
`source/stockxpert/features/builder.py`. For horizon `h`, it computes
`raw_dlog[j] = log_close[j+h] - log_close[j]`, then applies
`rolling(max(3, min(h, 7)), center=True).mean().fillna(raw_dlog)`.
At `h=1`, the three-row centered window for label row `t` averages raw
returns `t-1`, `t`, and `t+1`. The `t-1` return ends at close `t`,
which is already visible to the sample ending at `t`. The label therefore
depends on an already-known move. The focused synthetic test perturbs only the
prior close and shows the label changes while close `t` and later closes
remain fixed.

The same builder falls back to the raw target where centered rolling produces
NaN near a series end. For the 10-day horizon, its seven-row window means the
last three eligible target rows use this unsmoothed fallback.

## Other source-level leakage

- `scripts/train_v3.py:209-246` computes liquidity medians, rolling-volatility
  medians, beta, and cross-sectional ranks over the full supplied price history,
  then writes those fixed traits onto each row. V3's
  `dataset/make_samples.py:24-35, 44-51` collapses the trait columns to one
  median vector per symbol and attaches it to every sample. Future dates can
  therefore affect earlier samples' traits.
- `scripts/train_v3.py:139, 170, 191` uses `bfill` after forward fill. A
  later nonmissing value can fill an earlier gap.
- `dataset/make_samples.py:164-174` makes each feature window inclusive of
  row `t`; context also comes from `t`. This makes the centered h=1 target's
  inclusion of the return ending at `t` material.
- `v3/dataset/splits.py:32-46` applies a 10-calendar-day embargo to sample
  dates. No overlap conclusion is made here; ticket 09 should verify target
  reach against split boundaries using synthetic dates before rebuilding the
  split behavior.

The target audit uses synthetic arithmetic only. No historical prices, saved
weights, checkpoints, market results, or held-out files were read.

## Runtime check

The focused CPU test creates random tensors in memory, calls the copied V3
model once, forms a scalar loss from its outputs, and backpropagates. This is a
dummy gradient check, not a training run.

The port follows the V3 classes instantiated by the exported train_v3.py and
config. I rejected a rewritten approximation because exact source was
available. The training script itself stays out of the package because it
contains the training and data-loading entry point. The copied feature builder
imports indicator and event-calendar modules that were outside this source
export; the leak test uses the exact pandas shift and centered rolling
expression from the copied builder without importing those unrelated feature
modules. The scaler source is copied but not runtime-tested because
scikit-learn is not installed in the approved environments.
