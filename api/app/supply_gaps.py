"""Supply gaps: which requests are still unmet, and whose deadline is at risk.

A supply request names a facility, a priority and when the supplies are needed by,
not quantities, and there is no stock or consumption data. So this cannot predict
a stockout. It says which requests are unmet, whether what is on the way should
arrive before the deadline, and where a delivery brought less than was sent.

An expected arrival is a trip's start plus its approved route's ETA. That ETA is a
configured estimate, not one calibrated on recorded trips, and the report says so.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

import psycopg
from pydantic import BaseModel

GapState = Literal[
    "overdue", "at_risk", "unknown", "waiting", "on_track", "no_deadline"
]
GapReason = Literal[
    "deadline_passed",
    "arrives_after_deadline",
    "arrives_before_deadline",
    "no_route_eta",
    "nothing_on_the_way",
    "no_deadline",
]

#: Nothing on the way and a deadline this close reads as at risk, not waiting.
AT_RISK_WINDOW = timedelta(hours=24)

#: Most urgent first.
STATE_ORDER: tuple[GapState, ...] = (
    "overdue",
    "at_risk",
    "unknown",
    "waiting",
    "on_track",
    "no_deadline",
)
PRIORITY_ORDER = ("critical", "high", "normal", "low")

UNMET = ("open", "planned", "partially_fulfilled")
ARRIVED = ("delivered", "partially_delivered")
ON_THE_WAY = ("planned", "assigned", "in_transit")


class GapQuantity(BaseModel):
    commodity: str
    unit: str
    quantity: float


class GapShortfall(BaseModel):
    commodity: str
    unit: str
    dispatched: float
    received: float
    short: float


class GapRequest(BaseModel):
    request_id: str
    facility_id: str
    facility_name: str | None
    district_id: str
    priority: str
    needed_by: datetime | None
    status: str
    state: GapState
    reason: GapReason
    #: The earliest expected arrival of what is on the way, when a route ETA exists.
    expected_arrival: datetime | None
    consignments_on_the_way: int
    consignments_arrived: int
    consignments_failed: int
    on_the_way: list[GapQuantity]
    #: Where a receipt shows less than was sent.
    shortfalls: list[GapShortfall]


class SupplyGapsResponse(BaseModel):
    as_of: datetime
    district_id: str | None
    at_risk_window_hours: int
    counts: dict[str, int]
    requests: list[GapRequest]
    notes: list[str]


def _number(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def judge(
    *,
    needed_by: datetime | None,
    arrivals: list[datetime | None],
    now: datetime,
) -> tuple[GapState, GapReason, datetime | None]:
    """One unmet request's deadline state, from what is on the way.

    ``arrivals`` holds each consignment on the way's expected arrival, or None
    where its route has no ETA.
    """

    known = [arrival for arrival in arrivals if arrival is not None]
    best = min(known) if known else None
    if needed_by is None:
        return "no_deadline", "no_deadline", best
    if now > needed_by:
        return "overdue", "deadline_passed", best
    if arrivals:
        if best is None:
            return "unknown", "no_route_eta", None
        if best <= needed_by:
            return "on_track", "arrives_before_deadline", best
        return "at_risk", "arrives_after_deadline", best
    if needed_by - now <= AT_RISK_WINDOW:
        return "at_risk", "nothing_on_the_way", None
    return "waiting", "nothing_on_the_way", None


def supply_gaps(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_ids: list[str] | None,
    district_id: str | None,
    now: datetime,
) -> SupplyGapsResponse:
    """Every unmet request in scope, most urgent first."""

    requests = connection.execute(
        """
        select r.id::text as id, r.facility_id::text as facility_id,
               f.name as facility_name, f.district_id::text as district_id,
               r.priority, r.needed_by, r.status::text as status
        from public.supply_requests as r
        join public.facilities as f
          on f.id = r.facility_id and f.organization_id = r.organization_id
        where r.organization_id = %(org)s::uuid
          and r.status::text = any(%(unmet)s::text[])
          and (%(districts)s::uuid[] is null or f.district_id = any(%(districts)s::uuid[]))
          and (%(district)s::uuid is null or f.district_id = %(district)s::uuid)
        """,
        {
            "org": organization_id,
            "unmet": list(UNMET),
            "districts": district_ids,
            "district": district_id,
        },
    ).fetchall()
    request_ids = [row["id"] for row in requests]

    consignments = connection.execute(
        """
        select c.id::text as id, c.supply_request_id::text as request_id,
               c.status::text as status, t.status::text as trip_status,
               t.started_at, ra.eta_seconds
        from public.consignments as c
        left join lateral (
          select trip.status, trip.started_at, trip.route_plan_id
          from public.trips as trip
          where trip.consignment_id = c.id and trip.organization_id = c.organization_id
            and trip.status not in ('cancelled', 'failed')
          order by trip.created_at desc
          limit 1
        ) as t on true
        left join public.route_plans as rp
          on rp.id = t.route_plan_id and rp.organization_id = c.organization_id
         and rp.status = 'approved'
        left join public.route_alternatives as ra
          on ra.id = rp.chosen_alternative_id and ra.organization_id = rp.organization_id
        where c.organization_id = %(org)s::uuid
          and c.supply_request_id = any(%(requests)s::uuid[])
          and c.status <> 'cancelled'
        """,
        {"org": organization_id, "requests": request_ids},
    ).fetchall()
    consignment_ids = [row["id"] for row in consignments]

    items = connection.execute(
        """
        select ci.consignment_id::text as consignment_id, ci.commodity, ci.unit,
               ci.quantity, received.delivered_quantity
        from public.consignment_items as ci
        left join lateral (
          select dri.delivered_quantity
          from public.delivery_receipt_items as dri
          join public.delivery_receipts as dr
            on dr.id = dri.delivery_receipt_id and dr.organization_id = dri.organization_id
          where dri.consignment_item_id = ci.id
            and dri.organization_id = ci.organization_id
          order by dr.received_at desc
          limit 1
        ) as received on true
        where ci.organization_id = %(org)s::uuid
          and ci.consignment_id = any(%(consignments)s::uuid[])
        """,
        {"org": organization_id, "consignments": consignment_ids},
    ).fetchall()
    items_by_consignment: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        items_by_consignment[item["consignment_id"]].append(item)
    consignments_by_request: dict[str, list[dict]] = defaultdict(list)
    for consignment in consignments:
        consignments_by_request[consignment["request_id"]].append(consignment)

    results: list[GapRequest] = []
    for request in requests:
        mine = consignments_by_request.get(request["id"], [])
        on_the_way = [c for c in mine if c["status"] in ON_THE_WAY]
        arrived = [c for c in mine if c["status"] in ARRIVED]
        failed = [c for c in mine if c["status"] == "failed"]

        arrivals: list[datetime | None] = []
        for consignment in on_the_way:
            eta = consignment["eta_seconds"]
            if eta is None or consignment["trip_status"] is None:
                arrivals.append(None)
            elif (
                consignment["trip_status"] in ("active", "paused")
                and consignment["started_at"]
            ):
                arrivals.append(consignment["started_at"] + timedelta(seconds=eta))
            else:
                # Not started yet: it cannot arrive sooner than a full trip from now.
                arrivals.append(now + timedelta(seconds=eta))
        state, reason, expected = judge(
            needed_by=request["needed_by"], arrivals=arrivals, now=now
        )

        coming: dict[tuple[str, str], float] = defaultdict(float)
        for consignment in on_the_way:
            for item in items_by_consignment.get(consignment["id"], []):
                coming[(item["commodity"], item["unit"])] += _number(item["quantity"])
        sent: dict[tuple[str, str], float] = defaultdict(float)
        got: dict[tuple[str, str], float] = defaultdict(float)
        for consignment in arrived:
            for item in items_by_consignment.get(consignment["id"], []):
                key = (item["commodity"], item["unit"])
                sent[key] += _number(item["quantity"])
                # A line missing from the receipt is nothing received, as the
                # receipt itself reads.
                got[key] += _number(item["delivered_quantity"])

        results.append(
            GapRequest(
                request_id=request["id"],
                facility_id=request["facility_id"],
                facility_name=request["facility_name"],
                district_id=request["district_id"],
                priority=request["priority"],
                needed_by=request["needed_by"],
                status=request["status"],
                state=state,
                reason=reason,
                expected_arrival=expected,
                consignments_on_the_way=len(on_the_way),
                consignments_arrived=len(arrived),
                consignments_failed=len(failed),
                on_the_way=[
                    GapQuantity(commodity=commodity, unit=unit, quantity=quantity)
                    for (commodity, unit), quantity in sorted(coming.items())
                ],
                shortfalls=[
                    GapShortfall(
                        commodity=commodity,
                        unit=unit,
                        dispatched=quantity,
                        received=got[(commodity, unit)],
                        short=round(quantity - got[(commodity, unit)], 3),
                    )
                    for (commodity, unit), quantity in sorted(sent.items())
                    if got[(commodity, unit)] < quantity
                ],
            )
        )

    results.sort(
        key=lambda item: (
            STATE_ORDER.index(item.state),
            PRIORITY_ORDER.index(item.priority)
            if item.priority in PRIORITY_ORDER
            else len(PRIORITY_ORDER),
            item.needed_by or datetime.max.replace(tzinfo=now.tzinfo),
        )
    )
    counts = dict.fromkeys(STATE_ORDER, 0)
    for item in results:
        counts[item.state] += 1
    return SupplyGapsResponse(
        as_of=now,
        district_id=district_id,
        at_risk_window_hours=int(AT_RISK_WINDOW.total_seconds() // 3600),
        counts=counts,
        requests=results,
        notes=[
            "Requests do not state quantities and there is no stock or consumption "
            "data, so this shows unmet requests and deadline risk, not a predicted "
            "stockout.",
            "An expected arrival is the trip's start plus its approved route's ETA, a "
            "configured estimate not yet calibrated on recorded trips.",
        ],
    )


__all__ = [
    "AT_RISK_WINDOW",
    "GapRequest",
    "GapShortfall",
    "SupplyGapsResponse",
    "judge",
    "supply_gaps",
]
