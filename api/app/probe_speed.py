"""Unusually slow vehicles on a road, from trips' own GPS: the ``telemetry_anomaly`` input.

Recent positions from tracked trips are matched to the road and the direction
they were travelling, and their speeds are set against the speed route planning
expects there. A road reads as slow only when several different trips were slow on
it, with good GPS fixes, away from the facilities where vehicles stop on purpose.

A two-way road is two segments, one per direction, so a fix has to say which one
it was on: a jam on the way up must not mark the way down. A moving vehicle's
heading says so. A crawling one's heading is noise, so it takes its trip's
direction from that trip's nearest moving fix on the road, or from its progress
along it; a fix with neither is left out rather than counted twice.

Speeds are the device's own readings, never distance divided by time between two
fixes, so a gap while a phone was offline cannot look like a crawl. Thresholds are
engineering assumptions until real trips tune them, and every value says so.
A road where vehicles have nearly stopped is put to a dispatcher as worth an
inspection. These readings never close a road: a person decides that.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from app.alerts import AlertCandidate, raise_alert
from app.risk_engine import FRESHNESS, Feature
from app.routing.graph import PILOT_ROAD_CLASS_SPEED_KPH

#: Positions older than this say nothing about the road now.
WINDOW: timedelta = FRESHNESS["telemetry_anomaly"]
#: A fix less certain than this could be on the next road.
MAX_ACCURACY_M = 30.0
#: How far from a road a fix may be and still count as on it.
MATCH_RADIUS_M = 25.0
#: Roads within this much of the nearest one are equally near: the two directions
#: of a road, and the roads meeting at a junction.
TIE_M = 1.0
#: Below this speed a GPS heading is noise, so it cannot pick a direction.
HEADING_MIN_SPEED_KPH = 5.0
#: How far a heading may turn from the road's own bearing and still be along it.
#: The opposite direction is about 180 degrees off; a crossing road about 90.
HEADING_TOLERANCE_DEG = 60.0
#: The stretch of road, either side of a fix, whose bearing the heading is set
#: against; long enough to steady a bearing on a bend.
BEARING_SPAN_M = 10.0
#: A trip that has moved this far along a road, by its fixes' positions on it, was
#: travelling that road's way.
PROGRESS_M = 20.0
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
#: A road at least this slow (a median at or under about a sixth of the expected
#: speed) is put to a dispatcher as worth an inspection. An assumption, like
#: SLOW_SHARE.
SUGGEST_AT = 0.5
#: While a road stays slow it is one suggestion, however often roads are re-scored.
SUGGESTION_BUCKET = timedelta(hours=6)
#: Inspections that mean somebody is already on the way to look.
OPEN_INSPECTIONS = ("assigned", "accepted", "in_progress", "overdue")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A road near a fix: how far, and which way the road runs there."""

    segment_id: str
    metres: float
    #: Where on the road the fix is, in metres from the road's start.
    along_m: float
    #: The road's own bearing at that point, in degrees from north; None when the
    #: road is too short to have one.
    bearing: float | None
    expected_kph: float


@dataclass(frozen=True, slots=True)
class Fix:
    """One GPS fix, with every road it might have been on."""

    fix_id: str
    trip_id: str
    speed_kph: float
    heading: float | None
    captured_at: datetime
    candidates: tuple[Candidate, ...]


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


def angle_between(first: float, second: float) -> float:
    """The smaller angle between two bearings, 0 to 180 degrees."""

    difference = abs(first - second) % 360.0
    return min(difference, 360.0 - difference)


def heading_usable(fix: Fix) -> bool:
    return fix.heading is not None and fix.speed_kph >= HEADING_MIN_SPEED_KPH


def _nearest(candidates: tuple[Candidate, ...] | list[Candidate]) -> list[Candidate]:
    if not candidates:
        return []
    best = min(candidate.metres for candidate in candidates)
    return [candidate for candidate in candidates if candidate.metres <= best + TIE_M]


