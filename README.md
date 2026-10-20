# Kairos

Kairos is a daily swing system for NSE cash equities. Every trading evening it ranks the 200 most liquid stocks, proposes at most two buys, holds each for three sessions, and reconciles against official prices or broker fills. It runs on paper first. Live trading needs a passed evidence gate, a paper record, and one-tap approval.

Doing nothing is a valid outcome. If no strategy beats costs on sealed evidence, paper keeps running and live never unlocks.

## Status

Stage 0, the clean daily panel and universe, is in review. Later stages add baselines and a backtest, the paper runner, Telegram approval, and a disabled Kite adapter.

## Layout

| Path | What it holds |
|---|---|
| `ingest/` | Bhavcopy collection and parsing, session calendar, rename lineage, fund classification, corporate actions, adjustment factors |
| `features/daily_universe.py` | Monthly top-200 cohort by trailing traded value |
| `evidence/` | Costs, gates, walk-forward, sealed test, research ledger, session ledger, global trial count |
| `ops/` | Paper ledger, halts, alerts, broker gate, graduation |
| `serve/` | FastAPI dashboard |
| `contracts/` | Typed records shared across the above |
| `scripts/data/` | Command-line entry points for each build step |

## Data

Market data never goes in git. Every build reads retained source bytes, checks their sha256, and writes to `derived_data/`, or to the directory in `KAIROS_DERIVED_DATA`. The same inputs give a byte-identical panel.

```bash
uv sync
uv run python scripts/data/build_daily_panel.py
uv run python scripts/data/check_daily_panel.py
uv run pytest
```

## License

MIT. See `LICENSE`.
