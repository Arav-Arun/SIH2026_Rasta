"""Runtime check for alerts, data health, recorded sources, risk and exposure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import psycopg.types.json

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.lib.identities import create_auth_user, create_profile  # noqa: E402
from scripts.lib.stack import (  # noqa: E402
    ServiceProcess,
    require_free_port,
    require_tool,
    run_command,
    start_service,
    stop_services,
    supabase_status_env,
    wait_for_url,
    write_env_files,
)
from scripts.pipeline.recorded_sources import redate_recorded_sources  # noqa: E402

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "operations_check.json"
API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
API = f"http://127.0.0.1:{API_PORT}"

PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))

checks: list[dict[str, Any]] = []
evidence: dict[str, Any] = {}


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)


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
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{API}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return error.code, {"raw": raw}


def test_png() -> bytes:
    """A one-pixel PNG built here, so no fixture image is mistaken for evidence."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x7f\x7f\x7f")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def inbox(token: str, **params: Any) -> list[dict[str, Any]]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    _, payload = call("GET", f"/v1/alerts{'?' + query if query else ''}", token=token)
    return payload.get("alerts", [])


def of_type(alerts: list[dict[str, Any]], alert_type: str) -> list[dict[str, Any]]:
    return [a for a in alerts if a.get("type") == alert_type]


def code_of(payload: Any) -> str | None:
    if isinstance(payload, dict):
        return payload.get("error", {}).get("code")
    return None


# --------------------------------------------------------------------------- #
# Setup that the API itself performs                                           #
# --------------------------------------------------------------------------- #


