#!/usr/bin/env python3
"""Load the validated OSM pilot network into the operational database."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PILOT_DIR = REPOSITORY_ROOT / "data" / "pilot"
GRAPH_PATH = PILOT_DIR / "shillong_graph.json"
FACILITIES_PATH = PILOT_DIR / "shillong_facilities.json"

DEFAULT_ORGANIZATION_ID = "a2600002-0000-4000-8000-000000000001"

# The pilot geography is central Shillong, which lies in East Khasi Hills district of
# Meghalaya.
PILOT_DISTRICT = {
    "state_code": "ML",
    "code": "EAST-KHASI-HILLS",
    "name": "East Khasi Hills",
}

ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")


def deterministic_id(*parts: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, ":".join(parts)))


def pilot_district_id() -> str:
    return deterministic_id(
        "district", PILOT_DISTRICT["state_code"], PILOT_DISTRICT["code"]
    )


def supabase_database_url() -> str:
    output = subprocess.check_output(
        ["supabase", "status", "-o", "env"],
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    for line in output.splitlines():
        if line.startswith("DB_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("DB_URL not found in `supabase status -o env` output")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def linestring_wkt(coordinates: list[list[float]]) -> str:
    points = ", ".join(f"{lon!r} {lat!r}" for lon, lat in coordinates)
    return f"SRID=4326;LINESTRING({points})"


def point_wkt(longitude: float, latitude: float) -> str:
    return f"SRID=4326;POINT({longitude!r} {latitude!r})"


def import_network(
    connection: psycopg.Connection[Any],
    *,
    organization_id: str,
    graph: dict[str, Any],
    facilities: dict[str, Any],
) -> dict[str, Any]:
    graph_version = str(graph["graph_version"])
    if facilities.get("graph_version") != graph_version:
        raise RuntimeError(
            "facilities file was generated for a different graph version: "
            f"{facilities.get('graph_version')} != {graph_version}"
        )

    source = graph["source"]
    baseline_as_of = datetime.fromisoformat(
        str(source["osm_base_timestamp"]).replace("Z", "+00:00")
    ).astimezone(UTC)
    district_id = pilot_district_id()
    nodes = {node["node_id"]: node for node in graph["nodes"]}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.districts (id, state_code, code, name, boundary)
            values (%s::uuid, %s, %s, %s, null)
            on conflict (id) do update
            set state_code = excluded.state_code,
                code = excluded.code,
                name = excluded.name
            """,
            (
                district_id,
                PILOT_DISTRICT["state_code"],
                PILOT_DISTRICT["code"],
                PILOT_DISTRICT["name"],
            ),
        )
        cursor.execute(
            """
            insert into public.organization_districts (organization_id, district_id, active)
            values (%s::uuid, %s::uuid, true)
            on conflict (organization_id, district_id) do update
            set active = true
            """,
            (organization_id, district_id),
        )

        segment_rows = []
        state_rows = []
        bridge_rows = []
        for edge in graph["edges"]:
            segment_id = deterministic_id("segment", graph_version, edge["edge_id"])
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
                "attribution": source.get("attribution"),
                "license": source.get("license"),
            }
            segment_rows.append(
                (
                    segment_id,
                    organization_id,
                    district_id,
                    edge["from_node"],
                    edge["to_node"],
                    linestring_wkt(edge["geometry"]),
                    edge["length_m"],
                    edge["road_class"],
                    edge.get("base_speed_kph"),
                    edge.get("max_weight_t"),
                    graph_version,
                    "recorded",
                    graph_version,
                    Jsonb(metadata),
                )
            )
            state_rows.append(
                (
                    organization_id,
                    segment_id,
                    "unknown",
                    "unknown",
                    None,
                    baseline_as_of,
                    Jsonb(
                        {
                            "baseline": "osm_import",
                            "source": source.get("name"),
                            "graph_version": graph_version,
                            "passability_basis": "no observation recorded",
                            "risk_basis": "risk engine not available",
                        }
                    ),
                    graph_version,
                )
            )
            if edge.get("bridge"):
                bridge_rows.append(
                    (
                        deterministic_id("bridge", graph_version, edge["edge_id"]),
                        organization_id,
                        segment_id,
                        edge.get("max_weight_t"),
                        "unknown",
                        f"osm-way-{edge.get('source_way_id')}",
                    )
                )

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
            set district_id = excluded.district_id,
                from_node_id = excluded.from_node_id,
                to_node_id = excluded.to_node_id,
                geometry = excluded.geometry,
                length_m = excluded.length_m,
                road_class = excluded.road_class,
                base_speed_kph = excluded.base_speed_kph,
                max_weight_t = excluded.max_weight_t,
                network_version = excluded.network_version,
                source_mode = excluded.source_mode,
                source_ref = excluded.source_ref,
                metadata = excluded.metadata
            """,
            segment_rows,
        )
        cursor.executemany(
            """
            insert into public.bridges (
              id, organization_id, segment_id, max_weight_t, status, source_id
            )
            values (%s::uuid, %s::uuid, %s::uuid, %s, %s::public.road_passability, %s)
            on conflict (id) do update
            set segment_id = excluded.segment_id,
                max_weight_t = excluded.max_weight_t,
                source_id = excluded.source_id
            """,
            bridge_rows,
        )
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
            set network_version = excluded.network_version,
                source_summary = excluded.source_summary
            where public.segment_current_state.passability = 'unknown'
              and public.segment_current_state.risk_level = 'unknown'
            """,
            state_rows,
        )

        facility_rows = []
        for facility in facilities["facilities"]:
            routing_node = nodes.get(facility.get("routing_node_id") or "")
            source_name = (facility.get("name") or "").strip()
            # OSM sometimes has no name tag. Say so instead of inventing one.
            name = source_name or (
                f"Unnamed {facility['facility_type']} "
                f"(OSM {facility.get('source_type', 'feature')} {facility.get('source_id')})"
            )
            facility_rows.append(
                (
                    deterministic_id(
                        "facility", graph_version, facility["facility_id"]
                    ),
                    organization_id,
                    district_id,
                    facility["facility_type"],
                    name,
                    point_wkt(facility["longitude"], facility["latitude"]),
                    True,
                    "recorded",
                    graph_version,
                    Jsonb(
                        {
                            "source_facility_id": facility["facility_id"],
                            "name_source": "osm"
                            if source_name
                            else "unnamed_in_source",
                            "source_id": facility.get("source_id"),
                            "source_type": facility.get("source_type"),
                            "routing_node_id": facility.get("routing_node_id"),
                            "routing_eligible": bool(facility.get("routing_eligible")),
                            "snap_distance_m": facility.get("snap_distance_m"),
                            "routing_node_coordinates": (
                                [routing_node["longitude"], routing_node["latitude"]]
                                if routing_node
                                else None
                            ),
                            # A warehouse in the source is the only candidate
                            # supply origin; the flag is explicit so a
                            # dispatcher can review it rather than infer it.
                            "supply_hub_candidate": facility["facility_type"]
                            == "warehouse",
                            "attribution": source.get("attribution"),
                            "license": source.get("license"),
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
            set district_id = excluded.district_id,
                type = excluded.type,
                name = excluded.name,
                location = excluded.location,
                active = excluded.active,
                source_mode = excluded.source_mode,
                source_ref = excluded.source_ref,
                metadata = excluded.metadata
            """,
            facility_rows,
        )

    return {
        "graph_version": graph_version,
        "organization_id": organization_id,
        "district_id": district_id,
        "district": PILOT_DISTRICT,
        "segments": len(segment_rows),
        "bridges": len(bridge_rows),
        "facilities": len(facility_rows),
        "baseline_as_of": baseline_as_of.isoformat(),
        "source": source,
        "imported_at": datetime.now(UTC).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Defaults to the local Supabase DB_URL")
    parser.add_argument("--organization-id", default=DEFAULT_ORGANIZATION_ID)
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args(argv)

    database_url = args.database_url or supabase_database_url()
    graph = load_json(GRAPH_PATH)
    facilities = load_json(FACILITIES_PATH)

    with psycopg.connect(database_url) as connection:
        summary = import_network(
            connection,
            organization_id=args.organization_id,
            graph=graph,
            facilities=facilities,
        )
        connection.commit()

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
