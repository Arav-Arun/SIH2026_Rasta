"""The benchmarks a model must beat, the candidate models, and their calibration.

Every scorer answers the same question for the same rows: how strongly does it
expect a catalogued landslide in this cell on this day. Benchmarks need no labels
(baseline-v1) or only training labels (climatology), or choose one setting on the
validation years (the rainfall rule). Candidates are fitted on the training years,
tuned on the validation years, and calibrated there; the test years are never seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from rasta_ml.dataset import RAIN_WINDOWS, Dataset
from rasta_ml.metrics import average_precision

# baseline-v1 as the API defines it (api/app/risk_engine.py, api/app/sources.py and
# api/app/terrain.py); a test keeps these in step with the API.
BASELINE_WEIGHTS = {"forecast_rainfall": 0.35, "terrain_slope": 0.15}
RAINFALL_FULL_SCALE_MM_48H = 115.0
SLOPE_FULL_SCALE_DEG = 35.0

LOGISTIC_C = (0.01, 0.1, 1.0, 10.0)
BOOSTED_GRID = (
    {"learning_rate": 0.05, "max_leaf_nodes": 7},
    {"learning_rate": 0.05, "max_leaf_nodes": 15},
    {"learning_rate": 0.1, "max_leaf_nodes": 7},
)
EPSILON = 1e-6


def logit(p: np.ndarray) -> np.ndarray:
    clipped = np.clip(p, EPSILON, 1 - EPSILON)
    return np.log(clipped / (1 - clipped))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    clipped = np.clip(p, EPSILON, 1 - EPSILON)
    return float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)))


@dataclass(slots=True)
class Platt:
    """p = sigmoid(slope * z + intercept), fitted on the validation years only."""

    slope: float = 1.0
    intercept: float = 0.0

    @classmethod
    def fit(cls, z: np.ndarray, y: np.ndarray) -> Platt:
        model = LogisticRegression(C=1e6, max_iter=5000)
        model.fit(z.reshape(-1, 1), y)
        return cls(float(model.coef_[0, 0]), float(model.intercept_[0]))

    def apply(self, z: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(self.slope * z + self.intercept)))


class Scorer:
    """A ranking of rows, a link to a calibration input, and a calibration."""

    name: str = ""
    kind: str = "benchmark"
    calibration: Platt

    def score(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def link(self, scores: np.ndarray) -> np.ndarray:
        return scores

    def calibrate(self, data: Dataset) -> None:
        rows = data.mask("validate")
        self.calibration = Platt.fit(self.link(self.score(data, rows)), data.y[rows])

    def probability(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        return self.calibration.apply(self.link(self.score(data, rows)))


@dataclass
class Baseline(Scorer):
    """baseline-v1 on the two of its inputs that exist for past days.

    Rainfall stands in for the 48-hour forecast as the observed total of the day and
    the day before; terrain is the cell's 90th-percentile slope rather than the
    road's. Weights, scale and renormalisation are the API's own.
    """

    name: str = "baseline-v1"
    calibration: Platt = field(default_factory=Platt)

    def score(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        rain = np.minimum(
            1.0, data.column("rain_2d")[rows] / RAINFALL_FULL_SCALE_MM_48H
        )
        slope = np.minimum(
            1.0, data.column("slope_p90_deg")[rows] / SLOPE_FULL_SCALE_DEG
        )
        rain_weight = BASELINE_WEIGHTS["forecast_rainfall"]
        slope_weight = BASELINE_WEIGHTS["terrain_slope"]
        return (rain_weight * rain + slope_weight * slope) / (
            rain_weight + slope_weight
        )


@dataclass
class RainfallRule(Scorer):
    """More rain, more risk: the rainfall total whose window ranks best on validation."""

    feature: str = "rain_3d"
    name: str = "rainfall rule"
    calibration: Platt = field(default_factory=Platt)

    @classmethod
    def select(cls, data: Dataset) -> RainfallRule:
        rows = data.mask("validate")
        best = max(
            RAIN_WINDOWS,
            key=lambda name: (
                average_precision(data.y[rows], data.column(name)[rows]) or 0.0
            ),
        )
        return cls(feature=best)

    def score(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        return data.column(self.feature)[rows].astype(np.float64)

    def link(self, scores: np.ndarray) -> np.ndarray:
        return np.log1p(scores)


@dataclass
class Climatology(Scorer):
    """How often this cell had an event in this calendar month, in the training years.

    Shrunk towards the month's rate over all cells, so a cell with few days does not
    get an extreme rate from one event.
    """

    name: str = "climatology"
    strength: float = 30.0
    rates: dict[tuple[int, int], float] = field(default_factory=dict)
    month_rates: dict[int, float] = field(default_factory=dict)
    overall: float = 0.0
    calibration: Platt = field(default_factory=Platt)

    @classmethod
    def fit(cls, data: Dataset, rows: np.ndarray | None = None) -> Climatology:
        rows = data.mask("train") if rows is None else rows
        y, cells, months = data.y[rows], data.cell[rows], data.months()[rows]
        overall = (y.sum() + 1) / (y.size + 2)
        model = cls(overall=float(overall))
        for month in range(1, 13):
            in_month = months == month
            model.month_rates[month] = float(
                (y[in_month].sum() + model.strength * overall)
                / (in_month.sum() + model.strength)
            )
        for cell in np.unique(cells):
            for month in range(1, 13):
                here = (cells == cell) & (months == month)
                prior = model.month_rates[month]
                model.rates[(int(cell), month)] = float(
                    (y[here].sum() + model.strength * prior)
                    / (here.sum() + model.strength)
                )
        return model

    def score(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        cells, months = data.cell[rows], data.months()[rows]
        return np.array(
            [
                self.rates.get((int(cell), int(month)), self.month_rates[int(month)])
                for cell, month in zip(cells, months, strict=True)
            ]
        )

    def link(self, scores: np.ndarray) -> np.ndarray:
        return logit(scores)


@dataclass
class Logistic(Scorer):
    """Logistic regression on log rainfall totals and terrain, standardised."""

    name: str = "logistic regression"
    kind: str = "candidate"
    C: float = 1.0
    seed: int = 0
    means: np.ndarray = field(default_factory=lambda: np.zeros(0))
    scales: np.ndarray = field(default_factory=lambda: np.ones(0))
    coef: np.ndarray = field(default_factory=lambda: np.zeros(0))
    intercept: float = 0.0
    log_columns: tuple[int, ...] = ()
    calibration: Platt = field(default_factory=Platt)

    def transform(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        X = data.X[rows].astype(np.float64)
        columns = list(self.log_columns)
        X[:, columns] = np.log1p(np.maximum(X[:, columns], 0.0))
        return (X - self.means) / self.scales

    def fit(self, data: Dataset, rows: np.ndarray) -> Logistic:
        self.log_columns = tuple(data.features.index(name) for name in RAIN_WINDOWS)
        raw = data.X[rows].astype(np.float64)
        columns = list(self.log_columns)
        raw[:, columns] = np.log1p(np.maximum(raw[:, columns], 0.0))
        self.means = raw.mean(axis=0)
        self.scales = np.where(raw.std(axis=0) > 0, raw.std(axis=0), 1.0)
        model = LogisticRegression(C=self.C, max_iter=5000, random_state=self.seed)
        model.fit((raw - self.means) / self.scales, data.y[rows])
        self.coef = model.coef_[0].copy()
        self.intercept = float(model.intercept_[0])
        return self

    @classmethod
    def select(cls, data: Dataset, seed: int) -> Logistic:
        train, validate = data.mask("train"), data.mask("validate")
        fitted = [cls(C=C, seed=seed).fit(data, train) for C in LOGISTIC_C]
        return min(
            fitted,
            key=lambda model: log_loss(data.y[validate], model.score(data, validate)),
        )

    def score(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        z = self.transform(data, rows) @ self.coef + self.intercept
        return 1 / (1 + np.exp(-z))

    def link(self, scores: np.ndarray) -> np.ndarray:
        return logit(scores)


@dataclass
class Boosted(Scorer):
    """Gradient-boosted trees on the same features, with fixed seeds."""

    name: str = "gradient-boosted trees"
    kind: str = "candidate"
    params: dict = field(default_factory=dict)
    seed: int = 0
    model: HistGradientBoostingClassifier | None = None
    calibration: Platt = field(default_factory=Platt)

    def fit(self, data: Dataset, rows: np.ndarray) -> Boosted:
        self.model = HistGradientBoostingClassifier(
            max_iter=200,
            min_samples_leaf=40,
            l2_regularization=1.0,
            early_stopping=False,
            random_state=self.seed,
            **self.params,
        )
        self.model.fit(data.X[rows], data.y[rows])
        return self

    @classmethod
    def select(cls, data: Dataset, seed: int) -> Boosted:
        train, validate = data.mask("train"), data.mask("validate")
        fitted = [cls(params=dict(p), seed=seed).fit(data, train) for p in BOOSTED_GRID]
        return min(
            fitted,
            key=lambda model: log_loss(data.y[validate], model.score(data, validate)),
        )

    def score(self, data: Dataset, rows: np.ndarray) -> np.ndarray:
        assert self.model is not None, "fit first"
        return self.model.predict_proba(data.X[rows])[:, 1]

    def link(self, scores: np.ndarray) -> np.ndarray:
        return logit(scores)


__all__ = [
    "BASELINE_WEIGHTS",
    "RAINFALL_FULL_SCALE_MM_48H",
    "SLOPE_FULL_SCALE_DEG",
    "Baseline",
    "Boosted",
    "Climatology",
    "Logistic",
    "Platt",
    "RainfallRule",
    "Scorer",
    "log_loss",
    "logit",
]
