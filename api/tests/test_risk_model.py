"""When a trained model may run, and what it says, with a SYNTHETIC model file.

The coefficients here are made up to exercise the checks. They were not fitted to
anything and say nothing about any real road.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.risk_model import (
    ModelInput,
    ModelRejected,
    configured_model,
    load_model,
)

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
DATASET = "ab" * 32
MANIFEST = "cd" * 32


def document(**changes) -> dict:
    model = {
        "schema_version": "rasta-risk-model-v1",
        "name": "ner-landslide-logistic",
        "version": f"lr-{DATASET[:12]}",
        "trained": True,
        "features": [
            {
                "name": "rain_3d",
                "meaning": "rainfall over three days, mm",
                "transform": "log1p",
                "mean": 2.0,
                "scale": 1.5,
                "coefficient": 0.9,
            },
            {
                "name": "slope_p90_deg",
                "meaning": "90th-percentile slope, degrees",
                "transform": "none",
                "mean": 20.0,
                "scale": 8.0,
                "coefficient": 0.4,
            },
        ],
        "intercept": -6.0,
        "operating_threshold": 0.02,
        "manifest_sha256": MANIFEST,
        "dataset_sha256": DATASET,
        "test_metrics": {"pr_auc": 0.1},
        "decision": {"passed": True, "reasons": []},
        "created_at": "2026-10-06T00:00:00+00:00",
    }
    model.update(changes)
    return model


def write(tmp_path: Path, model: dict) -> tuple[Path, str]:
    path = tmp_path / "model_logistic.json"
    content = (json.dumps(model, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def inputs(**values: float) -> dict[str, ModelInput]:
    return {
        name: ModelInput(value, NOW - timedelta(hours=3), "synthetic")
        for name, value in values.items()
    }


def test_a_model_that_passed_its_gate_loads_and_explains_itself(tmp_path) -> None:
    model = load_model(*write(tmp_path, document()))
    assert model.version == f"lr-{DATASET[:12]}"
    opinion, missing = model.opinion(inputs(rain_3d=80.0, slope_p90_deg=35.0), now=NOW)
    assert missing == []
    assert opinion is not None
    # The contributions and the intercept sum to the logit, and the score is its
    # logistic function: the explanation accounts for the whole score.
    total = opinion.intercept + sum(c["contribution"] for c in opinion.contributions)
    assert total == pytest.approx(opinion.logit, abs=1e-5)
    assert opinion.score == pytest.approx(1 / (1 + math.exp(-total)), abs=1e-6)
    expected_rain = 0.9 * (math.log1p(80.0) - 2.0) / 1.5
    by_name = {c["name"]: c for c in opinion.contributions}
    assert by_name["rain_3d"]["contribution"] == pytest.approx(expected_rain, abs=1e-6)
    assert opinion.as_json()["mode"] == "shadow"


def test_levels_follow_the_operating_threshold(tmp_path) -> None:
    model = load_model(*write(tmp_path, document()))
    assert model.level_for(0.03) == "high"
    assert model.level_for(0.015) == "moderate"
    assert model.level_for(0.001) == "low"


def test_a_missing_input_gives_no_opinion_and_names_it(tmp_path) -> None:
    model = load_model(*write(tmp_path, document()))
    opinion, missing = model.opinion(inputs(slope_p90_deg=35.0), now=NOW)
    assert opinion is None
    assert missing == ["rain_3d"]


def test_an_out_of_date_input_is_not_used(tmp_path) -> None:
    model = load_model(*write(tmp_path, document()))
    stale = {
        "rain_3d": ModelInput(40.0, NOW - timedelta(days=3), "synthetic"),
        **inputs(slope_p90_deg=35.0),
    }
    opinion, missing = model.opinion(stale, now=NOW)
    assert opinion is None
    assert missing == ["rain_3d"]


def test_a_changed_file_is_refused(tmp_path) -> None:
    path, digest = write(tmp_path, document())
    path.write_bytes(path.read_bytes().replace(b"0.9", b"9.9"))
    with pytest.raises(ModelRejected, match="SHA-256"):
        load_model(path, digest)


def test_a_model_that_failed_its_gate_is_never_loaded(tmp_path) -> None:
    failed = document(decision={"passed": False, "reasons": ["Brier too high"]})
    with pytest.raises(ModelRejected, match="did not pass its gate: Brier too high"):
        load_model(*write(tmp_path, failed))


def test_a_version_its_training_data_does_not_imply_is_refused(tmp_path) -> None:
    with pytest.raises(ModelRejected, match="training data implies"):
        load_model(*write(tmp_path, document(version="lr-000000000000")))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"schema_version": "something-else"}, "schema"),
        ({"trained": False}, "trained"),
        ({"operating_threshold": 2}, "probability"),
        ({"intercept": "a"}, "not a number"),
    ],
)
def test_a_file_that_is_not_what_this_api_reads_is_refused(
    tmp_path, changes, reason
) -> None:
    with pytest.raises(ModelRejected, match=reason):
        load_model(*write(tmp_path, document(**changes)))


@pytest.mark.parametrize(
    ("feature", "reason"),
    [
        ({"name": "rain_tomorrow"}, "not one this API knows"),
        ({"transform": "exp"}, "unknown transform"),
        ({"scale": 0}, "not positive"),
        ({"coefficient": float("nan")}, "not finite"),
    ],
)
def test_a_feature_this_api_cannot_supply_or_trust_is_refused(
    tmp_path, feature, reason
) -> None:
    model = document()
    model["features"][0] = {**model["features"][0], **feature}
    path = tmp_path / "model_logistic.json"
    # json.dumps writes NaN, which the loader must still refuse.
    content = (json.dumps(model, sort_keys=True) + "\n").encode()
    path.write_bytes(content)
    with pytest.raises(ModelRejected, match=reason):
        load_model(path, hashlib.sha256(content).hexdigest())


def test_status_says_why_a_model_is_not_running(tmp_path) -> None:
    status, model = configured_model(None, None)
    assert (status.state, model) == ("not_configured", None)

    path, digest = write(tmp_path, document())
    status, model = configured_model(path, None)
    assert status.state == "rejected" and "both" in (status.reason or "")

    status, model = configured_model(path, "0" * 64)
    assert status.state == "rejected" and "SHA-256" in (status.reason or "")

    status, model = configured_model(path, digest)
    assert status.state == "shadow" and model is not None
    # The API supplies none of its inputs yet, and says so.
    assert status.inputs_missing == ["rain_3d", "slope_p90_deg"]
