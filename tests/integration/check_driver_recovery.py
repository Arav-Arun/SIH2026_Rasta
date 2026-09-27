"""Runtime check: what the driver app can actually know."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
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
from tests.integration.check_telemetry import (  # noqa: E402
    API,
    API_PORT,
    PILOT_DISTRICT_ID,
    call,
    point,
    seed_trip,
    sign_in,
)

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "driver_recovery_check.json"

checks: list[dict[str, Any]] = []


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")


def node_pair(database_url: str) -> tuple[str, str]:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select a.from_node_id, b.to_node_id
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
    if row is None:
        raise RuntimeError("No two-hop path exists in the pilot graph.")
    return row[0], row[1]


def plan_and_approve(
    token: str, origin: str, destination: str, alternative_index: int = 0
) -> dict[str, Any]:
    status, created, _ = call(
        "POST",
        "/v1/route-plans",
        token=token,
        idempotency_key=str(uuid.uuid4()),
        body={
            "district_id": PILOT_DISTRICT_ID,
            "priority": "normal",
            "requested_alternatives": 3,
            "origin_node_id": origin,
            "destination_node_id": destination,
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not plan a route: {status} {created}")
    plan = created["plan"]
    alternatives = plan["alternatives"]
    chosen = alternatives[min(alternative_index, len(alternatives) - 1)]
    status, approved, _ = call(
        "POST",
        f"/v1/route-plans/{plan['id']}/approve",
        token=token,
        idempotency_key=str(uuid.uuid4()),
        body={
            "alternative_id": chosen["id"],
            "expected_network_version": plan["network_version"],
        },
    )
    if status != 200:
        raise RuntimeError(f"Could not approve the route: {status} {approved}")
    return {"plan": plan, "chosen": chosen, "alternative_count": len(alternatives)}


def run_checks(*, env: dict[str, str], identities: dict[str, Any]) -> None:
    database_url = env["DB_URL"]
    dispatcher = sign_in(
        env["API_URL"],
        env["ANON_KEY"],
        identities["pilot-dispatcher"]["email"],
        identities["pilot-dispatcher"]["password"],
    )
    driver_identity = identities["driver"]
    driver = sign_in(
        env["API_URL"],
        env["ANON_KEY"],
        driver_identity["email"],
        driver_identity["password"],
    )

    seeded = seed_trip(
        dispatcher=dispatcher,
        driver_profile_id=driver_identity["profile_id"],
        database_url=database_url,
        reference=f"TEST-{uuid.uuid4().hex[:8]}",
    )
    trip_id = seeded["trip_id"]
    origin, destination = node_pair(database_url)

    # 1. Before anything is approved, the driver's own read says so plainly.
    status, mine, _ = call("GET", "/v1/trips", token=driver)
    trip_row = next((t for t in mine.get("trips", []) if t["id"] == trip_id), {})
    record(
        "a_driver_sees_that_no_route_is_approved_rather_than_an_empty_field",
        status == 200
        and mine.get("scope") == "assigned_to_me"
        and trip_row.get("route_plan_id") is None
        and trip_row.get("route_plan_status") is None,
        f"scope={mine.get('scope')} route_plan_id={trip_row.get('route_plan_id')}",
    )

    # 2. An approved route reaches the driver with everything the screen states.
    first = plan_and_approve(dispatcher, origin, destination, 0)
    status, bound, _ = call(
        "POST",
        f"/v1/trips/{trip_id}/route-plan",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={"route_plan_id": first["plan"]["id"]},
    )
    status_read, mine, _ = call("GET", "/v1/trips", token=driver)
    trip_row = next((t for t in mine.get("trips", []) if t["id"] == trip_id), {})
    record(
        "an_approved_route_reaches_the_driver_on_their_own_trip",
        status == 200
        and status_read == 200
        and trip_row.get("route_plan_id") == first["plan"]["id"]
        and trip_row.get("route_plan_status") == "approved",
        f"bind={status} route_plan_status={trip_row.get('route_plan_status')}",
    )

    status, plan_detail, _ = call(
        "GET", f"/v1/route-plans/{first['plan']['id']}", token=driver
    )
    chosen_id = plan_detail.get("chosen_alternative_id")
    chosen = next(
        (a for a in plan_detail.get("alternatives", []) if a["id"] == chosen_id), {}
    )
    geometry = chosen.get("geometry") or {}
    record(
        "the_driver_can_read_the_plan_and_the_chosen_route_is_named_on_it",
        status == 200
        and chosen_id is not None
        and chosen.get("id") == chosen_id
        and plan_detail.get("safe_route_claim") is False,
        f"status={status} chosen={bool(chosen)} "
        f"safe_route_claim={plan_detail.get('safe_route_claim')}",
    )
    record(
        "the_chosen_route_carries_geometry_a_phone_can_draw",
        geometry.get("type") in ("LineString", "MultiLineString")
        and len(geometry.get("coordinates") or []) >= 2,
        f"type={geometry.get('type')} parts={len(geometry.get('coordinates') or [])}",
    )
    record(
        "the_route_states_what_is_unverified_rather_than_leaving_it_blank",
        isinstance(chosen.get("constraint_warnings"), list)
        and (chosen.get("risk_summary") or {}).get("segments_without_a_risk_score")
        is not None
        and chosen.get("eta_range_seconds") is not None,
        f"warnings={len(chosen.get('constraint_warnings') or [])} "
        f"no_risk_score={(chosen.get('risk_summary') or {}).get('segments_without_a_risk_score')} "
        f"eta_range={chosen.get('eta_range_seconds')}",
    )

    # 3. A revision is visible as a change, which is what the app makes the
    #    driver acknowledge. If the read looked identical, no UI could catch it.
    if first["alternative_count"] < 2:
        record(
            "a_revised_route_is_visible_to_the_driver_as_a_different_route",
            False,
            "The pilot graph returned only one alternative, so no revision could be made.",
        )
    else:
        second = plan_and_approve(dispatcher, origin, destination, 1)
        call(
            "POST",
            f"/v1/trips/{trip_id}/route-plan",
            token=dispatcher,
            idempotency_key=str(uuid.uuid4()),
            body={"route_plan_id": second["plan"]["id"]},
        )
        status, revised, _ = call("GET", "/v1/trips", token=driver)
        revised_row = next(
            (t for t in revised.get("trips", []) if t["id"] == trip_id), {}
        )
        record(
            "a_revised_route_is_visible_to_the_driver_as_a_different_route",
            status == 200
            and revised_row.get("route_plan_id") == second["plan"]["id"]
            and revised_row.get("route_plan_id") != first["plan"]["id"],
            f"was={first['plan']['id'][:8]} now={revised_row.get('route_plan_id', '')[:8]}",
        )
        current_plan_id = second["plan"]["id"]

    # 4. The queue behaviour the app relies on, over the real endpoint: a full
    #    batch is answered point by point, and a reconnect after a gap files the
    #    backlog without the server re-ordering it.
    for action in ("awaiting_driver", "active"):
        call(
            "POST",
            f"/v1/trips/{trip_id}/{action}",
            token=dispatcher if action == "awaiting_driver" else driver,
            idempotency_key=str(uuid.uuid4()),
            body={},
        )
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "update public.trips set started_at = now() - interval '2 hours' where id = %s::uuid",
            (trip_id,),
        )
        connection.commit()

    public_id = f"test-device-{uuid.uuid4().hex}"
    call(
        "POST",
        "/v1/devices",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={
            "device_public_id": public_id,
            "platform": "android",
            "display_label": "Driver recovery check",
        },
    )
    status, grant_payload, _ = call(
        "POST",
        f"/v1/trips/{trip_id}/tracking-grants",
        token=driver,
        idempotency_key=str(uuid.uuid4()),
        body={"device_public_id": public_id},
    )
    grant, device_id = grant_payload.get("token"), grant_payload.get("device_id")
    policy = grant_payload.get("policy", {})
    record(
        "the_server_states_the_sampling_policy_instead_of_the_app_hardcoding_it",
        status == 201
        and policy.get("max_batch_points") == 20
        and policy.get("min_movement_m") == 25
        and policy.get("heartbeat_seconds") == 60
        and policy.get("stale_after_seconds") == 300,
        f"policy={policy}",
    )

    # A full batch, as the uploader sends it: 20 fixes a minute apart.
    base = datetime.now(UTC) - timedelta(minutes=90)
    full = [point(index=i, captured_at=base + timedelta(minutes=i)) for i in range(20)]
    status, answered, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={"trip_id": trip_id, "device_id": device_id, "points": full},
    )
    ids_sent = {p["client_point_id"] for p in full}
    ids_answered = {r["client_point_id"] for r in answered.get("results", [])}
    record(
        "every_fix_in_a_full_batch_comes_back_with_an_answer",
        status == 200 and ids_answered == ids_sent and len(ids_sent) == 20,
        f"sent={len(ids_sent)} answered={len(ids_answered)} "
        f"accepted={answered.get('accepted')} rejected={answered.get('rejected')}",
    )

    # A reconnect: the backlog that built up during the gap goes in next, older than
    # what has already been filed.
    backlog = [
        point(index=i, captured_at=base - timedelta(minutes=20 - i)) for i in range(5)
    ]
    for suffix, entry in enumerate(backlog[:5]):
        entry["client_point_id"] = f"backlog{suffix}"
    status, caught_up, _ = call(
        "POST",
        "/v1/telemetry/batches",
        grant=grant,
        idempotency_key=str(uuid.uuid4()),
        body={"trip_id": trip_id, "device_id": device_id, "points": backlog},
    )
    _, history, _ = call("GET", f"/v1/trips/{trip_id}/telemetry", token=driver)
    captured = [p["captured_at"] for p in history.get("points", [])]
    record(
        "a_backlog_filed_after_a_gap_lands_in_time_order_not_arrival_order",
        status == 200
        and caught_up.get("accepted") == 5
        and history.get("total_points") == 25
        and captured == sorted(captured),
        f"accepted={caught_up.get('accepted')} total={history.get('total_points')} "
        f"ordered={captured == sorted(captured)}",
    )

    # 5. The driver's own read of where they are, which is what the panel shows.
    location = caught_up.get("current_location") or {}
    record(
        "the_app_is_told_where_the_server_thinks_the_vehicle_is_and_how_old_that_is",
        location.get("as_of") is not None
        and location.get("is_stale") is not None
        and caught_up.get("stale_after_seconds") == 300,
        f"as_of={location.get('as_of')} is_stale={location.get('is_stale')}",
    )
    # 6. A withdrawn route is visible as withdrawn, so the screen can refuse it.
    #    Last, because invalidating the route also stops the trip restarting.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "update public.route_plans set status = 'invalidated' where id = %s::uuid",
            (current_plan_id,),
        )
        connection.commit()
    status, after, _ = call("GET", "/v1/trips", token=driver)
    after_row = next((t for t in after.get("trips", []) if t["id"] == trip_id), {})
    record(
        "a_route_the_control_room_pulled_is_visible_as_pulled",
        status == 200 and after_row.get("route_plan_status") == "invalidated",
        f"route_plan_status={after_row.get('route_plan_status')}",
    )


def main() -> int:
    require_tool("supabase")
    require_tool("npx")

    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError("API virtualenv not found under api/.venv.")

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
    identities = {
        item["key"]: item
        for item in json.loads(bootstrap.stdout or "{}").get("identities", [])
    }

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
                "not_claimed": [
                    "No physical Android device took part in this run.",
                    "Locked-screen and background tracking need the native service and a real device.",
                ],
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
