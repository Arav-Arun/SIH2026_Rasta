"""Accessibility check: accessibility, keyboard reach and the three locales."""

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
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_accessibility.json"
AXE_REPORT = REPOSITORY_ROOT / "artifacts" / "reports" / "accessibility-audit.json"
SCREENSHOT_DIR = REPOSITORY_ROOT / "artifacts" / "reports" / "accessibility-screenshots"

API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")


def main() -> int:
    require_free_port(API_PORT, what="API")
    require_free_port(CLIENT_PORT, what="client dev server")
    for tool in ("supabase", "npm", "npx"):
        require_tool(tool)

    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError("API virtualenv not found under api/.venv.")

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
    if not IDENTITY_FIXTURE.exists():
        raise RuntimeError("The E2E identity fixture was not written.")

    # Alerts and a scored map are part of what these screens render, so the run
    # audits screens with content on them rather than eight empty states.
    services: list[ServiceProcess] = []
    checks: list[dict[str, Any]] = []
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
        # The dev server compiles a route on first request. Paying that inside a
        # test's own timeout fails for a reason unrelated to the screen.
        for path in (
            "/overview",
            "/planner",
            "/alerts",
            "/data-health",
            "/map",
            "/deliveries",
            "/fleet",
            "/incidents",
            "/inspections",
            "/field/home",
            "/field/report",
            "/sync",
            "/permissions",
            "/driver/trip",
        ):
            wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}{path}", timeout_seconds=180)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        # The spec accumulates into this file across worker restarts, so a fresh
        # run must start from nothing or it would report an earlier run's screens.
        AXE_REPORT.unlink(missing_ok=True)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "accessibility.spec.ts",
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
        combined = (playwright.stdout + playwright.stderr).strip()
        checks.append(
            {
                "name": "playwright_accessibility_suite",
                "passed": playwright.returncode == 0,
                "detail": combined[-5000:],
            }
        )

        audit: dict[str, Any] = {}
        if AXE_REPORT.exists():
            audit = json.loads(AXE_REPORT.read_text(encoding="utf-8"))
        checks.append(
            {
                "name": "axe_report_written",
                "passed": bool(audit) and audit.get("screens_audited", 0) >= 15,
                "detail": f"screens_audited={audit.get('screens_audited')} "
                f"blocking={audit.get('blocking_violations')}",
            }
        )
        checks.append(
            {
                "name": "no_serious_or_critical_violation",
                "passed": audit.get("blocking_violations") == 0,
                "detail": json.dumps(
                    [
                        {
                            "screen": f["screen"],
                            "locale": f["locale"],
                            "critical": f["critical"],
                            "serious": f["serious"],
                            "moderate": f["moderate"],
                            "minor": f["minor"],
                        }
                        for f in audit.get("findings", [])
                    ]
                )[:3000],
            }
        )
        shots = sorted(p.name for p in SCREENSHOT_DIR.glob("*.png"))
        checks.append(
            {
                "name": "walkthrough_screenshots_written",
                "passed": len(shots) >= 8,
                "detail": f"{len(shots)} files: {shots[:20]}",
            }
        )
    finally:
        stop_services(services)

    passed = all(check["passed"] for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "check": "e2e_accessibility",
                "screenshots": str(SCREENSHOT_DIR.relative_to(REPOSITORY_ROOT)),
                "axe_report": str(AXE_REPORT.relative_to(REPOSITORY_ROOT)),
                "checks": checks,
                "passed": passed,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print()
    for check in checks:
        print(f"{'PASS' if check['passed'] else 'FAIL'} {check['name']}")
    if passed:
        print(f"\nALL {len(checks)} CHECKS PASSED → {REPORT_PATH}")
        return 0
    print(f"\nFAILURES → {REPORT_PATH}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
