"""Economic metrics used by the sealed research and paper-trading gates."""
from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any, Iterable, Mapping


# Sleeve F used 36 Phase-2, 24 extended, and 28 Phase-3 configurations.  This
# campaign-wide count is intentionally monotonic: add future searched configs,
# but never reset it for a narrower follow-up grid.
SLEEVE_F_CAMPAIGN_TRIALS = 88

# Day-block draws use 5th/95th percentiles, which form a two-sided 90% CI.
DAY_BLOCK_BOOTSTRAP_CONFIDENCE = 0.90


def day_block_bootstrap_ci_label(confidence: float = DAY_BLOCK_BOOTSTRAP_CONFIDENCE) -> str:
    """Return the display label corresponding to a day-block CI confidence."""
    return f"{float(confidence):.0%} CI"


def block_bootstrap_ci(values: Iterable[float], *, samples: int = 1_000, confidence: float = 0.95, seed: int = 42) -> tuple[float, float]:
    """Deterministic moving-block bootstrap CI for mean bps/trade.

    Intraday observations are autocorrelated, so resampling short contiguous
    blocks is deliberately more conservative than iid resampling.
    """
    data = list(values)
    if not data:
        return (0.0, 0.0)
    block = max(1, int(math.sqrt(len(data))))
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw: list[float] = []
        while len(draw) < len(data):
            start = rng.randrange(len(data))
            draw.extend(data[start : min(len(data), start + block)])
        means.append(sum(draw[: len(data)]) / len(data))
    means.sort()
    tail = (1.0 - confidence) / 2.0
    return means[int(tail * (samples - 1))], means[int((1.0 - tail) * (samples - 1))]


def day_block_bootstrap_ci(
    values_by_day: Mapping[Any, Iterable[float]],
    *,
    samples: int = 1_000,
    confidence: float = DAY_BLOCK_BOOTSTRAP_CONFIDENCE,
    seed: int = 42,
) -> tuple[float, float]:
    """Deterministic two-sided day-block bootstrap CI for mean bps/trade.

    Each bootstrap draw resamples trading days with replacement, then computes
    the per-trade mean over all returns in the sampled day blocks. Its display
    label is derived from ``confidence`` via ``day_block_bootstrap_ci_label``.
    """
    day_blocks = [[float(value) for value in values] for _, values in sorted(values_by_day.items(), key=lambda item: str(item[0]))]
    day_blocks = [block for block in day_blocks if block]
    if not day_blocks:
        return (0.0, 0.0)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    lower_quantile = (1.0 - confidence) / 2.0
    draws = max(1, int(samples))
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(draws):
        total = 0.0
        count = 0
        for _day in day_blocks:
            block = day_blocks[rng.randrange(len(day_blocks))]
            total += sum(block)
            count += len(block)
        means.append(total / count if count else 0.0)
    means.sort()
    return means[int(lower_quantile * (draws - 1))], means[int((1.0 - lower_quantile) * (draws - 1))]