def _along_heading(fix: Fix) -> list[Candidate]:
    """The nearest of the roads that run the way the vehicle was heading."""

    heading = fix.heading if fix.heading is not None else 0.0
    return _nearest(
        [
            candidate
            for candidate in fix.candidates
            if candidate.bearing is not None
            and angle_between(candidate.bearing, heading) <= HEADING_TOLERANCE_DEG
        ]
    )


def match(fixes: list[Fix]) -> list[MatchedFix]:
    """Each fix on the road and direction it was on, or nowhere if that is unclear."""

    chosen: dict[str, list[Candidate]] = {}
    # 1. A moving vehicle's heading picks the road that runs its way, which may be
    #    a little further off than a crossing road. Pointing along no road near it
    #    (turning, or on a road the network lacks), it is left out.
    for fix in fixes:
        if heading_usable(fix):
            chosen[fix.fix_id] = _along_heading(fix)

    directed: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for fix in fixes:
        for candidate in chosen.get(fix.fix_id, []):
            directed[fix.trip_id].append((fix.captured_at, candidate.segment_id))
    positions: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for fix in fixes:
        for candidate in _nearest(fix.candidates):
            positions[(fix.trip_id, candidate.segment_id)].append(
                (fix.captured_at, candidate.along_m)
            )

    def progress(trip_id: str, segment_id: str) -> float:
        points = sorted(positions.get((trip_id, segment_id), []))
        return points[-1][1] - points[0][1] if len(points) > 1 else 0.0

    # 2. A crawling or stopped vehicle, whose heading is noise, is on the nearest
    #    road. When that is a road with two directions, the trip's own moving fix on
    #    it nearest in time says which; failing that, which way the trip has been
    #    progressing along it.
    for fix in fixes:
        if heading_usable(fix):
            continue
        options = _nearest(fix.candidates)
        if len(options) <= 1:
            chosen[fix.fix_id] = options
            continue
        ids = {candidate.segment_id for candidate in options}
        known = [
            (abs((at - fix.captured_at).total_seconds()), segment_id)
            for at, segment_id in directed.get(fix.trip_id, [])
            if segment_id in ids
        ]
        if known:
            segment_id = min(known)[1]
            chosen[fix.fix_id] = [c for c in options if c.segment_id == segment_id]
            continue
        moving = [
            candidate
            for candidate in options
            if progress(fix.trip_id, candidate.segment_id) >= PROGRESS_M
        ]
        chosen[fix.fix_id] = moving if len(moving) == 1 else []

    return [
        MatchedFix(
            segment_id=candidate.segment_id,
            trip_id=fix.trip_id,
            speed_kph=fix.speed_kph,
            expected_kph=candidate.expected_kph,
            captured_at=fix.captured_at,
        )
        for fix in fixes
        for candidate in chosen.get(fix.fix_id, [])
    ]


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


