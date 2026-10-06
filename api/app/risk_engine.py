"""The explainable baseline risk engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

RiskLevel = Literal["unknown", "low", "moderate", "high", "critical"]

MODEL_NAME = "baseline"
MODEL_VERSION = "baseline-v1"

#: Weights sum to 1.0 over the full feature set.
WEIGHTS: dict[str, float] = {
    "forecast_rainfall": 0.35,
    "official_warning": 0.25,
    "terrain_slope": 0.15,
    "recent_incidents": 0.15,
    "telemetry_anomaly": 0.10,
}

#: Without at least one of these, there is no weather evidence at all and the
#: engine refuses to produce a number.
REQUIRED_ANY = ("forecast_rainfall", "official_warning")

#: Bands over the *renormalised* score, so a segment scored from three features
#: is not pushed down merely because two were missing.
BANDS: tuple[tuple[float, RiskLevel], ...] = (
    (0.70, "critical"),
    (0.50, "high"),
    (0.30, "moderate"),
    (0.0, "low"),
)

#: Below this share of the total weight, the evidence is too thin to support a severe
#: verdict however high the one input that exists happens to be.
MIN_COVERAGE_FOR_SEVERE = 0.6
CAPPED_LEVEL: RiskLevel = "moderate"

#: How long each feature's observation stays usable.
FRESHNESS: dict[str, timedelta] = {
    "forecast_rainfall": timedelta(hours=12),
    "official_warning": timedelta(hours=6),
    "terrain_slope": timedelta(days=3650),  # terrain does not go stale
    "recent_incidents": timedelta(days=7),
    "telemetry_anomaly": timedelta(hours=2),
}


@dataclass(frozen=True, slots=True)
class Feature:
    """One normalised input with the provenance needed to defend it."""

    name: str
    value: float
    observed_at: datetime
    source: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Contribution:
    name: str
    value: float
    weight: float
    contribution: float
    source: str
    observed_at: str
    age_seconds: int


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    score: float | None
    model_version: str
    computed_at: datetime
    contributions: list[Contribution]
    missing_inputs: list[str]
    stale_inputs: list[str]
    #: Sentences a person reads. Ordered by contribution, largest first.
    explanations: list[str]
    #: What this score does NOT mean, carried with it so no screen has to add it.
    caveats: list[str]
    coverage: float

    def as_json(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "computed_at": self.computed_at.isoformat(),
            "level": self.level,
            "score": self.score,
            "coverage": self.coverage,
            "contributions": [
                {
                    "name": item.name,
                    "value": item.value,
                    "weight": item.weight,
                    "contribution": item.contribution,
                    "source": item.source,
                    "observed_at": item.observed_at,
                    "age_seconds": item.age_seconds,
                }
                for item in self.contributions
            ],
            "missing_inputs": self.missing_inputs,
            "stale_inputs": self.stale_inputs,
            "explanations": self.explanations,
            "caveats": self.caveats,
        }


READABLE: dict[str, str] = {
    "forecast_rainfall": "forecast rainfall",
    "official_warning": "an official warning in force",
    "terrain_slope": "terrain slope around this road",
    "recent_incidents": "the record of recent verified incidents on this road",
    "telemetry_anomaly": "unusually slow vehicle traffic here",
}


def feature_schema_hash() -> str:
    """Identifies the exact feature set and weights a score was produced with."""

    payload = json.dumps(
        {
            "features": sorted(WEIGHTS),
            "weights": WEIGHTS,
            "bands": [list(b) for b in BANDS],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def assess(
    features: list[Feature],
    *,
    now: datetime | None = None,
    passability: str | None = None,
) -> RiskAssessment:
    """Score one segment from whatever evidence exists for it."""

    moment = now or datetime.now(tz=UTC)
    by_name = {feature.name: feature for feature in features if feature.name in WEIGHTS}

    contributions: list[Contribution] = []
    stale: list[str] = []
    used: set[str] = set()

    for name, weight in WEIGHTS.items():
        feature = by_name.get(name)
        if feature is None:
            continue
        age = int(max(0.0, (moment - feature.observed_at).total_seconds()))
        if age > FRESHNESS[name].total_seconds():
            # Dropped, not down-weighted: an out-of-date observation is not
            # weak evidence, it is evidence about a different afternoon.
            stale.append(name)
            continue
        value = min(1.0, max(0.0, float(feature.value)))
        contributions.append(
            Contribution(
                name=name,
                value=round(value, 4),
                weight=weight,
                contribution=round(value * weight, 4),
                source=feature.source,
                observed_at=feature.observed_at.isoformat(),
                age_seconds=age,
            )
        )
        used.add(name)

    missing = [name for name in WEIGHTS if name not in used]

    if not (used & set(REQUIRED_ANY)):
        return RiskAssessment(
            level="unknown",
            score=None,
            model_version=MODEL_VERSION,
            computed_at=moment,
            contributions=contributions,
            missing_inputs=missing,
            stale_inputs=stale,
            explanations=[
                "No usable weather evidence for this road, so no risk score was "
                "produced. This is unknown, not low."
            ],
            caveats=_caveats(passability, missing, stale),
            coverage=0.0,
        )

    weight_used = sum(item.weight for item in contributions)
    raw = sum(item.contribution for item in contributions)
    # Renormalised over the weight actually available, so a segment scored from rainfall
    # alone is not dragged toward zero by four absent features.
    score = round(raw / weight_used, 4) if weight_used > 0 else 0.0
    level = next(band for threshold, band in BANDS if score >= threshold)
    capped = False
    if weight_used < MIN_COVERAGE_FOR_SEVERE and level in ("high", "critical"):
        level = CAPPED_LEVEL
        capped = True

    ordered = sorted(contributions, key=lambda item: item.contribution, reverse=True)
    caveats = _caveats(passability, missing, stale)
    if capped:
        caveats.insert(
            1,
            f"Only {weight_used:.0%} of the model's inputs were available, so this "
            f"is reported as {CAPPED_LEVEL} rather than higher. Thin evidence "
            "cannot support a severe verdict.",
        )

    explanations = [
        f"{READABLE[item.name].capitalize()} contributes "
        f"{item.contribution:.2f} of the score ({item.source})."
        for item in ordered
        if item.contribution > 0
    ] or ["Every available input is at its lowest value, so exposure looks low."]

    return RiskAssessment(
        level=level,
        score=score,
        model_version=MODEL_VERSION,
        computed_at=moment,
        contributions=ordered,
        missing_inputs=missing,
        stale_inputs=stale,
        explanations=explanations,
        caveats=caveats,
        coverage=round(weight_used, 4),
    )


def _caveats(
    passability: str | None, missing: list[str], stale: list[str]
) -> list[str]:
    caveats = [
        "This is a forecast of exposure, not a statement that the road is open "
        "or closed. Only a reviewed report changes passability.",
    ]
    if missing:
        caveats.append(
            "Missing inputs: "
            + ", ".join(READABLE.get(name, name) for name in missing)
            + ". Their absence is not evidence of safety."
        )
    if stale:
        caveats.append(
            "Ignored as out of date: "
            + ", ".join(READABLE.get(name, name) for name in stale)
            + "."
        )
    if passability == "closed":
        caveats.append(
            "This road is recorded as closed. The score describes exposure if it "
            "reopens; it does not make the closure negotiable."
        )
    if passability == "unknown":
        caveats.append("Nobody has reported on this road's state.")
    return caveats


def snapshot_version(assessments: list[RiskAssessment]) -> str:
    """A token that changes whenever any scored segment's answer changes.

    Callers must pass assessments in stable segment order. The timestamp of the
    run is not part of the token, so recomputing the same answers is a no-op,
    and two segments exchanging scores still changes it.
    """

    payload = json.dumps(
        [f"{item.model_version}:{item.level}:{item.score}" for item in assessments],
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{MODEL_VERSION}-{digest}"


__all__ = [
    "BANDS",
    "FRESHNESS",
    "MIN_COVERAGE_FOR_SEVERE",
    "MODEL_NAME",
    "MODEL_VERSION",
    "REQUIRED_ANY",
    "WEIGHTS",
    "Contribution",
    "Feature",
    "RiskAssessment",
    "assess",
    "feature_schema_hash",
    "snapshot_version",
]
