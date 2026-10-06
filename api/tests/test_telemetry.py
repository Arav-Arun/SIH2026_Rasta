"""Unit proof of the telemetry rules that keep a track honest.

The database path is proved end to end by
`scripts/run_t025_telemetry_acceptance.py` against the real stack. What is
isolated here is the judgement: which fixes are usable, what a credential does
and does not authorise, and how a long history is thinned without lying about
where the vehicle finished.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.errors import ApiError
from app.telemetry import (
    FUTURE_TOLERANCE,
    MAX_BATCH_POINTS,
    STALE_AFTER,
    Fix,
    TelemetryBatchRequest,
    TelemetryPointInput,
    TripWindow,
    classify_reporting_state,
    downsample,
    haversine_metres,
    issue_tracking_grant,
    judge_point,
    verify_tracking_grant,
)

SECRET = "unit-test-secret-not-a-real-key"
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
STARTED = NOW - timedelta(hours=1)
RUNNING = TripWindow(status="active", started_at=STARTED, ended_at=None)

# Two points on the Shillong bypass, about 1.1 km apart.
SHILLONG = (25.5788, 91.8933)
NEARBY = (25.5888, 91.8933)


def point(
    *,
    captured_at: datetime,
    latitude: float = SHILLONG[0],
    longitude: float = SHILLONG[1],
    accuracy_m: str = "12",
    speed_kph: str | None = None,
    client_point_id: str = "p1",
) -> TelemetryPointInput:
    return TelemetryPointInput(
        client_point_id=client_point_id,
        idempotency_key=uuid.uuid4(),
        captured_at=captured_at,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=Decimal(accuracy_m),
        speed_kph=None if speed_kph is None else Decimal(speed_kph),
    )


# --- which fixes are usable -------------------------------------------------


def test_a_normal_fix_during_a_running_trip_is_accepted() -> None:
    assert (
        judge_point(point(captured_at=NOW), window=RUNNING, previous=None, now=NOW)
        is None
    )


def test_a_fix_less_accurate_than_the_pilot_threshold_is_rejected() -> None:
    reason = judge_point(
        point(captured_at=NOW, accuracy_m="101"), window=RUNNING, previous=None, now=NOW
    )
    assert reason == "accuracy_too_poor"


def test_a_fix_exactly_at_the_threshold_is_kept() -> None:
    assert (
        judge_point(
            point(captured_at=NOW, accuracy_m="100"),
            window=RUNNING,
            previous=None,
            now=NOW,
        )
        is None
    )


def test_a_trip_that_is_not_running_takes_no_positions() -> None:
    for status in ("planned", "awaiting_driver", "completed", "failed", "cancelled"):
        window = TripWindow(status=status, started_at=STARTED, ended_at=None)
        assert (
            judge_point(point(captured_at=NOW), window=window, previous=None, now=NOW)
            == "trip_not_tracking"
        )


def test_a_paused_trip_still_takes_positions() -> None:
    """A driver waiting out a landslide clearance is still somewhere."""

    window = TripWindow(status="paused", started_at=STARTED, ended_at=None)
    assert (
        judge_point(point(captured_at=NOW), window=window, previous=None, now=NOW)
        is None
    )


def test_a_fix_from_before_the_trip_started_is_rejected() -> None:
    reason = judge_point(
        point(captured_at=STARTED - timedelta(minutes=1)),
        window=RUNNING,
        previous=None,
        now=NOW,
    )
    assert reason == "captured_before_trip_start"


def test_a_fix_from_after_the_trip_ended_is_rejected() -> None:
    window = TripWindow(
        status="paused", started_at=STARTED, ended_at=NOW - timedelta(minutes=5)
    )
    reason = judge_point(point(captured_at=NOW), window=window, previous=None, now=NOW)
    assert reason == "captured_after_trip_end"


def test_a_clock_slightly_ahead_is_tolerated_but_a_broken_one_is_not() -> None:
    assert (
        judge_point(
            point(captured_at=NOW + FUTURE_TOLERANCE - timedelta(seconds=1)),
            window=RUNNING,
            previous=None,
            now=NOW,
        )
        is None
    )
    assert (
        judge_point(
            point(captured_at=NOW + FUTURE_TOLERANCE + timedelta(seconds=1)),
            window=RUNNING,
            previous=None,
            now=NOW,
        )
        == "captured_in_future"
    )


def test_an_impossible_reported_speed_is_rejected() -> None:
    reason = judge_point(
        point(captured_at=NOW, speed_kph="161"), window=RUNNING, previous=None, now=NOW
    )
    assert reason == "impossible_speed"


def test_a_jump_no_vehicle_could_make_is_rejected() -> None:
    previous = Fix(
        captured_at=NOW - timedelta(seconds=10),
        latitude=SHILLONG[0],
        longitude=SHILLONG[1],
    )
    reason = judge_point(
        point(captured_at=NOW, latitude=NEARBY[0], longitude=NEARBY[1]),
        window=RUNNING,
        previous=previous,
        now=NOW,
    )
    assert reason == "implausible_jump"


def test_the_same_distance_over_a_plausible_interval_is_accepted() -> None:
    previous = Fix(
        captured_at=NOW - timedelta(minutes=5),
        latitude=SHILLONG[0],
        longitude=SHILLONG[1],
    )
    assert (
        judge_point(
            point(captured_at=NOW, latitude=NEARBY[0], longitude=NEARBY[1]),
            window=RUNNING,
            previous=previous,
            now=NOW,
        )
        is None
    )


def test_gps_noise_about_a_parked_vehicle_is_not_a_jump() -> None:
    """Two fixes seconds apart and metres apart are one stationary truck."""

    previous = Fix(
        captured_at=NOW - timedelta(seconds=1),
        latitude=SHILLONG[0],
        longitude=SHILLONG[1],
    )
    jittered = point(
        captured_at=NOW, latitude=SHILLONG[0] + 0.0002, longitude=SHILLONG[1]
    )
    assert judge_point(jittered, window=RUNNING, previous=previous, now=NOW) is None


def test_two_fixes_with_the_same_capture_time_are_not_judged_as_a_jump() -> None:
    previous = Fix(captured_at=NOW, latitude=SHILLONG[0], longitude=SHILLONG[1])
    assert (
        judge_point(
            point(captured_at=NOW, latitude=NEARBY[0], longitude=NEARBY[1]),
            window=RUNNING,
            previous=previous,
            now=NOW,
        )
        is None
    )


def test_the_clock_is_checked_before_the_jump_it_would_cause() -> None:
    """A device with a broken clock is told about the clock."""

    previous = Fix(captured_at=NOW, latitude=SHILLONG[0], longitude=SHILLONG[1])
    reason = judge_point(
        point(
            captured_at=NOW + timedelta(hours=1),
            latitude=NEARBY[0],
            longitude=NEARBY[1],
        ),
        window=RUNNING,
        previous=previous,
        now=NOW,
    )
    assert reason == "captured_in_future"


# --- what a credential authorises -------------------------------------------


def test_a_grant_round_trips_its_claims() -> None:
    token, issued = issue_tracking_grant(
        SECRET,
        organization_id="org-1",
        trip_id="trip-1",
        device_id="device-1",
        driver_profile_id="driver-1",
        issued_at=NOW,
    )
    verified = verify_tracking_grant(SECRET, token, now=NOW + timedelta(minutes=1))
    assert verified.trip_id == "trip-1"
    assert verified.device_id == "device-1"
    assert verified.driver_profile_id == "driver-1"
    assert verified.organization_id == "org-1"
    assert verified.expires_at == issued.expires_at


def test_a_grant_signed_with_another_secret_is_refused() -> None:
    token, _ = issue_tracking_grant(
        SECRET,
        organization_id="org-1",
        trip_id="trip-1",
        device_id="device-1",
        driver_profile_id="driver-1",
        issued_at=NOW,
    )
    with pytest.raises(ApiError) as refusal:
        verify_tracking_grant("a-different-secret", token, now=NOW)
    assert refusal.value.code == "tracking_grant_invalid"


def test_an_edited_grant_is_refused() -> None:
    """Rewriting the trip id invalidates the signature over it."""

    token, _ = issue_tracking_grant(
        SECRET,
        organization_id="org-1",
        trip_id="trip-1",
        device_id="device-1",
        driver_profile_id="driver-1",
        issued_at=NOW,
    )
    body, signature = token.split(".")
    forged = f"{body[:-4]}AAAA.{signature}"
    with pytest.raises(ApiError):
        verify_tracking_grant(SECRET, forged, now=NOW)


def test_an_expired_grant_is_refused() -> None:
    token, grant = issue_tracking_grant(
        SECRET,
        organization_id="org-1",
        trip_id="trip-1",
        device_id="device-1",
        driver_profile_id="driver-1",
        issued_at=NOW,
        ttl=timedelta(minutes=30),
    )
    with pytest.raises(ApiError):
        verify_tracking_grant(SECRET, token, now=grant.expires_at)


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "....", "abc."])
def test_a_malformed_grant_is_refused_without_leaking_why(token: str) -> None:
    with pytest.raises(ApiError) as refusal:
        verify_tracking_grant(SECRET, token, now=NOW)
    assert refusal.value.code == "tracking_grant_invalid"
    assert refusal.value.status_code == 401


# --- batches ----------------------------------------------------------------


def test_a_batch_is_capped_at_the_documented_size() -> None:
    points = [
        point(captured_at=NOW - timedelta(seconds=i), client_point_id=f"p{i}")
        for i in range(MAX_BATCH_POINTS + 1)
    ]
    with pytest.raises(ValueError):
        TelemetryBatchRequest(trip_id="t", device_id="d", points=points)


def test_an_empty_batch_is_refused() -> None:
    with pytest.raises(ValueError):
        TelemetryBatchRequest(trip_id="t", device_id="d", points=[])


# --- staleness and thinning -------------------------------------------------


def test_a_position_inside_its_window_is_live_and_outside_it_is_stale() -> None:
    as_of = NOW - timedelta(minutes=1)
    stale_after = as_of + STALE_AFTER
    assert (
        classify_reporting_state(as_of=as_of, now=NOW, stale_after=stale_after)
        == "live"
    )
    assert (
        classify_reporting_state(
            as_of=as_of, now=stale_after + timedelta(seconds=1), stale_after=stale_after
        )
        == "stale"
    )


def test_a_trip_that_never_reported_is_not_called_stale() -> None:
    """ "Nobody has heard from this trip" is a different answer from "late"."""

    assert (
        classify_reporting_state(as_of=None, now=NOW, stale_after=None)
        == "never_reported"
    )


@pytest.mark.parametrize(
    ("count", "limit", "stride"),
    [(0, 1000, 1), (999, 1000, 1), (1000, 1000, 1), (1001, 1000, 2), (5000, 1000, 5)],
)
def test_history_is_thinned_only_when_it_has_to_be(
    count: int, limit: int, stride: int
) -> None:
    assert downsample(count, limit) == stride


def test_distance_is_measured_on_the_globe_not_the_plane() -> None:
    metres = haversine_metres(*SHILLONG, *NEARBY)
    assert 1100 < metres < 1120
