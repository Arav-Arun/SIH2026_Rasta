#!/usr/bin/env python3
"""Browser test for sign-in and route access against the local stack."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.lib.stack import (  # noqa: E402
    IDENTITY_FIXTURE,
    ServiceProcess,
    redact_identity_payload,
    require_tool,
    run_command,
    start_service,
    stop_services,
    supabase_status_env,
    wait_for_url,
    write_env_files,
)

REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_auth.json"


def main() -> int:
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
    runtime_env = write_env_files(env)

    bootstrap = subprocess.run(
        [str(api_python), "scripts/lib/identities.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if not IDENTITY_FIXTURE.exists():
        raise RuntimeError("E2E identity fixture was not written.")

    service_env = {**os.environ.copy(), **runtime_env}
    services: list[ServiceProcess] = []
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "identity_fixture": str(IDENTITY_FIXTURE.relative_to(REPOSITORY_ROOT)),
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
                    "8000",
                ],
                cwd=REPOSITORY_ROOT / "api",
                env=service_env,
            )
        )
        wait_for_url("http://127.0.0.1:8000/health")

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
                    "3000",
                ],
                cwd=REPOSITORY_ROOT,
                env=service_env,
            )
        )
        wait_for_url("http://127.0.0.1:3000/sign-in")

        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "auth-bootstrap.spec.ts",
            ],
            cwd=REPOSITORY_ROOT,
            env={
                **service_env,
                "E2E_IDENTITY_FIXTURE": str(IDENTITY_FIXTURE),
                "E2E_BASE_URL": "http://127.0.0.1:3000",
            },
            check=False,
            capture_output=True,
            text=True,
        )
        bootstrap_payload = json.loads(bootstrap.stdout or "{}")
        report["checks"].append(
            {
                "name": "bootstrap_local_e2e_identity",
                "passed": bootstrap.returncode == 0,
                "detail": redact_identity_payload(bootstrap_payload),
            }
        )
        report["checks"].append(
            {
                "name": "playwright_auth_bootstrap",
                "passed": playwright.returncode == 0,
                "detail": (playwright.stdout + playwright.stderr).strip(),
            }
        )
        report["passed"] = playwright.returncode == 0
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        if playwright.returncode != 0:
            print(playwright.stdout)
            print(playwright.stderr, file=sys.stderr)
            return playwright.returncode
        print(json.dumps(report, indent=2))
        return 0
    finally:
        stop_services(services)


if __name__ == "__main__":
    raise SystemExit(main())
