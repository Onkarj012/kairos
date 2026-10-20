"""Run both daily baselines across the frozen development folds."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.daily_backtest import BacktestResult, run_backtest
from evidence.daily_verdict import ACCOUNTS, FOLDS, THRESHOLDS, VERDICT_ACCOUNT, build_candidate, fold_metrics
from evidence.gates import evaluate_candidate
from evidence.research_ledger import ResearchLedgerWriter, read_events
from evidence.sealed_test import HOLDOUT_START, assert_dev_only, ensure_holdout_sealed, load_dev_panel
from evidence.trials import global_k
from ingest.daily_panel import PANEL_FILE, START, derived_root, replace_directory
from models.daily_baselines import STRATEGIES, Candidate, panel_as_of, trading_sessions


COHORT_DIR = "daily-runner/cohort-v1"
SESSION_LEDGER = "daily-runner/session-ledger-v1/sessions.jsonl"
REVIEW_MOVES = "daily-runner/adjustments-v1/review_moves.csv"
STUDY_ID = "daily-runner-stage1"
JOB_ID = "dev-folds"
DISABLED_CHECKS = {
    check
    for check, enabled in (
        ("beats_rung0_baselines_net_sharpe", THRESHOLDS.require_baseline_sharpe_beaten),
        ("sealed_test_once_passed", THRESHOLDS.require_sealed_test),
    )
    if not enabled
}


def _parser() -> argparse.ArgumentParser:
    """Build the runner command line and data-root defaults."""
    root = derived_root()
    parser = argparse.ArgumentParser(description="Run daily baseline development-fold backtests.")
    parser.add_argument("--out", type=Path, default=root / "daily-runner/backtest-v1")
    parser.add_argument("--ledger", type=Path, default=root / "daily-runner/research-ledger-v1/events.jsonl")
    return parser


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ledger_sessions(path: Path) -> list[date]:
    """Return expected ledger sessions from START onward."""
    sessions = {
        date.fromisoformat(row["session_date"])
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())
        if row.get("state") == "expected"
    }
    return sorted(session for session in sessions if session >= START)


def _git_state() -> tuple[str, bool]:
    """Read the current commit and whether the checkout has changes."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip())
    return commit, dirty


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    """Hash stable payload JSON without any recording timestamp."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cohort_by_month(cohort: pd.DataFrame) -> dict[str, list[str]]:
    """Build month-to-id lists while retaining the full cohort member universe."""
    return {
        str(month): sorted(group["security_id"].astype(str).unique().tolist())
        for month, group in cohort.groupby("month", sort=False)
    }


def _rank_cache(
    strategy_name: str,
    panel: pd.DataFrame,
    sessions: list[date],
    cohorts: Mapping[str, list[str]],
) -> dict[date, list[Candidate]]:
    """Rank every development session once for one strategy."""
    strategy = STRATEGIES[strategy_name]
    indexed = panel.set_index(["security_id", "session_date"]).sort_index()
    cache: dict[date, list[Candidate]] = {}
    for session in sessions:
        ids = cohorts.get(session.strftime("%Y-%m"), [])
        view = panel_as_of(indexed, sessions, session, ids, strategy.lookback_sessions) if ids else indexed.iloc[0:0].reset_index()
        cache[session] = strategy.rank(view, ids, session) if ids and not view.empty else []
    return cache


def _run_folds(
    panel: pd.DataFrame,
    sessions: list[date],
    cache: Mapping[date, list[Candidate]],
    capital: int,
) -> tuple[list[dict[str, Any]], list[pd.DataFrame], list[pd.DataFrame]]:
    """Run one strategy and account across all frozen folds."""
    fold_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    for fold in FOLDS:
        fold_sessions = [session for session in sessions if fold.start <= session <= fold.end]
        if not fold_sessions:
            raise ValueError(f"no expected development sessions in {fold.name}")
        fold_panel = panel.loc[panel["session_date"].between(fold.start, fold.end)].copy()
        result: BacktestResult = run_backtest(
            fold_panel,
            fold_sessions,
            lambda session: cache.get(session, []),
            capital=capital,
            first_signal=fold_sessions[0],
            last_session=fold_sessions[-1],
        )
        metrics = fold_metrics(result.trades, result.equity, capital)
        fold_rows.append({"name": fold.name, **metrics})
        trade_frames.append(result.trades.assign(fold=fold.name))
        equity_frames.append(result.equity.assign(fold=fold.name))
    return fold_rows, trade_frames, equity_frames


def _all_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate fold frames in their frozen order."""
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _review_matches(trades: pd.DataFrame, moves: pd.DataFrame) -> list[dict[str, str]]:
    """Find filled trades whose inclusive hold window contains a review move."""
    matches: list[dict[str, str]] = []
    if moves.empty or trades.empty:
        return matches
    review_rows = moves.copy()
    review_rows["session_date"] = pd.to_datetime(review_rows["session_date"]).dt.date
    for row in trades.itertuples(index=False):
        if int(row.qty) <= 0 or pd.isna(row.exit_session):
            continue
        entry = pd.Timestamp(row.entry_session).date()
        exit_ = pd.Timestamp(row.exit_session).date()
        selected = review_rows.loc[
            (review_rows["security_id"].astype(str) == str(row.security_id))
            & review_rows["session_date"].between(entry, exit_)
        ]
        for move in selected.itertuples(index=False):
            matches.append({
                "security_id": str(row.security_id),
                "entry_session": entry.isoformat(),
                "exit_session": exit_.isoformat(),
                "review_session": move.session_date.isoformat(),
                "reason": str(move.reason),
            })
    return matches


