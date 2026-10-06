"""A trained risk model, run in shadow beside baseline-v1 until people switch.

The model pipeline (``ml/``) exports its logistic model as plain JSON: each
feature's transform, mean, scale and calibrated coefficient, the intercept, the
gate decision and the hashes of the data it was trained on. This module reads that
file and nothing else. No pickle, and nothing in the file is executed.

A model is loaded only when

- ``RISK_MODEL_FILE`` and ``RISK_MODEL_SHA256`` are both set and the file's SHA-256
  is that value, so a changed or swapped file is refused;
- it is the schema this code reads, says it was trained, and passed its gate: a
  model that failed is never run;
- its version is the one its own training data implies, no other model is already
  registered under that version, and every feature is one this code knows, with
  finite numbers and a positive scale.

Even then it runs in SHADOW. Its opinion of a road is stored beside the baseline's
and logged against next-day incidents, while routing, the map and alerts keep
using baseline-v1. There are two reasons. The model was trained on observed
satellite rainfall per 0.25 degree cell and day, and the API does not have that
yet, so today it has none of its inputs. And it knows nothing of official warnings
or reported incidents, so replacing the baseline with it is a decision for people,
after the outcome log has compared the two.

A road missing any input gets no opinion, and the missing inputs are named.
Inputs are never imputed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from app.config import Settings
from app.risk_engine import RiskLevel

logger = logging.getLogger("rasta.risk_model")

SCHEMA_VERSION = "rasta-risk-model-v1"

#: The inputs a model may use, in the units the pipeline trained on, and how long
#: an observation of each stays usable. Rainfall is per UTC day, so a day and a
#: half; terrain does not change.
KNOWN_INPUTS: dict[str, timedelta] = {
    "rain_1d": timedelta(hours=36),
    "rain_2d": timedelta(hours=36),
    "rain_3d": timedelta(hours=36),
    "rain_7d": timedelta(hours=36),
    "rain_30d": timedelta(hours=36),
    "slope_mean_deg": timedelta(days=3650),
    "slope_p90_deg": timedelta(days=3650),
    "steep_fraction": timedelta(days=3650),
    "relief_m": timedelta(days=3650),
}

#: What the API can supply today. The terrain-slope file describes ground along each
#: road, not a 0.25 degree cell, and there is no observed-rainfall source, so none.
SUPPLIED_INPUTS: frozenset[str] = frozenset()

TRANSFORMS = {"none": lambda value: value, "log1p": math.log1p}

VERSION_PATTERN = re.compile(r"^lr-[0-9a-f]{12}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ModelRejected(Exception):
    """The configured model may not be run; the message says why."""


@dataclass(frozen=True, slots=True)
class ModelFeature:
    name: str
    meaning: str
    transform: Literal["none", "log1p"]
    mean: float
    scale: float
    coefficient: float


@dataclass(frozen=True, slots=True)
class ModelInput:
    """One input for one road, in the model's own unit."""

    value: float
    observed_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class Opinion:
    """The model's view of one road. Contributions are in log-odds: with the
    intercept they sum to the logit, and the score is its logistic function."""

    model_version: str
    score: float
    level: RiskLevel
    logit: float
    intercept: float
    contributions: list[dict[str, Any]]

    def as_json(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "mode": "shadow",
            "score": self.score,
            "level": self.level,
            "logit": self.logit,
            "intercept": self.intercept,
            "contributions": self.contributions,
            "note": (
                "A trained model's opinion, logged for comparison. It does not "
                "change this road's score, its route cost or any alert."
            ),
        }


