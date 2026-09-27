#!/usr/bin/env python3
"""Push notification check: push is a courtesy, and a failed one changes nothing."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

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
from tests.integration.check_operations import (  # noqa: E402
    API,
    API_PORT,
    PILOT_DISTRICT_ID,
    approve_plan_over,
    call,
    code_of,
    confirm_closure,
    inbox,
    of_type,
    seed_running_trip,
    sign_in,
)

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "push_check.json"
checks: list[dict[str, Any]] = []
evidence: dict[str, Any] = {}


def record(name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)


def vapid_keys() -> tuple[str, str, Any]:
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415

    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return (
        pem,
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        private.public_key(),
    )


class StubPushService:
    """Records every push and answers with the status set for its path."""

    def __init__(self, statuses: dict[str, int]) -> None:
        self.statuses = statuses
        self.received: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                stub.received.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body_bytes": len(self.rfile.read(length)),
                        "at": time.time(),
                    }
                )
                self.send_response(stub.statuses.get(self.path, 404))
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def to(self, path: str) -> list[dict[str, Any]]:
        return [item for item in self.received if item["path"] == path]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def verify_vapid(
    request: dict[str, Any], public: str, public_key: Any, audience: str
) -> bool:
    import jwt  # noqa: PLC0415

    scheme, _, rest = request["headers"].get("authorization", "").partition(" ")
    if scheme != "vapid":
        return False
    parts = dict(part.strip().split("=", 1) for part in rest.split(","))
    if parts.get("k") != public:
        return False
    try:
        jwt.decode(parts["t"], public_key, algorithms=["ES256"], audience=audience)
    except jwt.PyJWTError:
        return False
    return True


def attempts_for(database_url: str, alert_ids: list[str]) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        return connection.execute(
            """
            select a.alert_id::text as alert_id, a.status, a.error_code, a.http_status,
                   s.profile_id::text as profile_id, s.revoked_at is not null as revoked
            from public.push_attempts as a
            left join public.push_subscriptions as s
              on s.id = a.subscription_id and s.organization_id = a.organization_id
            where a.alert_id = any(%s::uuid[])
            order by a.attempted_at
            """,
            (alert_ids,),
        ).fetchall()


def run_checks(
    *,
    env: dict[str, str],
    identities: dict[str, Any],
    stub: StubPushService,
    public: str,
    public_key: Any,
) -> None:
    database_url = env["DB_URL"]
    supabase_url, anon = env["API_URL"], env["ANON_KEY"]

    def creds(key: str) -> tuple[str, str]:
        return identities[key]["email"], identities[key]["password"]

    dispatcher = sign_in(supabase_url, anon, *creds("pilot-dispatcher"))
    officer = sign_in(supabase_url, anon, *creds("pilot-officer"))
    driver = sign_in(supabase_url, anon, *creds("driver"))

    # -- configuration: only the public key leaves the server ---------------
    status, push_status = call("GET", "/v1/push-subscriptions", token=dispatcher)
    served = json.dumps(push_status)
    record(
        "the_browser_is_given_the_public_key_and_nothing_else",
        status == 200
        and push_status.get("configured") is True
        and push_status.get("public_key") == public
        and "PRIVATE KEY" not in served,
        f"status={status} configured={push_status.get('configured')} "
        f"sender={push_status.get('sender')} public_key_matches={push_status.get('public_key') == public}",
    )

    refused = []
    for endpoint in (
        "http://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/push",
        "https://fcm.googleapis.com.attacker.test/x",
    ):
        status, body = call(
            "POST",
            "/v1/push-subscriptions",
            token=dispatcher,
            idempotency_key=str(uuid.uuid4()),
            body={"platform": "web", "endpoint": endpoint, "keys": {}},
        )
        refused.append((status, code_of(body)))
    record(
        "an_endpoint_that_is_not_a_push_service_is_refused",
        all(item == (422, "push_endpoint_not_allowed") for item in refused),
        f"responses={refused}",
    )

    registered: dict[str, dict[str, Any]] = {}
    for name, token in (
        ("dispatcher", dispatcher),
        ("driver", driver),
        ("officer", officer),
    ):
        status, body = call(
            "POST",
            "/v1/push-subscriptions",
            token=token,
            idempotency_key=str(uuid.uuid4()),
            body={
                "platform": "web",
                "endpoint": f"{stub.origin}/push/{name}",
                "keys": {
                    "p256dh": "BTestKeyNotUsedByHeadersOnlyPush",
                    "auth": "dGVzdA",
                },
                "user_agent": "Push check run",
            },
        )
        registered[name] = {"status": status, "body": body}
    record(
        "each_person_registers_and_the_endpoint_is_never_echoed_back",
        all(item["status"] == 201 for item in registered.values())
        and all(
            stub.origin not in json.dumps(item["body"]) for item in registered.values()
        ),
        f"statuses={ {k: v['status'] for k, v in registered.items()} }",
    )

    # -- a test notification -------------------------------------------------
    status, test = call(
        "POST", "/v1/push-subscriptions/test", token=dispatcher, body={}
    )
    to_dispatcher = stub.to("/push/dispatcher")
    record(
        "a_test_notification_reaches_the_push_service_signed_and_empty",
        status == 200
        and test.get("sent") == 1
        and len(to_dispatcher) == 1
        and to_dispatcher[0]["body_bytes"] == 0
        and to_dispatcher[0]["headers"].get("ttl") == "900"
        and verify_vapid(to_dispatcher[0], public, public_key, stub.origin),
        f"status={status} result={ {k: test.get(k) for k in ('attempted', 'sent', 'failed')} } "
        f"received={len(to_dispatcher)} vapid_verified="
        f"{bool(to_dispatcher) and verify_vapid(to_dispatcher[0], public, public_key, stub.origin)}",
    )

    status, officer_test = call(
        "POST", "/v1/push-subscriptions/test", token=officer, body={}
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        officer_live = connection.execute(
            """
            select count(*) as n from public.push_subscriptions
            where profile_id = %s::uuid and revoked_at is null
            """,
            (identities["pilot-officer"]["profile_id"],),
        ).fetchone()["n"]
    record(
        "a_push_service_error_is_recorded_and_the_registration_kept",
        status == 200
        and officer_test.get("failed") == 1
        and "http_500" in (officer_test.get("errors") or [])
        and officer_live == 1,
        f"result={ {k: officer_test.get(k) for k in ('sent', 'failed', 'errors')} } "
        f"officer_live_registrations={officer_live}",
    )

    # -- a real alert, raised by a confirmed closure ------------------------
    with psycopg.connect(database_url) as connection:
        origin, destination = connection.execute(
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
    call(
        "POST",
        f"/v1/trips/{trip['trip_id']}/route-plan",
        token=dispatcher,
        idempotency_key=str(uuid.uuid4()),
        body={"route_plan_id": plan["id"]},
    )
    for action, actor in (("awaiting_driver", dispatcher), ("active", driver)):
        status, moved = call(
            "POST",
            f"/v1/trips/{trip['trip_id']}/{action}",
            token=actor,
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

    before = len(stub.received)
    confirm_closure(
        officer=officer,
        dispatcher=dispatcher,
        database_url=database_url,
        segment_id=closing_segment,
    )
    dispatcher_alerts = inbox(dispatcher)
    driver_alerts = of_type(inbox(driver), "trip_route_invalidated")
    alert_ids = sorted(
        {a["id"] for a in dispatcher_alerts} | {a["id"] for a in driver_alerts}
    )
    attempts = attempts_for(database_url, alert_ids)
    evidence["closure_attempts"] = attempts
    evidence["pushes_after_closure"] = [
        {"path": item["path"], "body_bytes": item["body_bytes"]}
        for item in stub.received[before:]
    ]

    driver_profile = identities["driver"]["profile_id"]
    dispatcher_profile = identities["pilot-dispatcher"]["profile_id"]
    sent_to_dispatcher = [
        a
        for a in attempts
        if a["profile_id"] == dispatcher_profile and a["status"] == "sent"
    ]
    driver_attempts = [a for a in attempts if a["profile_id"] == driver_profile]
    closure_pushes = stub.received[before:]
    record(
        "a_confirmed_closure_is_pushed_to_the_people_it_concerns",
        len(sent_to_dispatcher) >= 1
        and len(driver_attempts) >= 1
        and any(item["path"] == "/push/driver" for item in closure_pushes)
        and all(
            verify_vapid(item, public, public_key, stub.origin)
            for item in closure_pushes
        ),
        f"alerts={len(alert_ids)} attempts={len(attempts)} "
        f"sent_to_dispatcher={len(sent_to_dispatcher)} driver_attempts={len(driver_attempts)} "
        f"pushes={[item['path'] for item in closure_pushes]}",
    )
    record(
        "a_registration_the_browser_discarded_is_revoked",
        bool(driver_attempts)
        and driver_attempts[0]["status"] == "failed"
        and driver_attempts[0]["error_code"] == "subscription_gone"
        and driver_attempts[0]["http_status"] == 410
        and driver_attempts[0]["revoked"] is True,
        f"driver_attempt={driver_attempts[0] if driver_attempts else None}",
    )
    record(
        "the_failed_push_left_the_driver_alert_in_their_inbox",
        len(driver_alerts) == 1
        and driver_alerts[0]["status"] != "acknowledged"
        and driver_alerts[0]["subject_id"] == trip["trip_id"],
        f"driver_alerts={[(a['type'], a['status']) for a in driver_alerts]}",
    )

    # Nothing more reaches a revoked registration.
    pushes_to_driver = len(stub.to("/push/driver"))
    call("POST", "/v1/push-subscriptions/test", token=driver, body={})
    record(
        "a_revoked_registration_is_not_sent_to_again",
        len(stub.to("/push/driver")) == pushes_to_driver,
        f"pushes_to_driver_before={pushes_to_driver} after={len(stub.to('/push/driver'))}",
    )

    # -- revocation ---------------------------------------------------------
    endpoint = f"{stub.origin}/push/dispatcher"
    status_revoke, _ = call(
        "POST",
        "/v1/push-subscriptions/revoke",
        token=dispatcher,
        body={"endpoint": endpoint},
    )
    status_again, again = call(
        "POST",
        "/v1/push-subscriptions/revoke",
        token=dispatcher,
        body={"endpoint": endpoint},
    )
    status_query, _ = call(
        "DELETE", f"/v1/push-subscriptions?endpoint={endpoint}", token=dispatcher
    )
    _, after = call("GET", "/v1/push-subscriptions", token=dispatcher)
    record(
        "revocation_takes_the_endpoint_in_the_body_not_the_url",
        status_revoke == 204
        and status_again == 404
        and status_query in (404, 405)
        and after.get("subscriptions") == 0,
        f"revoke={status_revoke} again={status_again}/{code_of(again)} "
        f"delete_with_query={status_query} remaining={after.get('subscriptions')}",
    )

    # The API log must not carry a push endpoint: they are bearer-equivalent.
    log = (REPOSITORY_ROOT / "artifacts" / "e2e" / "logs" / "api.log").read_text(
        encoding="utf-8", errors="replace"
    )
    record(
        "no_push_endpoint_is_written_to_the_api_log",
        "/push/dispatcher" not in log and "/push/driver" not in log,
        f"log_lines={len(log.splitlines())}",
    )


def main() -> int:
    require_tool("supabase")
    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    require_free_port(int(API_PORT), what="the API")

    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(env, api_port=API_PORT)

    pem, public, public_key = vapid_keys()
    stub = StubPushService(
        {"/push/dispatcher": 201, "/push/driver": 410, "/push/officer": 500}
    )
    # Passed to this run's API process only: nothing is written to an env file.
    service_env = {
        **os.environ.copy(),
        **runtime_env,
        "VAPID_PRIVATE_KEY": pem,
        "VAPID_PUBLIC_KEY": public,
        "VAPID_SUBJECT": "mailto:test-acceptance@example.test",
        "PUSH_EXTRA_ENDPOINT_HOSTS": "127.0.0.1",
    }
    for script in (
        "scripts/pipeline/import_pilot_network.py",
        "scripts/lib/identities.py",
    ):
        completed = subprocess.run(
            [str(api_python), script],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=service_env,
        )
    payload = json.loads(completed.stdout or "{}")
    identities = {item["key"]: item for item in payload.get("identities", [])}

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
        run_checks(
            env=env,
            identities=identities,
            stub=stub,
            public=public,
            public_key=public_key,
        )
    finally:
        stop_services(services)
        stub.close()

    passed = bool(checks) and all(check["passed"] for check in checks)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "check": "check_push",
                "push_service": "stub on loopback (browser vendors' push services are "
                "unreachable from this environment)",
                "elapsed_seconds": round(time.perf_counter() - started, 1),
                "not_run": [
                    "A real browser receiving a push from a vendor push service "
                    "(needs network access to it; the service worker's handlers are "
                    "unit-tested in apps/client/lib/pwa/sw-notifications.test.ts)",
                    "Android notifications: registrations are stored, and skipped as "
                    "no_android_sender until an FCM or Expo sender is configured",
                ],
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
