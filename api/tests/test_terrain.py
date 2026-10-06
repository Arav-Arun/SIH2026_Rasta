"""Terrain slope as a risk input: parsing, scaling, missing data and the shipped file."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.config import Settings
from app.risk_engine import WEIGHTS, Feature, assess
from app.terrain import SCHEMA_VERSION, load_terrain, parse_terrain

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
REPOSITORY_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "data" / "pilot").is_dir()
)


def document(**over) -> dict:
    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-10-06T00:00:00Z",
        "method": {"buffer_m": 45.0, "full_scale_deg": 35.0},
        "segments": {
            "edge-flat": [3.5, 2.0, 6],
            "edge-half": [17.5, 12.0, 9],
            "edge-cliff": [48.0, 40.0, 4],
        },
    }
    base.update(over)
    return base


def weather() -> list[Feature]:
    return [
        Feature("forecast_rainfall", 0.7, NOW, "rain-source"),
        Feature("official_warning", 0.75, NOW, "warning-source"),
    ]


def test_a_slope_is_scaled_against_the_documented_full_scale() -> None:
    layer = parse_terrain(document())
    assert layer.feature_for("edge-flat").value == pytest.approx(0.1)
    assert layer.feature_for("edge-half").value == pytest.approx(0.5)


def test_a_slope_beyond_full_scale_reads_as_one_not_more() -> None:
    assert parse_terrain(document()).feature_for("edge-cliff").value == 1.0


def test_an_edge_the_file_does_not_cover_has_no_feature() -> None:
    layer = parse_terrain(document())
    assert layer.feature_for("edge-unknown") is None
    assert layer.feature_for(None) is None


def test_the_feature_carries_its_measurement_in_its_source_text() -> None:
    feature = parse_terrain(document()).feature_for("edge-half")
    assert feature.name == "terrain_slope"
    assert "18 degrees" in feature.source  # 17.5 rounds half to even
    assert "90th percentile within 45 m" in feature.source
    assert feature.detail["samples"] == 9


def test_terrain_enters_the_score_and_is_explained_as_terrain_not_susceptibility() -> (
    None
):
    layer = parse_terrain(document())
    assessment = assess([*weather(), layer.feature_for("edge-half")], now=NOW)
    names = {item.name for item in assessment.contributions}
    assert "terrain_slope" in names
    assert "terrain_slope" not in assessment.missing_inputs
    assert assessment.coverage == pytest.approx(
        WEIGHTS["forecast_rainfall"]
        + WEIGHTS["official_warning"]
        + WEIGHTS["terrain_slope"]
    )
    assert any(
        "Terrain slope around this road" in line for line in assessment.explanations
    )
    assert not any("susceptib" in line.lower() for line in assessment.explanations)


def test_steeper_ground_scores_higher_than_level_ground_in_the_same_weather() -> None:
    layer = parse_terrain(document())
    level = assess([*weather(), layer.feature_for("edge-flat")], now=NOW)
    steep = assess([*weather(), layer.feature_for("edge-cliff")], now=NOW)
    assert steep.score > level.score


def test_terrain_does_not_go_stale_within_the_life_of_the_file() -> None:
    old = Feature(
        "terrain_slope", 0.4, datetime(2026, 10, 6, tzinfo=UTC), "SRTM terrain"
    )
    later = datetime(2030, 1, 1, tzinfo=UTC)
    assessment = assess(
        [
            Feature("forecast_rainfall", 0.5, later, "rain"),
            old,
        ],
        now=later,
    )
    assert "terrain_slope" not in assessment.stale_inputs


@pytest.mark.parametrize(
    "bad", [{}, {"schema_version": "terrain-v0"}, document(segments={})]
)
def test_a_document_that_is_not_terrain_is_refused(bad: dict) -> None:
    with pytest.raises(ValueError):
        parse_terrain(bad)


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_terrain(None) is None
    assert load_terrain(tmp_path / "nope.json") is None


def test_an_unreadable_file_is_reported_and_treated_as_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    broken = tmp_path / "terrain.json"
    broken.write_text("{ not json")
    with caplog.at_level(logging.WARNING, logger="rasta.terrain"):
        assert load_terrain(broken) is None
    assert "could not be used" in caplog.text


def test_the_shipped_file_covers_every_edge_of_the_shipped_graph() -> None:
    layer = load_terrain(REPOSITORY_ROOT / "data" / "pilot" / "shillong_terrain.json")
    assert layer is not None
    graph = json.loads(
        (REPOSITORY_ROOT / "data" / "pilot" / "shillong_graph.json").read_text()
    )
    edge_ids = {edge["edge_id"] for edge in graph["edges"]}
    assert set(layer.slopes) == edge_ids
    for p90, mean, samples in layer.slopes.values():
        assert 0 <= mean <= p90 <= 90
        assert samples >= 1


def test_the_default_setting_points_at_the_shipped_file() -> None:
    path = Settings().resolved_terrain_file
    assert path is not None and path.is_file()
