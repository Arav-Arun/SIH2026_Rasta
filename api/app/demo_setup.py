"""Load the Shillong pilot and the demo accounts into a RASTA database.

    api/.venv/bin/python -m app.demo_setup --password 'Choose-A-Demo-Password'
    api/.venv/bin/python -m app.demo_setup --password '...' --story

It uses the API's own settings (``api/.env`` or the environment): DATABASE_URL,
SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY. Point them at
a hosted project to set up the hosted demo the same way. Every step can be run
again: the network is upserted with fixed ids and the accounts get the new
password.

1. **Pilot network.** The 2,860 OpenStreetMap road segments, 23 bridges and 30
   facilities of central Shillong (``data/pilot``), stored as recorded data
   with every road's passability ``unknown`` until someone reports on it.
2. **Demo accounts.** ``dispatcher@``, ``officer@``, ``driver@`` and
   ``admin@demo.rasta.test``, each with one role in East Khasi Hills. Give the
   web client the same password as NEXT_PUBLIC_DEMO_PASSWORD and its sign-in
   page offers one-click demo buttons.
3. **The story, with --story.** The recorded weather samples are re-dated and
   scored, then a medicine consignment is planned from a hospital to a clinic,
   its trip is given an approved route and the driver starts it. That is where
   the demo begins: a field officer reports a landslide on that route. A demo
   trip left over from an earlier run is cancelled first. These steps go
   through the API's own endpoints, in process, so they follow the same rules
   and leave the same audit trail as the app does.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import secrets
import sys
import tempfile
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PILOT_DIR = REPOSITORY_ROOT / "data" / "pilot"

#: The organisation supabase/seed.sql creates.
ORGANIZATION_ID = "a2600002-0000-4000-8000-000000000001"
#: Ids are UUIDv5 under this namespace, so a second import updates rows in place.
ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT = {
    "state_code": "ML",
    "code": "EAST-KHASI-HILLS",
    "name": "East Khasi Hills",
}

DEMO_PEOPLE = (
    ("dispatcher", "district_dispatcher", "Demo dispatcher (East Khasi Hills)"),
    ("officer", "field_officer", "Demo field officer"),
    ("driver", "driver", "Demo driver"),
    ("admin", "admin", "Demo administrator"),
)
DEMO_VEHICLE = "DEMO-ML-05"
FINISHED_TRIPS = ("completed", "failed", "cancelled")


def deterministic_id(*parts: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, ":".join(parts)))


def pilot_district_id() -> str:
    return deterministic_id(
        "district", PILOT_DISTRICT["state_code"], PILOT_DISTRICT["code"]
    )


def demo_email(key: str) -> str:
    return f"{key}@demo.rasta.test"


# --------------------------------------------------------------------------- #
# 1. Pilot network                                                             #
# --------------------------------------------------------------------------- #


def _linestring(coordinates: list[list[float]]) -> str:
    return (
        "SRID=4326;LINESTRING("
        + ", ".join(f"{lon!r} {lat!r}" for lon, lat in coordinates)
        + ")"
    )


def ensure_organization(connection: psycopg.Connection, mode: str) -> None:
    """The demo organisation, if this database does not have it yet."""

    connection.execute(
        """
        insert into public.organizations (id, name, mode)
        values (%s, 'RASTA demonstration', %s)
        on conflict (id) do nothing
        """,
        (ORGANIZATION_ID, mode),
    )


def import_network(
    connection: psycopg.Connection, data_dir: Path = PILOT_DIR
) -> dict[str, Any]:
    graph = json.loads((data_dir / "shillong_graph.json").read_text(encoding="utf-8"))
    facilities = json.loads(
        (data_dir / "shillong_facilities.json").read_text(encoding="utf-8")
    )
    version = str(graph["graph_version"])
    if facilities.get("graph_version") != version:
        raise RuntimeError(
            "The facilities file was made for a different graph version."
        )

    source = graph["source"]
    baseline_as_of = datetime.fromisoformat(
        source["osm_base_timestamp"].replace("Z", "+00:00")
    )
    district_id = pilot_district_id()
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    licence = {
        "attribution": source.get("attribution"),
        "license": source.get("license"),
    }

    connection.execute(
        """
        insert into public.districts (id, state_code, code, name, boundary)
        values (%s::uuid, %s, %s, %s, null)
        on conflict (id) do update
        set state_code = excluded.state_code, code = excluded.code, name = excluded.name
        """,
        (
            district_id,
            PILOT_DISTRICT["state_code"],
            PILOT_DISTRICT["code"],
            PILOT_DISTRICT["name"],
        ),
    )
    connection.execute(
        """
        insert into public.organization_districts (organization_id, district_id, active)
        values (%s::uuid, %s::uuid, true)
        on conflict (organization_id, district_id) do update set active = true
        """,
        (ORGANIZATION_ID, district_id),
    )

    segments, states, bridges = [], [], []
    for edge in graph["edges"]:
        segment_id = deterministic_id("segment", version, edge["edge_id"])
        metadata = {
            "edge_id": edge["edge_id"],
            "source_segment_id": edge.get("segment_id"),
            "source_way_id": edge.get("source_way_id"),
            "source_direction": edge.get("source_direction"),
            "name": edge.get("name"),
            "base_speed_source": edge.get("base_speed_source"),
            "bridge": bool(edge.get("bridge")),
            "max_height_m": edge.get("max_height_m"),
            "width_m": edge.get("width_m"),
            **licence,
        }
        segments.append(
            (
                segment_id,
                ORGANIZATION_ID,
                district_id,
                edge["from_node"],
                edge["to_node"],
                _linestring(edge["geometry"]),
                edge["length_m"],
                edge["road_class"],
                edge.get("base_speed_kph"),
                edge.get("max_weight_t"),
                version,
                "recorded",
                version,
                Jsonb(metadata),
            )
        )
        states.append(
            (
                ORGANIZATION_ID,
                segment_id,
                "unknown",
                "unknown",
                None,
                baseline_as_of,
                Jsonb(
                    {
                        "baseline": "osm_import",
                        "source": source.get("name"),
                        "graph_version": version,
                        "passability_basis": "no observation recorded",
                    }
                ),
                version,
            )
        )
        if edge.get("bridge"):
            bridges.append(
                (
                    deterministic_id("bridge", version, edge["edge_id"]),
                    ORGANIZATION_ID,
                    segment_id,
                    edge.get("max_weight_t"),
                    "unknown",
                    f"osm-way-{edge.get('source_way_id')}",
                )
            )

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into public.road_segments (
              id, organization_id, district_id, from_node_id, to_node_id, geometry,
              length_m, road_class, base_speed_kph, max_weight_t, network_version,
              source_mode, source_ref, metadata
            )
            values (
              %s::uuid, %s::uuid, %s::uuid, %s, %s, extensions.st_geomfromewkt(%s),
              %s, %s, %s, %s, %s, %s::public.data_mode, %s, %s
            )
            on conflict (id) do update
            set district_id = excluded.district_id, from_node_id = excluded.from_node_id,
                to_node_id = excluded.to_node_id, geometry = excluded.geometry,
                length_m = excluded.length_m, road_class = excluded.road_class,
                base_speed_kph = excluded.base_speed_kph, max_weight_t = excluded.max_weight_t,
                network_version = excluded.network_version, source_mode = excluded.source_mode,
                source_ref = excluded.source_ref, metadata = excluded.metadata
            """,
            segments,
        )
        cursor.executemany(
            """
            insert into public.bridges (id, organization_id, segment_id, max_weight_t, status, source_id)
            values (%s::uuid, %s::uuid, %s::uuid, %s, %s::public.road_passability, %s)
            on conflict (id) do update
            set segment_id = excluded.segment_id, max_weight_t = excluded.max_weight_t,
                source_id = excluded.source_id
            """,
            bridges,
        )
        # Only a road nobody has reported on is reset: a re-import never
        # overwrites an observed state.
        cursor.executemany(
            """
            insert into public.segment_current_state (
              organization_id, segment_id, passability, risk_level, risk_score,
              as_of, source_summary, network_version
            )
            values (
              %s::uuid, %s::uuid, %s::public.road_passability, %s::public.risk_level,
              %s, %s, %s, %s
            )
            on conflict (organization_id, segment_id) do update
            set network_version = excluded.network_version, source_summary = excluded.source_summary
            where public.segment_current_state.passability = 'unknown'
              and public.segment_current_state.risk_level = 'unknown'
            """,
            states,
        )

        rows = []
        for facility in facilities["facilities"]:
            node = nodes.get(facility.get("routing_node_id") or "")
            name = (facility.get("name") or "").strip() or (
                f"Unnamed {facility['facility_type']} "
                f"(OSM {facility.get('source_type', 'feature')} {facility.get('source_id')})"
            )
            rows.append(
                (
                    deterministic_id("facility", version, facility["facility_id"]),
                    ORGANIZATION_ID,
                    district_id,
                    facility["facility_type"],
                    name,
                    f"SRID=4326;POINT({facility['longitude']!r} {facility['latitude']!r})",
                    True,
                    "recorded",
                    version,
                    Jsonb(
                        {
                            "source_facility_id": facility["facility_id"],
                            "name_source": "osm"
                            if (facility.get("name") or "").strip()
                            else "unnamed_in_source",
                            "source_id": facility.get("source_id"),
                            "source_type": facility.get("source_type"),
                            "routing_node_id": facility.get("routing_node_id"),
                            "routing_eligible": bool(facility.get("routing_eligible")),
                            "snap_distance_m": facility.get("snap_distance_m"),
                            "routing_node_coordinates": (
                                [node["longitude"], node["latitude"]] if node else None
                            ),
                            "supply_hub_candidate": facility["facility_type"]
                            == "warehouse",
                            **licence,
                        }
                    ),
                )
            )
        cursor.executemany(
            """
            insert into public.facilities (
              id, organization_id, district_id, type, name, location, active,
              source_mode, source_ref, metadata
            )
            values (
              %s::uuid, %s::uuid, %s::uuid, %s, %s, extensions.st_geomfromewkt(%s), %s,
              %s::public.data_mode, %s, %s
            )
            on conflict (id) do update
            set district_id = excluded.district_id, type = excluded.type, name = excluded.name,
                location = excluded.location, active = excluded.active,
                source_mode = excluded.source_mode, source_ref = excluded.source_ref,
                metadata = excluded.metadata
            """,
            rows,
        )
    return {
        "graph_version": version,
        "segments": len(segments),
        "bridges": len(bridges),
        "facilities": len(rows),
    }


