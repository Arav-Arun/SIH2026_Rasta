#!/usr/bin/env python3
"""Generate deterministic, explicitly synthetic SIH routing scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SEED = 26002
SIMULATION_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
GENERATOR_VERSION = "scenario-generator-v1"
UI_LABEL = "SIMULATED SCENARIO"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--graph",
        type=Path,
        default=Path("data/pilot/shillong_graph.json"),
    )
    parser.add_argument(
        "--facilities",
        type=Path,
        default=Path("data/pilot/shillong_facilities.json"),
    )
    parser.add_argument(
        "--endpoints",
        type=Path,
        default=Path("data/pilot/scenario_endpoints.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/fixtures/synthetic_scenarios.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/synthetic_scenarios.json"),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def at_offset(seconds: int) -> str:
    return (
        (SIMULATION_EPOCH + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


def physical_segments(
    graph: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for edge in graph["edges"]:
        segment_id = edge["segment_id"]
        endpoints = tuple(sorted((edge["from_node"], edge["to_node"])))
        prior = result.setdefault(segment_id, endpoints)
        if prior != endpoints:
            raise ValueError(f"Segment has inconsistent endpoints: {segment_id}")
    return result


def find_bridges(
    segments: dict[str, tuple[str, str]],
) -> set[str]:
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for segment_id, (left, right) in segments.items():
        adjacency[left].append((right, segment_id))
        adjacency[right].append((left, segment_id))
    for neighbors in adjacency.values():
        neighbors.sort()

    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    bridges: set[str] = set()
    clock = 0

    def visit(node: str, parent_segment: str | None) -> None:
        nonlocal clock
        clock += 1
        discovery[node] = clock
        low[node] = clock
        for neighbor, segment_id in adjacency[node]:
            if segment_id == parent_segment:
                continue
            if neighbor not in discovery:
                visit(neighbor, segment_id)
                low[node] = min(low[node], low[neighbor])
                if low[neighbor] > discovery[node]:
                    bridges.add(segment_id)
            else:
                low[node] = min(low[node], discovery[neighbor])

    for node in sorted(adjacency):
        if node not in discovery:
            visit(node, None)
    return bridges


def reachable_nodes(
    graph: dict[str, Any],
    origin: str,
    blocked_segments: set[str] | None = None,
) -> set[str]:
    blocked_segments = blocked_segments or set()
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["segment_id"] not in blocked_segments:
            adjacency[edge["from_node"]].append(edge["to_node"])
    reached = {origin}
    queue = deque([origin])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency.get(node, []):
            if neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)
    return reached


def select_isolation_case(
    graph: dict[str, Any],
    facilities: list[dict[str, Any]],
    origin_node: str,
) -> tuple[str, dict[str, Any], int]:
    segments = physical_segments(graph)
    bridges = find_bridges(segments)
    all_nodes = {node["node_id"] for node in graph["nodes"]}
    choices: list[tuple[int, str, str, dict[str, Any]]] = []
    for segment_id in sorted(bridges):
        reached = reachable_nodes(graph, origin_node, {segment_id})
        disconnected = all_nodes - reached
        if not disconnected:
            continue
        affected = [
            facility
            for facility in facilities
            if facility["routing_eligible"]
            and facility["name"]
            and facility["routing_node_id"] in disconnected
        ]
        for facility in affected:
            choices.append(
                (len(disconnected), facility["facility_id"], segment_id, facility)
            )
    if not choices:
        raise ValueError("No sourced facility can be isolated by one graph bridge")
    disconnected_count, _, segment_id, facility = min(choices)
    return segment_id, facility, disconnected_count


def base_route_request(
    origin_node: str, destination_node: str, vehicle_id: str
) -> dict[str, Any]:
    return {
        "origin_node_id": origin_node,
        "destination_node_id": destination_node,
        "vehicle_id": vehicle_id,
        "commodity": {
            "category": "medicine",
            "priority": "essential",
            "weight_kg": 420,
            "synthetic": True,
        },
        "departure_at": at_offset(300),
        "deadline_at": at_offset(10_800),
    }


def main() -> None:
    args = parse_args()
    graph = load_json(args.graph)
    facilities_doc = load_json(args.facilities)
    endpoint_doc = load_json(args.endpoints)
    if graph["graph_version"] != endpoint_doc["graph_version"]:
        raise SystemExit("Graph and endpoint versions do not match")

    rng = random.Random(SEED)
    common = endpoint_doc["common_facilities"]
    origin_node = common["origin"]["routing_node_id"]
    destination_node = common["destination"]["routing_node_id"]
    s2_segment = endpoint_doc["scenarios"]["S2"]["candidate_closure_segment_id"]
    s3_segment = endpoint_doc["scenarios"]["S3"]["candidate_forecast_segment_id"]
    s6_segment = endpoint_doc["scenarios"]["S6"]["candidate_bridge_segment_id"]
    facilities = facilities_doc["facilities"]
    s4_segment, s4_facility, s4_disconnected_nodes = select_isolation_case(
        graph, facilities, origin_node
    )
    s2_destination_reachable = destination_node in reachable_nodes(
        graph, origin_node, {s2_segment}
    )

    node_by_id = {node["node_id"]: node for node in graph["nodes"]}
    reference_nodes = endpoint_doc["reference_path"]["node_ids"]
    telemetry_indices = sorted(
        {0, len(reference_nodes) // 4, len(reference_nodes) // 2}
    )
    telemetry_points = []
    for sequence, node_index in enumerate(telemetry_indices, start=1):
        node = node_by_id[reference_nodes[node_index]]
        telemetry_points.append(
            {
                "event_id": f"sim-position-{sequence:02d}",
                "recorded_at": at_offset(sequence * 120),
                "latitude": node["latitude"],
                "longitude": node["longitude"],
                "accuracy_m": round(rng.uniform(6, 14), 1),
                "source_mode": "synthetic",
            }
        )

    vehicles = {
        "SIM-LCV-01": {
            "vehicle_id": "SIM-LCV-01",
            "vehicle_class": "light_commercial",
            "gross_weight_t": 3.5,
            "capacity_kg": 1200,
            "synthetic": True,
        },
        "SIM-TRUCK-08T": {
            "vehicle_id": "SIM-TRUCK-08T",
            "vehicle_class": "medium_truck",
            "gross_weight_t": 8.0,
            "capacity_kg": 5000,
            "synthetic": True,
        },
    }
    network_policy = {
        "mode": "synthetic",
        "default_passability": "open",
        "road_class_speed_kph": {
            "motorway": 50,
            "trunk": 40,
            "primary": 35,
            "secondary": 30,
            "tertiary": 25,
            "unclassified": 20,
            "residential": 18,
            "service": 12,
        },
        "notice": (
            "Passability and fallback speeds are simulation assumptions. "
            "They do not describe current Shillong roads."
        ),
    }

    scenarios = [
        {
            "id": "S1",
            "title": "Normal medicine delivery",
            "mode": "synthetic",
            "ui_label": UI_LABEL,
            "route_request": base_route_request(
                origin_node, destination_node, "SIM-LCV-01"
            ),
            "overlays": {"segment_states": [], "risk_observations": []},
            "expected": {
                "route_status": "feasible",
                "closed_segments_used": 0,
                "claim_scope": "routing behavior only",
            },
        },
        {
            "id": "S2",
            "title": "Reviewed landslide closure",
            "mode": "synthetic",
            "ui_label": UI_LABEL,
            "route_request": base_route_request(
                origin_node, destination_node, "SIM-LCV-01"
            ),
            "overlays": {
                "segment_states": [
                    {
                        "segment_id": s2_segment,
                        "passability": "closed",
                        "cause": "landslide_debris",
                        "review_status": "approved",
                        "observed_at": at_offset(600),
                        "source_mode": "synthetic",
                    }
                ],
                "risk_observations": [],
            },
            "expected": {
                "route_status": (
                    "feasible_replan" if s2_destination_reachable else "no_route"
                ),
                "excluded_segment_ids": [s2_segment],
                "claim_scope": "simulated closure handling",
            },
        },
        {
            "id": "S3",
            "title": "Forecast-only heavy rainfall",
            "mode": "synthetic",
            "ui_label": UI_LABEL,
            "route_request": base_route_request(
                origin_node, destination_node, "SIM-LCV-01"
            ),
            "overlays": {
                "segment_states": [],
                "risk_observations": [
                    {
                        "segment_id": s3_segment,
                        "observation_type": "forecast_rainfall",
                        "rainfall_intensity_mm_h": 45,
                        "warning_severity": "orange",
                        "valid_from": at_offset(900),
                        "valid_until": at_offset(22_500),
                        "source_name": "SIMULATED_WEATHER_ADAPTER",
                        "source_mode": "synthetic",
                    }
                ],
            },
            "expected": {
                "passability_change": False,
                "future_risk_visible": True,
                "claim_scope": "risk rendering, not a closure prediction",
            },
        },
        {
            "id": "S4",
            "title": "Closure isolates a monitored facility",
            "mode": "synthetic",
            "ui_label": UI_LABEL,
            "route_request": base_route_request(
                origin_node, s4_facility["routing_node_id"], "SIM-LCV-01"
            ),
            "overlays": {
                "segment_states": [
                    {
                        "segment_id": s4_segment,
                        "passability": "closed",
                        "cause": "flooding",
                        "review_status": "approved",
                        "observed_at": at_offset(600),
                        "source_mode": "synthetic",
                    }
                ],
                "risk_observations": [],
            },
            "expected": {
                "facility_id": s4_facility["facility_id"],
                "facility_name": s4_facility["name"],
                "reachability": "isolated",
                "disconnected_graph_nodes": s4_disconnected_nodes,
                "claim_scope": "simulated graph reachability",
            },
        },
        {
            "id": "S5",
            "title": "Vehicle temporarily stops reporting",
            "mode": "synthetic",
            "ui_label": UI_LABEL,
            "route_request": base_route_request(
                origin_node, destination_node, "SIM-LCV-01"
            ),
            "telemetry": {
                "vehicle_id": "SIM-LCV-01",
                "points": telemetry_points,
                "offline_after": at_offset(480),
                "stale_after_seconds": 600,
                "buffered_upload_event_ids": [
                    telemetry_points[-1]["event_id"],
                    telemetry_points[-1]["event_id"],
                ],
            },
            "overlays": {"segment_states": [], "risk_observations": []},
            "expected": {
                "vehicle_state_after_timeout": "stale",
                "buffered_upload_unique_inserts": 1,
                "claim_scope": "offline queue and idempotency behavior",
            },
        },
        {
            "id": "S6",
            "title": "Vehicle exceeds a simulated bridge limit",
            "mode": "synthetic",
            "ui_label": UI_LABEL,
            "route_request": {
                **base_route_request(
                    endpoint_doc["scenarios"]["S6"]["bridge_from_node_id"],
                    endpoint_doc["scenarios"]["S6"]["bridge_to_node_id"],
                    "SIM-TRUCK-08T",
                ),
            },
            "overlays": {
                "segment_states": [],
                "risk_observations": [],
                "segment_constraints": [
                    {
                        "segment_id": s6_segment,
                        "max_weight_t": 5.0,
                        "source_mode": "synthetic",
                        "notice": (
                            "Test-only constraint because the OSM source limit is unknown."
                        ),
                    }
                ],
            },
            "expected": {
                "route_uses_constrained_segment": False,
                "reason_code": "vehicle_exceeds_max_weight",
                "claim_scope": "constraint enforcement only",
            },
        },
    ]

    fixture = {
        "schema_version": "synthetic-scenarios-v1",
        "fixture_set_id": "sih26002-core-scenarios-v1",
        "generator_version": GENERATOR_VERSION,
        "seed": SEED,
        "mode": "synthetic",
        "ui_label": UI_LABEL,
        "simulation_epoch": at_offset(0),
        "notice": (
            "Every event, vehicle, consignment, weather value, closure, and "
            "constraint in this file is synthetic. OSM supplies topology and "
            "facility references only."
        ),
        "graph_version": graph["graph_version"],
        "network_policy": network_policy,
        "vehicles": vehicles,
        "scenarios": scenarios,
    }
    manifest = {
        "schema_version": "fixture-manifest-v1",
        "fixture_set_id": fixture["fixture_set_id"],
        "mode": "synthetic",
        "ui_label": UI_LABEL,
        "seed": SEED,
        "generator": "scripts/pipeline/generate_scenarios.py",
        "generator_version": GENERATOR_VERSION,
        "graph_version": graph["graph_version"],
        "input_checksums": {
            str(args.graph): "sha256:" + sha256_value(graph),
            str(args.facilities): "sha256:" + sha256_value(facilities_doc),
            str(args.endpoints): "sha256:" + sha256_value(endpoint_doc),
        },
        "output": str(args.output),
        "output_checksum": "sha256:" + sha256_value(fixture),
        "generated_at": at_offset(0),
        "live_data": False,
    }
    write_json(args.output, fixture)
    write_json(args.manifest, manifest)
    print(
        json.dumps(
            {
                "fixture_set_id": fixture["fixture_set_id"],
                "graph_version": graph["graph_version"],
                "scenario_ids": [scenario["id"] for scenario in scenarios],
                "s4_isolated_facility": s4_facility["facility_id"],
                "s4_closure_segment": s4_segment,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
