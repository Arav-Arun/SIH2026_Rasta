#!/usr/bin/env python3
"""Security acceptance checks against the local stack."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import socket
import struct
import subprocess
import sys
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
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

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "security_check.json"
API_PORT = "8010"
API = f"http://127.0.0.1:{API_PORT}"
#: A second API process on the same database, as a deployment with two
#: workers would have: the racing-retry check sends to both.
SECOND_API_PORT = "8011"
SECOND_API = f"http://127.0.0.1:{SECOND_API_PORT}"
ALLOWED_ORIGIN = "http://127.0.0.1:3000"

PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))
ORG_A = "a2600002-0000-4000-8000-000000000001"
SHILLONG = (25.5788, 91.8933)

checks: list[dict[str, Any]] = []
not_run = [
    {
        "name": "native_tracking_stops_at_sign_out",
        "reason": "Needs a physical Android phone. The Expo app's sign-out now "
        "stops tracking and deletes the trip credential and unsent positions "
        "(apps/mobile/services/session.ts); that is code, not a device observation.",
    },
    {
        "name": "first_tracking_prompt_and_notification_disclose_collection",
        "reason": "Needs a physical Android phone. The disclosure shown before the system "
        "prompt is in apps/mobile/components/driver/TrackerPanel.tsx; the persistent "
        "notification belongs to the native foreground service, which is not built yet.",
    },
]
secrets_used: list[str] = []


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")


# --- people -------------------------------------------------------------------


def create_person(
    env: dict[str, str],
    *,
    organization_id: str,
    role: str,
    district_id: str | None,
    label: str,
) -> dict[str, str]:
    """An Auth user with a profile and one role grant, signed in."""

    email = f"test-{role}-{uuid.uuid4().hex[:8]}@example.test"
    password = f"TEST-{uuid.uuid4().hex}"
    service_key = env["SERVICE_ROLE_KEY"]
    created = httpx.post(
        f"{env['API_URL']}/auth/v1/admin/users",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30.0,
    )
    created.raise_for_status()
    with psycopg.connect(env["DB_URL"]) as connection:
        profile_id = connection.execute(
            """
            insert into public.profiles (id, user_id, organization_id, display_name, locale, active)
            values (%s::uuid, %s::uuid, %s::uuid, %s, 'en', true)
            returning id::text
            """,
            (str(uuid.uuid4()), created.json()["id"], organization_id, label),
        ).fetchone()[0]
        connection.execute(
            """
            insert into public.role_assignments (organization_id, profile_id, role, district_id)
            values (%s::uuid, %s::uuid, %s::public.app_role, %s::uuid)
            """,
            (organization_id, profile_id, role, district_id),
        )
        connection.commit()
    token = httpx.post(
        f"{env['API_URL']}/auth/v1/token?grant_type=password",
        headers={"apikey": env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]},
        json={"email": email, "password": password},
        timeout=30.0,
    )
    token.raise_for_status()
    access = token.json()["access_token"]
    secrets_used.extend([access, password])
    return {"profile_id": profile_id, "token": access, "role": role}


def second_organization(env: dict[str, str]) -> str:
    """Another agency working the same district: the tenancy boundary under test."""

    organization_id = str(uuid.uuid4())
    with psycopg.connect(env["DB_URL"]) as connection:
        connection.execute(
            "insert into public.organizations (id, name, mode) values (%s::uuid, %s, 'local_demo')",
            (organization_id, "Second agency (synthetic)"),
        )
        connection.execute(
            """
            insert into public.organization_districts (organization_id, district_id, active)
            values (%s::uuid, %s::uuid, true)
            """,
            (organization_id, PILOT_DISTRICT_ID),
        )
        connection.commit()
    return organization_id


# --- HTTP ---------------------------------------------------------------------


def api(
    method: str,
    path: str,
    *,
    token: str | None = None,
    grant: str | None = None,
    body: Any = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    base: str = API,
) -> httpx.Response:
    sent = {"Accept": "application/json", **(headers or {})}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    if grant:
        sent["X-Tracking-Grant"] = grant
    if method in {"POST", "PATCH", "PUT", "DELETE"}:
        sent.setdefault("Idempotency-Key", str(uuid.uuid4()))
    return httpx.request(
        method, f"{base}{path}", headers=sent, json=body, content=content, timeout=60.0
    )


def code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:
        return None


def test_png(seed: int) -> bytes:
    """A tiny valid PNG (a generated pattern) used only as test evidence."""

    width = height = 16
    rows = b"".join(
        b"\x00" + bytes(((x * 17 + y * 29 + seed) % 256) for x in range(width * 3))
        for y in range(height)
    )

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def storage_upload(
    env: dict[str, str], *, token: str, path: str, content: bytes, mime: str
) -> httpx.Response:
    return httpx.post(
        f"{env['API_URL']}/storage/v1/object/evidence/{path}",
        headers={
            "apikey": env.get("PUBLISHABLE_KEY") or env["ANON_KEY"],
            "Authorization": f"Bearer {token}",
            "Content-Type": mime,
            "x-upsert": "false",
        },
        content=content,
        timeout=30.0,
    )


def file_report(officer: dict[str, str], photo: bytes, *, seed: str) -> dict[str, Any]:
    response = api(
        "POST",
        "/v1/incidents",
        token=officer["token"],
        body={
            "type": "landslide_debris",
            "captured_at": datetime.now(UTC).isoformat(),
            "location": {"latitude": SHILLONG[0], "longitude": SHILLONG[1]},
            "location_source": "device_gps",
            "accuracy_m": 8,
            "note": f"Security check report {seed} (synthetic)",
            "attachments": [
                {
                    "local_id": f"photo-{seed}",
                    "mime_type": "image/png",
                    "bytes": len(photo),
                    "sha256": hashlib.sha256(photo).hexdigest(),
                }
            ],
        },
    )
    if response.status_code != 201:
        raise RuntimeError(
            f"Could not file a report: {response.status_code} {response.text[:300]}"
        )
    return response.json()


# --- trips --------------------------------------------------------------------


def seed_trip(
    env: dict[str, str], dispatcher: dict[str, str], driver: dict[str, str], label: str
) -> str:
    with psycopg.connect(env["DB_URL"]) as connection:
        facilities = connection.execute(
            """
            select id::text from public.facilities
            where district_id = %s::uuid and organization_id = %s::uuid order by id limit 2
            """,
            (PILOT_DISTRICT_ID, ORG_A),
        ).fetchall()
        vehicle_id = connection.execute(
            """
            insert into public.vehicles (organization_id, registration_ref, class, capacity_kg, active, source_mode)
            values (%s::uuid, %s, 'light_truck', 2000, true, 'synthetic')
            returning id::text
            """,
            (ORG_A, f"TEST-{uuid.uuid4().hex[:8]}"),
        ).fetchone()[0]
        connection.commit()

    consignment = api(
        "POST",
        "/v1/consignments",
        token=dispatcher["token"],
        body={
            "district_id": PILOT_DISTRICT_ID,
            "reference": f"TEST-{label}-{uuid.uuid4().hex[:6]}",
            "origin_facility_id": facilities[0][0],
            "destination_facility_id": facilities[1][0],
            "priority": "normal",
            "deadline_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "supply_request_id": None,
            "items": [
                {
                    "commodity": "Rice",
                    "quantity": "100",
                    "unit": "kg",
                    "weight_kg": "100",
                }
            ],
        },
    )
    consignment.raise_for_status()
    consignment_id = consignment.json()["id"]
    api(
        "POST",
        f"/v1/consignments/{consignment_id}/planned",
        token=dispatcher["token"],
        body={},
    ).raise_for_status()
    trip = api(
        "POST",
        "/v1/trips",
        token=dispatcher["token"],
        body={
            "consignment_id": consignment_id,
            "vehicle_id": vehicle_id,
            "driver_profile_id": driver["profile_id"],
        },
    )
    trip.raise_for_status()
    trip_id = trip.json()["trip"]["id"]
    api(
        "POST",
        f"/v1/trips/{trip_id}/awaiting_driver",
        token=dispatcher["token"],
        body={},
    ).raise_for_status()
    api(
        "POST", f"/v1/trips/{trip_id}/active", token=driver["token"], body={}
    ).raise_for_status()
    # Positions must fall inside the trip; move its start back so an hour of
    # history can be filed now (the same adjustment the telemetry check makes).
    with psycopg.connect(env["DB_URL"]) as connection:
        connection.execute(
            "update public.trips set started_at = now() - interval '1 hour' where id = %s::uuid",
            (trip_id,),
        )
        connection.commit()
    return trip_id


def track(driver: dict[str, str], trip_id: str, device_public_id: str) -> str:
    grant = api(
        "POST",
        f"/v1/trips/{trip_id}/tracking-grants",
        token=driver["token"],
        body={"device_public_id": device_public_id},
    )
    grant.raise_for_status()
    payload = grant.json()
    secrets_used.append(payload["token"])
    base = datetime.now(UTC) - timedelta(minutes=30)
    points = [
        {
            "client_point_id": f"p{index}",
            "idempotency_key": str(uuid.uuid4()),
            "captured_at": (base + timedelta(minutes=index)).isoformat(),
            "latitude": SHILLONG[0] + index * 0.0003,
            "longitude": SHILLONG[1] + index * 0.0003,
            "accuracy_m": "8.5",
        }
        for index in range(3)
    ]
    sent = api(
        "POST",
        "/v1/telemetry/batches",
        grant=payload["token"],
        body={"trip_id": trip_id, "device_id": payload["device_id"], "points": points},
    )
    sent.raise_for_status()
    return payload["token"]


def points_for(database_url: str, trip_id: str) -> tuple[int, int]:
    with psycopg.connect(database_url) as connection:
        history = connection.execute(
            "select count(*) from public.telemetry_points where trip_id = %s::uuid",
            (trip_id,),
        ).fetchone()[0]
        current = connection.execute(
            "select count(*) from public.trip_current_location where trip_id = %s::uuid",
            (trip_id,),
        ).fetchone()[0]
    return int(history), int(current)


# --- the checks ---------------------------------------------------------------


def run_checks(env: dict[str, str], identities: dict[str, Any], api_log: Path) -> None:
    database_url = env["DB_URL"]
    publishable = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]

    officer = create_person(
        env,
        organization_id=ORG_A,
        role="field_officer",
        district_id=PILOT_DISTRICT_ID,
        label="Security check officer",
    )
    dispatcher = create_person(
        env,
        organization_id=ORG_A,
        role="district_dispatcher",
        district_id=PILOT_DISTRICT_ID,
        label="Security check dispatcher",
    )
    driver = create_person(
        env,
        organization_id=ORG_A,
        role="driver",
        district_id=PILOT_DISTRICT_ID,
        label="Security check driver",
    )
    admin = create_person(
        env,
        organization_id=ORG_A,
        role="admin",
        district_id=None,
        label="Security check administrator",
    )
    seed_district_dispatcher = identities["dispatcher"]
    seed_district = create_person(
        env,
        organization_id=ORG_A,
        role="district_dispatcher",
        district_id=seed_district_dispatcher["district_id"],
        label="Dispatcher of another district",
    )
    org_b = second_organization(env)
    outsider = create_person(
        env,
        organization_id=org_b,
        role="district_dispatcher",
        district_id=PILOT_DISTRICT_ID,
        label="Other agency dispatcher",
    )

    photo = test_png(37)
    filed = file_report(officer, photo, seed="a")
    incident = filed["incident"]
    instruction = filed["upload_instructions"][0]

    # --- a retry racing the first attempt ------------------------------------- A phone
    # that times out resends with the same key while the first request may still be
    # running, possibly on another API process.
    racing_key = str(uuid.uuid4())
    racing_body = {
        "type": "flooding",
        "captured_at": datetime.now(UTC).isoformat(),
        "location": {"latitude": SHILLONG[0], "longitude": SHILLONG[1]},
        "location_source": "device_gps",
        "accuracy_m": 12,
        "note": "Racing retry (synthetic)",
        "attachments": [],
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        racing = list(
            pool.map(
                lambda n: api(
                    "POST",
                    "/v1/incidents",
                    token=officer["token"],
                    body=racing_body,
                    headers={"Idempotency-Key": racing_key},
                    base=SECOND_API if n % 2 else API,
                ),
                range(8),
            )
        )
    ids = {
        r.json().get("incident", {}).get("id")
        for r in racing
        if r.status_code in (200, 201)
    }
    with psycopg.connect(database_url) as connection:
        stored = connection.execute(
            "select count(*) from public.incidents where idempotency_key = %s::uuid",
            (racing_key,),
        ).fetchone()[0]
    record(
        "simultaneous_retries_of_one_write_apply_it_once",
        all(r.status_code in (200, 201) for r in racing)
        and len(ids) == 1
        and stored == 1,
        f"statuses={[r.status_code for r in racing]} distinct_ids={len(ids)} rows={stored}",
    )

    # --- tenancy: another organisation, a guessed id --------------------------
    response = api("GET", f"/v1/incidents/{incident['id']}", token=outsider["token"])
    record(
        "another_organisation_cannot_read_a_report_by_guessed_id",
        response.status_code == 404,
        f"status={response.status_code} code={code(response)} (404, so its existence is not confirmed)",
    )
    response = api("GET", "/v1/incidents", token=outsider["token"])
    listed = (
        [row["id"] for row in response.json().get("incidents", [])]
        if response.status_code == 200
        else []
    )
    record(
        "another_organisation_does_not_see_it_listed",
        response.status_code == 200 and incident["id"] not in listed,
        f"status={response.status_code} listed={len(listed)}",
    )
    rest = httpx.get(
        f"{env['API_URL']}/rest/v1/incidents",
        params={"id": f"eq.{incident['id']}", "select": "id"},
        headers={"apikey": publishable, "Authorization": f"Bearer {outsider['token']}"},
        timeout=30.0,
    )
    record(
        "row_level_security_hides_it_from_the_database_api_too",
        rest.status_code in (200, 401, 403)
        and (rest.status_code != 200 or rest.json() == []),
        f"status={rest.status_code} rows={len(rest.json()) if rest.status_code == 200 else '-'}",
    )
    response = api(
        "POST",
        f"/v1/incidents/{incident['id']}/review",
        token=outsider["token"],
        body={
            "decision": "confirm_closure",
            "reason": "forged by another agency",
            "expected_version": incident["version"],
        },
    )
    record(
        "another_organisation_cannot_decide_on_it",
        response.status_code in (403, 404),
        f"status={response.status_code} code={code(response)}",
    )

    # --- roles: a forged request without the capability ----------------------
    response = api(
        "POST",
        f"/v1/incidents/{incident['id']}/review",
        token=officer["token"],
        body={
            "decision": "confirm_closure",
            "reason": "officer tries to close",
            "expected_version": incident["version"],
        },
    )
    record(
        "a_field_officer_cannot_confirm_a_closure",
        response.status_code == 403,
        f"status={response.status_code} code={code(response)}",
    )
    response = api(
        "POST",
        f"/v1/incidents/{incident['id']}/review",
        token=seed_district["token"],
        body={
            "decision": "confirm_closure",
            "reason": "wrong district",
            "expected_version": incident["version"],
        },
    )
    record(
        "a_dispatcher_of_another_district_cannot_decide",
        response.status_code in (403, 404),
        f"status={response.status_code} code={code(response)}",
    )
    response = api(
        "POST",
        "/v1/route-plans",
        token=driver["token"],
        body={
            "district_id": PILOT_DISTRICT_ID,
            "origin_node_id": "n1",
            "destination_node_id": "n2",
        },
    )
    record(
        "a_driver_cannot_plan_or_approve_a_route",
        response.status_code == 403,
        f"status={response.status_code} code={code(response)}",
    )

    # --- evidence: the storage path, the bytes, the type ----------------------
    elsewhere = instruction["path"].replace(
        instruction["attachment_id"], str(uuid.uuid4())
    )
    upload = storage_upload(
        env, token=officer["token"], path=elsewhere, content=photo, mime="image/png"
    )
    record(
        "evidence_cannot_be_written_to_another_path",
        upload.status_code >= 400,
        f"status={upload.status_code}",
    )
    upload = storage_upload(
        env,
        token=outsider["token"],
        path=instruction["path"],
        content=photo,
        mime="image/png",
    )
    record(
        "another_organisation_cannot_write_to_the_issued_path",
        upload.status_code >= 400,
        f"status={upload.status_code}",
    )
    upload = storage_upload(
        env,
        token=officer["token"],
        path=instruction["path"],
        content=b"<script>alert(1)</script>",
        mime="text/html",
    )
    record(
        "a_non_image_is_refused_by_the_bucket",
        upload.status_code >= 400,
        f"status={upload.status_code}",
    )
    tampered = photo + b"\x00" * 64
    upload = storage_upload(
        env,
        token=officer["token"],
        path=instruction["path"],
        content=tampered,
        mime="image/png",
    )
    completed = api(
        "POST",
        f"/v1/incidents/{incident['id']}/attachments/{instruction['attachment_id']}/complete",
        token=officer["token"],
        body={"sha256": hashlib.sha256(photo).hexdigest(), "size_bytes": len(photo)},
    )
    verdict = (
        completed.json().get("verification", {}).get("status")
        if completed.status_code == 200
        else None
    )
    record(
        "bytes_that_differ_from_the_declaration_are_not_evidence",
        upload.status_code < 300 and verdict == "rejected",
        f"upload={upload.status_code} verification={verdict}",
    )
    response = api(
        "POST",
        "/v1/incidents",
        token=officer["token"],
        body={
            "type": "flooding",
            "captured_at": datetime.now(UTC).isoformat(),
            "location": {"latitude": SHILLONG[0], "longitude": SHILLONG[1]},
            "attachments": [
                {
                    "local_id": "big",
                    "mime_type": "image/jpeg",
                    "bytes": 5 * 1024 * 1024 + 1,
                    "sha256": "a" * 64,
                }
            ],
        },
    )
    record(
        "an_oversized_photo_is_refused_before_upload",
        response.status_code == 422,
        f"status={response.status_code}",
    )

    # A clean report the dispatcher decides, so the audit trail has a decision.
    clean = file_report(officer, test_png(38), seed="b")
    clean_instruction = clean["upload_instructions"][0]
    storage_upload(
        env,
        token=officer["token"],
        path=clean_instruction["path"],
        content=test_png(38),
        mime="image/png",
    )
    api(
        "POST",
        f"/v1/incidents/{clean['incident']['id']}/attachments/{clean_instruction['attachment_id']}/complete",
        token=officer["token"],
        body={
            "sha256": hashlib.sha256(test_png(38)).hexdigest(),
            "size_bytes": len(test_png(38)),
        },
    )
    decided = api(
        "POST",
        f"/v1/incidents/{clean['incident']['id']}/review",
        token=dispatcher["token"],
        body={
            "decision": "reject",
            "reason": "Security check: synthetic report",
            "expected_version": clean["incident"]["version"],
        },
    )
    record(
        "the_dispatcher_can_decide_in_their_own_district",
        decided.status_code == 200,
        f"status={decided.status_code} code={code(decided)}",
    )

    # --- telemetry: the credential names one trip and one device ---------------
    ended_trip = seed_trip(env, dispatcher, driver, "ended")
    running_trip = seed_trip(env, dispatcher, driver, "running")
    device = api(
        "POST",
        "/v1/devices",
        token=driver["token"],
        body={
            "device_public_id": f"test-{uuid.uuid4().hex}",
            "platform": "android",
            "display_label": "Security check handset",
        },
    )
    device.raise_for_status()
    device_public_id = api("GET", "/v1/devices", token=driver["token"]).json()[
        "devices"
    ][0]["device_public_id"]
    ended_grant = track(driver, ended_trip, device_public_id)
    track(driver, running_trip, device_public_id)

    with psycopg.connect(database_url) as connection:
        device_id = connection.execute(
            "select id::text from public.device_registrations where device_public_id = %s",
            (device_public_id,),
        ).fetchone()[0]
    wrong_trip = api(
        "POST",
        "/v1/telemetry/batches",
        grant=ended_grant,
        body={
            "trip_id": running_trip,
            "device_id": device_id,
            "points": [
                {
                    "client_point_id": "x1",
                    "idempotency_key": str(uuid.uuid4()),
                    "captured_at": datetime.now(UTC).isoformat(),
                    "latitude": SHILLONG[0],
                    "longitude": SHILLONG[1],
                    "accuracy_m": "8",
                }
            ],
        },
    )
    record(
        "a_trip_credential_cannot_post_for_another_trip",
        wrong_trip.status_code in (401, 403, 409, 422),
        f"status={wrong_trip.status_code} code={code(wrong_trip)}",
    )
    # A valid batch, so the only thing wrong with it is the credential.
    session_as_grant = api(
        "POST",
        "/v1/telemetry/batches",
        grant=driver["token"],
        body={
            "trip_id": running_trip,
            "device_id": device_id,
            "points": [
                {
                    "client_point_id": "s1",
                    "idempotency_key": str(uuid.uuid4()),
                    "captured_at": datetime.now(UTC).isoformat(),
                    "latitude": SHILLONG[0],
                    "longitude": SHILLONG[1],
                    "accuracy_m": "8",
                }
            ],
        },
    )
    record(
        "a_sign_in_token_is_not_a_tracking_credential",
        session_as_grant.status_code in (401, 403)
        and code(session_as_grant) != "validation_error",
        f"status={session_as_grant.status_code} code={code(session_as_grant)}",
    )

    # --- transport ------------------------------------------------------------
    health = httpx.get(f"{API}/health", timeout=30.0)
    scoped = api("GET", "/v1/me", token=officer["token"])
    wanted = {
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "x-frame-options": "DENY",
    }
    missing = [
        f"{name} on {label}"
        for label, response in (("/health", health), ("/v1/me", scoped))
        for name, value in wanted.items()
        if response.headers.get(name) != value
    ]
    if "frame-ancestors 'none'" not in scoped.headers.get(
        "content-security-policy", ""
    ):
        missing.append("content-security-policy on /v1/me")
    if scoped.headers.get("cache-control") != "no-store":
        missing.append("cache-control no-store on /v1/me")
    if not scoped.headers.get("x-request-id"):
        missing.append("x-request-id on /v1/me")
    record(
        "every_response_carries_the_security_headers",
        not missing,
        "missing: " + (", ".join(missing) or "none"),
    )

    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    foreign = httpx.options(
        f"{API}/v1/me",
        headers={"Origin": "https://attacker.example", **preflight},
        timeout=30.0,
    )
    ours = httpx.options(
        f"{API}/v1/me", headers={"Origin": ALLOWED_ORIGIN, **preflight}, timeout=30.0
    )
    record(
        "cors_answers_only_the_configured_origins",
        "access-control-allow-origin" not in foreign.headers
        and ours.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
        and ours.headers.get("access-control-allow-credentials") is None,
        f"foreign={foreign.status_code}/{foreign.headers.get('access-control-allow-origin')} "
        f"ours={ours.status_code}/{ours.headers.get('access-control-allow-origin')}",
    )

    oversized = api(
        "POST",
        "/v1/incidents",
        token=officer["token"],
        content=json.dumps({"note": "x" * 1_200_000}).encode(),
        headers={"Content-Type": "application/json"},
    )
    record(
        "an_oversized_request_body_is_refused",
        oversized.status_code == 413 and code(oversized) == "payload_too_large",
        f"status={oversized.status_code} code={code(oversized)}",
    )

    statuses = []
    for _ in range(11):
        response = api(
            "POST",
            "/v1/devices",
            token=driver["token"],
            body={
                "device_public_id": f"test-burst-{uuid.uuid4().hex}",
                "platform": "android",
                "display_label": "burst",
            },
        )
        statuses.append(response.status_code)
    record(
        "a_burst_is_throttled_with_a_retry_time",
        statuses[-1] == 429
        and int(response.headers.get("retry-after", "0")) >= 1
        and code(response) == "rate_limited",
        f"statuses={statuses} retry_after={response.headers.get('retry-after')}",
    )

    # --- audit trail ----------------------------------------------------------
    refused = api("GET", "/v1/audit-events", token=dispatcher["token"])
    record(
        "the_audit_trail_is_refused_to_a_dispatcher",
        refused.status_code == 403,
        f"status={refused.status_code} code={code(refused)}",
    )
    exported = api("GET", "/v1/audit-events?limit=200", token=admin["token"])
    events = exported.json().get("events", []) if exported.status_code == 200 else []
    record(
        "an_administrator_exports_the_append_only_trail",
        exported.status_code == 200
        and exported.json().get("append_only") is True
        and len(events) > 0,
        f"status={exported.status_code} events={len(events)}",
    )
    as_csv = api("GET", "/v1/audit-events?format=csv&limit=200", token=admin["token"])
    cells = (
        [cell for row in csv.reader(io.StringIO(as_csv.text)) for cell in row]
        if as_csv.status_code == 200
        else []
    )
    dangerous = [cell for cell in cells if cell[:1] in ("=", "+", "-", "@")]
    record(
        "the_csv_export_holds_no_spreadsheet_formula",
        as_csv.status_code == 200
        and as_csv.headers.get("content-type", "").startswith("text/csv")
        and not dangerous,
        f"status={as_csv.status_code} rows={len(as_csv.text.splitlines())} formula_cells={len(dangerous)}",
    )

    # --- retention ------------------------------------------------------------
    api(
        "POST", f"/v1/trips/{ended_trip}/failed", token=driver["token"], body={}
    ).raise_for_status()
    with psycopg.connect(database_url) as connection:
        # Move the whole trip a month back: it may not end before it started.
        connection.execute(
            """
            update public.trips
            set started_at = now() - interval '31 days 1 hour', ended_at = now() - interval '31 days'
            where id = %s::uuid
            """,
            (ended_trip,),
        )
        audit_before = connection.execute(
            "select count(*) from public.audit_events"
        ).fetchone()[0]
        connection.commit()
    before_ended, before_running = (
        points_for(database_url, ended_trip),
        points_for(database_url, running_trip),
    )

    api_python = REPOSITORY_ROOT / "services" / "api" / ".venv" / "bin" / "python"
    retention_env = {**os.environ, "DATABASE_URL": database_url}
    dry = subprocess.run(
        [str(api_python), "scripts/tools/apply_retention.py", "--dry-run"],
        cwd=REPOSITORY_ROOT,
        env=retention_env,
        capture_output=True,
        text=True,
        check=False,
    )
    after_dry = points_for(database_url, ended_trip)
    real = subprocess.run(
        [str(api_python), "scripts/tools/apply_retention.py"],
        cwd=REPOSITORY_ROOT,
        env=retention_env,
        capture_output=True,
        text=True,
        check=False,
    )
    after_ended, after_running = (
        points_for(database_url, ended_trip),
        points_for(database_url, running_trip),
    )
    with psycopg.connect(database_url) as connection:
        audit_after = connection.execute(
            "select count(*) from public.audit_events"
        ).fetchone()[0]
    record(
        "a_dry_run_counts_and_deletes_nothing",
        dry.returncode == 0 and after_dry == before_ended and before_ended[0] > 0,
        f"exit={dry.returncode} ended_trip_points={before_ended}→{after_dry}",
    )
    record(
        "positions_go_30_days_after_a_trip_ends_and_a_running_trip_keeps_its_own",
        real.returncode == 0
        and after_ended == (0, 0)
        and after_running == before_running
        and before_running[0] > 0,
        f"exit={real.returncode} ended={before_ended}→{after_ended} running={before_running}→{after_running}",
    )
    record(
        "retention_never_touches_the_audit_trail",
        audit_after >= audit_before,
        f"audit_events {audit_before}→{audit_after}",
    )

    # The exception retention needed is narrow: outside apply_retention a
    # position is never deleted, never edited, and the audit trail is never
    # touched even with retention's own transaction mark set.
    refusals: dict[str, str] = {}
    attempts = {
        "delete_a_position": "delete from public.telemetry_points where trip_id = %(trip)s::uuid",
        "edit_a_position_marked": (
            "select set_config('rasta.retention_in_progress', 'on', true); "
            "update public.telemetry_points set accuracy_m = 1 where trip_id = %(trip)s::uuid"
        ),
        "delete_audit_marked": (
            "select set_config('rasta.retention_in_progress', 'on', true); "
            "delete from public.audit_events"
        ),
    }
    for name, statement in attempts.items():
        with psycopg.connect(database_url) as connection:
            try:
                for part in statement.split("; "):
                    connection.execute(part, {"trip": running_trip})
                refusals[name] = "allowed"
            except psycopg.Error as error:
                refusals[name] = (
                    "refused"
                    if "append-only" in str(error)
                    else f"error:{type(error).__name__}"
                )
            finally:
                connection.rollback()
    record(
        "outside_retention_positions_and_audit_stay_append_only",
        all(value == "refused" for value in refusals.values()),
        ", ".join(f"{name}={value}" for name, value in refusals.items()),
    )
    with psycopg.connect(database_url) as connection:
        job = connection.execute(
            "select schedule, command, active from cron.job where jobname = 'rasta-apply-retention'"
        ).fetchone()
    record(
        "retention_runs_nightly_without_an_operator",
        job is not None
        and job[2] is True
        and "apply_retention(30, 90, false)" in job[1],
        f"job={'present' if job else 'missing'} schedule={job[0] if job else '-'}",
    )
    rpc_user = httpx.post(
        f"{env['API_URL']}/rest/v1/rpc/apply_retention",
        headers={"apikey": publishable, "Authorization": f"Bearer {admin['token']}"},
        json={"p_telemetry_days": 1, "p_push_attempt_days": 1, "p_dry_run": False},
        timeout=30.0,
    )
    rpc_service = httpx.post(
        f"{env['API_URL']}/rest/v1/rpc/apply_retention",
        headers={
            "apikey": env["SERVICE_ROLE_KEY"],
            "Authorization": f"Bearer {env['SERVICE_ROLE_KEY']}",
        },
        json={"p_telemetry_days": 1, "p_push_attempt_days": 1, "p_dry_run": False},
        timeout=30.0,
    )
    record(
        "no_api_key_can_run_the_retention_function",
        rpc_user.status_code >= 400 and rpc_service.status_code >= 400,
        f"signed_in_user={rpc_user.status_code} service_key={rpc_service.status_code}",
    )

    # --- logs and the repository ------------------------------------------------
    log = (
        api_log.read_text(encoding="utf-8", errors="replace")
        if api_log.exists()
        else ""
    )
    leaked = sum(1 for secret in secrets_used if secret and secret in log)
    request_lines = [line for line in log.splitlines() if "request_id=" in line]
    queries = [
        line for line in log.splitlines() if "?" in line and "?[redacted]" not in line
    ]
    record(
        "api_logs_carry_request_ids_and_no_credentials",
        len(request_lines) > 20 and leaked == 0 and not queries,
        f"request_lines={len(request_lines)} credentials_found={leaked} unredacted_queries={len(queries)}",
    )
    scan = subprocess.run(
        [sys.executable, "scripts/tools/scan_secrets.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    record(
        "the_repository_secret_scan_is_clean",
        scan.returncode == 0,
        scan.stdout.strip().splitlines()[-1]
        if scan.stdout.strip()
        else f"exit={scan.returncode}",
    )


def main() -> int:
    require_tool("supabase")
    api_python = REPOSITORY_ROOT / "services" / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError("API virtualenv not found under services/api/.venv.")
    for port in (API_PORT, SECOND_API_PORT):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", int(port))) == 0:
                raise RuntimeError(
                    f"Something is already listening on port {port}. Stop it first; "
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
    identities = {
        item["key"]: item
        for item in json.loads(bootstrap.stdout or "{}").get("identities", [])
    }

    services: list[ServiceProcess] = []
    started = datetime.now(UTC)
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
                cwd=REPOSITORY_ROOT / "services" / "api",
                env=service_env,
            )
        )
        services.append(
            start_service(
                "api-second",
                [
                    str(api_python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    SECOND_API_PORT,
                ],
                cwd=REPOSITORY_ROOT / "services" / "api",
                env=service_env,
            )
        )
        wait_for_url(f"{API}/health")
        wait_for_url(f"{SECOND_API}/health")
        run_checks(env, identities, services[0].log_path or Path("/nonexistent"))
    finally:
        stop_services(services)

    passed = all(check["passed"] for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "check": "check_security",
                "standard": "RASTA security acceptance list",
                "duration_seconds": round(
                    (datetime.now(UTC) - started).total_seconds(), 1
                ),
                "passed": passed,
                "checks": checks,
                "not_run": not_run,
                "note": "Ids, status codes and counts only; no credential appears in this file.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"{'ALL' if passed else 'NOT ALL'} {len(checks)} CHECKS {'PASSED' if passed else 'PASSED, see failures'} → {REPORT_PATH}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