# --------------------------------------------------------------------------- #
# 2. Demo accounts                                                             #
# --------------------------------------------------------------------------- #


def _admin_headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }


def _auth_user_id(settings: Settings, email: str, password: str) -> str:
    """Create the sign-in, or give an existing one this password."""

    base = settings.supabase_url.rstrip("/")
    headers = _admin_headers(settings)
    created = httpx.post(
        f"{base}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30.0,
    )
    if created.status_code < 300:
        return str(created.json()["id"])
    if created.status_code not in (400, 409, 422):
        created.raise_for_status()

    # Already registered: find it and set the password.
    page = 1
    while True:
        listing = httpx.get(
            f"{base}/auth/v1/admin/users",
            headers=headers,
            params={"page": page, "per_page": 200},
            timeout=30.0,
        )
        listing.raise_for_status()
        users = listing.json().get("users", [])
        match = next(
            (u for u in users if (u.get("email") or "").lower() == email), None
        )
        if match:
            updated = httpx.put(
                f"{base}/auth/v1/admin/users/{match['id']}",
                headers=headers,
                json={"password": password, "email_confirm": True},
                timeout=30.0,
            )
            updated.raise_for_status()
            return str(match["id"])
        if len(users) < 200:
            raise RuntimeError(
                f"Could not create or find the sign-in for {email}: {created.text}"
            )
        page += 1