@dataclass(frozen=True, slots=True)
class TrainedModel:
    name: str
    version: str
    file_sha256: str
    features: tuple[ModelFeature, ...]
    intercept: float
    operating_threshold: float
    manifest_sha256: str
    dataset_sha256: str
    test_metrics: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @property
    def inputs(self) -> list[str]:
        return [feature.name for feature in self.features]

    def feature_schema_hash(self) -> str:
        payload = json.dumps(
            [
                [f.name, f.transform, f.mean, f.scale, f.coefficient]
                for f in self.features
            ]
            + [self.intercept],
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def level_for(self, score: float) -> RiskLevel:
        """Provisional: the operating threshold chosen on the validation years
        marks "high", half of it "moderate". To be calibrated with the bands (A7)."""

        if score >= self.operating_threshold:
            return "high"
        if score >= self.operating_threshold / 2:
            return "moderate"
        return "low"

    def opinion(
        self, inputs: Mapping[str, ModelInput], *, now: datetime
    ) -> tuple[Opinion | None, list[str]]:
        """The opinion, or None and the inputs that were missing or out of date."""

        unusable = [
            name
            for name in self.inputs
            if name not in inputs
            or now - inputs[name].observed_at > KNOWN_INPUTS[name]
            or not math.isfinite(inputs[name].value)
            or (self._feature(name).transform == "log1p" and inputs[name].value < 0)
        ]
        if unusable:
            return None, unusable

        contributions = []
        logit = self.intercept
        for feature in self.features:
            given = inputs[feature.name]
            standardised = (
                TRANSFORMS[feature.transform](given.value) - feature.mean
            ) / feature.scale
            part = feature.coefficient * standardised
            logit += part
            contributions.append(
                {
                    "name": feature.name,
                    "value": given.value,
                    "standardised": round(standardised, 6),
                    "coefficient": feature.coefficient,
                    "contribution": round(part, 6),
                    "source": given.source,
                    "observed_at": given.observed_at.isoformat(),
                }
            )
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        score = 1.0 / (1.0 + math.exp(-logit))
        return (
            Opinion(
                model_version=self.version,
                score=round(score, 6),
                level=self.level_for(score),
                logit=round(logit, 6),
                intercept=self.intercept,
                contributions=contributions,
            ),
            [],
        )

    def _feature(self, name: str) -> ModelFeature:
        return next(feature for feature in self.features if feature.name == name)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModelRejected(f"{what} is not a number")
    if not math.isfinite(value):
        raise ModelRejected(f"{what} is not finite")
    return float(value)


def load_model(path: Path, expected_sha256: str) -> TrainedModel:
    """Read and check one exported model. Raises ModelRejected with the reason."""

    expected = (expected_sha256 or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise ModelRejected("RISK_MODEL_SHA256 is not a SHA-256 in hex")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ModelRejected(f"the model file cannot be read: {error}") from error
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ModelRejected(
            f"the model file's SHA-256 is {actual}, not the configured {expected}"
        )
    try:
        document = json.loads(content)
    except ValueError as error:
        raise ModelRejected("the model file is not JSON") from error
    if not isinstance(document, dict):
        raise ModelRejected("the model file is not a JSON object")

    if document.get("schema_version") != SCHEMA_VERSION:
        raise ModelRejected(
            f"schema {document.get('schema_version')!r} is not {SCHEMA_VERSION!r}"
        )
    if document.get("trained") is not True:
        raise ModelRejected("the file does not describe a trained model")
    decision = document.get("decision")
    if not isinstance(decision, dict) or decision.get("passed") is not True:
        reasons = decision.get("reasons") if isinstance(decision, dict) else None
        raise ModelRejected(
            "the model did not pass its gate"
            + (": " + "; ".join(map(str, reasons)) if reasons else "")
        )
    dataset_sha256 = str(document.get("dataset_sha256", ""))
    manifest_sha256 = str(document.get("manifest_sha256", ""))
    if not SHA256_PATTERN.fullmatch(dataset_sha256) or not SHA256_PATTERN.fullmatch(
        manifest_sha256
    ):
        raise ModelRejected("the dataset or manifest hash is missing")
    version = str(document.get("version", ""))
    if not VERSION_PATTERN.fullmatch(version) or version != f"lr-{dataset_sha256[:12]}":
        raise ModelRejected(
            f"version {version!r} is not the one its training data implies "
            f"(lr-{dataset_sha256[:12]})"
        )

    entries = document.get("features")
    if not isinstance(entries, list) or not entries:
        raise ModelRejected("the model lists no features")
    features: list[ModelFeature] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ModelRejected("a feature entry is not an object")
        name = entry.get("name")
        if name not in KNOWN_INPUTS:
            raise ModelRejected(f"feature {name!r} is not one this API knows")
        if any(existing.name == name for existing in features):
            raise ModelRejected(f"feature {name!r} is listed twice")
        transform = entry.get("transform")
        if transform not in TRANSFORMS:
            raise ModelRejected(f"feature {name!r} has unknown transform {transform!r}")
        scale = _number(entry.get("scale"), f"{name}'s scale")
        if scale <= 0:
            raise ModelRejected(f"{name}'s scale is not positive")
        features.append(
            ModelFeature(
                name=name,
                meaning=str(entry.get("meaning", name)),
                transform=transform,
                mean=_number(entry.get("mean"), f"{name}'s mean"),
                scale=scale,
                coefficient=_number(entry.get("coefficient"), f"{name}'s coefficient"),
            )
        )
    threshold = _number(document.get("operating_threshold"), "the operating threshold")
    if not 0 < threshold < 1:
        raise ModelRejected("the operating threshold is not a probability")

    return TrainedModel(
        name=str(document.get("name") or "trained-model"),
        version=version,
        file_sha256=actual,
        features=tuple(features),
        intercept=_number(document.get("intercept"), "the intercept"),
        operating_threshold=threshold,
        manifest_sha256=manifest_sha256,
        dataset_sha256=dataset_sha256,
        test_metrics=dict(document.get("test_metrics") or {}),
        decision=decision,
        created_at=str(document.get("created_at", "")),
    )


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """What data health says about the trained model."""

    state: Literal["not_configured", "rejected", "shadow"]
    version: str | None = None
    reason: str | None = None
    inputs_missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "version": self.version,
            "reason": self.reason,
            "inputs_missing": self.inputs_missing,
        }


_cache: dict[tuple[str, str, int, int], TrainedModel | ModelRejected] = {}


def configured_model(
    model_file: Path | None, model_sha256: str | None
) -> tuple[ModelStatus, TrainedModel | None]:
    """The configured model, checked once per file version, and its status."""

    if model_file is None and not model_sha256:
        return ModelStatus("not_configured"), None
    if model_file is None or not model_sha256:
        return (
            ModelStatus(
                "rejected",
                reason="set both RISK_MODEL_FILE and RISK_MODEL_SHA256, or neither",
            ),
            None,
        )
    try:
        stat = model_file.stat()
        key = (str(model_file), model_sha256, stat.st_mtime_ns, stat.st_size)
    except OSError as error:
        return ModelStatus("rejected", reason=f"the model file: {error}"), None
    outcome = _cache.get(key)
    if outcome is None:
        try:
            outcome = load_model(model_file, model_sha256)
        except ModelRejected as rejection:
            outcome = rejection
            logger.warning("trained risk model not loaded: %s", rejection)
        _cache.clear()
        _cache[key] = outcome
    if isinstance(outcome, ModelRejected):
        return ModelStatus("rejected", reason=str(outcome)), None
    return (
        ModelStatus(
            "shadow",
            version=outcome.version,
            inputs_missing=[
                name for name in outcome.inputs if name not in SUPPLIED_INPUTS
            ],
        ),
        outcome,
    )


def shadow_model(settings: Settings) -> TrainedModel | None:
    """The trained model to run in shadow under these settings, if any."""

    return configured_model(
        settings.resolved_risk_model_file, settings.risk_model_sha256
    )[1]


def register(
    connection: psycopg.Connection, *, organization_id: str, model: TrainedModel
) -> None:
    """Record the model next to the baseline, as trained and not serving.

    Raises ModelRejected when a different model is already registered under the
    same name and version.
    """

    existing = connection.execute(
        """
        select feature_schema_hash from public.model_versions
        where organization_id = %(org)s::uuid and name = %(name)s
          and version = %(version)s
        """,
        {"org": organization_id, "name": model.name, "version": model.version},
    ).fetchone()
    if existing is not None:
        if existing["feature_schema_hash"] != model.feature_schema_hash():
            raise ModelRejected(
                f"a different model is already registered as {model.version}"
            )
        return
    connection.execute(
        """
        insert into public.model_versions (
          organization_id, name, version, feature_schema_hash, metrics,
          training_manifest, active
        )
        values (
          %(org)s::uuid, %(name)s, %(version)s, %(hash)s, %(metrics)s,
          %(manifest)s, false
        )
        """,
        {
            "org": organization_id,
            "name": model.name,
            "version": model.version,
            "hash": model.feature_schema_hash(),
            "metrics": Jsonb(model.test_metrics),
            "manifest": Jsonb(
                {
                    "kind": "logistic_regression",
                    "trained": True,
                    "mode": "shadow",
                    "file_sha256": model.file_sha256,
                    "manifest_sha256": model.manifest_sha256,
                    "dataset_sha256": model.dataset_sha256,
                    "decision": model.decision,
                    "created_at": model.created_at,
                }
            ),
        },
    )


def supplied_inputs(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    segment_ids: list[str],
    now: datetime,
) -> dict[str, dict[str, ModelInput]]:
    """Each road's model inputs. None today: see SUPPLIED_INPUTS."""

    return {}


__all__ = [
    "KNOWN_INPUTS",
    "SCHEMA_VERSION",
    "SUPPLIED_INPUTS",
    "ModelFeature",
    "ModelInput",
    "ModelRejected",
    "ModelStatus",
    "Opinion",
    "TrainedModel",
    "configured_model",
    "load_model",
    "register",
    "shadow_model",
    "supplied_inputs",
]
