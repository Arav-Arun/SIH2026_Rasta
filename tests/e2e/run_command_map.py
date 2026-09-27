"""Browser end-to-end test for the command overview and accessibility map."""

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
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_command_map.json"
SCREENSHOT_DIR = REPOSITORY_ROOT / "artifacts" / "reports" / "command-map-screenshots"

API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")


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

    # Playwright's bundled Chromium is a large download that is not always reachable;
    # the config drives the Chrome already installed on the machine.
    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(env, api_port=API_PORT, client_port=CLIENT_PORT)

    # The map is only meaningful over the real graph, so import it before the
    # identities are created: the bootstrap grants a pilot-district dispatcher
    # only once that district row exists.
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

    # write_env_files already pinned the client's base URL to API_PORT; the
    # process environment alone would not reach the client dev server.
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

        # The dev server compiles each route on its first request.
        for route in ("/overview", "/map", "/incidents"):
            wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}{route}", timeout_seconds=180)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "command-map.spec.ts",
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
                "name": "playwright_command_map",
                "passed": playwright.returncode == 0,
                "detail": (playwright.stdout + playwright.stderr).strip()[-4000:],
            }
        )
        report["screenshot_files"] = sorted(
            path.name for path in SCREENSHOT_DIR.glob("*.png")
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
