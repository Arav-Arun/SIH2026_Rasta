"""Offline PWA check: PWA packaging and bounded offline data packs."""

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
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "e2e_offline_pwa.json"
SPEC_REPORT = REPOSITORY_ROOT / "artifacts" / "reports" / "pwa-audit.json"
SCREENSHOT_DIR = REPOSITORY_ROOT / "artifacts" / "reports" / "pwa-screenshots"
PACK_DIR = REPOSITORY_ROOT / "apps" / "client" / "public" / "packs"

API_PORT = os.environ.get("RASTA_E2E_API_PORT", "8010")
CLIENT_PORT = os.environ.get("RASTA_E2E_CLIENT_PORT", "3000")


def main() -> int:
    require_free_port(API_PORT, what="API")
    require_free_port(CLIENT_PORT, what="client dev server")
    for tool in ("supabase", "npm", "npx"):
        require_tool(tool)

    api_python = REPOSITORY_ROOT / "services" / "api" / ".venv" / "bin" / "python"
    if not api_python.exists():
        raise RuntimeError("API virtualenv not found under services/api/.venv.")

    checks: list[dict[str, Any]] = []

    # The pack is a build artefact, so it is rebuilt here rather than assumed.
    built = subprocess.run(
        [str(api_python), "scripts/pipeline/build_pilot_data_pack.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    pack_summary = json.loads(built.stdout or "{}")
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["packs"][0]
    checks.append(
        {
            "name": "pack_built_with_a_checksum_and_a_denominator",
            "passed": (
                entry["sha256"] == pack_summary["sha256"]
                and entry["bytes"] == pack_summary["bytes"]
                and entry["counts"]["edges"] > 1000
                and entry["license"] != ""
                and entry["attribution"] != ""
            ),
            "detail": f"{entry['bytes']} bytes, sha256={entry['sha256'][:16]}…, "
            f"counts={entry['counts']}, licence={entry['license']}",
        }
    )
    # A pack is meant to be bounded. Twenty megabytes is not a pack.
    checks.append(
        {
            "name": "the_pack_is_bounded",
            "passed": entry["bytes"] < 8 * 1024 * 1024,
            "detail": f"{round(entry['bytes'] / 1_048_576, 2)} MB",
        }
    )

    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    runtime_env = write_env_files(
        env,
        api_port=API_PORT,
        client_port=CLIENT_PORT,
        service_worker=True,
        app_version=f"test-acceptance-{entry['version']}",
    )
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

        # A production build, served the way it ships.
        subprocess.run(
            ["npm", "run", "build", "--workspace", "apps/client"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=service_env,
        )
        services.append(
            start_service(
                "client",
                ["npx", "vinext", "start", "-H", "127.0.0.1", "-p", CLIENT_PORT],
                cwd=REPOSITORY_ROOT / "apps" / "client",
                env=service_env,
            )
        )
        wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}/sign-in")
        for path in ("/overview", "/settings", "/alerts", "/fleet", "/map"):
            wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}{path}", timeout_seconds=180)
        # Served from the client's own public directory, so a 404 here would make
        # every download check fail for the wrong reason.
        wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}/packs/manifest.json")
        wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}/manifest.webmanifest")

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        SPEC_REPORT.unlink(missing_ok=True)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "--config",
                "tests/e2e/playwright.config.ts",
                "pwa.spec.ts",
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
                "name": "playwright_pwa_suite",
                "passed": playwright.returncode == 0,
                "detail": combined[-5000:],
            }
        )

        spec: dict[str, Any] = {}
        if SPEC_REPORT.exists():
            spec = json.loads(SPEC_REPORT.read_text(encoding="utf-8"))
        offenders = (spec.get("cache_inventory") or {}).get("offenders") or {}
        checks.append(
            {
                "name": "no_session_token_or_signed_media_is_cached",
                "passed": not offenders.get("auth")
                and not offenders.get("signedMedia"),
                "detail": json.dumps(offenders)[:1500],
            }
        )
        checks.append(
            {
                "name": "no_map_tile_is_cached",
                "passed": spec.get("cached_tiles") == [],
                "detail": json.dumps(spec.get("cached_tiles"))[:500],
            }
        )
        checks.append(
            {
                "name": "the_shell_and_the_pack_open_with_no_network",
                "passed": bool(
                    (spec.get("offline_shell") or {}).get("packVersionStillShown")
                ),
                "detail": json.dumps(spec.get("offline_shell"))[:500],
            }
        )
        shots = sorted(p.name for p in SCREENSHOT_DIR.glob("*.png"))
        checks.append(
            {
                "name": "offline_evidence_screenshots_written",
                "passed": len(shots) >= 2,
                "detail": f"{len(shots)} files: {shots}",
            }
        )
    finally:
        stop_services(services)
        # Leave the checkout as development expects it: no worker in front of the
        # dev server on the next `npm run dev`.
        write_env_files(env, api_port=API_PORT, client_port=CLIENT_PORT)

    passed = all(check["passed"] for check in checks)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "check": "e2e_offline_pwa",
                "pack": pack_summary,
                "screenshots": str(SCREENSHOT_DIR.relative_to(REPOSITORY_ROOT)),
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
        print(
            f"{'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail'][:200]}"
        )
    if passed:
        print(f"\nALL {len(checks)} CHECKS PASSED → {REPORT_PATH}")
        return 0
    print(f"\nFAILURES → {REPORT_PATH}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
