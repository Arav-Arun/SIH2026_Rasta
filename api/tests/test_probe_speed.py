"""When slow vehicles do and do not mark a road, with SYNTHETIC trips.

The trips here are made up to exercise the rules. Their speeds say nothing about
any real road.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.probe_speed import (
    SLOW_SHARE,
    Candidate,
    Fix,
    MatchedFix,
    angle_between,
    expected_speed,
    feature_for,
    match,
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


# --- which road, and which way ------------------------------------------------
# A SYNTHETIC two-way road running due east, stored as two segments: "up" runs
# east (bearing 90) and "down" runs west (bearing 270) over the same 400 m.


def near(*, along: float = 100.0, metres: float = 3.0) -> tuple[Candidate, ...]:
    return (
        Candidate("up", metres, along, 90.0, 40.0),
        Candidate("down", metres, 400.0 - along, 270.0, 40.0),
    )


def fix(
    name: str,
    trip_id: str,
    *,
    speed: float,
    heading: float | None = None,
    minute: int = 0,
    candidates: tuple[Candidate, ...] | None = None,
) -> Fix:
    return Fix(
        fix_id=name,
        trip_id=trip_id,
        speed_kph=speed,
        heading=heading,
        captured_at=NOW - timedelta(minutes=60 - minute),
        candidates=candidates if candidates is not None else near(),
    )


def roads(matched: list[MatchedFix]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for item in matched:
        found.setdefault(item.trip_id, set()).add(item.segment_id)
    return found


def test_a_moving_vehicle_is_on_the_direction_it_is_heading() -> None:
    matched = match(
        [
            fix("a", "east", speed=30, heading=85),
            fix("b", "west", speed=30, heading=268),
        ]
    )
    assert roads(matched) == {"east": {"up"}, "west": {"down"}}


def test_a_jam_one_way_does_not_mark_the_other() -> None:
    # Two trips crawl east; one trip runs freely west on the same road.
    fixes = [
        fix("e1", "trip-1", speed=30, heading=90, minute=0),
        fix("e2", "trip-1", speed=2, minute=5),
        fix("e3", "trip-1", speed=1, minute=10),
        fix("f1", "trip-2", speed=25, heading=95, minute=1),
        fix("f2", "trip-2", speed=3, minute=6),
        fix("f3", "trip-2", speed=2, minute=11),
        fix("w1", "trip-3", speed=38, heading=270, minute=2),
        fix("w2", "trip-3", speed=36, heading=271, minute=3),
    ]
    values = {s.segment_id: feature_for(s).value for s in summarise(match(fixes))}
    assert values["up"] > 0.5
    # The free-flowing westbound trip is one trip: not enough to judge "down", and
    # nothing of the eastbound jam reaches it.
    assert "down" not in values


def test_a_crawling_vehicle_takes_its_trips_nearest_moving_direction() -> None:
    fixes = [
        fix("moving-east", "trip-1", speed=20, heading=92, minute=0),
        fix("stopped", "trip-1", speed=0, minute=30),
        fix("moving-west", "trip-1", speed=20, heading=272, minute=55),
    ]
    by_fix = {
        (item.captured_at, item.segment_id)
        for item in match(fixes)
        if item.speed_kph == 0
    }
    # Thirty minutes after heading east and twenty-five before heading west: the
    # westward fix is nearer in time.
    assert {segment for _, segment in by_fix} == {"down"}


def test_a_crawling_vehicle_without_a_heading_goes_by_its_progress() -> None:
    fixes = [
        fix("c1", "trip-1", speed=3, minute=0, candidates=near(along=100)),
        fix("c2", "trip-1", speed=2, minute=10, candidates=near(along=130)),
        fix("c3", "trip-1", speed=4, minute=20, candidates=near(along=160)),
    ]
    assert roads(match(fixes)) == {"trip-1": {"up"}}


def test_a_stopped_vehicle_with_no_direction_is_left_out_not_counted_twice() -> None:
    fixes = [fix(f"s{i}", "trip-1", speed=0, minute=i) for i in range(5)]
    assert match(fixes) == []


def test_a_heading_across_every_nearby_road_matches_none() -> None:
    assert match([fix("turning", "trip-1", speed=20, heading=0)]) == []


def test_a_heading_prefers_the_road_it_runs_along_over_a_nearer_crossing() -> None:
    crossing = (
        Candidate("cross-north", 2.0, 50.0, 0.0, 30.0),
        Candidate("cross-south", 2.0, 50.0, 180.0, 30.0),
        Candidate("up", 9.0, 100.0, 90.0, 40.0),
        Candidate("down", 9.0, 300.0, 270.0, 40.0),
    )
    matched = match([fix("a", "trip-1", speed=30, heading=88, candidates=crossing)])
    assert roads(matched) == {"trip-1": {"up"}}


def test_a_one_way_road_needs_no_direction() -> None:
    one_way = (Candidate("only", 4.0, 50.0, 90.0, 40.0),)
    matched = match([fix("a", "trip-1", speed=0, candidates=one_way)])
    assert roads(matched) == {"trip-1": {"only"}}


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [(10, 350, 20), (90, 270, 180), (0, 0, 0), (359, 1, 2), (45, 100, 55)],
)
def test_angles_wrap_round_north(first, second, expected) -> None:
    assert angle_between(first, second) == pytest.approx(expected)
