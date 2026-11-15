# Source provenance

Source project: `/Users/onkarj012/Projects/major_pro/StockXpert`

Source Git HEAD: `7b3a865de5a5f5f7cd84852b29e27f84722fcbb8`

The exported tree was read only. The full SHA-256 values below were provided
with the exact source text in the completed Mac helper activity. Source files
were locally hashed after reconstruction. V3 absolute imports were adjusted
to the isolated port package; the local hashes are listed separately below.

| Mac source file | Source SHA-256 | Local treatment |
|---|---|---|
| `src/stockxpert/models/stockxpert.py` | `6d95f5c9ad335928a36d5382813755a0de70c498200c37428fc3a3ab9fee7c56` | Source text, trailing whitespace normalized |
| `src/stockxpert/v3/models/stockxpert.py` | `aab68c7c755f5e2bdd59fd9b218189d9707098123227e1df2829787ea33b5e32` | Absolute imports redirected into this namespace |
| `src/stockxpert/models/encoders.py` | `4ec476ce75a2b78886cadbd825abdab39cd2a04ada194215628fc44f4a4f1989` | Source text, trailing whitespace normalized |
| `src/stockxpert/models/fusion.py` | `34b18bd350c51026b069bafadcb56ef59462efa4cbab6ac20fc37ad956b3f320` | Source text, trailing whitespace normalized |
| `src/stockxpert/models/sentiment_encoder.py` | `d4924212d7a37e622d29f79a0d89abc674a438965ad7c16207c94c99448045c9` | Source text, trailing whitespace normalized |
| `src/stockxpert/models/time2vec.py` | `bbbc124c0c88514db0719f5aed4aa230403964bb3044a124159666ee0402af8d` | Source text, trailing whitespace normalized |
| `src/stockxpert/features/builder.py` | `7f61193f72fdf122ae79d82214ad8e08ff874dddb9bb15018621613b0d3ffff7` | Source text, trailing whitespace normalized |
| `src/stockxpert/dataset/make_samples.py` | `e01232bcbb45dd91297a5f5e9276038759f8877a6cf69ba1e4b988609e2e194f` | Source text, trailing whitespace normalized |
| `src/stockxpert/dataset/scaling.py` | `253512d208bc2c6d3907a6dfa91829d563c3d56b845ebda96adc4a8293afabfb` | Source text, trailing whitespace normalized |
| `src/stockxpert/v3/dataset/make_samples.py` | `4cd4cb65b6ac8bc1c3cd11f84efe998af1818dc914ae76406fd6fa31b89ba242` | Absolute imports redirected into this namespace |
| `src/stockxpert/v3/dataset/splits.py` | `22de8f9ce18ef30a5b4ed932f88f3a658705867a99ef88138386cb2073d56fc7` | Absolute imports redirected into this namespace |

The exported source files were byte-hash verified before cleanup. Trailing
whitespace and extra blank lines at end of file were removed so the port passes
`git diff --check`. The three adapted files verify against their source after
reversing the import-path substitutions and applying the same whitespace cleanup.
Their local SHA-256 values are:

| Local file | SHA-256 |
|---|---|
| `source/stockxpert/v3/models/stockxpert.py` | `b8c2b9d23d2542e220eab1a93779f47b544a19b397e811217d70d27dcda2e668` |
| `source/stockxpert/v3/dataset/make_samples.py` | `1b6f4e4e0daa45417af94f3114353227e36744efd540c3b88a72905faf783aef` |
| `source/stockxpert/v3/dataset/splits.py` | `60997d77854350b507569423005f3c0014382307052fa23bba462810358a68d8` |

`scripts/train_v3.py` was dirty on the Mac. Its exported working-copy hash
was `515165785b4ac79b0a1e908293d010e36ade2f9c61c22d78f15db28c6e7374c5`
(28,887 bytes). The clean HEAD version hash was
`2ba0acd79c9e82627165a2de003db0540ff2ab12f85b28981fe6df3e50539047`.
The working copy was used only to verify trait and backfill sites; the script
was not copied or executed. The reviewed config hash is
`3956f168de3b71ab617ac7090e22e3d67b51c4f00eb5d6fe31c8af694a554e74`.

The original Mac package initializer files were not exported or executed.
This port substitutes minimal docstring-only initializers for isolation.

## Local runtime

The isolated environment is `/tmp/kairos-ai-08-runtime`. The only package
installed there was the 190,312,281-byte Linux CPython 3.12 CPU wheel
`torch==2.11.0+cpu`, downloaded from the official PyTorch CPU index with uv
cache disabled and installed with dependencies disabled. The check reused the
coordinator-approved root environment read only via `PYTHONPATH`:
`numpy==2.5.4`, `pandas==3.0.6`, and `pytest==9.1.1`. PyTorch reported
`cuda_available=False`. The private environment occupied 716 MB after the
install; the filesystem had 9.0 GB free after testing. No GPU packages,
scikit-learn, or other dependencies were installed.

Focused command:

```sh
PYTHONPATH=/home/onkarj012/Projects/market/kairos/.venv/lib/python3.12/site-packages \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS= \
/tmp/kairos-ai-08-runtime/bin/python -m pytest -q tests/test_stockxpert_port.py
```

Result: 2 passed in 1.92 seconds; peak resident memory was 324,664 KB.
