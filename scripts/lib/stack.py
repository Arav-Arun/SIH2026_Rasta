"""Helpers for starting and stopping the local Supabase, API and web client stack."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ENV_PATH = REPOSITORY_ROOT / "apps" / "client" / ".env.local"
API_ENV_PATH = REPOSITORY_ROOT / "api" / ".env"


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required tool not found: {name}")


def run_command(command: list[str], *, cwd: Path = REPOSITORY_ROOT) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def supabase_status_env() -> dict[str, str]:
    output = run_command(["supabase", "status", "-o", "env"])
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def require_free_port(port: str | int, *, what: str) -> None:
    """Refuse to run when something else already answers on this port."""

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", int(port))) == 0:
            raise RuntimeError(
                f"Port {port} is already in use, so the {what} started by this run "
                "could not listen on it. Stop that process and try again."
            )


def wait_for_url(url: str, *, timeout_seconds: float = 90.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def write_env_files(
    env: dict[str, str],
    *,
    api_port: str = "8000",
    client_port: str = "3000",
    service_worker: bool = False,
    app_version: str = "local development build",
) -> dict[str, str]:
    """Write the local env files the two services read."""

    publishable_key = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]
    origins = [f"http://127.0.0.1:{client_port}", f"http://localhost:{client_port}"]
    api_values = {
        "APP_MODE": "local_demo",
        "APP_ORIGIN": origins[0],
        "ALLOWED_ORIGINS": ",".join(origins),
        "DATABASE_URL": env["DB_URL"],
        "SUPABASE_URL": env["API_URL"],
        "SUPABASE_PUBLISHABLE_KEY": publishable_key,
        "SUPABASE_SECRET_KEY": env["SERVICE_ROLE_KEY"],
        "SUPABASE_STORAGE_BUCKET": "evidence",
    }
    client_values = {
        "NEXT_PUBLIC_API_BASE_URL": f"http://127.0.0.1:{api_port}",
        "NEXT_PUBLIC_SUPABASE_URL": env["API_URL"],
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": publishable_key,
        # Off unless a caller asks for it: a service worker in front of a dev
        # server caches a build that is recompiled on the next request.
        "NEXT_PUBLIC_ENABLE_SERVICE_WORKER": "1" if service_worker else "0",
        "NEXT_PUBLIC_APP_VERSION": app_version,
    }
    API_ENV_PATH.write_text(
        "\n".join(f"{key}={value}" for key, value in api_values.items()) + "\n",
        encoding="utf-8",
    )
    CLIENT_ENV_PATH.write_text(
        "\n".join(f"{key}={value}" for key, value in client_values.items()) + "\n",
        encoding="utf-8",
    )
    return {**api_values, **client_values}
