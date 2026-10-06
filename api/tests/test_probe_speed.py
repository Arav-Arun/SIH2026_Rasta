"""When slow vehicles do and do not mark a road, with SYNTHETIC trips.

The trips here are made up to exercise the rules. Their speeds say nothing about
any real road.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.probe_speed import (
    SLOW_SHARE,
    MatchedFix,
    expected_speed,
    feature_for,
    slowness,
    summarise,
)

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def trip(
    name: str,
    speeds: list[float],
    *,
    road: str = "road-a",
    expected: float = 40.0,
    every: timedelta = timedelta(seconds=15),
) -> list[MatchedFix]:
    start = NOW - timedelta(minutes=30)
    return [
        MatchedFix(road, name, speed, expected, start + index * every)
        for index, speed in enumerate(speeds)
    ]


def test_two_slow_trips_mark_the_road_and_say_it_is_uncalibrated() -> None:
    (summary,) = summarise(trip("trip-1", [3, 4, 2]) + trip("trip-2", [5, 1]))
    assert (summary.trips, summary.points, summary.median_kph) == (2, 5, 3)
    feature = feature_for(summary)
    assert feature.name == "telemetry_anomaly"
    assert feature.value == slowness(3, 40) > 0.7
    assert feature.observed_at == max(f.captured_at for f in trip("trip-1", [3, 4, 2]))
    assert "uncalibrated" in feature.source
    assert feature.detail["calibrated"] is False


def test_one_slow_vehicle_is_not_enough_however_many_fixes() -> None:
    assert summarise(trip("trip-1", [1] * 40)) == []


def test_a_handful_of_fixes_is_not_enough() -> None:
    assert summarise(trip("trip-1", [1]) + trip("trip-2", [1, 2])) == []


def test_traffic_at_a_normal_pace_is_evidence_too_and_reads_as_zero() -> None:
    (summary,) = summarise(trip("trip-1", [30, 28, 33]) + trip("trip-2", [35, 31]))
    assert feature_for(summary).value == 0.0


def test_a_gap_while_offline_does_not_look_like_a_crawl() -> None:
    # Fixes forty minutes apart: distance over time would read as a crawl, but the
    # device's own speed readings are what count.
    gappy = trip("trip-1", [32, 34], every=timedelta(minutes=40))
    (summary,) = summarise(gappy + trip("trip-2", [30, 31]))
    assert feature_for(summary).value == 0.0


def test_roads_are_judged_separately() -> None:
    fixes = (
        trip("trip-1", [2, 3], road="blocked")
        + trip("trip-2", [1, 2], road="blocked")
        + trip("trip-1", [30, 31], road="clear")
        + trip("trip-3", [33, 29], road="clear")
    )
    values = {s.segment_id: feature_for(s).value for s in summarise(fixes)}
    assert values["blocked"] > 0.8
    assert values["clear"] == 0.0


def test_expected_speed_is_the_one_route_planning_uses() -> None:
    assert expected_speed(55.0, "residential") == 55.0
    assert expected_speed(None, "residential") == 25.0
    assert expected_speed(None, "a class nobody listed") == 30.0


@pytest.mark.parametrize(
    ("median", "expected"),
    [(40, 0.0), (40 * SLOW_SHARE, 0.0), (7, 0.5), (0, 1.0)],
)
def test_slowness_runs_from_normal_to_standstill(median, expected) -> None:
    assert slowness(median, 40) == pytest.approx(expected, abs=1e-4)
