#!/usr/bin/env python3
"""The local demo in one command: start, reset, check, back up, restore."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VENV = REPOSITORY_ROOT / "api" / ".venv"

# The helpers below need the API's packages (httpx, psycopg).
if Path(sys.prefix).resolve() != VENV.resolve() and (VENV / "bin" / "python").exists():
    os.execv(str(VENV / "bin" / "python"), [str(VENV / "bin" / "python"), *sys.argv])

sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.lib.identities import (  # noqa: E402
    ORG_ID,
    PILOT_DISTRICT_ID,
    create_auth_user,
    create_profile,
    create_role_grant,
    pilot_district_present,
)
from scripts.lib.stack import (  # noqa: E402
    require_free_port,
    require_tool,
    run_command,
    supabase_status_env,
    wait_for_url,
    write_env_files,
)
from scripts.pipeline.recorded_sources import redate_recorded_sources  # noqa: E402

DEMO_DIR = REPOSITORY_ROOT / "artifacts" / "demo"
STATE_PATH = DEMO_DIR / "state.json"
ACCOUNTS_PATH = DEMO_DIR / "accounts.json"
STORY_PATH = DEMO_DIR / "story.json"
BACKUP_DIR = DEMO_DIR / "backups"
#: Re-dated copies of the recorded IMD and SACHET documents the demo API reads.
DEMO_SOURCES = DEMO_DIR / "sources"
#: The demo API's tracking-credential signing key. Kept (gitignored) across
#: restarts, so restarting the API does not cut off a driver mid-trip.
TELEMETRY_SECRET_PATH = DEMO_DIR / "telemetry_token_secret"
LOG_DIR = DEMO_DIR / "logs"
API_PYTHON = REPOSITORY_ROOT / "api" / ".venv" / "bin" / "python"
API_PORT = "8000"
CLIENT_PORT = "3000"
EXPO_WEB_PORT = "8081"

#: The demo people. Emails are fixed so presenters can learn them; passwords
#: are not, and change with every reset.
PEOPLE = (
    ("dispatcher", "district_dispatcher", "Demo dispatcher (East Khasi Hills)"),
    ("officer", "field_officer", "Demo field officer"),
    ("driver", "driver", "Demo driver"),
    ("admin", "admin", "Demo administrator"),
)


def email_for(key: str) -> str:
    return f"{key}@demo.rasta.test"


# --------------------------------------------------------------------------- #
# Small HTTP helpers                                                           #
# --------------------------------------------------------------------------- #


def http(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if method == "POST" and "/v1/" in url and "Idempotency-Key" not in request_headers:
        request_headers["Idempotency-Key"] = str(uuid.uuid4())
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return error.code, {"raw": raw[:300]}


def sign_in(env: dict[str, str], email: str, password: str) -> str:
    anon = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]
    status, payload = http(
        "POST",
        f"{env['API_URL']}/auth/v1/token?grant_type=password",
        body={"email": email, "password": password},
        headers={"apikey": anon},
    )
    if status != 200:
        raise RuntimeError(f"Could not sign in as {email}: {status}")
    return payload["access_token"]


# --------------------------------------------------------------------------- #
# Processes                                                                    #
# --------------------------------------------------------------------------- #


def read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state: dict[str, Any]) -> None:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def start_detached(
    name: str, command: list[str], *, cwd: Path, env: dict[str, str]
) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOG_DIR / f"{name}.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return process.pid


def stop_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(2)


# --------------------------------------------------------------------------- #
# Data                                                                         #
# --------------------------------------------------------------------------- #


def reset_database(env: dict[str, str], service_env: dict[str, str]) -> None:
    run_command(["supabase", "db", "reset"])
    subprocess.run(
        [str(API_PYTHON), "scripts/pipeline/import_pilot_network.py"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=service_env,
    )
    if not pilot_district_present(env["DB_URL"]):
        raise RuntimeError(
            "The pilot network import did not create the pilot district."
        )


def create_people(env: dict[str, str]) -> dict[str, Any]:
    password = f"Demo-{secrets.token_urlsafe(9)}"
    people: dict[str, Any] = {}
    for key, role, name in PEOPLE:
        user_id = create_auth_user(
            supabase_url=env["API_URL"],
            service_role_key=env["SERVICE_ROLE_KEY"],
            email=email_for(key),
            password=password,
        )
        profile_id = create_profile(env["DB_URL"], user_id=user_id, display_name=name)
        # Organisation-wide roles carry no district (role_assignments_district_scope_check).
        district = None if role in ("admin", "state_coordinator") else PILOT_DISTRICT_ID
        create_role_grant(
            env["DB_URL"], profile_id=profile_id, role=role, district_id=district
        )
        people[key] = {"email": email_for(key), "role": role, "profile_id": profile_id}
    accounts = {
        "generated_at": datetime.now(UTC).isoformat(),
        "note": "Local demo accounts. Recreated with a new password on every reset.",
        "password": password,
        "people": people,
    }
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_PATH.write_text(json.dumps(accounts, indent=2) + "\n", encoding="utf-8")
    return accounts


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dx = (a[1] - b[1]) * 111.32 * math.cos(lat)
    dy = (a[0] - b[0]) * 110.57
    return math.hypot(dx, dy)


def seed_sources(token: str, base: str) -> dict[str, Any]:
    """Run the recorded IMD and SACHET documents and score the pilot roads."""

    redate_recorded_sources(DEMO_SOURCES)
    status, result = http(
        "POST", f"{base}/v1/risk/recompute?district_id={PILOT_DISTRICT_ID}", token=token
    )
    if status != 200:
        raise RuntimeError(f"The recorded sources did not run: {status} {result}")
    return {
        "runs": {run["source"]: run["status"] for run in result.get("runs", [])},
        "model_version": result.get("model_version"),
        "segments_scored": result.get("segments_scored", 0),
        "segments_unscored": result.get("segments_unscored", 0),
    }


def seed_story(
    env: dict[str, str], accounts: dict[str, Any], base: str
) -> dict[str, Any]:
    """The story's start: medicine on an approved route, the trip running."""

    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    password = accounts["password"]
    dispatcher = sign_in(env, email_for("dispatcher"), password)
    driver = sign_in(env, email_for("driver"), password)
    sources = seed_sources(dispatcher, base)

    with psycopg.connect(env["DB_URL"], row_factory=dict_row) as connection:
        facilities = connection.execute(
            """
            select id::text, name, type::text as type,
                   st_y(location::geometry) as lat, st_x(location::geometry) as lon,
                   metadata->>'routing_node_id' as node
            from public.facilities
            where district_id = %s::uuid and active and metadata ? 'routing_node_id'
            order by name
            """,
            (PILOT_DISTRICT_ID,),
        ).fetchall()
        vehicle_id = connection.execute(
            """
            insert into public.vehicles (
              organization_id, registration_ref, class, capacity_kg, active, source_mode
            )
            values (%s::uuid, 'DEMO-ML-05', 'light_truck', 2000, true, 'synthetic')
            returning id::text
            """,
            (ORG_ID,),
        ).fetchone()["id"]
        connection.commit()

    hospitals = [f for f in facilities if f["type"] == "hospital"]
    clinics = [f for f in facilities if f["type"] != "hospital"]
    # A hospital as the dispatching store and a clinic three to nine kilometres
    # away, with at least two routes between them so a closure leaves a choice.
    pairs = sorted(
        (
            (origin, destination)
            for origin in hospitals
            for destination in clinics
            if 3
            <= distance_km(
                (origin["lat"], origin["lon"]), (destination["lat"], destination["lon"])
            )
            <= 9
        ),
        key=lambda pair: (pair[0]["name"], pair[1]["name"]),
    )
    plan: dict[str, Any] | None = None
    chosen_pair = None
    for origin, destination in pairs[:25]:
        status, created = http(
            "POST",
            f"{base}/v1/route-plans",
            token=dispatcher,
            body={
                "district_id": PILOT_DISTRICT_ID,
                "origin_node_id": origin["node"],
                "destination_node_id": destination["node"],
                "priority": "critical",
                "requested_alternatives": 3,
            },
        )
        candidate = created.get("plan") if status == 201 else None
        if candidate and len(candidate.get("alternatives") or []) >= 2:
            first, second = candidate["alternatives"][0], candidate["alternatives"][1]
            only_first = [
                s for s in first["segment_ids"] if s not in set(second["segment_ids"])
            ]
            if only_first:
                plan, chosen_pair = candidate, (origin, destination, only_first)
                break
    if plan is None or chosen_pair is None:
        raise RuntimeError(
            "No facility pair in the pilot district has two distinct routes."
        )
    origin, destination, only_first = chosen_pair

    status, consignment = http(
        "POST",
        f"{base}/v1/consignments",
        token=dispatcher,
        body={
            "district_id": PILOT_DISTRICT_ID,
            "reference": f"DEMO-MED-{datetime.now(UTC):%m%d}",
            "origin_facility_id": origin["id"],
            "destination_facility_id": destination["id"],
            "priority": "critical",
            "deadline_at": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
            "supply_request_id": None,
            "items": [
                {
                    "commodity": "ORS sachets",
                    "quantity": "400",
                    "unit": "sachet",
                    "weight_kg": "40",
                },
                {
                    "commodity": "Paracetamol 500 mg",
                    "quantity": "60",
                    "unit": "strip",
                    "weight_kg": "6",
                },
            ],
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not create the consignment: {status} {consignment}")
    http(
        "POST",
        f"{base}/v1/consignments/{consignment['id']}/planned",
        token=dispatcher,
        body={},
    )
    status, created = http(
        "POST",
        f"{base}/v1/trips",
        token=dispatcher,
        body={
            "consignment_id": consignment["id"],
            "vehicle_id": vehicle_id,
            "driver_profile_id": accounts["people"]["driver"]["profile_id"],
        },
    )
    if status != 201:
        raise RuntimeError(f"Could not create the trip: {status} {created}")
    trip_id = created["trip"]["id"]

    # Approve the first alternative for this trip, then hand it to the driver.
    status, replanned = http(
        "POST",
        f"{base}/v1/route-plans",
        token=dispatcher,
        body={
            "district_id": PILOT_DISTRICT_ID,
            "origin_node_id": origin["node"],
            "destination_node_id": destination["node"],
            "priority": "critical",
            "requested_alternatives": 3,
            "trip_id": trip_id,
        },
    )
    trip_plan = replanned["plan"]
    chosen = trip_plan["alternatives"][0]
    status, approved = http(
        "POST",
        f"{base}/v1/route-plans/{trip_plan['id']}/approve",
        token=dispatcher,
        body={
            "alternative_id": chosen["id"],
            "expected_network_version": trip_plan["network_version"],
        },
    )
    if status != 200:
        raise RuntimeError(f"Could not approve the route: {status} {approved}")
    for path, token in (
        (f"/v1/trips/{trip_id}/route-plan", dispatcher),
        (f"/v1/trips/{trip_id}/awaiting_driver", dispatcher),
        (f"/v1/trips/{trip_id}/active", driver),
    ):
        body = {"route_plan_id": trip_plan["id"]} if path.endswith("route-plan") else {}
        status, moved = http("POST", f"{base}{path}", token=token, body=body)
        if status != 200:
            raise RuntimeError(f"Could not move the trip ({path}): {status} {moved}")

    second = (
        trip_plan["alternatives"][1]["segment_ids"]
        if len(trip_plan["alternatives"]) > 1
        else []
    )
    closable = [s for s in chosen["segment_ids"] if s not in set(second)] or only_first
    segment_id = closable[len(closable) // 2]
    with psycopg.connect(env["DB_URL"], row_factory=dict_row) as connection:
        where = connection.execute(
            """
            select st_y(st_lineinterpolatepoint(geometry::geometry, 0.5)) as lat,
                   st_x(st_lineinterpolatepoint(geometry::geometry, 0.5)) as lon,
                   coalesce(metadata->>'name', metadata->>'ref',
                            'an unnamed ' || road_class || ' road') as road
            from public.road_segments where id = %s::uuid
            """,
            (segment_id,),
        ).fetchone()

    story = {
        "generated_at": datetime.now(UTC).isoformat(),
        "labels": "Recorded OSM network and facilities; synthetic consignment, vehicle "
        "and trip.",
        "consignment": consignment.get("reference"),
        "origin": origin["name"],
        "destination": destination["name"],
        "trip_id": trip_id,
        "route_plan_id": trip_plan["id"],
        "alternatives": len(trip_plan["alternatives"]),
        "sources": sources,
        "landslide": {
            "segment_id": segment_id,
            "road": where["road"],
            "latitude": round(where["lat"], 6),
            "longitude": round(where["lon"], 6),
        },
    }
    STORY_PATH.write_text(json.dumps(story, indent=2) + "\n", encoding="utf-8")
    return story


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def telemetry_secret() -> str:
    """This machine's demo signing key for tracking credentials, made once."""

    if TELEMETRY_SECRET_PATH.exists():
        value = TELEMETRY_SECRET_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    TELEMETRY_SECRET_PATH.write_text(value + "\n", encoding="utf-8")
    TELEMETRY_SECRET_PATH.chmod(0o600)
    return value


def service_environment(host: str) -> tuple[dict[str, str], dict[str, str]]:
    env = supabase_status_env()
    runtime = write_env_files(
        env,
        api_port=API_PORT,
        client_port=CLIENT_PORT,
        # The installable shell and the offline pack are part of the demo.
        service_worker=True,
        app_version=f"local-demo-{datetime.now(UTC):%Y%m%d}",
    )
    runtime["SOURCE_FIXTURE_ROOT"] = str(DEMO_SOURCES)
    runtime["TELEMETRY_TOKEN_SECRET"] = telemetry_secret()
    # The phone app's web build (`npm run mobile:web`, Expo on port 8081) is a
    # way to show the field and driver screens with no phone at hand.
    runtime["ALLOWED_ORIGINS"] += (
        f",http://localhost:{EXPO_WEB_PORT},http://127.0.0.1:{EXPO_WEB_PORT}"
    )
    if host != "127.0.0.1":
        # A phone on the same network reaches the laptop by its LAN address.
        runtime["NEXT_PUBLIC_API_BASE_URL"] = f"http://{host}:{API_PORT}"
        runtime["ALLOWED_ORIGINS"] += f",http://{host}:{CLIENT_PORT}"
        client_env = REPOSITORY_ROOT / "apps" / "client" / ".env.local"
        text = client_env.read_text(encoding="utf-8").replace(
            f"NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:{API_PORT}",
            f"NEXT_PUBLIC_API_BASE_URL=http://{host}:{API_PORT}",
        )
        client_env.write_text(text, encoding="utf-8")
        api_env = REPOSITORY_ROOT / "api" / ".env"
        lines = [
            f"ALLOWED_ORIGINS={runtime['ALLOWED_ORIGINS']}"
            if line.startswith("ALLOWED_ORIGINS=")
            else line
            for line in api_env.read_text(encoding="utf-8").splitlines()
        ]
        api_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env, {**os.environ.copy(), **runtime}


def print_summary(
    state: dict[str, Any], accounts: dict[str, Any], story: dict[str, Any]
) -> None:
    host = state.get("host", "127.0.0.1")
    print()
    print(
        "RASTA local demo is ready. Mode: local demo (recorded network, synthetic trip)."
    )
    print(f"  Web client  http://{host}:{CLIENT_PORT}/sign-in")
    print(f"  API         http://{host}:{API_PORT}/health")
    print(
        f"  Password for every demo account (this reset only): {accounts['password']}"
    )
    for person in accounts["people"].values():
        print(f"    {person['email']:<32} {person['role']}")
    print()
    print(
        f"  Story: consignment {story['consignment']} from {story['origin']} to "
        f"{story['destination']}, trip running with the driver on an approved route "
        f"({story['alternatives']} alternatives were offered)."
    )
    sources = story.get("sources") or {}
    print(
        f"  Sources: {', '.join(f'{k} {v}' for k, v in sources.get('runs', {}).items())} "
        f"(recorded, re-dated to this reset); {sources.get('segments_scored', 0)} road "
        f"segments risk-scored by {sources.get('model_version')}."
    )
    slide = story["landslide"]
    print(
        f"  The landslide: report it on {slide['road']} at "
        f"{slide['latitude']}, {slide['longitude']} (on the approved route)."
    )
    print(
        f"  Accounts: {ACCOUNTS_PATH.relative_to(REPOSITORY_ROOT)} (gitignored); "
        f"story: {STORY_PATH.relative_to(REPOSITORY_ROOT)}"
    )


def command_up(args: argparse.Namespace) -> int:
    for tool in ("supabase", "npm", "npx", "docker"):
        require_tool(tool)
    if not API_PYTHON.exists():
        print(
            "API virtualenv missing: python3.12 -m venv api/.venv && "
            "api/.venv/bin/python -m pip install -e './api[dev]'"
        )
        return 2
    command_down(args, quiet=True)
    require_free_port(API_PORT, what="API")
    require_free_port(CLIENT_PORT, what="web client")
    timings: dict[str, float] = {}

    def timed(name: str, action: Any) -> Any:
        started = time.perf_counter()
        result = action()
        timings[name] = round(time.perf_counter() - started, 1)
        print(f"  {name}: {timings[name]} s", flush=True)
        return result

    print("Starting the local demo…", flush=True)
    timed("supabase start", lambda: run_command(["supabase", "start"]))
    env, service_env = service_environment(args.lan_ip)
    timed(
        "database reset and pilot network import",
        lambda: reset_database(env, service_env),
    )
    accounts = timed("demo people", lambda: create_people(env))

    bind = "0.0.0.0" if args.lan_ip != "127.0.0.1" else "127.0.0.1"
    state = {"host": args.lan_ip, "started_at": datetime.now(UTC).isoformat()}
    state["api_pid"] = start_detached(
        "api",
        [
            str(API_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            bind,
            "--port",
            API_PORT,
        ],
        cwd=REPOSITORY_ROOT / "api",
        env=service_env,
    )
    write_state(state)
    timed("API ready", lambda: wait_for_url(f"http://127.0.0.1:{API_PORT}/health"))
    story = timed(
        "story seeded",
        lambda: seed_story(env, accounts, f"http://127.0.0.1:{API_PORT}"),
    )
    timed(
        "web client build",
        lambda: subprocess.run(
            ["npm", "run", "build", "--workspace", "apps/client"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=service_env,
        ),
    )
    state["client_pid"] = start_detached(
        "client",
        ["npx", "vinext", "start", "-H", bind, "-p", CLIENT_PORT],
        cwd=REPOSITORY_ROOT / "apps" / "client",
        env=service_env,
    )
    write_state(state)
    timed(
        "web client ready",
        lambda: wait_for_url(f"http://127.0.0.1:{CLIENT_PORT}/sign-in"),
    )
    state["timings_seconds"] = timings
    write_state(state)
    print_summary(state, accounts, story)
    print()
    return command_check(args)


def command_reset(args: argparse.Namespace) -> int:
    state = read_state()
    try:
        wait_for_url(f"http://127.0.0.1:{API_PORT}/health", timeout_seconds=5)
    except RuntimeError:
        print("The API is not running. Use `python3 scripts/local_demo.py up`.")
        return 1
    started = time.perf_counter()
    env = supabase_status_env()
    service_env = {**os.environ.copy()}
    reset_database(env, service_env)
    accounts = create_people(env)
    story = seed_story(env, accounts, f"http://127.0.0.1:{API_PORT}")
    print(f"Reset in {round(time.perf_counter() - started, 1)} s.")
    print_summary(state or {"host": "127.0.0.1"}, accounts, story)
    return 0


def command_check(args: argparse.Namespace) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))

    try:
        env = supabase_status_env()
        check("Supabase stack running", True, env.get("API_URL", ""))
    except Exception as error:  # noqa: BLE001
        check("Supabase stack running", False, str(error)[:120])
        env = {}

    status, health = http("GET", f"http://127.0.0.1:{API_PORT}/health")
    check("API healthy", status == 200, f"status={status}")
    mode = (health or {}).get("mode")
    check("API says local demo mode", mode == "local_demo", f"mode={mode}")

    try:
        request = urllib.request.Request(f"http://127.0.0.1:{CLIENT_PORT}/sign-in")
        with urllib.request.urlopen(request, timeout=10) as response:
            csp = response.headers.get("content-security-policy", "")
            check(
                "Web client serving with its security policy",
                response.status == 200 and "frame-ancestors 'none'" in csp,
                f"status={response.status}",
            )
    except (urllib.error.URLError, OSError) as error:
        check("Web client serving with its security policy", False, str(error)[:120])

    accounts: dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    signed_in = []
    if env and accounts:
        for key, person in accounts.get("people", {}).items():
            try:
                sign_in(env, person["email"], accounts["password"])
                signed_in.append(key)
            except Exception:  # noqa: BLE001
                pass
    check(
        "Every demo account signs in",
        bool(accounts) and len(signed_in) == len(PEOPLE),
        f"{len(signed_in)}/{len(PEOPLE)}",
    )

    story: dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        story = json.loads(STORY_PATH.read_text(encoding="utf-8"))
    if env and story:
        import psycopg  # noqa: PLC0415

        with psycopg.connect(env["DB_URL"]) as connection:
            row = connection.execute(
                """
                select t.status::text, rp.status::text
                from public.trips t join public.route_plans rp on rp.id = t.route_plan_id
                where t.id = %s::uuid
                """,
                (story["trip_id"],),
            ).fetchone()
        check(
            "Story at its start: trip running on an approved route",
            row == ("active", "approved"),
            f"trip,plan={row}",
        )
        with psycopg.connect(env["DB_URL"]) as connection:
            scored = connection.execute(
                "select count(*) from public.segment_current_state where risk_score is not null"
            ).fetchone()[0]
            modes = [
                row[0]
                for row in connection.execute(
                    "select distinct source_mode::text from public.source_runs"
                ).fetchall()
            ]
            newest = connection.execute(
                "select max(started_at) from public.source_runs "
                "where source_mode = 'recorded' and status in ('success', 'unchanged')"
            ).fetchone()[0]
        # A warning older than 6 hours is missing to the risk engine; reset within that
        # window before presenting, or the map loses its risk layer.
        age_hours = (
            (datetime.now(UTC) - newest).total_seconds() / 3600 if newest else None
        )
        check(
            "Recorded sources ran recently and roads are risk-scored",
            scored > 0
            and "live" not in modes
            and age_hours is not None
            and age_hours < 6,
            f"scored={scored} modes={modes} last_run="
            f"{'never' if age_hours is None else f'{age_hours:.1f} h ago'}",
        )
    else:
        check(
            "Story at its start: trip running on an approved route",
            False,
            "no story seeded; run reset",
        )

    pack = REPOSITORY_ROOT / "apps" / "client" / "public" / "packs" / "manifest.json"
    check(
        "Offline data pack built", pack.exists(), str(pack.relative_to(REPOSITORY_ROOT))
    )

    dist = REPOSITORY_ROOT / "apps" / "client" / "dist"
    leaked = []
    secret = env.get("SERVICE_ROLE_KEY", "")
    if dist.exists() and secret:
        for path in dist.rglob("*"):
            if path.is_file() and path.suffix in (
                ".js",
                ".html",
                ".json",
                ".css",
                ".map",
            ):
                text = path.read_text(encoding="utf-8", errors="ignore")
                # The key itself, or any secret-format key. The bare prefix is
                # not enough: supabase-js checks key prefixes in its own code.
                if secret in text or re.search(r"sb_secret_[A-Za-z0-9_-]{16,}", text):
                    leaked.append(str(path.relative_to(REPOSITORY_ROOT)))
    check(
        "No server key in the web client build",
        dist.exists() and not leaked,
        f"{len(leaked)} files" if leaked else "clean",
    )

    scan = subprocess.run(
        [sys.executable, "scripts/tools/scan_secrets.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    check(
        "Repository secret scan",
        scan.returncode == 0,
        scan.stdout.strip().splitlines()[-1] if scan.stdout.strip() else "",
    )

    print("Pre-demo checks:")
    for name, passed, detail in results:
        print(
            f"  [{'PASS' if passed else 'FAIL'}] {name}{': ' + detail if detail else ''}"
        )
    print("  [TODO] Android APK installed on the demo phone (not checkable from here)")
    failed = [name for name, passed, _ in results if not passed]
    print(f"{'Ready.' if not failed else 'NOT READY: ' + ', '.join(failed)}")
    return 0 if not failed else 1


def _docker_names() -> dict[str, str]:
    project = "Rasta"
    config = REPOSITORY_ROOT / "supabase" / "config.toml"
    for line in config.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("project_id"):
            project = line.split("=", 1)[1].strip().strip('"')
    return {"db": f"supabase_db_{project}", "storage": f"supabase_storage_{project}"}


def command_backup(args: argparse.Namespace) -> int:
    names = _docker_names()
    target = BACKUP_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    # Data only: a restore goes into a database the migrations just built, so
    # the schema always matches the code that will read it.
    with (target / "data.sql").open("w", encoding="utf-8") as out:
        subprocess.run(
            # supabase_admin: the local stack's superuser, which can read auth.
            [
                "docker",
                "exec",
                names["db"],
                "pg_dump",
                "-U",
                "supabase_admin",
                "-d",
                "postgres",
                "--data-only",
                "--schema=public",
                "--schema=auth",
                "--schema=storage",
                "--no-owner",
                "--no-privileges",
            ],
            stdout=out,
            check=True,
        )
    # Evidence photos live in the storage service's file backend, not in Postgres.
    subprocess.run(
        ["docker", "cp", f"{names['storage']}:/mnt", str(target / "storage")],
        check=False,
        capture_output=True,
    )
    for name in ("accounts.json", "story.json"):
        if (DEMO_DIR / name).exists():
            shutil.copy2(DEMO_DIR / name, target / name)
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(
        f"Backed up to {target.relative_to(REPOSITORY_ROOT)} "
        f"({round(size / 1_048_576, 1)} MB) in {round(time.perf_counter() - started, 1)} s."
    )
    return 0


def command_restore(args: argparse.Namespace) -> int:
    source = Path(args.path).resolve()
    if not (source / "data.sql").exists():
        print(f"{source} is not a backup made by this script.")
        return 1
    names = _docker_names()
    started = time.perf_counter()
    # A clean schema from the migrations, emptied, then the backup's rows.
    run_command(["supabase", "db", "reset", "--no-seed"])
    sql = (source / "data.sql").read_text(encoding="utf-8")
    empty = (
        "do $$ declare r record; begin "
        "for r in select schemaname, tablename from pg_tables "
        "where schemaname in ('public', 'auth', 'storage') loop "
        "execute format('truncate table %I.%I cascade', r.schemaname, r.tablename); "
        "end loop; end $$;"
    )
    loaded = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            names["db"],
            "psql",
            "-U",
            "supabase_admin",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            # Triggers off while loading: rows come back exactly as they were,
            # append-only tables included, without re-running any side effect.
            "-c",
            "set session_replication_role = replica;",
            "-c",
            empty,
            "-f",
            "-",
            "-c",
            "set session_replication_role = origin;",
        ],
        input=sql,
        text=True,
        check=False,
        capture_output=True,
    )
    if loaded.returncode != 0:
        print(loaded.stderr.strip()[-800:])
        return 1
    if (source / "storage").exists():
        subprocess.run(
            ["docker", "cp", f"{source / 'storage'}/.", f"{names['storage']}:/mnt"],
            check=False,
            capture_output=True,
        )
    for name in ("accounts.json", "story.json"):
        if (source / name).exists():
            shutil.copy2(source / name, DEMO_DIR / name)
    print(f"Restored {source.name} in {round(time.perf_counter() - started, 1)} s.")
    return 0


def command_down(args: argparse.Namespace, *, quiet: bool = False) -> int:
    state = read_state()
    for key in ("client_pid", "api_pid"):
        pid = state.pop(key, None)
        if pid:
            stop_group(int(pid))
    write_state(state)
    if not quiet:
        print(
            "Stopped the API and web client. The Supabase stack keeps running "
            "(`supabase stop` stops it)."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="start everything and seed the story")
    up.add_argument(
        "--lan-ip",
        default="127.0.0.1",
        help="this machine's LAN address, so a phone can use the demo",
    )
    sub.add_parser("reset", help="back to the story's start")
    sub.add_parser("check", help="pre-demo checks")
    sub.add_parser("backup", help="database and evidence files")
    restore = sub.add_parser("restore", help="put a backup back")
    restore.add_argument("path")
    sub.add_parser("down", help="stop the API and the web client")
    args = parser.parse_args()
    os.environ["PATH"] = (
        f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    )
    return {
        "up": command_up,
        "reset": command_reset,
        "check": command_check,
        "backup": command_backup,
        "restore": command_restore,
        "down": command_down,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