def deflated_sharpe_ratio(returns: Iterable[float], *, trials: int) -> float:
    """Return the normal-CDF-scaled multiple-testing adjusted Sharpe score.

    Values near one mean the observed Sharpe exceeds the expected best Sharpe
    from ``trials`` independent candidates under a zero-edge null.
    """
    data = [float(value) for value in returns]
    if len(data) < 2:
        return 0.0
    mean = sum(data) / len(data)
    std = _std(data)
    if not std:
        return 0.0
    sharpe_value = mean / std
    trial_count = max(1, int(trials))
    benchmark = 0.0
    if trial_count > 1:
        gamma = 0.5772156649015329
        z_one = _inverse_normal_cdf(1.0 - 1.0 / trial_count)
        z_two = _inverse_normal_cdf(1.0 - 1.0 / (trial_count * math.e))
        benchmark = ((1.0 - gamma) * z_one + gamma * z_two) / math.sqrt(len(data) - 1)
    skew = _moment(data, mean, std, 3)
    kurtosis = _moment(data, mean, std, 4)
    denominator = math.sqrt(max(1e-12, 1.0 - skew * sharpe_value + ((kurtosis - 1.0) / 4.0) * sharpe_value * sharpe_value))
    z = (sharpe_value - benchmark) * math.sqrt(len(data) - 1) / denominator
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def economic_metrics(returns_bps: Iterable[float], daily_returns_bps: Iterable[float] = ()) -> dict[str, float]:
    returns = list(returns_bps)
    daily = list(daily_returns_bps)
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    expectancy = sum(returns) / len(returns) if returns else 0.0
    ci_low, ci_high = block_bootstrap_ci(returns)
    payoff = (gross_profit / len(wins)) / (gross_loss / len(losses)) if wins and losses else 0.0
    pf = gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)
    return {
        "net_expectancy_bps": expectancy,
        "expectancy_ci95_low_bps": ci_low,
        "expectancy_ci95_high_bps": ci_high,
        "net_hit_rate": len(wins) / len(returns) if returns else 0.0,
        "payoff_ratio": payoff,
        "profit_factor": pf,
        "max_drawdown_bps": max_drawdown(returns),
        "daily_sharpe": sharpe(daily),
        "daily_sortino": sortino(daily),
    }


def max_drawdown(values: Iterable[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def sharpe(values: Iterable[float]) -> float:
    data = list(values)
    if len(data) < 2:
        return 0.0
    mean = sum(data) / len(data)
    variance = sum((value - mean) ** 2 for value in data) / (len(data) - 1)
    return mean / math.sqrt(variance) * math.sqrt(252) if variance else 0.0


def sortino(values: Iterable[float]) -> float:
    data = list(values)
    downside = [min(0.0, value) ** 2 for value in data]
    deviation = math.sqrt(sum(downside) / len(data)) if data else 0.0
    return (sum(data) / len(data)) / deviation * math.sqrt(252) if deviation else 0.0


def contribution_shares(trades: Iterable[dict[str, object]], key: str) -> dict[str, float]:
    totals: Counter[str] = Counter()
    total = 0.0
    for trade in trades:
        value = float(trade["net_bps"])
        totals[str(trade[key])] += value
        total += value
    denominator = abs(total)
    return {name: abs(value) / denominator if denominator else 0.0 for name, value in totals.items()}


def bootstrap_sharpe_ci(
    returns: Iterable[float],
    *,
    samples: int = 10_000,
    confidence: float = 0.95,
    risk_free_rate: float = 0.0,
    annualization_factor: float = 252.0,
    seed: int = 42,
) -> dict[str, float]:
    data = [float(item) - risk_free_rate for item in returns]
    if not data:
        return {"sharpe": 0.0, "ci_lower": 0.0, "ci_upper": 0.0, "std": 0.0}
    rng = random.Random(seed)
    sharpes = []
    for _ in range(samples):
        draw = [data[rng.randrange(len(data))] for _ in data]
        sharpes.append(_annualized_sharpe(draw, annualization_factor))
    sharpes.sort()
    tail = (1.0 - confidence) / 2.0
    return {
        "sharpe": _annualized_sharpe(data, annualization_factor),
        "ci_lower": sharpes[int(tail * (samples - 1))],
        "ci_upper": sharpes[int((1.0 - tail) * (samples - 1))],
        "std": _std(sharpes),
    }


def paired_return_test(model_returns: Iterable[float], benchmark_returns: Iterable[float]) -> dict[str, float]:
    diff = [float(model) - float(benchmark) for model, benchmark in zip(model_returns, benchmark_returns)]
    if not diff:
        return {"t_stat": 0.0, "p_value": 1.0, "mean_diff": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
    mean = sum(diff) / len(diff)
    std = _std(diff)
    se = std / math.sqrt(len(diff)) if diff else 0.0
    t_stat = mean / se if se else 0.0
    p_value = 2.0 * (1.0 - _normal_cdf(abs(t_stat)))
    return {
        "t_stat": t_stat,
        "p_value": p_value,
        "mean_diff": mean,
        "ci_lower": mean - 1.96 * se,
        "ci_upper": mean + 1.96 * se,
    }


def direction_accuracy_test(n_correct: int, n_total: int, null_probability: float = 0.5) -> dict[str, float]:
    if n_total <= 0:
        raise ValueError("n_total must be positive")
    if not 0 <= n_correct <= n_total:
        raise ValueError("n_correct must be between 0 and n_total")
    p_value = _binomial_sf(n_correct, n_total, null_probability)
    accuracy = n_correct / n_total
    ci_low, ci_high = wilson_interval(n_correct, n_total)
    return {
        "accuracy": accuracy,
        "p_value": p_value,
        "ci_lower": ci_low,
        "ci_upper": ci_high,
        "n_correct": int(n_correct),
        "n_total": int(n_total),
    }


def binomial_hit_rate_test(n_wins: int, n_total: int, *, null_probability: float = 0.5) -> dict[str, float]:
    return direction_accuracy_test(n_wins, n_total, null_probability)


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    z = 1.959963984540054 if confidence == 0.95 else _inverse_normal_cdf(0.5 + confidence / 2.0)
    phat = successes / total
    denom = 1.0 + z * z / total
    centre = (phat + z * z / (2.0 * total)) / denom
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * total)) / total) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def diebold_mariano_test(errors_model: Iterable[float], errors_benchmark: Iterable[float], power: int = 2) -> dict[str, float]:
    loss_diff = [abs(float(benchmark)) ** power - abs(float(model)) ** power for model, benchmark in zip(errors_model, errors_benchmark)]
    if not loss_diff:
        return {"dm_stat": 0.0, "p_value": 1.0, "mean_loss_diff": 0.0}
    mean = sum(loss_diff) / len(loss_diff)
    variance = sum((value - mean) ** 2 for value in loss_diff) / (len(loss_diff) - 1) if len(loss_diff) > 1 else 0.0
    dm_stat = mean / math.sqrt(variance / len(loss_diff)) if variance else 0.0
    p_value = 2.0 * (1.0 - _normal_cdf(abs(dm_stat)))
    return {"dm_stat": dm_stat, "p_value": p_value, "mean_loss_diff": mean}


