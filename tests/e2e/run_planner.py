"""Browser end-to-end test for the dispatcher route planner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
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
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_planner.json"
SCREENSHOT_DIR = REPOSITORY_ROOT / "artifacts" / "reports" / "planner-screenshots"

API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")

PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))


def connected_pair(database_url: str) -> tuple[str, str]:
    """Two facilities the graph can actually route between."""

    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """
            -- The routing node lives in `metadata`, which is where the
            -- pilot import writes it; there is no column for it.
            select f.name, f.metadata ->> 'routing_node_id' as node_id
            from public.facilities as f
            where f.district_id = %s::uuid
              and f.metadata ->> 'routing_node_id' is not null
              and exists (
                select 1 from public.road_segments as rs
                where rs.from_node_id = f.metadata ->> 'routing_node_id'
                  and rs.district_id = f.district_id
              )
            order by f.name
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchall()

    if len(rows) < 2:
        raise RuntimeError(
            "Fewer than two facilities in the pilot district sit on a routable node."
        )
    return rows[0][0], rows[1][0]


def isolate_a_facility(database_url: str, avoid: set[str]) -> str | None:
    """Close every road into one facility's node so S4 has a real cut to explain."""

    with psycopg.connect(database_url) as connection:
        candidate = connection.execute(
            """
            select f.name, f.metadata ->> 'routing_node_id' as node_id,
                   f.organization_id
            from public.facilities as f
            where f.district_id = %s::uuid
              and f.metadata ->> 'routing_node_id' is not null
              and not (f.name = any(%s::text[]))
              and (
                select count(*) from public.road_segments as rs
                where rs.to_node_id = f.metadata ->> 'routing_node_id'
                  and rs.district_id = f.district_id
              ) between 1 and 4
            order by (
              select count(*) from public.road_segments as rs
              where rs.to_node_id = f.metadata ->> 'routing_node_id'
                and rs.district_id = f.district_id
            ), f.name
            limit 1
            """,
            (PILOT_DISTRICT_ID, list(avoid)),
        ).fetchone()
        if candidate is None:
            return None

        name, node_id, organization_id = candidate
        connection.execute(
            """
            insert into public.segment_current_state
              (organization_id, segment_id, passability, risk_level, as_of, network_version)
            select rs.organization_id, rs.id, 'closed', 'unknown', now(), 'e2e-isolation'
            from public.road_segments as rs
            where rs.to_node_id = %s
              and rs.district_id = %s::uuid
              and rs.organization_id = %s::uuid
            on conflict (organization_id, segment_id)
            do update set passability = 'closed', as_of = now(),
                          network_version = 'e2e-isolation'
            """,
            (node_id, PILOT_DISTRICT_ID, organization_id),
        )
        connection.commit()
    return name


def main() -> int:
    require_free_port(API_PORT, what="API")
    require_free_port(CLIENT_PORT, what="client dev server")
    require_tool("supabase")
    require_tool("npm")
    require_tool("npx")

    api_python = REPOSITORY_ROOT / "services" / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError(
            "API virtualenv not found. Create it with "
            "python3.12 -m venv services/api/.venv && pip install -e './services/api[dev]'"
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
    identity_keys = [
        item.get("key") for item in bootstrap_payload.get("identities", [])
    ]
    if "pilot-dispatcher" not in identity_keys:
        raise RuntimeError(
            "No pilot-district dispatcher was created; the pilot import must run first."
        )

    origin, destination = connected_pair(env["DB_URL"])
    isolated = isolate_a_facility(env["DB_URL"], avoid={origin, destination})

    service_env = {**os.environ.copy(), **runtime_env}
    services: list[ServiceProcess] = []
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "identity_fixture": str(IDENTITY_FIXTURE.relative_to(REPOSITORY_ROOT)),
        "screenshots": str(SCREENSHOT_DIR.relative_to(REPOSITORY_ROOT)),
        "scenario_endpoints": {
            "origin": origin,
            "destination": destination,
            "isolated": isolated,
        },
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
                cwd=REPOSITORY_ROOT / "services" / "api",
                env=service_env,
            )
        )
        wait_for_url(f"http://127.0.0.1:{API_PORT}/health")

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
        wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}/planner", timeout_seconds=180)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "planner.spec.ts",
            ],
            cwd=REPOSITORY_ROOT,
            env={
                **service_env,
                "E2E_IDENTITY_FIXTURE": str(IDENTITY_FIXTURE),
                "E2E_BASE_URL": f"http://127.0.0.1:{CLIENT_PORT}",
                "E2E_PLANNER_ORIGIN": origin,
                "E2E_PLANNER_DESTINATION": destination,
                "E2E_PLANNER_ISOLATED": isolated or "",
            },
            check=False,
            capture_output=True,
            text=True,
        )

        combined = playwright.stdout + playwright.stderr
        # The S4 facility is isolated above precisely so no test has to skip.
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
                "name": "prepare_isolated_facility",
                "passed": isolated is not None,
                "detail": f"isolated={isolated}",
            }
        )
        report["checks"].append(
            {
                "name": "playwright_route_planner",
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
                    "A test skipped; the isolated facility did not reach the screen.",
                    file=sys.stderr,
                )
            return playwright.returncode or 1

        print(json.dumps(report, indent=2))
        return 0
    finally:
        stop_services(services)


if __name__ == "__main__":
    raise SystemExit(main())
