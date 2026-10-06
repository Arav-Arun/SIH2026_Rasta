"""Unusually slow vehicles on a road, from trips' own GPS: the ``telemetry_anomaly`` input.

Recent positions from tracked trips are matched to the nearest road (both
directions), and their speeds are set against the speed route planning expects
there. A road reads as slow only when several different trips were slow on it,
with good GPS fixes, away from the facilities where vehicles stop on purpose.

Speeds are the device's own readings, never distance divided by time between two
fixes, so a gap while a phone was offline cannot look like a crawl. Thresholds are
engineering assumptions until real trips tune them, and every value says so.
These readings can suggest an inspection; they never close a road.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from app.risk_engine import FRESHNESS, Feature
from app.routing.graph import PILOT_ROAD_CLASS_SPEED_KPH

#: Positions older than this say nothing about the road now.
WINDOW: timedelta = FRESHNESS["telemetry_anomaly"]
#: A fix less certain than this could be on the next road.
MAX_ACCURACY_M = 30.0
#: How far from a road a fix may be and still count as on it.
MATCH_RADIUS_M = 25.0
#: Roads within this much of the nearest one share the fix: the two directions of a
#: road, and the roads meeting at a junction.
TIE_M = 1.0
#: Vehicles stop at facilities to load and unload; those fixes are left out.
STOP_RADIUS_M = 150.0
#: Independent vehicles needed before a road reads as slow, and fixes in all.
MIN_TRIPS = 2
MIN_POINTS = 4
#: A median speed at or above this share of the expected speed reads as normal (0);
#: a standstill reads as 1. An assumption, not a fitted value.
SLOW_SHARE = 0.35
#: Expected speed where neither the segment nor its road class says.
FALLBACK_SPEED_KPH = 30.0


@dataclass(frozen=True, slots=True)
class MatchedFix:
    segment_id: str
    trip_id: str
    speed_kph: float
    expected_kph: float
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class ProbeSummary:
    segment_id: str
    trips: int
    points: int
    median_kph: float
    expected_kph: float
    last_seen: datetime


def expected_speed(base_speed_kph: float | None, road_class: str) -> float:
    """The speed route planning assumes on a road, found the same way it does."""

    if base_speed_kph:
        return float(base_speed_kph)
    return PILOT_ROAD_CLASS_SPEED_KPH.get(road_class, FALLBACK_SPEED_KPH)


def slowness(median_kph: float, expected_kph: float) -> float:
    """0 for traffic at a normal pace, rising to 1 for a standstill."""

    share = median_kph / expected_kph if expected_kph > 0 else 1.0
    return round(min(1.0, max(0.0, (SLOW_SHARE - share) / SLOW_SHARE)), 4)


def summarise(fixes: list[MatchedFix]) -> list[ProbeSummary]:
    """One summary per road with enough independent evidence; none for the rest."""

    by_segment: dict[str, list[MatchedFix]] = defaultdict(list)
    for fix in fixes:
        by_segment[fix.segment_id].append(fix)
    summaries = []
    for segment_id, group in sorted(by_segment.items()):
        trips = {fix.trip_id for fix in group}
        if len(trips) < MIN_TRIPS or len(group) < MIN_POINTS:
            continue
        summaries.append(
            ProbeSummary(
                segment_id=segment_id,
                trips=len(trips),
                points=len(group),
                median_kph=statistics.median(fix.speed_kph for fix in group),
                expected_kph=group[0].expected_kph,
                last_seen=max(fix.captured_at for fix in group),
            )
        )
    return summaries


def feature_for(summary: ProbeSummary) -> Feature:
    return Feature(
        name="telemetry_anomaly",
        value=slowness(summary.median_kph, summary.expected_kph),
        observed_at=summary.last_seen,
        source=(
            f"GPS from {summary.trips} trips, median {summary.median_kph:.0f} km/h "
            f"against {summary.expected_kph:.0f} expected (uncalibrated)"
        ),
        detail={
            "trips": summary.trips,
            "points": summary.points,
            "median_kph": summary.median_kph,
            "expected_kph": summary.expected_kph,
            "calibrated": False,
        },
    )


def matched_fixes(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    now: datetime,
) -> list[MatchedFix]:
    """Recent, precise, moving-trip fixes in the district, each on its nearest road."""

    rows = connection.execute(
        """
        with recent as (
          select tp.trip_id, tp.location, tp.speed_kph, tp.captured_at
          from public.telemetry_points as tp
          join public.trips as t
            on t.id = tp.trip_id and t.organization_id = tp.organization_id
          where tp.organization_id = %(org)s::uuid
            and t.district_id = %(district)s::uuid
            and tp.captured_at > %(since)s and tp.captured_at <= %(now)s
            and tp.accuracy_m <= %(accuracy)s
            and tp.speed_kph is not null
            and not exists (
              select 1
              from public.facilities as f
              where f.organization_id = tp.organization_id
                and f.location is not null
                and extensions.st_dwithin(
                  f.location::extensions.geography,
                  tp.location::extensions.geography,
                  %(stop_radius)s
                )
            )
        )
        select r.trip_id::text as trip_id, r.speed_kph::float8 as speed_kph,
               r.captured_at, m.id::text as segment_id,
               m.base_speed_kph::float8 as base_speed_kph, m.road_class
        from recent as r
        cross join lateral (
          select c.id, c.base_speed_kph, c.road_class
          from (
            select s.id, s.base_speed_kph, s.road_class, d.metres,
                   min(d.metres) over () as nearest
            from public.road_segments as s
            cross join lateral (
              select extensions.st_distance(
                s.geometry::extensions.geography, r.location::extensions.geography
              ) as metres
            ) as d
            where s.organization_id = %(org)s::uuid
              and s.district_id = %(district)s::uuid
              -- The index narrows the search; the distance in metres decides.
              and extensions.st_dwithin(s.geometry, r.location, %(radius_deg)s)
              and d.metres <= %(radius)s
          ) as c
          where c.metres <= c.nearest + %(tie)s
        ) as m
        """,
        {
            "org": organization_id,
            "district": district_id,
            "since": now - WINDOW,
            "now": now,
            "accuracy": MAX_ACCURACY_M,
            "stop_radius": STOP_RADIUS_M,
            "radius": MATCH_RADIUS_M,
            # Generous in degrees at any latitude the pilot covers.
            "radius_deg": MATCH_RADIUS_M / 50_000,
            "tie": TIE_M,
        },
    ).fetchall()
    return [
        MatchedFix(
            segment_id=row["segment_id"],
            trip_id=row["trip_id"],
            speed_kph=row["speed_kph"],
            expected_kph=expected_speed(row["base_speed_kph"], row["road_class"]),
            captured_at=row["captured_at"],
        )
        for row in rows
    ]


def probe_features(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    now: datetime,
) -> dict[str, Feature]:
    """The telemetry_anomaly input for every road with enough recent probes."""

    fixes = matched_fixes(
        connection, organization_id=organization_id, district_id=district_id, now=now
    )
    return {summary.segment_id: feature_for(summary) for summary in summarise(fixes)}


__all__ = [
    "MIN_POINTS",
    "MIN_TRIPS",
    "SLOW_SHARE",
    "MatchedFix",
    "ProbeSummary",
    "expected_speed",
    "feature_for",
    "matched_fixes",
    "probe_features",
    "slowness",
    "summarise",
]