def seed_running_trip(
    *, dispatcher: str, driver_profile_id: str, database_url: str
) -> dict[str, Any]:
    """A consignment, a trip and a driver on the road, all through the API."""

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

    status, consignment = call(
        "POST",
        "/v1/consignments",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "district_id": PILOT_DISTRICT_ID,
            "reference": f"TEST-{uuid.uuid4().hex[:8]}",
            "origin_facility_id": facilities[0][0],
            "destination_facility_id": facilities[1][0],
            "priority": "high",
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

    status, _ = call(
        "POST",
        f"/v1/consignments/{consignment['id']}/planned",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={},
    )
    if status != 200:
        raise RuntimeError(f"Could not plan the consignment: {status}")

    status, created = call(
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
        "organization_id": organization_id,
        "vehicle_id": vehicle[0],
    }


def approve_plan_over(
    *, dispatcher: str, origin: str, destination: str, trip_id: str | None = None
) -> dict[str, Any]:
    """Plan and approve a route, the way a dispatcher does, and return it."""

    status, created = call(
        "POST",
        "/v1/route-plans",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "district_id": PILOT_DISTRICT_ID,
            "origin_node_id": origin,
            "destination_node_id": destination,
            "priority": "high",
            "requested_alternatives": 3,
            "trip_id": trip_id,
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not plan a route: {status} {created}")
    plan = created["plan"]
    if plan.get("status") == "no_route" or not plan.get("alternatives"):
        return plan

    chosen = plan["alternatives"][0]
    status, approved = call(
        "POST",
        f"/v1/route-plans/{plan['id']}/approve",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "alternative_id": chosen["id"],
            "expected_network_version": plan["network_version"],
        },
    )
    if status != 200:
        raise RuntimeError(f"Could not approve the plan: {status} {approved}")
    return approved["plan"]


def confirm_closure(
    *, officer: str, dispatcher: str, database_url: str, segment_id: str
) -> dict[str, Any]:
    """File a field report on one segment and confirm it closed."""

    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as connection:
        midpoint = connection.execute(
            """
            select st_y(st_lineinterpolatepoint(geometry::geometry, 0.5)) as lat,
                   st_x(st_lineinterpolatepoint(geometry::geometry, 0.5)) as lon
            from public.road_segments where id = %s::uuid
            """,
            (segment_id,),
        ).fetchone()

    image = test_png()
    status, created = call(
        "POST",
        "/v1/incidents",
        token=officer,
        idempotency_key=str(uuid.uuid4()),
        body={
            "type": "landslide_debris",
            "captured_at": (datetime.now(UTC) - timedelta(minutes=12)).isoformat(),
            "location": {"longitude": midpoint["lon"], "latitude": midpoint["lat"]},
            "location_source": "device_gps",
            "accuracy_m": 8.0,
            "note": "Operations check run. Generated test image, not a real observation.",
            "attachments": [
                {
                    "local_id": "att-1",
                    "mime_type": "image/png",
                    "bytes": len(image),
                    "sha256": hashlib.sha256(image).hexdigest(),
                }
            ],
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not file an incident: {status} {created}")
    incident_id = created["incident"]["id"]

    status, reviewed = call(
        "POST",
        f"/v1/incidents/{incident_id}/review",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={
            "decision": "confirm_closure",
            "affected_segment_ids": [segment_id],
            "reason": "Acceptance run: debris across the carriageway, confirmed closed.",
            "expected_version": created["incident"]["version"],
        },
    )
    if status != 200:
        raise RuntimeError(f"Could not review the incident: {status} {reviewed}")
    return {"incident_id": incident_id, "review": reviewed}


# --------------------------------------------------------------------------- #
# The checks                                                                   #
# --------------------------------------------------------------------------- #


def _lapsed_dispatcher(env: dict[str, str], *, organization_id: str) -> str:
    """A dispatcher of the pilot district whose grant ended yesterday."""

    user_id = create_auth_user(
        supabase_url=env["API_URL"],
        service_role_key=env["SERVICE_ROLE_KEY"],
        email=f"lapsed-{uuid.uuid4().hex[:10]}@e2e.rasta.test",
        password=f"Lapsed-{uuid.uuid4().hex}",
    )
    profile_id = create_profile(
        env["DB_URL"], user_id=user_id, display_name="Lapsed dispatcher"
    )
    with psycopg.connect(env["DB_URL"]) as connection:
        connection.execute(
            """
            insert into public.role_assignments (
              organization_id, profile_id, role, district_id, valid_from, valid_to
            )
            values (%s::uuid, %s::uuid, 'district_dispatcher', %s::uuid,
                    now() - interval '30 days', now() - interval '1 day')
            """,
            (organization_id, profile_id, PILOT_DISTRICT_ID),
        )
    return profile_id


def run_checks(*, env: dict[str, str], identities: dict[str, Any]) -> None:
    database_url = env["DB_URL"]
    supabase_url, anon = env["API_URL"], env["ANON_KEY"]

    dispatcher = sign_in(supabase_url, anon, *_creds(identities["pilot-dispatcher"]))
    officer = sign_in(supabase_url, anon, *_creds(identities["pilot-officer"]))
    driver = sign_in(supabase_url, anon, *_creds(identities["driver"]))
    outsider = sign_in(supabase_url, anon, *_creds(identities["dispatcher"]))
    organization_id = identities["pilot-dispatcher"]["organization_id"]

    # ------------------------------------------------------------------ #
    # Recorded sources and an explainable score                          #
    # ------------------------------------------------------------------ #

    status, first_run = call(
        "POST",
        f"/v1/risk/recompute?district_id={PILOT_DISTRICT_ID}",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
    )
    runs = {r["source"]: r for r in first_run.get("runs", [])}
    evidence["first_recompute"] = first_run
    record(
        "recorded_sources_run_and_are_labelled_recorded",
        status == 200
        and len(runs) >= 2
        and all(r["status"] in ("success", "unchanged") for r in runs.values())
        and any("recorded" in name for name in runs),
        f"status={status} runs={ {k: v['status'] for k, v in runs.items()} }",
    )
    record(
        "the_pilot_graph_is_scored_and_scoring_does_not_touch_passability",
        status == 200
        and first_run.get("segments_scored", 0) > 0
        and first_run.get("changes_passability") is False
        and first_run.get("model_version") == "baseline-v1",
        f"scored={first_run.get('segments_scored')} unscored={first_run.get('segments_unscored')} "
        f"levels={first_run.get('levels')} changes_passability={first_run.get('changes_passability')}",
    )

    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as connection:
        scored = connection.execute(
            """
            select segment_id::text, risk_score, risk_level::text, risk_model_version,
                   risk_computed_at, risk_explanation, passability::text
            from public.segment_current_state
            where organization_id = %s::uuid and risk_model_version is not null
            order by risk_score desc nulls last limit 1
            """,
            (organization_id,),
        ).fetchone()
        unscored_without_version = connection.execute(
            """
            select count(*) as n from public.segment_current_state
            where organization_id = %s::uuid
              and risk_score is not null and risk_model_version is null
            """,
            (organization_id,),
        ).fetchone()["n"]

    explanation = (scored or {}).get("risk_explanation") or {}
    evidence["worst_scored_segment"] = {
        k: v for k, v in (scored or {}).items() if k != "risk_computed_at"
    }
    record(
        "every_score_carries_its_model_version_and_explanation",
        scored is not None
        and scored["risk_model_version"] == "baseline-v1"
        and scored["risk_computed_at"] is not None
        and isinstance(explanation.get("contributions"), list)
        and len(explanation["contributions"]) > 0
        and "missing_inputs" in explanation
        and unscored_without_version == 0,
        f"version={(scored or {}).get('risk_model_version')} "
        f"contributions={len(explanation.get('contributions', []))} "
        f"missing={explanation.get('missing_inputs')} orphan_scores={unscored_without_version}",
    )
    record(
        "a_forecast_score_is_not_a_closure",
        scored is not None and scored["passability"] != "closed",
        f"risk_level={(scored or {}).get('risk_level')} "
        f"score={(scored or {}).get('risk_score')} passability={(scored or {}).get('passability')}",
    )

    # A second run over the same recorded documents must reuse them rather than
    # re-reading: the fixture's checksum is its ETag, exactly as a 304 would be.
    status, second_run = call(
        "POST",
        f"/v1/risk/recompute?district_id={PILOT_DISTRICT_ID}",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
    )
    reused = [r for r in second_run.get("runs", []) if r.get("reused_cached_content")]
    record(
        "unchanged_content_is_reused_rather_than_rescored_as_missing",
        status == 200
        and len(reused) >= 2
        and second_run.get("segments_scored") == first_run.get("segments_scored")
        and second_run.get("segments_unscored") == 0,
        f"status={status} reused={[r['source'] for r in reused]} "
        f"statuses={[r['status'] for r in second_run.get('runs', [])]} "
        f"scored {first_run.get('segments_scored')}→{second_run.get('segments_scored')}",
    )

    # ------------------------------------------------------------------ #
    # A closure withdraws the route but never swaps it                   #
    # ------------------------------------------------------------------ #

    with psycopg.connect(database_url) as connection:
        pair = connection.execute(
            """
            select a.from_node_id, b.to_node_id
            from public.road_segments as a
            join public.road_segments as b
              on b.from_node_id = a.to_node_id and b.organization_id = a.organization_id
            where a.district_id = %s::uuid and a.from_node_id <> b.to_node_id
            order by a.id limit 1
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchone()
    origin, destination = pair

    trip = seed_running_trip(
        dispatcher=dispatcher,
        driver_profile_id=identities["driver"]["profile_id"],
        database_url=database_url,
    )
    plan = approve_plan_over(
        dispatcher=dispatcher,
        origin=origin,
        destination=destination,
        trip_id=trip["trip_id"],
    )
    status, _ = call(
        "POST",
        f"/v1/trips/{trip['trip_id']}/route-plan",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={"route_plan_id": plan["id"]},
    )
    if status != 200:
        raise RuntimeError(f"Could not bind the plan to the trip: {status}")
    for action, actor in (("awaiting_driver", dispatcher), ("active", driver)):
        status, moved = call(
            "POST",
            f"/v1/trips/{trip['trip_id']}/{action}",
            token=actor if action == "active" else dispatcher,
            idempotency_key=str(uuid.uuid4()),
            body={},
        )
        if status != 200:
            raise RuntimeError(f"Could not move the trip to {action}: {status} {moved}")

    chosen = next(
        (
            a
            for a in plan["alternatives"]
            if a["id"] == plan.get("chosen_alternative_id")
        ),
        plan["alternatives"][0],
    )
    closing_segment = chosen["segment_ids"][len(chosen["segment_ids"]) // 2]
    evidence["approved_plan"] = {
        "plan_id": plan["id"],
        "chosen_alternative_id": plan.get("chosen_alternative_id"),
        "segments": len(chosen["segment_ids"]),
        "closing_segment": closing_segment,
    }

    lapsed = _lapsed_dispatcher(env, organization_id=organization_id)
    inbox_before = len(inbox(dispatcher))
    confirmed = confirm_closure(
        officer=officer,
        dispatcher=dispatcher,
        database_url=database_url,
        segment_id=closing_segment,
    )
    evidence["review"] = confirmed["review"]["segment_changes"]

    status, trip_list = call("GET", "/v1/trips?limit=200", token=dispatcher)
    trip_body = next(
        (t for t in trip_list.get("trips", []) if t["id"] == trip["trip_id"]), {}
    )
    record(
        "a_confirmed_closure_withdraws_the_route_without_moving_the_trip",
        status == 200
        and trip_body.get("route_plan_status") == "invalidated"
        and trip_body.get("status") == "active"
        and trip_body.get("route_plan_id") == plan["id"],
        f"trip_status={trip_body.get('status')} plan_status={trip_body.get('route_plan_status')}",
    )

    dispatcher_alerts = inbox(dispatcher)
    driver_alerts = inbox(driver)
    closed_alerts = of_type(dispatcher_alerts, "road_closed")
    invalidated = of_type(dispatcher_alerts, "trip_route_invalidated")
    driver_invalidated = of_type(driver_alerts, "trip_route_invalidated")
    evidence["dispatcher_alert_types"] = sorted({a["type"] for a in dispatcher_alerts})
    evidence["driver_alert_types"] = sorted({a["type"] for a in driver_alerts})

    record(
        "the_closure_reaches_the_dispatcher_inbox_once",
        len(closed_alerts) == 1
        and len(invalidated) == 1
        and len(dispatcher_alerts) > inbox_before,
        f"road_closed={len(closed_alerts)} trip_route_invalidated={len(invalidated)} "
        f"inbox {inbox_before}→{len(dispatcher_alerts)}",
    )
    with psycopg.connect(database_url) as connection:
        lapsed_copies = connection.execute(
            "select count(*) from public.alert_recipients where profile_id = %s::uuid",
            (lapsed,),
        ).fetchone()[0]
    record(
        "a_grant_that_has_ended_is_told_nothing",
        lapsed_copies == 0,
        f"alert copies for a dispatcher whose district grant ended yesterday: {lapsed_copies}",
    )
    record(
        "the_driver_is_told_about_their_own_trip_and_nothing_else",
        len(driver_invalidated) == 1
        and driver_invalidated[0]["subject_id"] == trip["trip_id"]
        and not of_type(driver_alerts, "road_closed"),
        f"driver_types={evidence['driver_alert_types']}",
    )
    payload = (invalidated[0] if invalidated else {}).get("payload", {})
    record(
        "the_alert_says_the_route_was_withdrawn_not_replaced",
        payload.get("route_replaced_automatically") is False
        and closing_segment in (payload.get("blocked_segment_ids") or [])
        and payload.get("route_plan_id") == plan["id"],
        f"route_replaced_automatically={payload.get('route_replaced_automatically')} "
        f"blocked={payload.get('blocked_segment_ids')}",
    )
    closure_payload = (closed_alerts[0] if closed_alerts else {}).get("payload", {})
    first = closed_alerts[0] if closed_alerts else {}
    record(
        "an_alert_keeps_its_validity_window_and_its_source",
        first.get("valid_from") is not None
        and first.get("valid_until") is not None
        and closure_payload.get("network_version") is not None
        and len(closure_payload.get("incident_ids") or []) >= 1,
        f"valid_from={first.get('valid_from')} valid_until={first.get('valid_until')} "
        f"network_version={closure_payload.get('network_version')} "
        f"incident_ids={closure_payload.get('incident_ids')}",
    )

    # ------------------------------------------------------------------ #
    # Dedupe, independent acknowledgement, scope                         #
    # ------------------------------------------------------------------ #

    with psycopg.connect(database_url) as connection:
        neighbour = connection.execute(
            """
            select id::text from public.road_segments
            where district_id = %s::uuid and id <> %s::uuid
            order by id limit 1
            """,
            (PILOT_DISTRICT_ID, closing_segment),
        ).fetchone()[0]

    confirm_closure(
        officer=officer,
        dispatcher=dispatcher,
        database_url=database_url,
        segment_id=neighbour,
    )
    after_second = of_type(inbox(dispatcher), "road_closed")
    record(
        "a_second_closure_in_the_same_hour_extends_one_alert_rather_than_adding_another",
        len(after_second) == 1
        and after_second[0]["id"] == closed_alerts[0]["id"]
        and after_second[0]["payload"].get("segment_count", 0) >= 1,
        f"road_closed_alerts={len(after_second)} same_id="
        f"{bool(after_second) and after_second[0]['id'] == closed_alerts[0]['id']}",
    )

    driver_alert_id = driver_invalidated[0]["id"]
    status, acked = call(
        "POST",
        f"/v1/alerts/{driver_alert_id}/acknowledge",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
    )
    status_replay, replay = call(
        "POST",
        f"/v1/alerts/{driver_alert_id}/acknowledge",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
    )
    dispatcher_copy = next(
        (
            a
            for a in of_type(inbox(dispatcher), "trip_route_invalidated")
            if a["id"] == driver_alert_id
        ),
        None,
    )
    record(
        "each_recipient_acknowledges_their_own_copy",
        status == 200
        and acked.get("acknowledged_at") is not None
        and status_replay == 200
        and replay.get("acknowledged_at") == acked.get("acknowledged_at")
        and dispatcher_copy is not None
        and dispatcher_copy.get("acknowledged_at") is None,
        f"driver_ack={acked.get('acknowledged_at')} replay_same="
        f"{replay.get('acknowledged_at') == acked.get('acknowledged_at')} "
        f"dispatcher_ack={dispatcher_copy.get('acknowledged_at') if dispatcher_copy else 'missing'}",
    )
    unacked = inbox(driver, unacknowledged="true")
    record(
        "the_unacknowledged_filter_drops_what_was_acknowledged",
        all(a["id"] != driver_alert_id for a in unacked),
        f"unacknowledged={len(unacked)} still_listed="
        f"{any(a['id'] == driver_alert_id for a in unacked)}",
    )

    outsider_alerts = inbox(outsider)
    status, refused = call(
        "POST",
        f"/v1/alerts/{driver_alert_id}/acknowledge",
        token=outsider,
        idempotency_key=str(uuid.uuid4()),
    )
    record(
        "an_alert_nobody_sent_you_cannot_be_read_or_acknowledged",
        all(a["id"] != driver_alert_id for a in outsider_alerts) and status == 404,
        f"outsider_inbox={len(outsider_alerts)} acknowledge_status={status} code={code_of(refused)}",
    )

    # ------------------------------------------------------------------ #
    # A destination with no route is an escalation                       #
    # ------------------------------------------------------------------ #

    isolated = _isolate_a_node(database_url, organization_id)
    if isolated is None:
        record(
            "a_cut_off_destination_raises_an_escalation",
            False,
            "No isolable node found",
        )
    else:
        node_id, closed_count = isolated
        no_route = approve_plan_over(
            dispatcher=dispatcher, origin=origin, destination=node_id
        )
        escalations = of_type(inbox(dispatcher), "facility_isolated")
        match = [a for a in escalations if a.get("subject_id") == node_id]
        record(
            "a_cut_off_destination_raises_an_escalation",
            no_route.get("result_status") == "no_route"
            and len(match) == 1
            and match[0]["severity"] == "critical"
            and match[0]["payload"].get("next_step")
            == "escalate_for_alternative_transport"
            and len(match[0]["payload"].get("reason_codes") or []) > 0,
            f"result_status={no_route.get('result_status')} closed_approaches={closed_count} "
            f"escalations={len(match)} reasons="
            f"{match[0]['payload'].get('reason_codes') if match else None}",
        )

    # ------------------------------------------------------------------ #
    # Health an operator can read, and a source that breaks              #
    # ------------------------------------------------------------------ #

    status, health = call("GET", "/v1/data-health", token=dispatcher)
    sources = {s["source"]: s for s in health.get("sources", [])}
    coverage = next(
        (
            c
            for c in health.get("coverage", [])
            if c["district_id"] == PILOT_DISTRICT_ID
        ),
        None,
    )
    evidence["data_health"] = health
    record(
        "data_health_reports_recorded_sources_as_recorded_not_live",
        status == 200
        and len(sources) >= 2
        and all(
            s["state"]
            in ("recorded", "live", "stale", "failed", "never_run", "disabled")
            for s in sources.values()
        )
        and any(s["state"] == "recorded" for s in sources.values())
        and not any(s["state"] == "live" for s in sources.values()),
        f"status={status} states={ {k: v['state'] for k, v in sources.items()} }",
    )
    record(
        "coverage_is_reported_with_its_denominator",
        coverage is not None
        and coverage["segments_total"] > 1000
        and coverage["segments_with_risk_score"] > 0
        and coverage["facilities_total"] > 0
        and coverage["risk_model_version"] == "baseline-v1"
        and coverage["graph_version"] is not None,
        f"risk_scored={coverage['segments_with_risk_score'] if coverage else None}"
        f"/{coverage['segments_total'] if coverage else None} "
        f"facilities_on_graph={coverage['facilities_on_routing_graph'] if coverage else None}"
        f"/{coverage['facilities_total'] if coverage else None}",
    )
    queue = health.get("queues") or {}
    record(
        "queue_and_device_health_are_visible",
        isinstance(queue, dict)
        and "unprocessed_outbox" in queue
        and "trips_never_reported" in queue
        and queue.get("trips_never_reported", -1) >= 1,
        f"queue={queue}",
    )

    secrets = {
        "service_role_key": env["SERVICE_ROLE_KEY"],
        "anon_key": anon,
        "database_url": database_url,
    }
    serialized = json.dumps(health)
    leaked = [name for name, value in secrets.items() if value and value in serialized]
    jwt_like = re.findall(r"eyJ[A-Za-z0-9_-]{10,}", serialized)
    record(
        "no_secret_or_token_appears_in_the_health_report",
        not leaked and not jwt_like and "postgres://" not in serialized,
        f"leaked={leaked} jwt_like={len(jwt_like)} bytes={len(serialized)}",
    )

    status_driver, refused_driver = call("GET", "/v1/data-health", token=driver)
    status_none, refused_none = call(
        "GET",
        "/v1/data-health",
        token=sign_in(supabase_url, anon, *_creds(identities["no-scope"])),
    )
    record(
        "health_is_an_operator_view_and_is_refused_to_others",
        status_driver == 403 and status_none in (403, 404),
        f"driver={status_driver}/{code_of(refused_driver)} no_scope={status_none}/{code_of(refused_none)}",
    )

    # A source that stops answering must not erase what was already decided, and must
    # show as stale rather than quietly as fine.
    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as connection:
        scores_before = connection.execute(
            "select count(*) as n from public.segment_current_state "
            "where organization_id = %s::uuid and risk_score is not null",
            (organization_id,),
        ).fetchone()["n"]
        connection.execute(
            """
            update public.source_runs
            set started_at = now() - interval '9 hours', finished_at = now() - interval '9 hours'
            where organization_id = %s::uuid
            """,
            (organization_id,),
        )
        connection.commit()

    _, stale_health = call("GET", "/v1/data-health", token=dispatcher)
    stale_states = {s["source"]: s["state"] for s in stale_health.get("sources", [])}
    record(
        "an_expired_source_reads_as_stale",
        bool(stale_states) and all(state == "stale" for state in stale_states.values()),
        f"states={stale_states}",
    )

    broken = _break_a_source(
        env=env, organization_id=organization_id, dispatcher=dispatcher
    )
    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as connection:
        scores_after = connection.execute(
            "select count(*) as n from public.segment_current_state "
            "where organization_id = %s::uuid and risk_score is not null",
            (organization_id,),
        ).fetchone()["n"]
    _, broken_health = call("GET", "/v1/data-health", token=dispatcher)
    failed = [s for s in broken_health.get("sources", []) if s["state"] == "failed"]
    evidence["broken_source_run"] = broken
    record(
        "a_malformed_source_fails_visibly_and_erases_nothing",
        broken is not None
        and broken["status"] == "failed"
        and broken["error_code"] == "malformed_payload"
        and scores_after == scores_before
        and scores_before > 0
        and len(failed) >= 1
        and all(
            "Traceback" not in json.dumps(s) and "postgres" not in json.dumps(s)
            for s in failed
        ),
        f"run_status={broken['status'] if broken else None} "
        f"error={broken['error_code'] if broken else None} "
        f"scores {scores_before}→{scores_after} failed_sources={len(failed)}",
    )


def _creds(identity: dict[str, Any]) -> tuple[str, str]:
    return identity["email"], identity["password"]


def _isolate_a_node(database_url: str, organization_id: str) -> tuple[str, int] | None:
    """Close every road into one node, so the planner genuinely has no answer."""

    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as connection:
        candidate = connection.execute(
            """
            select to_node_id as node_id, count(*) as approaches
            from public.road_segments
            where district_id = %s::uuid
            group by to_node_id
            having count(*) between 1 and 2
            order by count(*), to_node_id
            limit 1
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchone()
        if candidate is None:
            return None
        node_id = candidate["node_id"]
        segments = connection.execute(
            """
            select id::text as id, network_version from public.road_segments
            where district_id = %s::uuid and (to_node_id = %s or from_node_id = %s)
            """,
            (PILOT_DISTRICT_ID, node_id, node_id),
        ).fetchall()
        for segment in segments:
            connection.execute(
                """
                insert into public.segment_current_state (
                  organization_id, segment_id, passability, network_version,
                  as_of, source_summary
                )
                values (
                  %(org)s::uuid, %(segment)s::uuid, 'closed', %(version)s, now(),
                  %(summary)s
                )
                on conflict (organization_id, segment_id) do update
                set passability = 'closed',
                    as_of = now(),
                    source_summary = excluded.source_summary
                """,
                {
                    "org": organization_id,
                    "segment": segment["id"],
                    "version": segment["network_version"],
                    "summary": psycopg.types.json.Jsonb(
                        {
                            "passability_basis": "Operations check: every approach "
                            "closed for the no-route check",
                            "source_mode": "synthetic",
                        }
                    ),
                },
            )
        connection.commit()
    return node_id, len(segments)


def _break_a_source(
    *, env: dict[str, str], organization_id: str, dispatcher: str
) -> dict[str, Any] | None:
    """Run the pipeline against the malformed CAP document and report the run."""

    from app.sources import (  # noqa: PLC0415
        CapWarningAdapter,
        FixtureAdapter,
        connect,
        run_source,
    )

    fixture = REPOSITORY_ROOT / "data" / "fixtures" / "sources" / "malformed_cap.xml"
    if not fixture.exists():
        return None
    adapter = FixtureAdapter("sachet_cap_recorded", fixture, CapWarningAdapter(None))
    with connect(env["DB_URL"]) as connection, connection.transaction():
        outcome, records = run_source(
            connection,
            organization_id=organization_id,
            adapter=adapter,
            source_mode="recorded",
            district_id=PILOT_DISTRICT_ID,
        )
    return {**outcome.as_dict(), "records": len(records)}


def main() -> int:
    require_tool("supabase")
    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError("API virtualenv not found under api/.venv.")
    require_free_port(int(API_PORT), what="the API")

    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(env, api_port=API_PORT)
    service_env = {**os.environ.copy(), **runtime_env}
    sys.path.insert(0, str(REPOSITORY_ROOT / "api"))
    os.environ.update(runtime_env)

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
    for identity in identities.values():
        identity["organization_id"] = payload["organization_id"]
    for required in (
        "pilot-dispatcher",
        "pilot-officer",
        "driver",
        "dispatcher",
        "no-scope",
    ):
        if required not in identities:
            raise RuntimeError(f"The identity bootstrap did not create {required!r}.")

    # The recorded documents have fixed issue times, and a forecast over 12 h old is
    # missing to the risk engine.
    sources = redate_recorded_sources(REPOSITORY_ROOT / "artifacts" / "e2e" / "sources")
    service_env["SOURCE_FIXTURE_ROOT"] = sources["directory"]
    evidence["recorded_sources"] = sources

    services: list[ServiceProcess] = []
    started = time.perf_counter()
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
                "check": "check_operations",
                "district_id": PILOT_DISTRICT_ID,
                "elapsed_seconds": round(time.perf_counter() - started, 1),
                "checks": checks,
                "evidence": evidence,
                "passed": passed,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print()
    if passed:
        print(f"ALL {len(checks)} CHECKS PASSED → {REPORT_PATH}")
        return 0
    failures = [c["name"] for c in checks if not c["passed"]]
    print(f"FAILURES ({len(failures)}): {failures} → {REPORT_PATH}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
