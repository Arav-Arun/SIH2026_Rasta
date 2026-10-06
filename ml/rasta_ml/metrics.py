"""Metrics for rare events, and day-block bootstrap intervals around them."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from sklearn.metrics import roc_auc_score


def average_precision(y: np.ndarray, scores: np.ndarray) -> float | None:
    """Area under the precision-recall curve, as scikit-learn's average_precision_score.

    None when there is no positive to find. Rows with equal scores share a threshold,
    so ties neither help nor hurt a model.
    """

    positives = int(y.sum())
    if positives == 0:
        return None
    order = np.argsort(-scores, kind="mergesort")
    ranked_scores, ranked_y = scores[order], y[order]
    ends = np.r_[np.flatnonzero(np.diff(ranked_scores)), ranked_y.size - 1]
    true_pos = np.cumsum(ranked_y)[ends]
    precision = true_pos / (ends + 1)
    recall = true_pos / positives
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def roc_auc(y: np.ndarray, scores: np.ndarray) -> float | None:
    if y.min() == y.max():
        return None
    return float(roc_auc_score(y, scores))


def brier(y: np.ndarray, probabilities: np.ndarray) -> float:
    return float(np.mean((probabilities - y) ** 2))


def best_f1_threshold(y: np.ndarray, probabilities: np.ndarray) -> float:
    """The threshold with the best F1 on the rows given; the higher one on a tie."""

    if y.sum() == 0:
        return 1.0
    order = np.argsort(-probabilities, kind="mergesort")
    ranked, hits = probabilities[order], y[order]
    ends = np.r_[np.flatnonzero(np.diff(ranked)), ranked.size - 1]
    true_pos = np.cumsum(hits)[ends]
    flagged = ends + 1
    f1 = 2 * true_pos / (flagged + y.sum())
    return float(ranked[ends[int(np.argmax(f1))]])


def at_threshold(
    y: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
    cells: np.ndarray,
    days: np.ndarray,
) -> dict[str, float | int | None]:
    """What an operator would see at one threshold: hits, misses and alert burden."""

    flagged = probabilities >= threshold
    hits = int((flagged & (y == 1)).sum())
    weeks = (int(days.max()) - int(days.min()) + 1) / 7 if days.size else 0
    cell_count = np.unique(cells).size
    return {
        "threshold": threshold,
        "alerts": int(flagged.sum()),
        "hits": hits,
        "missed": int(y.sum()) - hits,
        "precision": round(hits / int(flagged.sum()), 4) if flagged.any() else None,
        "recall": round(hits / int(y.sum()), 4) if y.sum() else None,
        "alerts_per_cell_per_week": (
            round(int(flagged.sum()) / (cell_count * weeks), 4)
            if cell_count and weeks
            else None
        ),
    }


def reliability(
    y: np.ndarray, probabilities: np.ndarray, bins: int = 10
) -> list[dict[str, float | int]]:
    """Mean prediction against observed rate, in bins of equal row count."""

    order = np.argsort(probabilities, kind="mergesort")
    table = []
    for chunk in np.array_split(order, bins):
        if chunk.size == 0:
            continue
        table.append(
            {
                "rows": int(chunk.size),
                "mean_predicted": float(probabilities[chunk].mean()),
                "observed_rate": float(y[chunk].mean()),
            }
        )
    return table


def day_bootstrap(
    y: np.ndarray,
    days: np.ndarray,
    scores: dict[str, np.ndarray],
    probabilities: dict[str, np.ndarray],
    *,
    pairs: Iterable[tuple[str, str]],
    resamples: int,
    interval: float,
    seed: int,
) -> dict:
    """Intervals from resampling whole days with replacement.

    Cells on one day share that day's weather, so rows are not independent; days are
    the unit resampled. A resample with no positive day is skipped and counted.
    """

    order = np.argsort(days, kind="stable")
    _, starts = np.unique(days[order], return_index=True)
    edges = np.r_[starts, order.size]
    groups = [order[edges[i] : edges[i + 1]] for i in range(starts.size)]
    pairs = list(pairs)

    rng = np.random.default_rng(seed)
    pr_auc: dict[str, list[float]] = {name: [] for name in scores}
    brier_scores: dict[str, list[float]] = {name: [] for name in probabilities}
    differences: dict[tuple[str, str], list[float]] = {pair: [] for pair in pairs}
    skipped = 0
    for _ in range(resamples):
        picked = np.concatenate(
            [groups[i] for i in rng.integers(0, len(groups), len(groups))]
        )
        sample_y = y[picked]
        if sample_y.sum() == 0:
            skipped += 1
            continue
        values = {
            name: average_precision(sample_y, values[picked])
            for name, values in scores.items()
        }
        for name, value in values.items():
            pr_auc[name].append(value)
        for name, values_p in probabilities.items():
            brier_scores[name].append(brier(sample_y, values_p[picked]))
        for left, right in pairs:
            differences[(left, right)].append(values[left] - values[right])

    low, high = 50 * (1 - interval), 50 * (1 + interval)

    def bounds(values: list[float]) -> list[float] | None:
        if not values:
            return None
        return [
            round(float(np.percentile(values, low)), 6),
            round(float(np.percentile(values, high)), 6),
        ]

    return {
        "resamples": resamples,
        "skipped_without_positives": skipped,
        "interval": interval,
        "pr_auc": {name: bounds(values) for name, values in pr_auc.items()},
        "brier": {name: bounds(values) for name, values in brier_scores.items()},
        "pr_auc_difference": {
            f"{left} minus {right}": bounds(values)
            for (left, right), values in differences.items()
        },
    }


__all__ = [
    "at_threshold",
    "average_precision",
    "best_f1_threshold",
    "brier",
    "day_bootstrap",
    "reliability",
    "roc_auc",
]