def nearby_fixes(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    now: datetime,
) -> list[Fix]:
    """Recent, precise, moving-trip fixes in the district, with the roads near each."""

    rows = connection.execute(
        """
        with recent as (
          select tp.id, tp.trip_id, tp.location, tp.speed_kph, tp.heading,
                 tp.captured_at
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
        select r.id::text as fix_id, r.trip_id::text as trip_id,
               r.speed_kph::float8 as speed_kph, r.heading::float8 as heading,
               r.captured_at, c.id::text as segment_id,
               c.base_speed_kph::float8 as base_speed_kph, c.road_class,
               c.metres, c.along_m, c.bearing
        from recent as r
        cross join lateral (
          select s.id, s.base_speed_kph, s.road_class, d.metres,
                 pos.fraction * s.length_m::float8 as along_m,
                 degrees(extensions.st_azimuth(
                   extensions.st_lineinterpolatepoint(
                     s.geometry, greatest(pos.fraction - span.step, 0)
                   )::extensions.geography,
                   extensions.st_lineinterpolatepoint(
                     s.geometry, least(pos.fraction + span.step, 1)
                   )::extensions.geography
                 )) as bearing
          from public.road_segments as s
          cross join lateral (
            select extensions.st_distance(
              s.geometry::extensions.geography, r.location::extensions.geography
            ) as metres
          ) as d
          cross join lateral (
            select extensions.st_linelocatepoint(s.geometry, r.location) as fraction
          ) as pos
          cross join lateral (
            select least(0.5, %(span)s / s.length_m::float8) as step
          ) as span
          where s.organization_id = %(org)s::uuid
            and s.district_id = %(district)s::uuid
            -- The index narrows the search; the distance in metres decides.
            and extensions.st_dwithin(s.geometry, r.location, %(radius_deg)s)
            and d.metres <= %(radius)s
        ) as c
        order by r.captured_at, r.id
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
            "span": BEARING_SPAN_M,
        },
    ).fetchall()
    fixes: dict[str, Fix] = {}
    for row in rows:
        candidate = Candidate(
            segment_id=row["segment_id"],
            metres=row["metres"],
            along_m=row["along_m"],
            bearing=row["bearing"],
            expected_kph=expected_speed(row["base_speed_kph"], row["road_class"]),
        )
        fix = fixes.get(row["fix_id"])
        fixes[row["fix_id"]] = Fix(
            fix_id=row["fix_id"],
            trip_id=row["trip_id"],
            speed_kph=row["speed_kph"],
            heading=row["heading"],
            captured_at=row["captured_at"],
            candidates=(fix.candidates if fix else ()) + (candidate,),
        )
    return list(fixes.values())


def matched_fixes(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    now: datetime,
) -> list[MatchedFix]:
    """Recent fixes in the district, each on the road and direction it was on."""

    return match(
        nearby_fixes(
            connection,
            organization_id=organization_id,
            district_id=district_id,
            now=now,
        )
    )


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


def suggest_inspections(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    probes: dict[str, Feature],
    closed: set[str],
    now: datetime,
) -> list[str]:
    """Ask a dispatcher to send someone to roads where vehicles have nearly stopped.

    A suggestion only: the road's state does not change until a person decides.
    Roads already closed, or with an inspection under way, are not suggested.
    """

    slow = sorted(
        segment_id
        for segment_id, feature in probes.items()
        if feature.value >= SUGGEST_AT and segment_id not in closed
    )
    if not slow:
        return []
    covered = {
        row["target_id"]
        for row in connection.execute(
            """
            select target_id::text as target_id
            from public.inspections
            where organization_id = %(org)s::uuid
              and target_type = 'segment'
              and target_id = any(%(ids)s::uuid[])
              and status::text = any(%(open)s::text[])
            """,
            {"org": organization_id, "ids": slow, "open": list(OPEN_INSPECTIONS)},
        )
    }
    suggested = []
    for segment_id in slow:
        if segment_id in covered:
            continue
        feature = probes[segment_id]
        raise_alert(
            connection,
            organization_id=organization_id,
            candidate=AlertCandidate(
                alert_type="road_slow_traffic",
                severity="warning",
                title_key="alert.road_slow_traffic",
                subject_type="segment",
                subject_id=segment_id,
                district_id=district_id,
                payload={
                    "segment_id": segment_id,
                    "trips": feature.detail["trips"],
                    "points": feature.detail["points"],
                    "median_kph": feature.detail["median_kph"],
                    "expected_kph": feature.detail["expected_kph"],
                    "slowness": feature.value,
                    "observed_until": feature.observed_at.isoformat(),
                    "calibrated": False,
                    "suggested_action": "assign_inspection",
                    "changes_passability": False,
                },
                recipient_capabilities=("inspection:manage",),
                valid_for=WINDOW,
                dedupe_bucket=SUGGESTION_BUCKET,
            ),
            now=now,
        )
        suggested.append(segment_id)
    return suggested


__all__ = [
    "MIN_POINTS",
    "MIN_TRIPS",
    "SLOW_SHARE",
    "SUGGEST_AT",
    "Candidate",
    "Fix",
    "MatchedFix",
    "ProbeSummary",
    "angle_between",
    "expected_speed",
    "feature_for",
    "match",
    "matched_fixes",
    "nearby_fixes",
    "probe_features",
    "slowness",
    "suggest_inspections",
    "summarise",
]