def _fmt(value: float, digits: int = 2) -> str:
    """Format a metric for compact markdown tables."""
    return f"{value:.{digits}f}"


def _report_strategy(
    name: str,
    verdict: Mapping[str, Any],
    account_data: Mapping[int, Mapping[str, Any]],
    review_matches: list[dict[str, str]],
) -> list[str]:
    """Render one strategy's verdict, fold tables, and gate evidence."""
    lines = [f"## {name}", "", f"Verdict: **{'eligible' if verdict['eligible'] else 'not eligible'}** at ₹{VERDICT_ACCOUNT:,} ({verdict['label']}).", ""]
    for account in ACCOUNTS:
        results = account_data[account]["fold_results"]
        lines.extend([
            f"### ₹{account:,} account",
            "",
            "| Fold | Trades | Mean bps | CI low | Win rate | Fold return | Max DD | Sharpe |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for fold in results:
            lines.append(
                f"| {fold['name']} | {fold['trades']} | {_fmt(fold['mean_net_bps'])} | "
                f"{_fmt(fold['ci95_low_bps'])} | {_fmt(fold['win_rate'] * 100)}% | "
                f"{_fmt(fold['fold_return'] * 100)}% | {_fmt(fold['max_drawdown'] * 100)}% | {_fmt(fold['daily_sharpe'])} |"
            )
        lines.append("")
    lines.extend(["### Gate checks at ₹50,000", "", "| Check | Result |", "| --- | --- |"])
    for check, passed in verdict["checks"].items():
        result = "off for baselines" if check in DISABLED_CHECKS else ("PASS" if passed else "FAIL")
        lines.append(f"| {check} | {result} |")
    break_even = verdict["break_even_k"] or "none (DSR < 0.95 even at k=1)"
    lines.extend([
        "",
        f"DSR at k={verdict['k']}: {_fmt(verdict['dsr'], 4)}. Break-even k: {break_even}.",
        f"Entry misses: {verdict['entry_misses']}. Exit-band closes: {verdict['exit_band_closes']}.",
        f"Flag counts: `{json.dumps(verdict['flag_counts'], sort_keys=True)}`.",
        f"Review-move trades: {len(review_matches)}.",
    ])
    for match in review_matches[:10]:
        lines.append(
            f"- {match['security_id']}: {match['entry_session']}..{match['exit_session']}, "
            f"review row {match['review_session']} ({match['reason']})."
        )
    lines.append("")
    return lines


def _write_outputs(
    out_dir: Path,
    strategies_data: Mapping[str, Mapping[str, Any]],
    report: str,
) -> None:
    """Write the full output tree to staging and swap it into place."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        for strategy, data in strategies_data.items():
            strategy_dir = staging / strategy
            strategy_dir.mkdir(parents=True, exist_ok=True)
            for account, account_data in data["accounts"].items():
                account_dir = strategy_dir / str(account)
                account_dir.mkdir(parents=True, exist_ok=True)
                account_data["trades"].to_csv(account_dir / "trades.csv", index=False)
                account_data["equity"].to_csv(account_dir / "equity.csv", index=False)
            (strategy_dir / "verdict.json").write_text(
                json.dumps(data["verdict"], indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        (staging / "report.md").write_text(report, encoding="utf-8")
        replace_directory(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Load guarded development data, run baselines, write reports, and append verdicts."""
    root = derived_root()
    ledger = ResearchLedgerWriter(args.ledger)
    ensure_holdout_sealed(ledger)
    panel_path = root / "daily-runner/panel-v1" / PANEL_FILE
    cohort_path = root / COHORT_DIR / "cohort.parquet"
    session_path = root / SESSION_LEDGER
    panel = load_dev_panel(panel_path)
    cohort = pd.read_parquet(cohort_path)
    panel["security_id"] = panel["security_id"].astype(str)
    panel = panel.sort_values(["security_id", "session_date"]).reset_index(drop=True)
    sessions = trading_sessions(_ledger_sessions(session_path))
    sessions = [session for session in sessions if session < HOLDOUT_START]
    assert_dev_only(sessions)
    cohorts = _cohort_by_month(cohort)
    prior_identities = {
        str(event.payload["strategy"])
        for event in read_events(args.ledger)
        if event.event_type == "GATE_VERDICT" and isinstance(event.payload.get("strategy"), str)
    }
    identities = prior_identities | {f"{name}@{strategy.version}" for name, strategy in STRATEGIES.items()}
    k = global_k() + len(identities)
    panel_hash = _sha256(panel_path)
    cohort_hash = _sha256(cohort_path)
    commit, dirty = _git_state()
    moves_path = root / REVIEW_MOVES
    moves = pd.read_csv(moves_path, dtype={"security_id": str}, keep_default_na=False) if moves_path.exists() else pd.DataFrame()
    outcomes: dict[str, dict[str, Any]] = {}
    report_lines = ["# Daily baseline development-fold report", ""]
    for strategy_name in STRATEGIES:
        cache = _rank_cache(strategy_name, panel, sessions, cohorts)
        account_data: dict[int, dict[str, Any]] = {}
        for account in ACCOUNTS:
            fold_results, trade_frames, equity_frames = _run_folds(panel, sessions, cache, account)
            trades = _all_frames(trade_frames)
            equity = _all_frames(equity_frames)
            account_data[account] = {
                "fold_results": fold_results,
                "trades": trades,
                "equity": equity,
            }
        verdict_account = account_data[VERDICT_ACCOUNT]
        returns = pd.to_numeric(verdict_account["equity"]["daily_return"], errors="coerce").dropna()
        candidate = build_candidate(verdict_account["fold_results"], verdict_account["trades"], returns, k)
        gate = evaluate_candidate(candidate, THRESHOLDS)
        aggregate = fold_metrics(verdict_account["trades"], verdict_account["equity"], VERDICT_ACCOUNT)
        review_matches = _review_matches(verdict_account["trades"], moves)
        strategy_identity = f"{strategy_name}@{STRATEGIES[strategy_name].version}"
        payload = {
            "strategy": strategy_identity,
            "label": "dev-fold verdict",
            "account": VERDICT_ACCOUNT,
            "eligible": gate["eligible"],
            "rejection_reasons": gate["rejection_reasons"],
            "checks": gate["checks"],
            "k": k,
            "dsr": candidate["deflated_sharpe_ratio"],
            "break_even_k": candidate["break_even_k"],
            "per_fold_mean_bps": {row["name"]: row["mean_net_bps"] for row in verdict_account["fold_results"]},
            "git_commit": commit,
            "git_dirty": dirty,
            "panel_sha256": panel_hash,
            "cohort_sha256": cohort_hash,
        }
        verdict = {
            **payload,
            "trades": aggregate["trades"],
            "entry_misses": aggregate["entry_misses"],
            "exit_band_closes": aggregate["exit_band_closes"],
            "flag_counts": aggregate["flag_counts"],
            "candidate": candidate,
            "gate": gate,
            "thresholds": {
                "ci_low_min_bps": THRESHOLDS.ci_low_min_bps,
                "min_trades": THRESHOLDS.min_trades,
                "min_trading_days": THRESHOLDS.min_trading_days,
                "max_single_symbol_share": THRESHOLDS.max_single_symbol_share,
                "min_distinct_symbols": THRESHOLDS.min_distinct_symbols,
                "min_positive_fold_share": THRESHOLDS.min_positive_fold_share,
                "worst_fold_floor_bps": THRESHOLDS.worst_fold_floor_bps,
                "min_dsr": THRESHOLDS.min_dsr,
            },
        }
        outcomes[strategy_name] = {"accounts": account_data, "verdict": verdict, "review_matches": review_matches}
        report_lines.extend(_report_strategy(strategy_name, verdict, account_data, review_matches))
    report_lines.extend([
        "## Notes",
        "",
        "- The sector cap is not applied because sector data is unavailable.",
        "- `worst_fold_floor_bps = 0` requires every fold mean to be at least 0 bps, which is stricter than the 70% positive-fold share.",
        "- The holdout is sealed. The last development exit is at or before 2025-09-30.",
        "- Costs use the Zerodha delivery profile with 5 bps per side slippage.",
        "",
    ])
    _write_outputs(args.out, outcomes, "\n".join(report_lines))
    for strategy_name in STRATEGIES:
        payload = {key: value for key, value in outcomes[strategy_name]["verdict"].items() if key in {
            "strategy", "label", "account", "eligible", "rejection_reasons", "checks", "k", "dsr",
            "break_even_k", "per_fold_mean_bps", "git_commit", "git_dirty", "panel_sha256", "cohort_sha256",
        }}
        event_id = f"gate-verdict-{payload['strategy']}-{_canonical_hash(payload)}"
        ledger.append(
            event_id=event_id,
            event_type="GATE_VERDICT",
            study_id=STUDY_ID,
            job_id=JOB_ID,
            attempt=1,
            payload=payload,
        )
    return {
        "out": str(args.out),
        "ledger": str(args.ledger),
        "strategies": {
            name: {
                "eligible": item["verdict"]["eligible"],
                "trades": item["verdict"]["trades"],
                "per_fold_mean_bps": item["verdict"]["per_fold_mean_bps"],
                "dsr": item["verdict"]["dsr"],
                "break_even_k": item["verdict"]["break_even_k"],
            }
            for name, item in outcomes.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Run the daily development-fold backtest and print its summary."""
    args = _parser().parse_args(argv)
    print(json.dumps(_run(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
