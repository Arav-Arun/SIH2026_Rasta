#!/usr/bin/env python3
"""Run named Playwright specs against a freshly reset local stack."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

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

IDENTITY_FIXTURE = REPOSITORY_ROOT / "artifacts" / "e2e" / "e2e_identities.json"
API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")
#: Every screen, compiled once before any test's own timeout starts.
SCREENS = (
    "/sign-in",
    "/overview",
    "/map",
    "/planner",
    "/incidents",
    "/inspections",
    "/deliveries",
    "/fleet",
    "/alerts",
    "/data-health",
    "/settings",
    "/field/home",
    "/field/report",
    "/sync",
    "/permissions",
    "/driver/trip",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("specs", nargs="+", help="spec file names under tests/e2e")
    parser.add_argument("--report", required=True, help="report file name")
    args = parser.parse_args()

    require_free_port(API_PORT, what="API")
    require_free_port(CLIENT_PORT, what="client dev server")
    for tool in ("supabase", "npm", "npx"):
        require_tool(tool)
    api_python = REPOSITORY_ROOT / "services" / "api" / ".venv" / "bin" / "python"

    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(env, api_port=API_PORT, client_port=CLIENT_PORT)
    service_env = {**os.environ.copy(), **runtime_env}
    for script in (
        "scripts/pipeline/import_pilot_network.py",
        "scripts/lib/identities.py",
    ):
        subprocess.run(
            [str(api_python), script],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=service_env,
        )

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
        for path in SCREENS:
            wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}{path}", timeout_seconds=180)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                *args.specs,
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
    finally:
        stop_services(services)

    output = (playwright.stdout + playwright.stderr).strip()
    passed = playwright.returncode == 0
    report = REPOSITORY_ROOT / "artifacts" / "reports" / args.report
    report.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "specs": args.specs,
                "passed": passed,
                "detail": output[-6000:],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output[-3000:])
    print(f"{'PASSED' if passed else 'FAILED'} → {report}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
