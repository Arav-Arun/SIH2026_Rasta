"""Unit proof that a risk score is honest about what produced it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.risk_engine import (
    MIN_COVERAGE_FOR_SEVERE,
    MODEL_VERSION,
    WEIGHTS,
    Feature,
    assess,
    feature_schema_hash,
    snapshot_version,
)

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def feature(name: str, value: float, *, age: timedelta = timedelta(0)) -> Feature:
    return Feature(
        name=name, value=value, observed_at=NOW - age, source=f"{name}-source"
    )


def test_weights_cover_the_whole_feature_set() -> None:
    assert pytest.approx(sum(WEIGHTS.values()), abs=1e-9) == 1.0


def test_no_weather_evidence_produces_unknown_not_low() -> None:
    """The failure this rule prevents: an unmonitored road reading as safe."""

    assessment = assess([feature("terrain_slope", 0.9)], now=NOW)
    assert assessment.level == "unknown"
    assert assessment.score is None
    assert "unknown, not low" in assessment.explanations[0]


def test_an_empty_input_set_produces_unknown() -> None:
    assessment = assess([], now=NOW)
    assert assessment.level == "unknown"
    assert set(assessment.missing_inputs) == set(WEIGHTS)


def test_a_score_names_every_contribution_and_every_absence() -> None:
    assessment = assess(
        [feature("forecast_rainfall", 0.8), feature("official_warning", 0.6)], now=NOW
    )
    assert assessment.score is not None
    assert {item.name for item in assessment.contributions} == {
        "forecast_rainfall",
        "official_warning",
    }
    assert set(assessment.missing_inputs) == {
        "terrain_slope",
        "recent_incidents",
        "telemetry_anomaly",
    }
    assert assessment.model_version == MODEL_VERSION


def test_an_out_of_date_input_is_dropped_rather_than_used() -> None:
    assessment = assess(
        [
            feature("forecast_rainfall", 0.9, age=timedelta(days=2)),
            feature("official_warning", 0.2),
        ],
        now=NOW,
    )
    assert assessment.stale_inputs == ["forecast_rainfall"]
    assert all(item.name != "forecast_rainfall" for item in assessment.contributions)
    assert any("out of date" in caveat for caveat in assessment.caveats)


def test_terrain_does_not_go_stale() -> None:
    assessment = assess(
        [
            feature("forecast_rainfall", 0.5),
            feature("terrain_slope", 0.8, age=timedelta(days=400)),
        ],
        now=NOW,
    )
    assert "terrain_slope" not in assessment.stale_inputs


def test_thin_evidence_cannot_produce_a_severe_verdict() -> None:
    """One rainfall figure at 0.9 is not a critical road."""

    assessment = assess([feature("forecast_rainfall", 0.95)], now=NOW)
    assert assessment.coverage < MIN_COVERAGE_FOR_SEVERE
    assert assessment.level == "moderate"
    assert any("inputs were available" in caveat for caveat in assessment.caveats)


def test_broad_evidence_can_produce_a_severe_verdict() -> None:
    assessment = assess(
        [
            feature("forecast_rainfall", 0.9),
            feature("official_warning", 0.9),
            feature("terrain_slope", 0.9),
            feature("recent_incidents", 0.9),
        ],
        now=NOW,
    )
    assert assessment.coverage >= MIN_COVERAGE_FOR_SEVERE
    assert assessment.level == "critical"


def test_every_score_carries_the_separation_from_passability() -> None:
    assessment = assess([feature("forecast_rainfall", 0.4)], now=NOW)
    assert any("not a statement that the road is open" in c for c in assessment.caveats)


def test_a_closed_road_is_not_made_negotiable_by_a_low_score() -> None:
    assessment = assess(
        [feature("forecast_rainfall", 0.05)], now=NOW, passability="closed"
    )
    assert assessment.level == "low"
    assert any("does not make the closure negotiable" in c for c in assessment.caveats)


def test_an_unreported_road_says_so() -> None:
    assessment = assess(
        [feature("forecast_rainfall", 0.2)], now=NOW, passability="unknown"
    )
    assert any("Nobody has reported" in c for c in assessment.caveats)


def test_values_are_clamped_rather_than_trusted() -> None:
    assessment = assess(
        [feature("forecast_rainfall", 4.2), feature("official_warning", -1.0)], now=NOW
    )
    assert assessment.score is not None
    assert all(0.0 <= item.value <= 1.0 for item in assessment.contributions)


def test_the_same_inputs_always_give_the_same_score() -> None:
    inputs = [feature("forecast_rainfall", 0.42), feature("official_warning", 0.31)]
    assert assess(inputs, now=NOW).score == assess(inputs, now=NOW).score


def test_the_feature_schema_hash_changes_when_the_weights_do() -> None:
    before = feature_schema_hash()
    WEIGHTS["forecast_rainfall"] += 0.01
    try:
        assert feature_schema_hash() != before
    finally:
        WEIGHTS["forecast_rainfall"] -= 0.01
    assert feature_schema_hash() == before


def test_a_snapshot_version_changes_with_any_scored_segment() -> None:
    one = assess([feature("forecast_rainfall", 0.2)], now=NOW)
    two = assess([feature("forecast_rainfall", 0.9)], now=NOW)
    assert snapshot_version([one]) != snapshot_version([two])
    assert snapshot_version([one]).startswith(MODEL_VERSION)


def test_the_explanation_serialises_everything_a_screen_needs() -> None:
    payload = assess(
        [feature("forecast_rainfall", 0.7), feature("recent_incidents", 0.3)], now=NOW
    ).as_json()
    assert payload["model_version"] == MODEL_VERSION
    assert payload["contributions"][0]["source"]
    assert payload["missing_inputs"]
    assert payload["caveats"]
    assert payload["coverage"] > 0
