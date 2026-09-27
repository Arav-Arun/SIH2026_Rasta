"""Browser end-to-end test for the deliveries and fleet screens."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    redact_identity_payload,
    require_free_port,
    require_tool,
    run_command,
    start_service,
    stop_services,
    supabase_status_env,
    wait_for_url,
    write_env_files,
)

IDENTITY_FIXTURE = REPOSITORY_ROOT / "artifacts" / "e2e" / "e2e_identities.json"
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_deliveries.json"
SCREENSHOT_DIR = REPOSITORY_ROOT / "artifacts" / "reports" / "deliveries-screenshots"

API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")

PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))


def sign_in(supabase_url: str, anon: str, email: str, password: str) -> str:
    request = urllib.request.Request(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["access_token"]


def api_post(path: str, token: str, body: dict[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode() or "{}")


def seed_draft_consignment(token: str, database_url: str) -> dict[str, Any]:
    """One consignment still being written up: no trip, no receipt."""

    with psycopg.connect(database_url) as connection:
        facilities = connection.execute(
            """
            select id::text from public.facilities
            where district_id = %s::uuid
            order by id
            limit 2
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchall()

    if len(facilities) < 2:
        raise RuntimeError(
            "The pilot district has fewer than two facilities, so a consignment "
            "cannot be addressed from one to another."
        )

    origin, destination = facilities[0][0], facilities[1][0]
    deadline = (datetime.now(UTC) + timedelta(days=3)).isoformat()

    status, created = api_post(
        "/v1/consignments",
        token,
        {
            "district_id": PILOT_DISTRICT_ID,
            "reference": f"TEST-DRAFT-{uuid.uuid4().hex[:8]}",
            "origin_facility_id": origin,
            "destination_facility_id": destination,
            "priority": "normal",
            "deadline_at": deadline,
            "supply_request_id": None,
            "items": [
                {
                    "commodity": "Tarpaulin sheets",
                    "quantity": "60",
                    "unit": "rolls",
                    "weight_kg": "240",
                }
            ],
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not seed a draft consignment: {status} {created}")
    if created["status"] != "draft":
        raise RuntimeError(
            f"A new consignment should start as a draft, not {created['status']!r}."
        )
    return created


def main() -> int:
    require_free_port(API_PORT, what="API")
    require_free_port(CLIENT_PORT, what="client dev server")
    require_tool("supabase")
    require_tool("npm")
    require_tool("npx")

    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError(
            "API virtualenv not found. Create it with "
            "python3.12 -m venv api/.venv && pip install -e './api[dev]'"
        )

    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(env, api_port=API_PORT, client_port=CLIENT_PORT)

    import_result = subprocess.run(
        [str(api_python), "scripts/pipeline/import_pilot_network.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **runtime_env},
    )

    bootstrap = subprocess.run(
        [str(api_python), "scripts/lib/identities.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **runtime_env},
    )
    if not IDENTITY_FIXTURE.exists():
        raise RuntimeError("E2E identity fixture was not written.")

    bootstrap_payload = json.loads(bootstrap.stdout or "{}")
    identities = {item["key"]: item for item in bootstrap_payload.get("identities", [])}
    if "pilot-dispatcher" not in identities:
        raise RuntimeError(
            "No pilot-district dispatcher was created; the pilot import must run first."
        )

    service_env = {**os.environ.copy(), **runtime_env}

    services: list[ServiceProcess] = []
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "identity_fixture": str(IDENTITY_FIXTURE.relative_to(REPOSITORY_ROOT)),
        "screenshots": str(SCREENSHOT_DIR.relative_to(REPOSITORY_ROOT)),
        "checks": [],
    }

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
        wait_for_url(f"http://127.0.0.1:{API_PORT}/health")

        # The supply chain the screens are meant to show. Driving the logistics check
        # keeps one definition of what a correct chain looks like.
        acceptance = subprocess.run(
            [str(api_python), "tests/integration/check_logistics.py"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**service_env, "RASTA_STACK_READY": "1"},
        )
        report["checks"].append(
            {
                "name": "check_logistics",
                "passed": acceptance.returncode == 0,
                "detail": (acceptance.stdout + acceptance.stderr).strip()[-2000:],
            }
        )
        if acceptance.returncode != 0:
            print(acceptance.stdout)
            print(acceptance.stderr, file=sys.stderr)
            raise RuntimeError("The logistics check supply chain could not be created.")

        token = sign_in(
            env["API_URL"],
            env["ANON_KEY"],
            identities["pilot-dispatcher"]["email"],
            identities["pilot-dispatcher"]["password"],
        )
        draft = seed_draft_consignment(token, env["DB_URL"])
        report["checks"].append(
            {
                "name": "seed_draft_consignment",
                "passed": True,
                "detail": f"{draft['reference']} status={draft['status']}",
            }
        )

        services.append(
            start_service(
                "client",
                [
                    "npm",
                    "run",
                    "dev",
                    "--workspace",
                    "apps/client",
                    "--",
                    "-H",
                    "127.0.0.1",
                    "-p",
                    CLIENT_PORT,
                ],
                cwd=REPOSITORY_ROOT,
                env=service_env,
            )
        )
        wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}/sign-in")

        # The dev server compiles each route on first request; paying that
        # inside a test's own timeout fails for a reason unrelated to the screen.
        for route in ("/deliveries", "/fleet"):
            wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}{route}", timeout_seconds=180)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "deliveries.spec.ts",
            ],
            cwd=REPOSITORY_ROOT,
            env={
                **service_env,
                "E2E_IDENTITY_FIXTURE": str(IDENTITY_FIXTURE),
                "E2E_BASE_URL": f"http://127.0.0.1:{CLIENT_PORT}",
            },
            check=False,
            capture_output=True,
            text=True,
        )

        combined = playwright.stdout + playwright.stderr
        # A skipped test asserts nothing. The draft is seeded above precisely so
        # every check can run, so a skip means that seeding stopped working.
        skipped = "skipped" in combined and "0 skipped" not in combined
        report["checks"].append(
            {
                "name": "import_pilot_network",
                "passed": import_result.returncode == 0,
                "detail": import_result.stdout.strip()[-800:],
            }
        )
        report["checks"].append(
            {
                "name": "bootstrap_local_e2e_identity",
                "passed": bootstrap.returncode == 0,
                "detail": redact_identity_payload(bootstrap_payload),
            }
        )
        report["checks"].append(
            {
                "name": "playwright_deliveries_fleet",
                "passed": playwright.returncode == 0 and not skipped,
                "detail": combined.strip()[-4000:],
            }
        )
        report["screenshot_files"] = sorted(
            path.name for path in SCREENSHOT_DIR.glob("*.png")
        )
        report["passed"] = all(check["passed"] for check in report["checks"])

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        if not report["passed"]:
            print(playwright.stdout)
            print(playwright.stderr, file=sys.stderr)
            if skipped:
                print(
                    "A test skipped; the seeded draft did not reach the screen.",
                    file=sys.stderr,
                )
            return playwright.returncode or 1

        print(json.dumps(report, indent=2))
        return 0
    finally:
        stop_services(services)


if __name__ == "__main__":
    raise SystemExit(main())