def ensure_demo_accounts(
    settings: Settings, connection: psycopg.Connection, password: str
) -> dict[str, dict[str, str]]:
    district_id = pilot_district_id()
    people: dict[str, dict[str, str]] = {}
    for key, role, display_name in DEMO_PEOPLE:
        email = demo_email(key)
        user_id = _auth_user_id(settings, email, password)
        profile_id = connection.execute(
            """
            insert into public.profiles (id, user_id, organization_id, display_name, locale, active)
            values (%s::uuid, %s::uuid, %s::uuid, %s, 'en', true)
            on conflict (user_id) do update
            set display_name = excluded.display_name, active = true
            returning id::text as id
            """,
            (str(uuid.uuid4()), user_id, ORGANIZATION_ID, display_name),
        ).fetchone()["id"]
        # Organisation-wide roles carry no district (role_assignments_district_scope_check).
        grant_district = None if role in ("admin", "state_coordinator") else district_id
        existing = connection.execute(
            """
            select 1 from public.role_assignments
            where profile_id = %s::uuid and role = %s::public.app_role
              and district_id is not distinct from %s::uuid
              and (valid_to is null or valid_to > now())
            """,
            (profile_id, role, grant_district),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                insert into public.role_assignments (id, organization_id, profile_id, role, district_id)
                values (%s::uuid, %s::uuid, %s::uuid, %s::public.app_role, %s::uuid)
                """,
                (str(uuid.uuid4()), ORGANIZATION_ID, profile_id, role, grant_district),
            )
        people[key] = {"email": email, "role": role, "profile_id": profile_id}
    return people


# --------------------------------------------------------------------------- #
# 3. The story                                                                 #
# --------------------------------------------------------------------------- #


def _sign_in(settings: Settings, email: str, password: str) -> str:
    response = httpx.post(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": settings.supabase_publishable_key},
        json={"email": email, "password": password},
        timeout=30.0,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _distance_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    return math.hypot(
        (a["lon"] - b["lon"]) * 111.32 * math.cos(lat), (a["lat"] - b["lat"]) * 110.57
    )


def _pick_route(
    connection: psycopg.Connection, facilities: list[dict[str, Any]]
) -> tuple:
    """A hospital and a clinic 3-9 km apart with two routes that differ.

    Worked out with the planner itself, without saving anything, so the trip's
    route has a real alternative once a road on it closes.
    """

    from app.route_plans import pilot_cost_policy  # noqa: PLC0415
    from app.routing.core import VehicleProfile  # noqa: PLC0415
    from app.routing.graph import load_district_graph  # noqa: PLC0415
    from app.routing.planner import RouteRequest, Snapshot, plan_routes  # noqa: PLC0415

    graph = load_district_graph(
        connection, organization_id=ORGANIZATION_ID, district_id=pilot_district_id()
    )
    policy = pilot_cost_policy()
    snapshot = Snapshot(
        network_version=graph.network_version,
        risk_snapshot_version=graph.risk_snapshot_version,
        cost_policy_version=policy.version,
        graph_edge_count=len(graph.edges),
        computed_at=graph.loaded_at,
    )
    hospitals = [f for f in facilities if f["type"] == "hospital"]
    others = [f for f in facilities if f["type"] != "hospital"]
    pairs = sorted(
        ((o, d) for o in hospitals for d in others if 3 <= _distance_km(o, d) <= 9),
        key=lambda pair: (pair[0]["name"], pair[1]["name"]),
    )
    for origin, destination in pairs:
        result = plan_routes(
            graph.edges,
            RouteRequest(
                origin_node_id=origin["node"],
                destination_node_id=destination["node"],
                vehicle=VehicleProfile(vehicle_id="demo"),
                priority="critical",
            ),
            snapshot,
            policy,
        )
        if len(result.alternatives) >= 2:
            first, second = result.alternatives[0], result.alternatives[1]
            if set(first.segment_ids) - set(second.segment_ids):
                return origin, destination
    raise RuntimeError(
        "No hospital and clinic pair in the pilot has two different routes."
    )


def seed_story(settings: Settings, password: str) -> dict[str, Any]:
    warnings.filterwarnings("ignore", message=".*starlette.testclient.*")
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import create_app  # noqa: PLC0415
    from app.recorded_samples import redate_recorded_sources  # noqa: PLC0415
    from app.risk_pipeline import run_pipeline  # noqa: PLC0415

    district_id = pilot_district_id()
    dispatcher = {
        "Authorization": f"Bearer {_sign_in(settings, demo_email('dispatcher'), password)}"
    }
    driver = {
        "Authorization": f"Bearer {_sign_in(settings, demo_email('driver'), password)}"
    }

    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        # The recorded samples, re-dated so the risk engine does not treat them
        # as stale, still stored and shown as recorded.
        with tempfile.TemporaryDirectory() as sources:
            redate_recorded_sources(Path(sources))
            with connection.transaction():
                risk = run_pipeline(
                    connection,
                    organization_id=ORGANIZATION_ID,
                    district_id=district_id,
                    imd_base_url=None,
                    cap_base_url=None,
                    fixture_root=Path(sources),
                ).as_dict()

        facilities = connection.execute(
            """
            select id::text, name, type::text as type,
                   extensions.st_y(location::extensions.geometry) as lat,
                   extensions.st_x(location::extensions.geometry) as lon,
                   metadata->>'routing_node_id' as node
            from public.facilities
            where district_id = %s::uuid and active and metadata ? 'routing_node_id'
            order by name
            """,
            (district_id,),
        ).fetchall()
        vehicle = connection.execute(
            "select id::text as id from public.vehicles where organization_id = %s::uuid and registration_ref = %s",
            (ORGANIZATION_ID, DEMO_VEHICLE),
        ).fetchone()
        if vehicle is None:
            vehicle = connection.execute(
                """
                insert into public.vehicles (organization_id, registration_ref, class, capacity_kg, active, source_mode)
                values (%s::uuid, %s, 'light_truck', 2000, true, 'synthetic')
                returning id::text as id
                """,
                (ORGANIZATION_ID, DEMO_VEHICLE),
            ).fetchone()
        driver_profile = connection.execute(
            "select p.id::text as id from public.profiles p join auth.users u on u.id = p.user_id where u.email = %s",
            (demo_email("driver"),),
        ).fetchone()["id"]
        leftovers = connection.execute(
            """
            select id::text as id, status::text as status from public.trips
            where organization_id = %s::uuid and vehicle_id = %s::uuid and status::text <> all(%s)
            """,
            (ORGANIZATION_ID, vehicle["id"], list(FINISHED_TRIPS)),
        ).fetchall()
        connection.commit()
        origin, destination = _pick_route(connection, facilities)

    app = create_app(settings)
    # The app installs its request log as it is built; the seed's own calls
    # would only repeat what the summary below says.
    logging.getLogger("rasta.api").setLevel(logging.WARNING)
    with TestClient(app) as client:

        def post(
            path: str, headers: dict[str, str], body: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            response = client.post(
                f"/v1{path}",
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                json=body if body is not None else {},
            )
            if response.status_code >= 300:
                raise RuntimeError(
                    f"POST {path} answered {response.status_code}: {response.text}"
                )
            return response.json()

        # A demo trip from an earlier run is cancelled, so the driver has one trip.
        for trip in leftovers:
            if trip["status"] == "active":
                post(f"/trips/{trip['id']}/paused", dispatcher)
            post(f"/trips/{trip['id']}/cancelled", dispatcher)

        consignment = post(
            "/consignments",
            dispatcher,
            {
                "district_id": district_id,
                "reference": f"DEMO-MED-{datetime.now(UTC):%m%d}-{secrets.token_hex(2).upper()}",
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
        post(f"/consignments/{consignment['id']}/planned", dispatcher)
        trip = post(
            "/trips",
            dispatcher,
            {
                "consignment_id": consignment["id"],
                "vehicle_id": vehicle["id"],
                "driver_profile_id": driver_profile,
            },
        )["trip"]
        plan = post(
            "/route-plans",
            dispatcher,
            {
                "district_id": district_id,
                "origin_node_id": origin["node"],
                "destination_node_id": destination["node"],
                "vehicle_id": vehicle["id"],
                "priority": "critical",
                "requested_alternatives": 3,
                "trip_id": trip["id"],
            },
        )["plan"]
        chosen = plan["alternatives"][0]
        post(
            f"/route-plans/{plan['id']}/approve",
            dispatcher,
            {
                "alternative_id": chosen["id"],
                "expected_network_version": plan["network_version"],
            },
        )
        post(
            f"/trips/{trip['id']}/route-plan", dispatcher, {"route_plan_id": plan["id"]}
        )
        post(f"/trips/{trip['id']}/awaiting_driver", dispatcher)
        post(f"/trips/{trip['id']}/active", driver)

    # Where the landslide goes: a road on the approved route that the next
    # option avoids, so closing it leaves a choice.
    others = (
        set(plan["alternatives"][1]["segment_ids"])
        if len(plan["alternatives"]) > 1
        else set()
    )
    closable = [s for s in chosen["segment_ids"] if s not in others] or chosen[
        "segment_ids"
    ]
    segment_id = closable[len(closable) // 2]
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        spot = connection.execute(
            """
            select extensions.st_y(extensions.st_lineinterpolatepoint(geometry, 0.5)) as lat,
                   extensions.st_x(extensions.st_lineinterpolatepoint(geometry, 0.5)) as lon,
                   coalesce(metadata->>'name', 'an unnamed ' || road_class || ' road') as road
            from public.road_segments where id = %s::uuid
            """,
            (segment_id,),
        ).fetchone()
    return {
        "consignment": consignment["reference"],
        "from": origin["name"],
        "to": destination["name"],
        "trip_id": trip["id"],
        "route_plan_id": plan["id"],
        "alternatives": len(plan["alternatives"]),
        "risk": {
            "segments_scored": risk.get("segments_scored"),
            "runs": [r["status"] for r in risk.get("runs", [])],
        },
        "landslide": {
            "segment_id": segment_id,
            "road": spot["road"],
            "latitude": round(spot["lat"], 6),
            "longitude": round(spot["lon"], 6),
        },
        "retired_trips": len(leftovers),
    }


# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load the Shillong pilot and demo accounts."
    )
    parser.add_argument(
        "--password", help="password for every demo account (default: a new random one)"
    )
    parser.add_argument(
        "--story", action="store_true", help="also seed the demo story's starting point"
    )
    parser.add_argument(
        "--skip-network", action="store_true", help="the network is already loaded"
    )
    parser.add_argument(
        "--json", type=Path, help="also write the accounts and story to this file"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    missing = [
        name
        for name, value in (
            ("DATABASE_URL", settings.database_url),
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_SECRET_KEY", settings.supabase_secret_key),
            ("SUPABASE_PUBLISHABLE_KEY", settings.supabase_publishable_key),
        )
        if not value
    ]
    if missing:
        print(
            f"Set {', '.join(missing)} in api/.env or the environment first.",
            file=sys.stderr,
        )
        return 2
    if settings.app_mode not in ("local_demo", "hosted_demo"):
        # Demo accounts share one password and the story writes trips: neither
        # belongs in a deployment that holds real operations.
        print(
            f"APP_MODE is {settings.app_mode}; demo data is only loaded into a "
            "local_demo or hosted_demo database.",
            file=sys.stderr,
        )
        return 2
    password = args.password or f"Demo-{secrets.token_urlsafe(9)}"

    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        ensure_organization(connection, settings.app_mode)
        if not args.skip_network:
            network = import_network(connection)
            print(
                f"Pilot network: {network['segments']} road segments, {network['bridges']} bridges, "
                f"{network['facilities']} facilities ({network['graph_version']})."
            )
        people = ensure_demo_accounts(settings, connection, password)
        connection.commit()

    print("Demo accounts (one password for all):")
    for person in people.values():
        print(f"  {person['email']:<28} {person['role']}")
    print(f"  password: {password}")
    print(
        "  Set NEXT_PUBLIC_DEMO_PASSWORD to the same value for the one-click demo sign-in."
    )

    story: dict[str, Any] | None = None
    if args.story:
        story = seed_story(settings, password)
        slide = story["landslide"]
        print(
            f"Story: {story['consignment']} from {story['from']} to {story['to']}, trip running with "
            f"the driver on an approved route ({story['alternatives']} options were planned)."
        )
        print(
            f"  Report the landslide on {slide['road']} at {slide['latitude']}, {slide['longitude']}."
        )
        print(
            f"  Risk: {story['risk']['segments_scored']} roads scored from the recorded samples."
        )
    if args.json:
        args.json.write_text(
            json.dumps(
                {"password": password, "people": people, "story": story}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
