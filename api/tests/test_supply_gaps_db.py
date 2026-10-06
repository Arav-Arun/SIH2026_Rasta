"""Supply gaps against the local database, with SYNTHETIC requests and deliveries.

Skipped without DATABASE_URL. Runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.config import Settings
from app.supply_gaps import supply_gaps
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DATABASE_URL = Settings().database_url
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")


@pytest.fixture
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def world(db) -> dict:
    facilities = db.execute(
        """
        select f.organization_id::text as org, f.district_id::text as district,
               array_agg(f.id::text order by f.id) as ids
        from public.facilities as f
        group by 1, 2
        having count(*) >= 2
        limit 1
        """
    ).fetchone()
    trip = db.execute(
        "select vehicle_id::text as vehicle, driver_id::text as driver "
        "from public.trips limit 1"
    ).fetchone()
    if facilities is None or trip is None:
        pytest.skip("needs the demo facilities and a trip")
    # Requests this database already holds would mix with the ones under test.
    db.execute(
        "update public.supply_requests set status = 'cancelled' "
        "where organization_id = %s::uuid",
        (facilities["org"],),
    )
    return {**facilities, **trip}


def request(db, world, *, needed_by, priority="high", facility=1) -> str:
    return db.execute(
        """
        insert into public.supply_requests (organization_id, facility_id, priority,
                                            needed_by)
        values (%s::uuid, %s::uuid, %s, %s)
        returning id::text as id
        """,
        (world["org"], world["ids"][facility], priority, needed_by),
    ).fetchone()["id"]


def consignment(db, world, request_id, *, status, items) -> str:
    consignment_id = db.execute(
        """
        insert into public.consignments (
          organization_id, district_id, supply_request_id, reference,
          origin_facility_id, destination_facility_id, priority, status
        )
        values (%s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, 'high', %s)
        returning id::text as id
        """,
        (
            world["org"],
            world["district"],
            request_id,
            f"GAP-TEST-{uuid.uuid4().hex[:8]}",
            world["ids"][0],
            world["ids"][1],
            status,
        ),
    ).fetchone()["id"]
    for commodity, quantity, unit in items:
        db.execute(
            """
            insert into public.consignment_items (
              organization_id, consignment_id, commodity, quantity, unit
            )
            values (%s::uuid, %s::uuid, %s, %s, %s)
            """,
            (world["org"], consignment_id, commodity, quantity, unit),
        )
    return consignment_id


def trip(db, world, consignment_id, *, status, started_at=None, eta=None) -> str:
    trip_id = db.execute(
        """
        insert into public.trips (organization_id, district_id, consignment_id,
                                  vehicle_id, driver_id, status, started_at)
        values (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s)
        returning id::text as id
        """,
        (
            world["org"],
            world["district"],
            consignment_id,
            world["vehicle"],
            world["driver"],
            status,
            started_at,
        ),
    ).fetchone()["id"]
    if eta is not None:
        plan = db.execute(
            """
            insert into public.route_plans (organization_id, district_id, trip_id,
                                            request, network_version,
                                            risk_snapshot_version)
            values (%s::uuid, %s::uuid, %s::uuid, %s, 'test', 'test')
            returning id::text as id
            """,
            (world["org"], world["district"], trip_id, Jsonb({})),
        ).fetchone()["id"]
        alternative = db.execute(
            """
            insert into public.route_alternatives (organization_id, route_plan_id,
                                                   rank, category, segment_ids,
                                                   distance_m, eta_seconds)
            values (%s::uuid, %s::uuid, 1, 'fastest', '[]'::jsonb, 1000, %s)
            returning id::text as id
            """,
            (world["org"], plan, int(eta.total_seconds())),
        ).fetchone()["id"]
        db.execute(
            """
            update public.route_plans
            set status = 'approved', chosen_alternative_id = %s::uuid,
                approved_by_profile_id = %s::uuid, approved_at = now()
            where id = %s::uuid
            """,
            (alternative, world["driver"], plan),
        )
        db.execute(
            "update public.trips set route_plan_id = %s::uuid where id = %s::uuid",
            (plan, trip_id),
        )
    return trip_id


def test_unmet_requests_come_most_urgent_first_with_their_reasons(db, world) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    overdue = request(db, world, needed_by=now - timedelta(hours=1), priority="normal")
    close = request(db, world, needed_by=now + timedelta(hours=3))
    far = request(db, world, needed_by=now + timedelta(days=3))
    late = request(db, world, needed_by=now + timedelta(hours=1))
    late_trip = consignment(
        db, world, late, status="in_transit", items=[("rice", 100, "kg")]
    )
    trip(
        db,
        world,
        late_trip,
        status="active",
        started_at=now - timedelta(minutes=10),
        eta=timedelta(hours=2),
    )
    unknown = request(db, world, needed_by=now + timedelta(hours=5))
    trip(
        db,
        world,
        consignment(db, world, unknown, status="in_transit", items=[]),
        status="active",
        started_at=now,
    )
    fulfilled = request(db, world, needed_by=now - timedelta(days=1))
    db.execute(
        "update public.supply_requests set status = 'fulfilled' where id = %s::uuid",
        (fulfilled,),
    )

    report = supply_gaps(
        db,
        organization_id=world["org"],
        district_ids=None,
        district_id=None,
        now=now,
    )
    by_id = {item.request_id: item for item in report.requests}
    assert fulfilled not in by_id
    assert [item.request_id for item in report.requests] == [
        overdue,
        late,
        close,
        unknown,
        far,
    ]
    assert (by_id[overdue].state, by_id[overdue].reason) == (
        "overdue",
        "deadline_passed",
    )
    assert (by_id[late].state, by_id[late].reason) == (
        "at_risk",
        "arrives_after_deadline",
    )
    assert by_id[late].expected_arrival == now + timedelta(minutes=110)
    assert [(q.commodity, q.quantity) for q in by_id[late].on_the_way] == [
        ("rice", 100.0)
    ]
    assert (by_id[close].state, by_id[close].reason) == (
        "at_risk",
        "nothing_on_the_way",
    )
    assert (by_id[unknown].state, by_id[unknown].reason) == (
        "unknown",
        "no_route_eta",
    )
    assert (by_id[far].state, by_id[far].reason) == ("waiting", "nothing_on_the_way")
    assert report.counts["at_risk"] == 2
    assert any("not a predicted stockout" in note for note in report.notes)


def test_a_receipt_short_of_what_was_sent_is_a_shortfall(db, world) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    need = request(db, world, needed_by=now + timedelta(days=2))
    db.execute(
        "update public.supply_requests set status = 'partially_fulfilled' "
        "where id = %s::uuid",
        (need,),
    )
    sent = consignment(
        db,
        world,
        need,
        status="partially_delivered",
        items=[
            ("rice", 500, "kg"),
            ("water", 200, "litre"),
            ("tarpaulin", 20, "piece"),
        ],
    )
    done = trip(
        db, world, sent, status="completed", started_at=now - timedelta(hours=5)
    )
    db.execute(
        "update public.trips set ended_at = %s where id = %s::uuid",
        (now - timedelta(hours=1), done),
    )
    receipt = db.execute(
        """
        insert into public.delivery_receipts (organization_id, trip_id, status)
        values (%s::uuid, %s::uuid, 'partially_delivered')
        returning id::text as id
        """,
        (world["org"], done),
    ).fetchone()["id"]
    lines = db.execute(
        "select id::text as id, commodity, unit from public.consignment_items "
        "where consignment_id = %s::uuid",
        (sent,),
    ).fetchall()
    received = {"rice": 300, "water": 200}  # no line at all for the tarpaulins
    for line in lines:
        if line["commodity"] in received:
            db.execute(
                """
                insert into public.delivery_receipt_items (
                  organization_id, delivery_receipt_id, consignment_item_id,
                  delivered_quantity, unit
                )
                values (%s::uuid, %s::uuid, %s::uuid, %s, %s)
                """,
                (
                    world["org"],
                    receipt,
                    line["id"],
                    received[line["commodity"]],
                    line["unit"],
                ),
            )

    report = supply_gaps(
        db,
        organization_id=world["org"],
        district_ids=[world["district"]],
        district_id=world["district"],
        now=now,
    )
    (item,) = [i for i in report.requests if i.request_id == need]
    assert item.consignments_arrived == 1
    assert [
        (s.commodity, s.dispatched, s.received, s.short) for s in item.shortfalls
    ] == [
        ("rice", 500.0, 300.0, 200.0),
        ("tarpaulin", 20.0, 0.0, 20.0),
    ]
    # Nothing more is on the way, and the deadline is two days off.
    assert (item.state, item.reason) == ("waiting", "nothing_on_the_way")


def test_a_district_outside_the_callers_grant_shows_nothing(db, world) -> None:
    now = datetime.now(UTC)
    request(db, world, needed_by=now + timedelta(hours=2))
    report = supply_gaps(
        db,
        organization_id=world["org"],
        district_ids=[str(uuid.uuid4())],
        district_id=None,
        now=now,
    )
    assert report.requests == []
