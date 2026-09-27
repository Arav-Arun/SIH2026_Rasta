"""Runtime check: telemetry ingestion, current location and staleness."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.lib.stack import (  # noqa: E402
    ServiceProcess,
    require_tool,
    run_command,
    start_service,
    stop_services,
    supabase_status_env,
    wait_for_url,
    write_env_files,
)

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "telemetry_check.json"
IDENTITY_FIXTURE = REPOSITORY_ROOT / "artifacts" / "e2e" / "e2e_identities.json"
API_PORT = "8010"
API = f"http://127.0.0.1:{API_PORT}"

PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))

# A short stretch of the Shillong bypass. Points are metres apart so nothing
# below is a jump; the jump case builds its own coordinates.
TRACK = [
    (25.5788, 91.8933),
    (25.5791, 91.8936),
    (25.5795, 91.8940),
    (25.5799, 91.8944),
]

checks: list[dict[str, Any]] = []
timings: dict[str, Any] = {}


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")


def sign_in(supabase_url: str, anon: str, email: str, password: str) -> str:
    request = urllib.request.Request(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["access_token"]


def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    grant: str | None = None,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, Any, float]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if grant:
        headers["X-Tracking-Grant"] = grant
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    request = urllib.request.Request(
        f"{API}{path}", data=data, headers=headers, method=method
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode() or "{}")
            return response.status, payload, time.perf_counter() - started
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read().decode() or "{}")
        return error.code, payload, time.perf_counter() - started


def point(
    *,
    index: int,
    captured_at: datetime,
    latitude: float | None = None,
    longitude: float | None = None,
    accuracy_m: str = "8.5",
    key: uuid.UUID | None = None,
) -> dict[str, Any]:
    lat, lon = TRACK[index % len(TRACK)]
    return {
        "client_point_id": f"p{index}",
        "idempotency_key": str(key or uuid.uuid4()),
        "captured_at": captured_at.isoformat(),
        "latitude": latitude if latitude is not None else lat,
        "longitude": longitude if longitude is not None else lon,
        "accuracy_m": accuracy_m,
        "speed_kph": "24.0",
        "battery_percent": "72",
    }


def outcomes(payload: Any) -> dict[str, str]:
    return {r["client_point_id"]: r["outcome"] for r in payload.get("results", [])}


def history_count(token: str, trip_id: str) -> int:
    _, payload, _ = call("GET", f"/v1/trips/{trip_id}/telemetry", token=token)
    return payload.get("total_points", -1)


def seed_trip(
    *, dispatcher: str, driver_profile_id: str, database_url: str, reference: str
) -> dict[str, Any]:
    """Create a consignment and a trip through the API, the way a dispatcher does."""

    with psycopg.connect(database_url) as connection:
        facilities = connection.execute(
            """
            select id::text, organization_id::text from public.facilities
            where district_id = %s::uuid order by id limit 2
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchall()
        if len(facilities) < 2:
            raise RuntimeError("The pilot district needs two facilities.")
        organization_id = facilities[0][1]
        # The pilot import carries roads and facilities, not a fleet.
        vehicle = connection.execute(
            """
            insert into public.vehicles (
              organization_id, registration_ref, class, capacity_kg, active, source_mode
            )
            values (%s::uuid, %s, 'light_truck', 2000, true, 'synthetic')
            returning id::text
            """,
            (organization_id, f"TEST-{uuid.uuid4().hex[:8]}"),
        ).fetchone()
        connection.commit()

    status, consignment, _ = call(
        "POST",
        "/v1/consignments",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "district_id": PILOT_DISTRICT_ID,
            "reference": reference,
            "origin_facility_id": facilities[0][0],
            "destination_facility_id": facilities[1][0],
            "priority": "normal",
            "deadline_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "supply_request_id": None,
            "items": [
                {
                    "commodity": "Rice",
                    "quantity": "500",
                    "unit": "kg",
                    "weight_kg": "500",
                }
            ],
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not create a consignment: {status} {consignment}")

    status, _, _ = call(
        "POST",
        f"/v1/consignments/{consignment['id']}/planned",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={},
    )
    if status != 200:
        raise RuntimeError(f"Could not plan the consignment: {status}")

    status, created, _ = call(
        "POST",
        "/v1/trips",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "consignment_id": consignment["id"],
            "vehicle_id": vehicle[0],
            "driver_profile_id": driver_profile_id,
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not create a trip: {status} {created}")
    return {
        "trip_id": created["trip"]["id"],
        "consignment_id": consignment["id"],
        "items": consignment["items"],
    }


def run_checks(*, env: dict[str, str], identities: dict[str, Any]) -> None:
    database_url = env["DB_URL"]
    supabase_url, anon = env["API_URL"], env["ANON_KEY"]

    dispatcher_identity = identities["pilot-dispatcher"]
    driver_identity = identities["driver"]
    other_identity = identities["dispatcher"]

    dispatcher = sign_in(
        supabase_url,
        anon,
        dispatcher_identity["email"],
        dispatcher_identity["password"],
    )
    driver = sign_in(
        supabase_url, anon, driver_identity["email"], driver_identity["password"]
    )
    other = sign_in(
        supabase_url, anon, other_identity["email"], other_identity["password"]
    )

    tracked = seed_trip(
        dispatcher=dispatcher,
        driver_profile_id=driver_identity["profile_id"],
        database_url=database_url,
        reference=f"TEST-{uuid.uuid4().hex[:8]}",
    )
    quiet = seed_trip(
        dispatcher=dispatcher,
        driver_profile_id=driver_identity["profile_id"],
        database_url=database_url,
        reference=f"TEST-QUIET-{uuid.uuid4().hex[:8]}",
    )
    trip_id, quiet_trip_id = tracked["trip_id"], quiet["trip_id"]

    # 1. Tracking is refused before the trip is running. The credential is not
    #    issued early and left waiting.
    status, _, _ = call(
        "POST",
        "/v1/devices",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={
            "device_public_id": f"e2e-device-{uuid.uuid4().hex}",
            "platform": "android",
            "display_label": "Telemetry check handset",
        },
    )
    device_public_id = None
    if status == 201:
        _, devices, _ = call("GET", "/v1/devices", token=driver)
        device_public_id = devices["devices"][0]["device_public_id"]
    record(
        "a_driver_can_register_their_own_device",
        status == 201 and device_public_id is not None,
        f"status={status} device_public_id={device_public_id}",
    )

    status, refusal, _ = call(
        "POST",
        f"/v1/trips/{trip_id}/tracking-grants",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={"device_public_id": device_public_id},
    )
    record(
        "no_credential_is_issued_before_the_trip_starts",
        status == 409 and refusal.get("error", {}).get("code") == "trip_not_tracking",
        f"status={status} code={refusal.get('error', {}).get('code')}",
    )

    for trip in (trip_id, quiet_trip_id):
        for action in ("awaiting_driver", "active"):
            status, moved, _ = call(
                "POST",
                f"/v1/trips/{trip}/{action}",
                token=dispatcher if action == "awaiting_driver" else driver,
                idempotency_key=str(uuid.uuid4()),
                body={},
            )
            if status != 200:
                raise RuntimeError(f"Could not move trip to {action}: {status} {moved}")

    # The trip just started, so every position a device could offer would be from before
    # it began.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            update public.trips set started_at = now() - interval '1 hour'
            where id = any(%s::uuid[])
            """,
            ([trip_id, quiet_trip_id],),
        )
        connection.commit()

    status, grant_payload, _ = call(
        "POST",
        f"/v1/trips/{trip_id}/tracking-grants",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={"device_public_id": device_public_id},
    )
    grant = grant_payload.get("token", "")
    device_id = grant_payload.get("device_id")
    readiness = grant_payload.get("readiness", {})
    record(
        "a_running_trip_issues_a_short_lived_scoped_credential",
        status == 201
        and bool(grant)
        and grant_payload["trip_id"] == trip_id
        and grant_payload["expires_at"] > grant_payload["issued_at"],
        f"status={status} expires_at={grant_payload.get('expires_at')}",
    )
    record(
        "the_start_check_states_that_no_route_is_approved_rather_than_implying_one",
        readiness.get("approved_route") is False
        and readiness.get("driver_assigned") is True
        and any("no approved route" in note for note in readiness.get("notes", [])),
        f"approved_route={readiness.get('approved_route')} notes={readiness.get('notes')}",
    )

    # 2. A normal batch is accepted and becomes the current position.
    # Recent enough that the newest fix is inside its freshness window: the
    # check below is about a live position, and staleness gets its own case.
    base = datetime.now(UTC) - timedelta(minutes=3)
    first_batch = [
        point(index=i, captured_at=base + timedelta(minutes=i)) for i in range(3)
    ]
    batch_key = str(uuid.uuid4())
    status, accepted, elapsed = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=batch_key,
        body={"trip_id": trip_id, "device_id": device_id, "points": first_batch},
    )
    timings["batch_seconds"] = round(elapsed, 3)
    location = accepted.get("current_location") or {}
    record(
        "a_normal_batch_is_accepted_and_becomes_the_current_position",
        status == 200
        and accepted.get("accepted") == 3
        and location.get("is_stale") is False
        and abs(location.get("latitude", 0) - TRACK[2][0]) < 1e-6,
        f"status={status} accepted={accepted.get('accepted')} "
        f"as_of={location.get('as_of')} seconds={timings['batch_seconds']}",
    )

    # 3. The same batch replayed under the same key returns the stored result.
    status, replay, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=batch_key,
        body={"trip_id": trip_id, "device_id": device_id, "points": first_batch},
    )
    record(
        "replaying_a_batch_returns_the_stored_result_and_adds_nothing",
        status == 200
        and replay.get("replayed") is True
        and history_count(driver, trip_id) == 3,
        f"status={status} replayed={replay.get('replayed')} "
        f"history={history_count(driver, trip_id)}",
    )

    # 4. The same points under a fresh batch key are recognised point by point.
    status, again, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={"trip_id": trip_id, "device_id": device_id, "points": first_batch},
    )
    record(
        "a_resumed_upload_of_stored_points_does_not_distort_history",
        status == 200
        and again.get("duplicates") == 3
        and again.get("accepted") == 0
        and history_count(driver, trip_id) == 3,
        f"duplicates={again.get('duplicates')} history={history_count(driver, trip_id)}",
    )

    # 5. A late point belongs in the timeline but must not move "now" backwards.
    before_late = (accepted.get("current_location") or {}).get("as_of")
    late = [point(index=0, captured_at=base - timedelta(minutes=5))]
    status, late_result, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={"trip_id": trip_id, "device_id": device_id, "points": late},
    )
    after_late = (late_result.get("current_location") or {}).get("as_of")
    record(
        "a_late_point_joins_the_timeline_without_moving_the_current_position_back",
        status == 200
        and late_result.get("accepted") == 1
        and after_late == before_late
        and history_count(driver, trip_id) == 4,
        f"accepted={late_result.get('accepted')} as_of_before={before_late} "
        f"as_of_after={after_late} history={history_count(driver, trip_id)}",
    )

    # 6. Points that cannot be true are refused with a reason the device can show.
    now = datetime.now(UTC)
    bad_batch = [
        point(index=1, captured_at=now, accuracy_m="150"),
        dict(
            point(index=2, captured_at=now + timedelta(seconds=30)),
            client_point_id="p_jump",
            latitude=26.9124,
            longitude=75.7873,
        ),
        dict(
            point(index=3, captured_at=now + timedelta(hours=2)),
            client_point_id="p_future",
        ),
    ]
    bad_batch[0]["client_point_id"] = "p_accuracy"
    status, judged, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={"trip_id": trip_id, "device_id": device_id, "points": bad_batch},
    )
    reasons = {r["client_point_id"]: r.get("reason") for r in judged.get("results", [])}
    record(
        "unusable_points_are_rejected_with_a_reason_the_device_can_show",
        status == 200
        and judged.get("rejected") == 3
        and reasons.get("p_accuracy") == "accuracy_too_poor"
        and reasons.get("p_jump") == "implausible_jump"
        and reasons.get("p_future") == "captured_in_future"
        and all(
            r.get("detail")
            for r in judged.get("results", [])
            if r["outcome"] == "rejected"
        ),
        f"rejected={judged.get('rejected')} reasons={reasons}",
    )

    # 7. The credential names one trip and one device. Claiming another fails.
    status, mismatch, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={
            "trip_id": quiet_trip_id,
            "device_id": device_id,
            "points": [point(index=0, captured_at=datetime.now(UTC))],
        },
    )
    record(
        "a_credential_cannot_file_positions_against_another_trip",
        status == 403
        and mismatch.get("error", {}).get("code") == "tracking_grant_mismatch"
        and history_count(driver, quiet_trip_id) == 0,
        f"status={status} code={mismatch.get('error', {}).get('code')}",
    )

    # 8. No credential at all, and a forged one.
    status, unauthenticated, _ = call(
        "POST",
        "/v1/telemetry/batches",
        idempotency_key=str(uuid.uuid4()),
        body={
            "trip_id": trip_id,
            "device_id": device_id,
            "points": [point(index=0, captured_at=datetime.now(UTC))],
        },
    )
    forged = grant[:-6] + "AAAAAA" if len(grant) > 10 else "forged"
    status_forged, refused, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=forged,
        idempotency_key=str(uuid.uuid4()),
        body={
            "trip_id": trip_id,
            "device_id": device_id,
            "points": [point(index=0, captured_at=datetime.now(UTC))],
        },
    )
    record(
        "positions_cannot_be_filed_without_a_valid_credential",
        status == 401
        and unauthenticated.get("error", {}).get("code") == "tracking_grant_required"
        and status_forged == 401
        and refused.get("error", {}).get("code") == "tracking_grant_invalid",
        f"missing={status} forged={status_forged} "
        f"codes={unauthenticated.get('error', {}).get('code')}/"
        f"{refused.get('error', {}).get('code')}",
    )

    # 9. A driver's session token is not a telemetry credential.
    status, session_attempt, _ = call(
        "POST",
        "/v1/telemetry/batches",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={
            "trip_id": trip_id,
            "device_id": device_id,
            "points": [point(index=0, captured_at=datetime.now(UTC))],
        },
    )
    record(
        "a_session_token_is_not_accepted_in_place_of_a_device_credential",
        status == 401
        and session_attempt.get("error", {}).get("code") == "tracking_grant_required",
        f"status={status} code={session_attempt.get('error', {}).get('code')}",
    )

    # 10. A driver cannot take a credential for somebody else's trip.
    other_driver_trip = quiet_trip_id
    status, foreign, _ = call(
        "POST",
        f"/v1/trips/{other_driver_trip}/tracking-grants",
        token=other,
        idempotency_key=str(uuid.uuid4()),
        body={"device_public_id": device_public_id},
    )
    record(
        "only_the_assigned_driver_can_take_a_tracking_credential",
        status in (403, 404),
        f"status={status} code={foreign.get('error', {}).get('code')}",
    )

    # 11. Reading a trip's track is scoped.
    status, readable, _ = call(
        "GET", f"/v1/trips/{trip_id}/telemetry", token=dispatcher
    )
    status_denied, denied, _ = call(
        "GET", f"/v1/trips/{trip_id}/telemetry", token=other
    )
    record(
        "a_track_is_readable_in_district_and_refused_outside_it",
        status == 200 and readable.get("total_points") == 4 and status_denied == 403,
        f"in_district={status} points={readable.get('total_points')} "
        f"out_of_district={status_denied}",
    )

    # 12. A long history is thinned, and the last fix is never dropped.
    status, thinned, _ = call(
        "GET", f"/v1/trips/{trip_id}/telemetry?limit=2", token=dispatcher
    )
    full_points = readable.get("points", [])
    thin_points = thinned.get("points", [])
    record(
        "a_thinned_track_still_ends_where_the_vehicle_ended",
        status == 200
        and thinned.get("downsampled") is True
        and len(thin_points) < len(full_points)
        and thin_points[-1]["id"] == full_points[-1]["id"],
        f"total={thinned.get('total_points')} returned={len(thin_points)} "
        f"stride={thinned.get('sample_interval')}",
    )

    # 13. The fleet view separates live, stale and silent trips.
    status, fleet, _ = call(
        "GET", f"/v1/fleet/locations?district_id={PILOT_DISTRICT_ID}", token=dispatcher
    )
    by_trip = {entry["trip_id"]: entry for entry in fleet.get("trips", [])}
    record(
        "the_fleet_view_says_live_or_silent_rather_than_showing_a_blank",
        status == 200
        and by_trip.get(trip_id, {}).get("reporting_state") == "live"
        and by_trip.get(quiet_trip_id, {}).get("reporting_state") == "never_reported"
        and by_trip.get(quiet_trip_id, {}).get("location") is None,
        f"tracked={by_trip.get(trip_id, {}).get('reporting_state')} "
        f"quiet={by_trip.get(quiet_trip_id, {}).get('reporting_state')}",
    )

    # 14. Staleness. The stored projection is backdated past its own window,
    #     which is the one thing that cannot be produced by waiting politely.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            update public.trip_current_location
            set as_of = now() - interval '30 minutes',
                stale_after = now() - interval '25 minutes'
            where trip_id = %s::uuid
            """,
            (trip_id,),
        )
        connection.commit()
    status, stale_fleet, _ = call(
        "GET", f"/v1/fleet/locations?district_id={PILOT_DISTRICT_ID}", token=dispatcher
    )
    stale_entry = next(
        (e for e in stale_fleet.get("trips", []) if e["trip_id"] == trip_id), {}
    )
    record(
        "a_position_past_its_window_is_shown_as_stale_not_as_current",
        status == 200
        and stale_entry.get("reporting_state") == "stale"
        and (stale_entry.get("location") or {}).get("is_stale") is True
        and stale_fleet.get("stale", 0) >= 1,
        f"state={stale_entry.get('reporting_state')} "
        f"is_stale={(stale_entry.get('location') or {}).get('is_stale')}",
    )

    # 15. Ending the trip closes ingestion, with the credential still in hand.
    status, _, _ = call(
        "POST",
        f"/v1/trips/{trip_id}/receipt",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={
            "status": "delivered",
            "received_by_ref": "Telemetry check receiver",
            "notes": None,
            "items": [
                {
                    "consignment_item_id": item["id"],
                    "delivered_quantity": item["quantity"],
                }
                for item in tracked["items"]
            ],
        },
    )
    status_after, after, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={
            "trip_id": trip_id,
            "device_id": device_id,
            "points": [point(index=0, captured_at=datetime.now(UTC))],
        },
    )
    after_reasons = {r.get("reason") for r in after.get("results", [])}
    record(
        "ending_the_trip_stops_ingestion_even_with_a_valid_credential",
        status == 201
        and status_after == 200
        and after.get("accepted") == 0
        and after_reasons == {"trip_not_tracking"},
        f"receipt={status} batch={status_after} accepted={after.get('accepted')} "
        f"reasons={sorted(r for r in after_reasons if r)}",
    )

    # 16. A completed trip leaves the live fleet view.
    _, fleet_after, _ = call(
        "GET", f"/v1/fleet/locations?district_id={PILOT_DISTRICT_ID}", token=dispatcher
    )
    record(
        "a_finished_trip_is_not_shown_as_a_vehicle_on_the_road",
        trip_id not in {entry["trip_id"] for entry in fleet_after.get("trips", [])},
        f"remaining={[e['trip_id'][:8] for e in fleet_after.get('trips', [])]}",
    )

    # 17. Every batch is auditable by counts, and no coordinate is in the audit.
    with psycopg.connect(database_url) as connection:
        events = connection.execute(
            """
            select metadata::text
            from public.audit_events
            where action = 'telemetry.batch_ingested' and entity_id = %s::uuid
            order by occurred_at
            """,
            (trip_id,),
        ).fetchall()
    metadata_blob = " ".join(row[0] for row in events)
    record(
        "telemetry_batches_are_audited_by_count_without_recording_coordinates",
        len(events) >= 4
        and "latitude" not in metadata_blob
        and "91.89" not in metadata_blob
        and '"accepted"' in metadata_blob,
        f"events={len(events)} contains_coordinates={'91.89' in metadata_blob}",
    )

    # 18. A route plan reaches a driver only once it has been approved.
    with psycopg.connect(database_url) as connection:
        pair = connection.execute(
            """
            select a.from_node_id as origin, b.to_node_id as destination
            from public.road_segments as a
            join public.road_segments as b
              on b.from_node_id = a.to_node_id
             and b.organization_id = a.organization_id
            where a.district_id = %s::uuid and a.from_node_id <> b.to_node_id
            order by a.id
            limit 1
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchone()
    if pair is None:
        raise RuntimeError("No two-hop path exists in the pilot graph.")

    status, plan_created, _ = call(
        "POST",
        "/v1/route-plans",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "district_id": PILOT_DISTRICT_ID,
            "priority": "normal",
            "requested_alternatives": 2,
            "origin_node_id": pair[0],
            "destination_node_id": pair[1],
        },
    )
    plan = plan_created.get("plan") or {}
    plan_id = plan.get("id")
    if status != 201 or not plan_id:
        raise RuntimeError(f"Could not plan a route: {status} {plan_created}")

    status_unapproved, unapproved, _ = call(
        "POST",
        f"/v1/trips/{quiet_trip_id}/route-plan",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={"route_plan_id": plan_id},
    )
    record(
        "a_proposed_route_cannot_be_handed_to_a_driver",
        status_unapproved == 409
        and unapproved.get("error", {}).get("code") == "route_plan_not_approved",
        f"plan_status={plan.get('status')} bind={status_unapproved} "
        f"code={unapproved.get('error', {}).get('code')}",
    )

    chosen = (plan.get("alternatives") or [{}])[0].get("id")
    status_approve, _, _ = call(
        "POST",
        f"/v1/route-plans/{plan_id}/approve",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "alternative_id": chosen,
            "expected_network_version": plan.get("network_version"),
        },
    )
    status_bind, bound, _ = call(
        "POST",
        f"/v1/trips/{quiet_trip_id}/route-plan",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={"route_plan_id": plan_id},
    )
    bound_trip = bound.get("trip") or {}
    record(
        "an_approved_route_is_bound_to_the_trip_and_visible_on_it",
        status_approve == 200
        and status_bind == 200
        and bound_trip.get("route_plan_id") == plan_id
        and bound_trip.get("route_plan_status") == "approved",
        f"approve={status_approve} bind={status_bind} "
        f"route_plan_status={bound_trip.get('route_plan_status')}",
    )

    # 19. Once a route is bound, the start check stops saying one is missing.
    status, second_grant, _ = call(
        "POST",
        f"/v1/trips/{quiet_trip_id}/tracking-grants",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={"device_public_id": device_public_id},
    )
    second_readiness = second_grant.get("readiness", {})
    record(
        "the_start_check_confirms_the_approved_route_once_there_is_one",
        status == 201
        and second_readiness.get("approved_route") is True
        and second_readiness.get("notes") == [],
        f"status={status} approved_route={second_readiness.get('approved_route')} "
        f"notes={second_readiness.get('notes')}",
    )

    # 20. A plan that has since been invalidated does not send a driver out.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "update public.route_plans set status = 'invalidated' where id = %s::uuid",
            (plan_id,),
        )
        connection.commit()
    status, _, _ = call(
        "POST",
        f"/v1/trips/{quiet_trip_id}/paused",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={},
    )
    status_restart, restart, _ = call(
        "POST",
        f"/v1/trips/{quiet_trip_id}/active",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={},
    )
    record(
        "a_trip_will_not_restart_on_a_route_that_has_been_invalidated",
        status == 200
        and status_restart == 409
        and restart.get("error", {}).get("code") == "route_plan_not_current",
        f"pause={status} restart={status_restart} "
        f"code={restart.get('error', {}).get('code')}",
    )


def main() -> int:
    require_tool("supabase")
    require_tool("npx")

    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError("API virtualenv not found under api/.venv.")

    # A server left running from an earlier session answers on this port and every check
    # then passes or fails against whatever build that process holds.
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", int(API_PORT))) == 0:
            raise RuntimeError(
                f"Something is already listening on port {API_PORT}. Stop it first; "
                "otherwise this run would test that process, not this checkout."
            )

    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(env, api_port=API_PORT)
    service_env = {**os.environ.copy(), **runtime_env}

    subprocess.run(
        [str(api_python), "scripts/pipeline/import_pilot_network.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=service_env,
    )
    bootstrap = subprocess.run(
        [str(api_python), "scripts/lib/identities.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=service_env,
    )
    payload = json.loads(bootstrap.stdout or "{}")
    identities = {item["key"]: item for item in payload.get("identities", [])}
    for required in ("pilot-dispatcher", "driver", "dispatcher"):
        if required not in identities:
            raise RuntimeError(f"The identity bootstrap did not create {required!r}.")

    services: list[ServiceProcess] = []
    try:
        services.append(
            start_service(
                "api",
                [
                    str(api_python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    API_PORT,
                ],
                cwd=REPOSITORY_ROOT / "api",
                env=service_env,
            )
        )
        wait_for_url(f"{API}/health")
        run_checks(env=env, identities=identities)
    finally:
        stop_services(services)

    passed = all(check["passed"] for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "district_id": PILOT_DISTRICT_ID,
                "timings": timings,
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
    failures = [check["name"] for check in checks if not check["passed"]]
    print(f"FAILURES ({len(failures)}): {failures} → {REPORT_PATH}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
