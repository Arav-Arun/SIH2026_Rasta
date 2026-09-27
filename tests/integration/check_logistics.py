"""Runtime check: consignments, vehicles, trips and receipts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.lib.stack import fresh_stack_with_api  # noqa: E402

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "logistics_check.json"
API_PORT = "8010"
API = f"http://127.0.0.1:{API_PORT}"

checks: list[dict[str, Any]] = []


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")


def supabase_env() -> dict[str, str]:
    out = subprocess.check_output(
        ["npx", "supabase", "status", "-o", "json"], cwd=REPOSITORY_ROOT, text=True
    )
    return json.loads(out)


def request(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    req = urllib.request.Request(
        f"{API}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"raw": raw[:300]}


def sign_in(supabase_url: str, anon: str, email: str, password: str) -> str:
    req = urllib.request.Request(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json", "apikey": anon},
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        return json.load(response)["access_token"]


def bootstrap(role: str, district_id: str | None = None) -> dict[str, Any]:
    args = [
        str(REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"),
        "tests/integration/demo_identity.py",
        "--role",
        role,
    ]
    if district_id:
        args += ["--district-id", district_id]
    out = subprocess.check_output(args, cwd=REPOSITORY_ROOT, text=True)
    return json.loads(out[out.index("{") :])


def run() -> int:
    env = supabase_env()
    anon = env["ANON_KEY"]
    supabase_url = env["API_URL"]
    database_url = env["DB_URL"]

    dispatcher = bootstrap("district_dispatcher")
    driver = bootstrap("driver", dispatcher["district_id"])
    dispatcher_token = sign_in(
        supabase_url, anon, dispatcher["email"], dispatcher["password"]
    )
    driver_token = sign_in(supabase_url, anon, driver["email"], driver["password"])
    district_id = dispatcher["district_id"]

    # Facilities and vehicles come from the imported pilot data; a vehicle is
    # created here because the pilot import does not carry a fleet.
    with psycopg.connect(database_url) as connection:
        facilities = connection.execute(
            """
            select id::text from public.facilities
            where organization_id = %s::uuid and district_id = %s::uuid
            limit 2
            """,
            (dispatcher["organization_id"], district_id)
            if "organization_id" in dispatcher
            else (None, None),
        ).fetchall()

    if len(facilities) < 2:
        with psycopg.connect(database_url) as connection:
            facilities = connection.execute(
                """
                select f.id::text from public.facilities as f
                join public.profiles as p on p.organization_id = f.organization_id
                where p.id = %s::uuid and f.district_id = %s::uuid
                limit 2
                """,
                (dispatcher["profile_id"], district_id),
            ).fetchall()

    if len(facilities) < 2:
        record(
            "pilot_facilities_present",
            False,
            "Fewer than two facilities in the district",
        )
        REPORT_PATH.write_text(
            json.dumps({"checks": checks, "passed": False}, indent=2)
        )
        return 1

    origin, destination = facilities[0][0], facilities[1][0]

    with psycopg.connect(database_url) as connection:
        organization_id = connection.execute(
            "select organization_id::text from public.profiles where id = %s::uuid",
            (dispatcher["profile_id"],),
        ).fetchone()[0]
        small_vehicle = str(uuid.uuid4())
        connection.execute(
            """
            insert into public.vehicles (
              id, organization_id, registration_ref, class, capacity_kg, active
            )
            values (%s::uuid, %s::uuid, %s, 'light_truck', 500, true)
            """,
            (small_vehicle, organization_id, f"TEST-SMALL-{small_vehicle[:8]}"),
        )
        big_vehicle = str(uuid.uuid4())
        connection.execute(
            """
            insert into public.vehicles (
              id, organization_id, registration_ref, class, capacity_kg, active
            )
            values (%s::uuid, %s::uuid, %s, 'freight', 8000, true)
            """,
            (big_vehicle, organization_id, f"TEST-BIG-{big_vehicle[:8]}"),
        )
        connection.commit()

    # 1. Create a consignment with two items.
    reference = f"TEST-{uuid.uuid4().hex[:8]}"
    status, consignment = request(
        "POST",
        "/v1/consignments",
        dispatcher_token,
        {
            "district_id": district_id,
            "reference": reference,
            "origin_facility_id": origin,
            "destination_facility_id": destination,
            "priority": "high",
            "deadline_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "items": [
                {
                    "commodity": "ORS sachets",
                    "quantity": "200",
                    "unit": "packs",
                    "weight_kg": "120",
                },
                {
                    "commodity": "Rice",
                    "quantity": "40",
                    "unit": "sacks",
                    "weight_kg": "1000",
                },
            ],
        },
        str(uuid.uuid4()),
    )
    record(
        "consignment_created_with_items_and_total_weight",
        status == 201
        and len(consignment["items"]) == 2
        and Decimal(consignment["total_weight_kg"]) == Decimal("1120"),
        f"status={status} items={len(consignment.get('items', []))} "
        f"total_weight={consignment.get('total_weight_kg')}",
    )
    consignment_id = consignment["id"]

    # 2. A trip cannot be raised while the consignment is still a draft.
    status, refused = request(
        "POST",
        "/v1/trips",
        dispatcher_token,
        {
            "consignment_id": consignment_id,
            "vehicle_id": big_vehicle,
            "driver_profile_id": driver["profile_id"],
        },
        str(uuid.uuid4()),
    )
    record(
        "trip_refused_while_consignment_is_draft",
        status == 409 and refused["error"]["code"] == "invalid_transition",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    # The manifest is confirmed through the API, not by SQL: releasing a draft
    # for planning is the dispatcher's explicit statement that the lines are
    # right, and it is the only thing that makes a trip possible.
    status, planned = request(
        "POST",
        f"/v1/consignments/{consignment_id}/planned",
        dispatcher_token,
        {},
        str(uuid.uuid4()),
    )
    record(
        "draft_released_for_planning_through_the_api",
        status == 200 and planned["status"] == "planned",
        f"status={status} consignment_status={planned.get('status')}",
    )

    status, refused = request(
        "POST",
        f"/v1/consignments/{consignment_id}/planned",
        dispatcher_token,
        {},
        str(uuid.uuid4()),
    )
    record(
        "an_already_planned_consignment_is_not_released_twice",
        status == 409 and refused["error"]["code"] == "invalid_transition",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    # The picker's roster must match the predicate the assign call enforces, or
    # the dashboard can offer a driver the server then refuses.
    status, roster = request(
        "GET", f"/v1/districts/{district_id}/drivers", dispatcher_token
    )
    offered = {row["profile_id"] for row in roster.get("drivers", [])}
    record(
        "driver_roster_matches_who_the_assign_call_accepts",
        status == 200 and driver["profile_id"] in offered,
        f"status={status} drivers={len(offered)} includes_assignable_driver="
        f"{driver['profile_id'] in offered}",
    )

    # 3. Capacity is enforced against declared weight.
    status, refused = request(
        "POST",
        "/v1/trips",
        dispatcher_token,
        {
            "consignment_id": consignment_id,
            "vehicle_id": small_vehicle,
            "driver_profile_id": driver["profile_id"],
        },
        str(uuid.uuid4()),
    )
    record(
        "overweight_vehicle_refused",
        status == 422 and refused["error"]["code"] == "vehicle_over_capacity",
        f"status={status} code={refused.get('error', {}).get('code')} "
        f"details={refused.get('error', {}).get('details')}",
    )

    # 4. A suitable vehicle is accepted and the check is reported.
    trip_key = str(uuid.uuid4())
    status, created = request(
        "POST",
        "/v1/trips",
        dispatcher_token,
        {
            "consignment_id": consignment_id,
            "vehicle_id": big_vehicle,
            "driver_profile_id": driver["profile_id"],
        },
        trip_key,
    )
    record(
        "trip_created_with_capacity_check_reported",
        status == 201
        and created["capacity_check"]["performed"] is True
        and created["capacity_check"]["within_capacity"] is True,
        f"status={status} capacity={created.get('capacity_check')}",
    )
    trip_id = created["trip"]["id"]

    # 5. Replaying the same key returns the stored result.
    status, replay = request(
        "POST",
        "/v1/trips",
        dispatcher_token,
        {
            "consignment_id": consignment_id,
            "vehicle_id": big_vehicle,
            "driver_profile_id": driver["profile_id"],
        },
        trip_key,
    )
    record(
        "trip_creation_replays_on_the_same_key",
        status == 201
        and replay["trip"]["id"] == trip_id
        and replay["replayed"] is True,
        f"status={status} same_trip={replay['trip']['id'] == trip_id} replayed={replay.get('replayed')}",
    )

    # 6. A trip cannot jump straight to active.
    status, refused = request(
        "POST", f"/v1/trips/{trip_id}/active", driver_token, None, str(uuid.uuid4())
    )
    record(
        "trip_cannot_skip_awaiting_driver",
        status == 409 and refused["error"]["code"] == "invalid_transition",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    request(
        "POST",
        f"/v1/trips/{trip_id}/awaiting_driver",
        dispatcher_token,
        None,
        str(uuid.uuid4()),
    )
    status, activated = request(
        "POST", f"/v1/trips/{trip_id}/active", driver_token, None, str(uuid.uuid4())
    )
    record(
        "driver_starts_their_own_trip",
        status == 200 and activated["trip"]["status"] == "active",
        f"status={status} trip_status={activated.get('trip', {}).get('status')}",
    )

    # 7. The driver's own listing is scoped to their trips.
    status, listing = request("GET", "/v1/trips", driver_token)
    record(
        "driver_sees_only_their_own_trips",
        status == 200 and listing["scope"] == "assigned_to_me",
        f"status={status} scope={listing.get('scope')}",
    )

    items = consignment["items"]
    ors = next(item for item in items if item["commodity"] == "ORS sachets")
    rice = next(item for item in items if item["commodity"] == "Rice")

    # 8. Over-delivery is refused.
    status, refused = request(
        "POST",
        f"/v1/trips/{trip_id}/receipt",
        driver_token,
        {
            "status": "delivered",
            "received_by_ref": "PHC storekeeper",
            "items": [
                {"consignment_item_id": ors["id"], "delivered_quantity": "500"},
                {"consignment_item_id": rice["id"], "delivered_quantity": "40"},
            ],
        },
        str(uuid.uuid4()),
    )
    record(
        "over_delivery_refused",
        status == 422 and refused["error"]["code"] == "delivered_exceeds_ordered",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    # 9. A status that disagrees with the quantities is refused.
    status, refused = request(
        "POST",
        f"/v1/trips/{trip_id}/receipt",
        driver_token,
        {
            "status": "delivered",
            "items": [
                {"consignment_item_id": ors["id"], "delivered_quantity": "200"},
                {"consignment_item_id": rice["id"], "delivered_quantity": "30"},
            ],
        },
        str(uuid.uuid4()),
    )
    record(
        "status_must_match_the_recorded_quantities",
        status == 422
        and refused["error"]["code"] == "status_does_not_match_quantities",
        f"status={status} code={refused.get('error', {}).get('code')} "
        f"details={refused.get('error', {}).get('details')}",
    )

    # 10. A genuine partial delivery is accepted and recorded per item.
    status, receipt = request(
        "POST",
        f"/v1/trips/{trip_id}/receipt",
        driver_token,
        {
            "status": "partially_delivered",
            "received_by_ref": "PHC storekeeper",
            "notes": "Ten sacks left behind at the landslide halt.",
            "items": [
                {"consignment_item_id": ors["id"], "delivered_quantity": "200"},
                {
                    "consignment_item_id": rice["id"],
                    "delivered_quantity": "30",
                    "note": "10 short",
                },
            ],
        },
        str(uuid.uuid4()),
    )
    delivered_rice = None
    if status == 201:
        delivered_rice = next(
            line["delivered_quantity"]
            for line in receipt["receipt"]["items"]
            if line["consignment_item_id"] == rice["id"]
        )
    record(
        "partial_delivery_recorded_per_item",
        status == 201
        and receipt["consignment_status"] == "partially_delivered"
        and receipt["trip"]["status"] == "completed"
        and delivered_rice is not None
        and Decimal(delivered_rice) == Decimal("30"),
        f"status={status} consignment={receipt.get('consignment_status')} "
        f"trip={receipt.get('trip', {}).get('status')} rice_delivered={delivered_rice}",
    )

    # The dispatcher reads the receipt back.
    status, readback = request("GET", f"/v1/trips/{trip_id}/receipt", dispatcher_token)
    read_rice = None
    read_ors = None
    if status == 200 and readback.get("receipt"):
        by_item = {
            line["consignment_item_id"]: line["delivered_quantity"]
            for line in readback["receipt"]["items"]
        }
        read_rice = by_item.get(rice["id"])
        read_ors = by_item.get(ors["id"])
    record(
        "dispatcher_reads_the_receipt_back_line_by_line",
        status == 200
        and readback.get("receipt") is not None
        and readback["receipt"]["status"] == "partially_delivered"
        and read_rice is not None
        and Decimal(read_rice) == Decimal("30")
        and read_ors is not None,
        f"status={status} receipt_status={(readback.get('receipt') or {}).get('status')} "
        f"rice={read_rice} lines={len((readback.get('receipt') or {}).get('items', []))}",
    )

    # 11. The receipt is durable and its line items survive in the database.
    with psycopg.connect(database_url) as connection:
        line_count = connection.execute(
            """
            select count(*) from public.delivery_receipt_items as i
            join public.delivery_receipts as r on r.id = i.delivery_receipt_id
            where r.trip_id = %s::uuid
            """,
            (trip_id,),
        ).fetchone()[0]
        audit_count = connection.execute(
            "select count(*) from public.audit_events where entity_id = %s::uuid",
            (trip_id,),
        ).fetchone()[0]
    record(
        "receipt_lines_and_audit_persisted",
        line_count == 2 and audit_count >= 2,
        f"receipt_lines={line_count} trip_audit_events={audit_count}",
    )

    # 12. A completed trip cannot receive a second receipt.
    status, refused = request(
        "POST",
        f"/v1/trips/{trip_id}/receipt",
        driver_token,
        {
            "status": "delivered",
            "items": [
                {"consignment_item_id": ors["id"], "delivered_quantity": "200"},
                {"consignment_item_id": rice["id"], "delivered_quantity": "40"},
            ],
        },
        str(uuid.uuid4()),
    )
    record(
        "completed_trip_refuses_a_second_receipt",
        status == 409 and refused["error"]["code"] == "invalid_transition",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    # 13. One request, two consignments: the request only reports fulfilled
    #     when both arrived in full.
    status, supply_request = request(
        "POST",
        "/v1/supply-requests",
        dispatcher_token,
        {
            "facility_id": destination,
            "priority": "critical",
            "note": "Monsoon pre-positioning for the PHC.",
        },
        str(uuid.uuid4()),
    )
    record(
        "supply_request_raised",
        status == 201 and supply_request["status"] == "open",
        f"status={status} request_status={supply_request.get('status')}",
    )
    supply_request_id = supply_request["id"]

    trips: list[str] = []
    for index in range(2):
        _, part = request(
            "POST",
            "/v1/consignments",
            dispatcher_token,
            {
                "district_id": district_id,
                "reference": f"{reference}-P{index}",
                "origin_facility_id": origin,
                "destination_facility_id": destination,
                "supply_request_id": supply_request_id,
                "items": [
                    {
                        "commodity": f"Batch {index}",
                        "quantity": "10",
                        "unit": "crates",
                        "weight_kg": "100",
                    }
                ],
            },
            str(uuid.uuid4()),
        )
        request(
            "POST",
            f"/v1/consignments/{part['id']}/planned",
            dispatcher_token,
            {},
            str(uuid.uuid4()),
        )
        _, part_trip = request(
            "POST",
            "/v1/trips",
            dispatcher_token,
            {
                "consignment_id": part["id"],
                "vehicle_id": big_vehicle,
                "driver_profile_id": driver["profile_id"],
            },
            str(uuid.uuid4()),
        )
        part_trip_id = part_trip["trip"]["id"]
        trips.append(part_trip_id)
        request(
            "POST",
            f"/v1/trips/{part_trip_id}/awaiting_driver",
            dispatcher_token,
            None,
            str(uuid.uuid4()),
        )
        request(
            "POST",
            f"/v1/trips/{part_trip_id}/active",
            driver_token,
            None,
            str(uuid.uuid4()),
        )
        # First arrives short, second arrives in full.
        delivered = "6" if index == 0 else "10"
        receipt_status = "partially_delivered" if index == 0 else "delivered"
        request(
            "POST",
            f"/v1/trips/{part_trip_id}/receipt",
            driver_token,
            {
                "status": receipt_status,
                "items": [
                    {
                        "consignment_item_id": part["items"][0]["id"],
                        "delivered_quantity": delivered,
                    }
                ],
            },
            str(uuid.uuid4()),
        )

    status, listing = request("GET", "/v1/supply-requests?limit=200", dispatcher_token)
    rolled = next(
        (row for row in listing.get("requests", []) if row["id"] == supply_request_id),
        None,
    )
    record(
        "one_request_many_consignments_rolls_up_honestly",
        status == 200
        and rolled is not None
        and rolled["status"] == "partially_fulfilled"
        and rolled["consignment_count"] == 2
        and rolled["delivered_consignment_count"] == 1,
        f"status={status} request_status={rolled and rolled['status']} "
        f"consignments={rolled and rolled['consignment_count']} "
        f"delivered={rolled and rolled['delivered_consignment_count']}",
    )

    passed = all(check["passed"] for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "district_id": district_id,
                "consignment_reference": reference,
                "checks": checks,
                "passed": passed,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    if passed:
        print(f"ALL {len(checks)} CHECKS PASSED → {REPORT_PATH}")
        return 0
    print(f"FAILURES → {REPORT_PATH}", file=sys.stderr)
    return 1


def main() -> int:
    # The delivery browser run has already reset the database and started this
    # checkout's API on the same port, and builds its screens on what this
    # check creates.
    if os.environ.get("RASTA_STACK_READY") == "1":
        return run()
    # Otherwise a reset database and this checkout's API, like every other
    # runtime gate: never whatever an earlier session left running.
    with fresh_stack_with_api(API_PORT):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
