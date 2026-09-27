"""What a confirmed change to the road does to work already under way."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

from app.alerts import AlertCandidate, raise_alert

#: A passability that stops a vehicle. `restricted` is a warning, not a cut, so
#: it does not invalidate a plan on its own.
BLOCKING = frozenset({"closed"})


@dataclass(frozen=True, slots=True)
class SegmentChange:
    segment_id: str
    district_id: str
    passability: str
    previous_passability: str | None
    network_version: str
    incident_id: str | None = None


@dataclass
class ExposureOutcome:
    """What the reaction did, so a caller can report it rather than assume."""

    invalidated_plan_ids: list[str] = field(default_factory=list)
    affected_trip_ids: list[str] = field(default_factory=list)
    alert_ids: list[str] = field(default_factory=list)
    notified_profile_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "invalidated_plan_ids": self.invalidated_plan_ids,
            "affected_trip_ids": self.affected_trip_ids,
            "alert_ids": self.alert_ids,
            "notified_profiles": len(self.notified_profile_ids),
        }


def find_exposed_trips(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    segment_ids: list[str],
) -> list[dict[str, Any]]:
    """Active trips whose approved route crosses one of these segments."""

    if not segment_ids:
        return []
    return connection.execute(
        """
        select
          t.id::text as trip_id,
          t.driver_id::text as driver_id,
          t.district_id::text as district_id,
          t.status::text as trip_status,
          rp.id::text as plan_id,
          rp.status::text as plan_status,
          c.reference as consignment_reference,
          ra.segment_ids as route_segment_ids
        from public.trips as t
        join public.route_plans as rp
          on rp.id = t.route_plan_id and rp.organization_id = t.organization_id
        join public.route_alternatives as ra
          on ra.id = rp.chosen_alternative_id and ra.organization_id = rp.organization_id
        join public.consignments as c
          on c.id = t.consignment_id and c.organization_id = t.organization_id
        where t.organization_id = %(org)s::uuid
          and t.status in ('planned', 'awaiting_driver', 'active', 'paused')
          and rp.status = 'approved'
          -- Does the approved route use any segment that just changed?
          and exists (
            select 1
            from jsonb_array_elements_text(ra.segment_ids) as used(segment_id)
            where used.segment_id = any(%(segments)s::text[])
          )
        """,
        {"org": organization_id, "segments": segment_ids},
    ).fetchall()


def react_to_state_change(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    changes: list[SegmentChange],
    now: datetime | None = None,
) -> ExposureOutcome:
    """Invalidate exposed plans and tell the people who need to know."""

    outcome = ExposureOutcome()
    if not changes:
        return outcome

    # 1. One alert per district about the road itself, whether or not any trip
    #    was on it: a dispatcher planning the next consignment needs to know.
    by_district: dict[str, list[SegmentChange]] = {}
    for change in changes:
        by_district.setdefault(change.district_id, []).append(change)

    for district_id, district_changes in by_district.items():
        blocking = [c for c in district_changes if c.passability in BLOCKING]
        restricted = [c for c in district_changes if c.passability == "restricted"]
        for group, alert_type, severity, title in (
            (blocking, "road_closed", "critical", "alert.road_closed"),
            (restricted, "road_restricted", "warning", "alert.road_restricted"),
        ):
            if not group:
                continue
            result = raise_alert(
                connection,
                organization_id=organization_id,
                candidate=AlertCandidate(
                    alert_type=alert_type,  # type: ignore[arg-type]
                    severity=severity,  # type: ignore[arg-type]
                    title_key=title,
                    subject_type="district",
                    subject_id=district_id,
                    district_id=district_id,
                    payload={
                        "segment_ids": [c.segment_id for c in group],
                        "segment_count": len(group),
                        "network_version": group[0].network_version,
                        "incident_ids": sorted(
                            {c.incident_id for c in group if c.incident_id}
                        ),
                    },
                    recipient_capabilities=("route:plan", "consignment:manage"),
                ),
                now=now,
            )
            outcome.alert_ids.append(result["alert_id"])

    # 2. The trips already committed to one of those roads.
    blocked_segments = [c.segment_id for c in changes if c.passability in BLOCKING]
    exposed = find_exposed_trips(
        connection, organization_id=organization_id, segment_ids=blocked_segments
    )
    if not exposed:
        return outcome

    plan_ids = sorted({row["plan_id"] for row in exposed})
    connection.execute(
        """
        update public.route_plans
        set status = 'invalidated', updated_at = now(), version = version + 1
        where organization_id = %(org)s::uuid and id = any(%(plans)s::uuid[])
          and status = 'approved'
        """,
        {"org": organization_id, "plans": plan_ids},
    )
    outcome.invalidated_plan_ids = plan_ids

    for row in exposed:
        outcome.affected_trip_ids.append(row["trip_id"])
        used = set(row["route_segment_ids"] or [])
        hit = sorted(used.intersection(blocked_segments))
        result = raise_alert(
            connection,
            organization_id=organization_id,
            candidate=AlertCandidate(
                alert_type="trip_route_invalidated",
                severity="critical",
                title_key="alert.trip_route_invalidated",
                subject_type="trip",
                subject_id=row["trip_id"],
                district_id=row["district_id"],
                payload={
                    "trip_id": row["trip_id"],
                    "consignment_reference": row["consignment_reference"],
                    "route_plan_id": row["plan_id"],
                    "blocked_segment_ids": hit,
                    "trip_status": row["trip_status"],
                    # Stated in the alert so nobody has to infer it: the route
                    # was withdrawn, not replaced. A new one has to be planned.
                    "route_replaced_automatically": False,
                },
                recipient_capabilities=("consignment:manage", "route:plan"),
                # The driver is told by name, whatever their district grant.
                explicit_profile_ids=(row["driver_id"],),
            ),
            now=now,
        )
        outcome.alert_ids.append(result["alert_id"])
        outcome.notified_profile_ids.append(row["driver_id"])

    return outcome


def escalate_no_route(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    subject_id: str,
    facility_name: str | None,
    reason_codes: list[str],
    exclusions: dict[str, Any],
    now: datetime | None = None,
) -> str:
    """A destination with no route left is an escalation, not a failed request."""

    result = raise_alert(
        connection,
        organization_id=organization_id,
        candidate=AlertCandidate(
            alert_type="facility_isolated",
            severity="critical",
            title_key="alert.facility_isolated",
            subject_type="facility",
            subject_id=subject_id,
            district_id=district_id,
            payload={
                "facility_id": subject_id,
                "facility_name": facility_name,
                "reason_codes": reason_codes,
                "exclusions": exclusions,
                "next_step": "escalate_for_alternative_transport",
            },
            recipient_capabilities=("route:plan", "consignment:manage", "alert:read"),
        ),
        now=now,
    )
    return result["alert_id"]


__all__ = [
    "BLOCKING",
    "ExposureOutcome",
    "SegmentChange",
    "escalate_no_route",
    "find_exposed_trips",
    "react_to_state_change",
]