def information_coefficient(predicted_returns: Iterable[float], actual_returns: Iterable[float]) -> dict[str, float]:
    predicted = list(predicted_returns)
    actual = list(actual_returns)
    if len(predicted) != len(actual) or len(predicted) < 2:
        return {"ic": 0.0, "p_value": 1.0}
    ic = _pearson(_ranks(predicted), _ranks(actual))
    if len(predicted) <= 3 or abs(ic) >= 1.0:
        p_value = 0.0 if abs(ic) >= 1.0 else 1.0
    else:
        t_stat = ic * math.sqrt((len(predicted) - 2) / max(1e-12, 1.0 - ic * ic))
        p_value = 2.0 * (1.0 - _normal_cdf(abs(t_stat)))
    return {"ic": ic, "p_value": p_value}


def compute_precision_recall_direction(predicted_direction: Iterable[int], actual_direction: Iterable[int]) -> dict[str, float]:
    pairs = [(int(pred), int(actual)) for pred, actual in zip(predicted_direction, actual_direction)]
    tp = sum(1 for pred, actual in pairs if pred == 1 and actual == 1)
    fp = sum(1 for pred, actual in pairs if pred == 1 and actual == 0)
    fn = sum(1 for pred, actual in pairs if pred == 0 and actual == 1)
    tn = sum(1 for pred, actual in pairs if pred == 0 and actual == 0)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    total = tp + fp + fn + tn
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": (tp + tn) / total if total else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def _annualized_sharpe(values: list[float], annualization_factor: float) -> float:
    std = _std(values)
    return (sum(values) / len(values)) / std * math.sqrt(annualization_factor) if values and std else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _moment(values: list[float], mean: float, std: float, power: int) -> float:
    return sum(((value - mean) / std) ** power for value in values) / len(values) if values and std else 0.0


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _inverse_normal_cdf(probability: float) -> float:
    # Acklam's approximation is overkill here; binary search is deterministic
    # and sufficient for confidence intervals.
    low, high = -8.0, 8.0
    for _ in range(80):
        mid = (low + high) / 2.0
        if _normal_cdf(mid) < probability:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _binomial_sf(successes: int, total: int, probability: float) -> float:
    if not 0.0 <= probability <= 1.0:
        raise ValueError("null_probability must be in [0, 1]")
    if successes <= 0:
        return 1.0
    if probability == 0.0:
        return 0.0
    if probability == 1.0:
        return 1.0
    terms = []
    for k in range(successes, total + 1):
        log_term = (
            math.lgamma(total + 1)
            - math.lgamma(k + 1)
            - math.lgamma(total - k + 1)
            + k * math.log(probability)
            + (total - k) * math.log1p(-probability)
        )
        terms.append(math.exp(log_term))
    return min(1.0, sum(terms))


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted((float(value), index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][0] == ordered[cursor][0]:
            end += 1
        rank = (cursor + end + 1) / 2.0
        for _, index in ordered[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denom_left = math.sqrt(sum((a - mean_left) ** 2 for a in left))
    denom_right = math.sqrt(sum((b - mean_right) ** 2 for b in right))
    return numerator / (denom_left * denom_right) if denom_left and denom_right else 0.0


def moving_block_bootstrap_ci(
    values: Iterable[float],
    *,
    block_size: int = 20,
    samples: int = 10_000,
    confidence: float = 0.95,
    seed: int = 42,
    statistic: Any = None,
) -> tuple[float, float]:
    """Return a deterministic moving-block bootstrap confidence interval.

    This is additive to the older ``block_bootstrap_ci`` primitive.  Blocks
    are sampled with replacement from valid non-wrapping starts, which
    keeps every draw exactly the same length and preserves local dependence.
    The default statistic is the arithmetic mean; callers may provide a
    deterministic callable such as an annualized Sharpe function.
    """
    data = [float(value) for value in values]
    if not data:
        return (0.0, 0.0)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if int(block_size) < 1 or int(samples) < 1:
        raise ValueError("block_size and samples must be positive")

    block = min(len(data), int(block_size))
    draws = int(samples)
    rng = random.Random(int(seed) & 0xFFFFFFFF)
    measure = statistic or (lambda draw: sum(draw) / len(draw))
    statistics: list[float] = []
    for _ in range(draws):
        draw: list[float] = []
        while len(draw) < len(data):
            start = 0 if block == len(data) else rng.randrange(len(data) - block + 1)
            draw.extend(data[start + offset] for offset in range(block))
        statistics.append(float(measure(draw[: len(data)])))
    statistics.sort()
    tail = (1.0 - confidence) / 2.0
    return (
        statistics[int(tail * (draws - 1))],
        statistics[int((1.0 - tail) * (draws - 1))],
    )


def paired_moving_block_bootstrap_ci(
    left: Iterable[float],
    right: Iterable[float],
    *,
    block_size: int = 20,
    samples: int = 10_000,
    confidence: float = 0.95,
    seed: int = 42,
    statistic: Any = None,
) -> tuple[float, float]:
    """Return a paired moving-block CI using identical sampled blocks.

    The two series are aligned by position.  A draw samples the same moving
    block starts from both series before applying ``statistic`` to their
    element-wise difference, so the default result is the CI-low/high for
    mean daily PnL difference.
    """
    left_data = [float(value) for value in left]
    right_data = [float(value) for value in right]
    if len(left_data) != len(right_data):
        raise ValueError("paired series must have equal lengths")
    if not left_data:
        return (0.0, 0.0)
    difference = [left_value - right_value for left_value, right_value in zip(left_data, right_data)]
    return moving_block_bootstrap_ci(
        difference,
        block_size=block_size,
        samples=samples,
        confidence=confidence,
        seed=seed,
        statistic=statistic,
    )
