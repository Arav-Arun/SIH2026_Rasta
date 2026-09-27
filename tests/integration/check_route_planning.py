"""Runtime check: route planning over the real pilot network."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.lib.stack import fresh_stack_with_api  # noqa: E402

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "route_planning_check.json"
API_PORT = "8010"
API = f"http://127.0.0.1:{API_PORT}"
PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))

checks: list[dict[str, Any]] = []
timings: dict[str, Any] = {}


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")


def supabase_env() -> dict[str, str]:
    out = subprocess.check_output(
        ["npx", "supabase", "status", "-o", "json"], cwd=REPOSITORY_ROOT, text=True
    )
    return json.loads(out)


def sign_in(supabase_url: str, anon: str, email: str, password: str) -> str:
    request = urllib.request.Request(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["access_token"]


def request(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, Any, float]:
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
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = json.loads(response.read().decode() or "{}")
            return response.status, payload, time.perf_counter() - started
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read().decode() or "{}")
        return error.code, payload, time.perf_counter() - started


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


def plan(token: str, **over: Any) -> tuple[int, Any, float]:
    body = {
        "district_id": PILOT_DISTRICT_ID,
        "priority": "normal",
        "requested_alternatives": 3,
    }
    body.update(over)
    return request("POST", "/v1/route-plans", token, body, str(uuid.uuid4()))


def run() -> int:
    env = supabase_env()
    database_url = env["DB_URL"]
    dispatcher = bootstrap("district_dispatcher", PILOT_DISTRICT_ID)
    token = sign_in(
        env["API_URL"], env["ANON_KEY"], dispatcher["email"], dispatcher["password"]
    )

    # A connected origin/destination pair on the real graph, found by walking
    # two hops out so the answer is a genuine multi-segment route.
    with psycopg.connect(database_url) as connection:
        pair = connection.execute(
            """
            select a.from_node_id as origin, b.to_node_id as destination,
                   a.id::text as first_segment, b.id::text as second_segment
            from public.road_segments as a
            join public.road_segments as b
              on b.from_node_id = a.to_node_id
             and b.organization_id = a.organization_id
            where a.district_id = %s::uuid
              and a.from_node_id <> b.to_node_id
            order by a.id
            limit 1
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchone()
        segment_total = connection.execute(
            "select count(*) from public.road_segments where district_id = %s::uuid",
            (PILOT_DISTRICT_ID,),
        ).fetchone()[0]

    if pair is None:
        record("pilot_graph_present", False, "No two-hop path found in the pilot graph")
        REPORT_PATH.write_text(
            json.dumps({"checks": checks, "passed": False}, indent=2)
        )
        return 1

    origin, destination, first_segment, _ = pair
    record(
        "pilot_graph_present",
        segment_total > 1000,
        f"segments={segment_total} origin={origin} destination={destination}",
    )

    # 1. A plain plan over the real graph.
    status, created, elapsed = plan(
        token, origin_node_id=origin, destination_node_id=destination
    )
    timings["first_plan_seconds"] = round(elapsed, 3)
    plan_body = created.get("plan", {})
    alternatives = plan_body.get("alternatives", [])
    record(
        "plan_created_over_the_real_graph",
        status == 201
        and plan_body.get("result_status") == "feasible"
        and len(alternatives) >= 1
        and plan_body.get("graph_edge_count", 0) > 1000,
        f"status={status} result={plan_body.get('result_status')} "
        f"alternatives={len(alternatives)} edges={plan_body.get('graph_edge_count')} "
        f"seconds={timings['first_plan_seconds']}",
    )

    # 2. The snapshot is recorded, not implied.
    record(
        "plan_records_the_snapshot_it_was_costed_against",
        bool(plan_body.get("network_version"))
        and bool(plan_body.get("risk_snapshot_version"))
        and bool(plan_body.get("cost_policy_version")),
        f"network={plan_body.get('network_version')} "
        f"risk={plan_body.get('risk_snapshot_version')} "
        f"policy={plan_body.get('cost_policy_version')}",
    )

    # 3. No unqualified safety claim, and unobserved road is labelled.
    coverage_codes = {item["code"] for item in plan_body.get("coverage_warnings", [])}
    record(
        "unobserved_network_is_declared_and_nothing_is_called_safe",
        plan_body.get("safe_route_claim") is False
        and "unobserved_network" in coverage_codes,
        f"safe_route_claim={plan_body.get('safe_route_claim')} coverage={sorted(coverage_codes)}",
    )

    # 4. Determinism: the same request twice is the same answer.
    _, again, elapsed_again = plan(
        token, origin_node_id=origin, destination_node_id=destination
    )
    timings["second_plan_seconds"] = round(elapsed_again, 3)
    first_segments = [item["segment_ids"] for item in alternatives]
    second_segments = [
        item["segment_ids"] for item in again.get("plan", {}).get("alternatives", [])
    ]
    record(
        "the_same_request_returns_the_same_route",
        first_segments == second_segments and first_segments != [],
        f"alternatives={len(first_segments)} identical={first_segments == second_segments} "
        f"seconds={timings['second_plan_seconds']}",
    )

    # 5. Replay on the same idempotency key.
    key = str(uuid.uuid4())
    body = {
        "district_id": PILOT_DISTRICT_ID,
        "origin_node_id": origin,
        "destination_node_id": destination,
        "priority": "normal",
        "requested_alternatives": 3,
    }
    status_a, first, _ = request("POST", "/v1/route-plans", token, body, key)
    status_b, replay, _ = request("POST", "/v1/route-plans", token, body, key)
    record(
        "plan_creation_replays_on_the_same_key",
        status_a == 201
        and status_b == 201
        and replay.get("replayed") is True
        and replay["plan"]["id"] == first["plan"]["id"],
        f"same_plan={replay.get('plan', {}).get('id') == first.get('plan', {}).get('id')} "
        f"replayed={replay.get('replayed')}",
    )

    # 6. An unknown node is refused rather than snapped to something nearby.
    status, refused, _ = plan(
        token, origin_node_id=origin, destination_node_id="osm-node-does-not-exist"
    )
    record(
        "an_unknown_graph_node_is_refused",
        status == 422 and refused["error"]["code"] == "unknown_graph_node",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    status, refused, _ = plan(token, origin_node_id=origin, destination_node_id=origin)
    record(
        "origin_and_destination_must_differ",
        status == 422 and refused["error"]["code"] == "origin_equals_destination",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    # An unobserved segment is reported as unknown, never as open.
    warned = [
        warning for item in alternatives for warning in item["constraint_warnings"]
    ]
    codes = {warning["code"] for warning in warned}
    record(
        "an_unknown_limit_is_a_warning_not_a_pass",
        bool(alternatives) and "unobserved_segment_state" in codes,
        f"alternatives={len(alternatives)} warning_codes={sorted(codes)}",
    )

    # 7. S2: a confirmed closure is excluded from every alternative.
    # Close a segment the fastest option uses but the others do not, so a
    # correct answer is a *re-plan*. Closing a segment every option needs would
    # produce a no-route, which passes "did not use the closed road" without
    # proving the planner can route around anything.
    other_segments = {
        segment for item in alternatives[1:] for segment in item["segment_ids"]
    }
    unique_to_primary = [
        segment
        for segment in (alternatives[0]["segment_ids"] if alternatives else [])
        if segment not in other_segments
    ]
    closed_segment = unique_to_primary[0] if unique_to_primary else first_segment
    with psycopg.connect(database_url) as connection:
        org = connection.execute(
            "select organization_id::text from public.profiles where id = %s::uuid",
            (dispatcher["profile_id"],),
        ).fetchone()[0]
        connection.execute(
            """
            insert into public.segment_current_state
              (organization_id, segment_id, passability, risk_level, as_of, network_version)
            values (%s::uuid, %s::uuid, 'closed', 'unknown', now(), 'closure-test-1')
            on conflict (organization_id, segment_id)
            do update set passability = 'closed', as_of = now(),
                          network_version = 'closure-test-1'
            """,
            (org, closed_segment),
        )
        connection.commit()

    status, after_closure, _ = plan(
        token, origin_node_id=origin, destination_node_id=destination
    )
    closure_body = after_closure.get("plan", {})
    closure_alternatives = closure_body.get("alternatives", [])
    uses_closed = any(
        closed_segment in item["segment_ids"] for item in closure_alternatives
    )
    excluded = closure_body.get("exclusions", {}).get("segment_closed", [])
    record(
        "S2_a_confirmed_closure_forces_a_replan_not_a_detour_through_it",
        status == 201
        and closure_body.get("result_status") == "feasible"
        and not uses_closed
        and any(item["segment_id"] == closed_segment for item in excluded),
        f"status={status} result={closure_body.get('result_status')} "
        f"uses_closed={uses_closed} excluded={len(excluded)} "
        f"alternatives={len(closure_alternatives)}",
    )

    # 8. S6: a vehicle heavier than a bridge limit cannot use it.
    with psycopg.connect(database_url) as connection:
        bridge = connection.execute(
            """
            select b.id::text as bridge_id, b.segment_id::text as segment_id
            from public.bridges as b
            join public.road_segments as rs on rs.id = b.segment_id
            where rs.district_id = %s::uuid
            order by b.id
            limit 1
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchone()
        if bridge:
            connection.execute(
                "update public.bridges set max_weight_t = 5.0 where id = %s::uuid",
                (bridge[0],),
            )
            connection.commit()

    status, heavy, _ = plan(
        token,
        origin_node_id=origin,
        destination_node_id=destination,
        gross_weight_t=12.0,
    )
    heavy_body = heavy.get("plan", {})
    over_weight = heavy_body.get("exclusions", {}).get("vehicle_exceeds_max_weight", [])
    bridge_segment = bridge[1] if bridge else None
    uses_bridge = bridge_segment is not None and any(
        bridge_segment in item["segment_ids"]
        for item in heavy_body.get("alternatives", [])
    )
    record(
        "S6_a_bridge_below_the_vehicle_weight_is_excluded_with_the_figures",
        status == 201
        and not uses_bridge
        and any(item["segment_id"] == bridge_segment for item in over_weight)
        if bridge_segment
        else False,
        f"status={status} excluded={len(over_weight)} uses_bridge={uses_bridge} "
        f"bridge_segment={bridge_segment}",
    )

    # 10. A no-route explains itself.
    with psycopg.connect(database_url) as connection:
        neighbours = connection.execute(
            """
            select id::text from public.road_segments
            where district_id = %s::uuid and from_node_id = %s
            """,
            (PILOT_DISTRICT_ID, origin),
        ).fetchall()
        for row in neighbours:
            connection.execute(
                """
                insert into public.segment_current_state
                  (organization_id, segment_id, passability, risk_level, as_of, network_version)
                values (%s::uuid, %s::uuid, 'closed', 'unknown', now(), 'closure-test-2')
                on conflict (organization_id, segment_id)
                do update set passability = 'closed', as_of = now(),
                              network_version = 'closure-test-2'
                """,
                (org, row[0]),
            )
        connection.commit()

    status, no_route, _ = plan(
        token, origin_node_id=origin, destination_node_id=destination
    )
    no_route_body = no_route.get("plan", {})
    record(
        "a_no_route_names_the_cause_and_a_next_step",
        status == 201
        and no_route_body.get("result_status") == "no_route"
        and no_route_body.get("alternatives") == []
        and bool(no_route_body.get("reason_codes"))
        and bool(no_route_body.get("actions")),
        f"status={status} result={no_route_body.get('result_status')} "
        f"reasons={no_route_body.get('reason_codes')} actions={len(no_route_body.get('actions', []))}",
    )

    status, refused, _ = request(
        "POST",
        f"/v1/route-plans/{no_route_body['id']}/approve",
        token,
        {
            "alternative_id": "alt-none",
            "expected_network_version": no_route_body["network_version"],
        },
        str(uuid.uuid4()),
    )
    record(
        "a_no_route_plan_cannot_be_approved",
        status == 422 and refused["error"]["code"] == "route_plan_has_no_route",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    # 11. Approval: reopen the road, plan again, approve.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            update public.segment_current_state
            set passability = 'open', as_of = now(), network_version = 'reopen-test'
            where organization_id = %s::uuid and network_version = 'closure-test-2'
            """,
            (org,),
        )
        connection.commit()

    status, approvable, elapsed = plan(
        token, origin_node_id=origin, destination_node_id=destination
    )
    timings["plan_after_state_changes_seconds"] = round(elapsed, 3)
    approvable_body = approvable.get("plan", {})
    if approvable_body.get("result_status") != "feasible":
        record(
            "a_route_is_available_again_after_reopening",
            False,
            f"result={approvable_body.get('result_status')}",
        )
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps({"checks": checks, "passed": False}, indent=2)
        )
        return 1

    chosen = approvable_body["alternatives"][0]["id"]

    status, stale, _ = request(
        "POST",
        f"/v1/route-plans/{approvable_body['id']}/approve",
        token,
        {
            "alternative_id": chosen,
            "expected_network_version": "some-other-version",
        },
        str(uuid.uuid4()),
    )
    record(
        "approval_refuses_a_network_version_the_caller_did_not_see",
        status == 409 and stale["error"]["code"] == "route_plan_stale",
        f"status={status} code={stale.get('error', {}).get('code')}",
    )

    status, approved, _ = request(
        "POST",
        f"/v1/route-plans/{approvable_body['id']}/approve",
        token,
        {
            "alternative_id": chosen,
            "expected_network_version": approvable_body["network_version"],
        },
        str(uuid.uuid4()),
    )
    record(
        "a_current_plan_is_approved_and_audited",
        status == 200
        and approved["plan"]["status"] == "approved"
        and approved["plan"]["chosen_alternative_id"] is not None
        and len(approved.get("audit_event_ids", [])) == 1,
        f"status={status} plan_status={approved.get('plan', {}).get('status')} "
        f"audit_events={len(approved.get('audit_event_ids', []))}",
    )

    status, twice, _ = request(
        "POST",
        f"/v1/route-plans/{approvable_body['id']}/approve",
        token,
        {
            "alternative_id": chosen,
            "expected_network_version": approvable_body["network_version"],
        },
        str(uuid.uuid4()),
    )
    record(
        "an_approved_plan_is_not_approved_twice",
        status == 409 and twice["error"]["code"] == "invalid_transition",
        f"status={status} code={twice.get('error', {}).get('code')}",
    )

    # 12. The central rule: a plan whose network moved underneath it is refused
    # and marked invalidated, not silently approved.
    status, fresh, _ = plan(
        token, origin_node_id=origin, destination_node_id=destination
    )
    fresh_body = fresh["plan"]
    fresh_alternative = fresh_body["alternatives"][0]["id"]
    stale_segment = fresh_body["alternatives"][0]["segment_ids"][0]

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            insert into public.segment_current_state
              (organization_id, segment_id, passability, risk_level, as_of, network_version)
            values (%s::uuid, %s::uuid, 'closed', 'unknown', now(), 'moved-after-planning')
            on conflict (organization_id, segment_id)
            do update set passability = 'closed', as_of = now(),
                          network_version = 'moved-after-planning'
            """,
            (org, stale_segment),
        )
        connection.commit()

    status, refused, _ = request(
        "POST",
        f"/v1/route-plans/{fresh_body['id']}/approve",
        token,
        {
            "alternative_id": fresh_alternative,
            "expected_network_version": fresh_body["network_version"],
        },
        str(uuid.uuid4()),
    )
    record(
        "a_plan_whose_network_moved_underneath_it_cannot_be_approved",
        status == 409 and refused["error"]["code"] == "route_plan_stale",
        f"status={status} code={refused.get('error', {}).get('code')}",
    )

    status, reread, _ = request("GET", f"/v1/route-plans/{fresh_body['id']}", token)
    record(
        "a_refused_plan_is_marked_invalidated_rather_than_left_proposed",
        status == 200 and reread["status"] == "invalidated",
        f"status={status} plan_status={reread.get('status')}",
    )

    # 13. Performance on the pilot graph, reported rather than asserted tightly.
    samples = []
    for _ in range(5):
        _, _, seconds = plan(
            token, origin_node_id=origin, destination_node_id=destination
        )
        samples.append(seconds)
    timings["plan_seconds_samples"] = [round(value, 3) for value in samples]
    timings["plan_seconds_median"] = round(sorted(samples)[len(samples) // 2], 3)
    timings["segment_count"] = segment_total
    record(
        "planning_the_pilot_graph_stays_under_five_seconds",
        timings["plan_seconds_median"] < 5.0,
        f"median={timings['plan_seconds_median']}s over {segment_total} segments "
        f"samples={timings['plan_seconds_samples']}",
    )

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
    print(f"FAILURES → {REPORT_PATH}", file=sys.stderr)
    return 1


def main() -> int:
    # A reset database and this checkout's API, like every other runtime gate:
    # never whatever an earlier session left running.
    with fresh_stack_with_api(API_PORT):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
