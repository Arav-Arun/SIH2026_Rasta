#!/usr/bin/env python3
"""The web client's Content Security Policy against the real stack."""

from __future__ import annotations

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
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_content_security.json"
API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")
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
    "/field/home",
    "/field/report",
    "/sync",
)


def main() -> int:
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
                "csp.spec.ts",
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
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "check": "e2e_content_security",
                "what": "Content-Security-Policy and framing headers on the web client; zero violations on 13 screens",
                "passed": passed,
                "detail": output[-4000:],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output[-3000:])
    print(f"{'PASSED' if passed else 'FAILED'} → {REPORT_PATH}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
