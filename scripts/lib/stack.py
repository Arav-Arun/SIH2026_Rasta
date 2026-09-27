"""Helpers for starting and stopping the local Supabase, API and web client stack."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_FIXTURE = REPOSITORY_ROOT / "artifacts" / "e2e" / "e2e_identities.json"
CLIENT_ENV_PATH = REPOSITORY_ROOT / "apps" / "client" / ".env.local"
API_ENV_PATH = REPOSITORY_ROOT / "api" / ".env"


def redact_identity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    identities = []
    for identity in payload.get("identities", []):
        identities.append(
            {key: value for key, value in identity.items() if key != "password"}
        )
    return {
        "organization_id": payload.get("organization_id"),
        "district_id": payload.get("district_id"),
        "identity_count": len(identities),
        "identity_keys": [identity.get("key") for identity in identities],
        "identities": identities,
    }


@dataclass
class ServiceProcess:
    name: str
    process: subprocess.Popen[str]
    #: Where the service's output goes (gitignored). Read it after the run.
    log_path: Path | None = None
    log_file: Any = None


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
                "would not be the one under test. Stop that process and try again."
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


def start_service(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> ServiceProcess:
    # Output goes straight to a file. A pipe nobody reads fills at 64 KB and
    # then blocks the service mid-request, which a long browser run reaches.
    log_path = REPOSITORY_ROOT / "artifacts" / "e2e" / "logs" / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        # Its own process group, so stopping it stops everything it started:
        # `npm run dev` does not pass SIGTERM on to the dev server it spawned,
        # which was left holding the port and failed the next run.
        start_new_session=True,
    )
    return ServiceProcess(
        name=name, process=process, log_path=log_path, log_file=log_file
    )


def _signal_group(process: subprocess.Popen[str], sig: int) -> None:
    # start_new_session made the service its group's leader, so the group id is
    # its pid, and stays valid after the leader itself has exited and been
    # reaped (os.getpgid would then fail and miss the children still running).
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)


def stop_services(services: list[ServiceProcess]) -> None:
    for service in services:
        _signal_group(service.process, signal.SIGTERM)
    deadline = time.time() + 15
    for service in services:
        remaining = max(0.0, deadline - time.time())
        with contextlib.suppress(subprocess.TimeoutExpired):
            service.process.wait(timeout=remaining)
        # Whatever the group leader did, nothing it started may outlive the run.
        _signal_group(service.process, signal.SIGKILL)
        if service.log_file is not None:
            service.log_file.close()


@contextmanager
def fresh_stack_with_api(api_port: str) -> Iterator[dict[str, str]]:
    """A database reset from the migrations, the pilot network, and this checkout's API."""

    api_python = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
    require_tool("supabase")
    require_free_port(api_port, what="the API")
    run_command(["supabase", "start"])
    run_command(["supabase", "db", "reset"])
    env = supabase_status_env()
    service_env = {**os.environ.copy(), **write_env_files(env, api_port=api_port)}
    subprocess.run(
        [str(api_python), "scripts/pipeline/import_pilot_network.py"],
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
                    api_port,
                ],
                cwd=REPOSITORY_ROOT / "api",
                env=service_env,
            )
        )
        wait_for_url(f"http://127.0.0.1:{api_port}/health")
        yield env
    finally:
        stop_services(services)
