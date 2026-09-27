#!/usr/bin/env python3
"""Validate the immutable pilot graph, provenance, and scenario endpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


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
        "--manifest",
        type=Path,
        default=Path("data/manifests/osm_shillong_pilot.json"),
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=Path("data/sources/osm_shillong_pilot.raw.json"),
    )
    parser.add_argument(
        "--query",
        type=Path,
        default=Path("data/sources/osm_shillong_pilot.overpassql"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/reports/graph_validation.json"),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def main() -> None:
    args = parse_args()
    graph = load_json(args.graph)
    facilities_doc = load_json(args.facilities)
    endpoints = load_json(args.endpoints)
    manifest = load_json(args.manifest)
    errors: list[str] = []
    warnings: list[str] = []

    if graph.get("schema_version") != "routing-graph-v1":
        errors.append("Unexpected graph schema version")
    graph_version = graph.get("graph_version")
    if not isinstance(graph_version, str) or not graph_version:
        errors.append("Graph version is missing")
    if graph.get("operational_status") != "not_live":
        errors.append("Pilot graph must not claim live operational status")
    if graph.get("source", {}).get("license") != "ODbL-1.0":
        errors.append("Graph does not identify the ODbL license")
    if graph.get("source", {}).get("attribution") != "© OpenStreetMap contributors":
        errors.append("Required OpenStreetMap attribution is missing")

    raw_sha = sha256_bytes(args.raw.read_bytes())
    query_sha = sha256_bytes(args.query.read_bytes())
    if manifest.get("raw_checksum") != "sha256:" + raw_sha:
        errors.append("Raw source checksum does not match manifest")
    if manifest.get("query_checksum") != "sha256:" + query_sha:
        errors.append("Query checksum does not match manifest")
    if graph.get("source", {}).get("raw_sha256") != raw_sha:
        errors.append("Graph raw checksum does not match source bytes")

    expected_outputs = manifest.get("outputs", {})
    output_values = {
        str(args.graph): graph,
        str(args.facilities): facilities_doc,
        str(args.endpoints): endpoints,
    }
    for path, value in output_values.items():
        expected = expected_outputs.get(path)
        actual = "sha256:" + canonical_sha(value)
        if expected != actual:
            errors.append(f"Canonical output checksum mismatch: {path}")

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes or not edges:
        errors.append("Graph contains no nodes or edges")
    node_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("node_id")
        if node_id in node_by_id:
            errors.append(f"Duplicate node id: {node_id}")
            continue
        node_by_id[node_id] = node
        latitude = node.get("latitude")
        longitude = node.get("longitude")
        if not isinstance(latitude, (int, float)) or not -90 <= latitude <= 90:
            errors.append(f"Invalid latitude: {node_id}")
        if not isinstance(longitude, (int, float)) or not -180 <= longitude <= 180:
            errors.append(f"Invalid longitude: {node_id}")
        if node.get("source") != "OpenStreetMap":
            errors.append(f"Unexpected node source: {node_id}")

    edge_ids: set[str] = set()
    segment_ids: set[str] = set()
    adjacency: dict[str, set[str]] = defaultdict(set)
    directed_edges: dict[str, tuple[str, str]] = {}
    path_edge_lengths: dict[str, float] = {}
    missing_speeds = 0
    bridge_edges = 0
    bridge_edges_without_weight = 0
    lengths: list[float] = []
    for edge in edges:
        edge_id = edge.get("edge_id")
        if edge_id in edge_ids:
            errors.append(f"Duplicate edge id: {edge_id}")
            continue
        edge_ids.add(edge_id)
        segment_ids.add(edge.get("segment_id"))
        source = edge.get("from_node")
        target = edge.get("to_node")
        if source not in node_by_id or target not in node_by_id:
            errors.append(f"Edge references a missing node: {edge_id}")
            continue
        if source == target:
            errors.append(f"Self-loop is not permitted: {edge_id}")
        adjacency[source].add(target)
        adjacency[target].add(source)
        directed_edges[edge_id] = (source, target)
        length = edge.get("length_m")
        if not isinstance(length, (int, float)) or not 0 < length < 100_000:
            errors.append(f"Implausible edge length: {edge_id}")
        else:
            lengths.append(float(length))
            path_edge_lengths[edge_id] = float(length)
        geometry = edge.get("geometry")
        if not isinstance(geometry, list) or len(geometry) < 2:
            errors.append(f"Missing edge geometry: {edge_id}")
        else:
            start = geometry[0]
            finish = geometry[-1]
            from_node = node_by_id[source]
            to_node = node_by_id[target]
            if start != [from_node["longitude"], from_node["latitude"]]:
                errors.append(f"Geometry start does not match from-node: {edge_id}")
            if finish != [to_node["longitude"], to_node["latitude"]]:
                errors.append(f"Geometry end does not match to-node: {edge_id}")
        if edge.get("passability") != "unknown" or edge.get("state_as_of") is not None:
            errors.append(f"Baseline edge invents a road state: {edge_id}")
        if edge.get("base_speed_kph") is None:
            missing_speeds += 1
        elif edge.get("base_speed_source") != "osm:maxspeed":
            errors.append(f"Edge speed lacks a source: {edge_id}")
        if edge.get("bridge"):
            bridge_edges += 1
            if edge.get("max_weight_t") is None:
                bridge_edges_without_weight += 1

    if adjacency:
        unseen = set(adjacency)
        components = 0
        while unseen:
            components += 1
            start = min(unseen)
            unseen.remove(start)
            queue = deque([start])
            while queue:
                current = queue.popleft()
                for neighbor in adjacency[current]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        queue.append(neighbor)
        if components != 1:
            errors.append(f"Retained routing graph has {components} weak components")
    else:
        components = 0

    counts = graph.get("counts", {})
    if counts.get("nodes") != len(nodes):
        errors.append("Stored node count does not match graph")
    if counts.get("directed_edges") != len(edges):
        errors.append("Stored edge count does not match graph")

    facilities = facilities_doc.get("facilities", [])
    facility_ids: set[str] = set()
    eligible_facilities = 0
    for facility in facilities:
        facility_id = facility.get("facility_id")
        if facility_id in facility_ids:
            errors.append(f"Duplicate facility id: {facility_id}")
        facility_ids.add(facility_id)
        if facility.get("source") != "OpenStreetMap":
            errors.append(f"Facility lacks OSM provenance: {facility_id}")
        routing_node = facility.get("routing_node_id")
        if routing_node not in node_by_id:
            errors.append(f"Facility snaps to a missing node: {facility_id}")
        if facility.get("routing_eligible"):
            eligible_facilities += 1
            if facility.get("snap_distance_m", 501) > 500:
                errors.append(
                    f"Eligible facility exceeds snap threshold: {facility_id}"
                )

    if facilities_doc.get("graph_version") != graph_version:
        errors.append("Facility document graph version mismatch")
    if endpoints.get("graph_version") != graph_version:
        errors.append("Scenario endpoint graph version mismatch")
    if endpoints.get("mode") != "endpoint_reference_only":
        errors.append("Scenario endpoints must not claim runnable fixture status")

    reference_path = endpoints.get("reference_path", {})
    reference_nodes = reference_path.get("node_ids", [])
    reference_edges = reference_path.get("edge_ids", [])
    if len(reference_nodes) != len(reference_edges) + 1:
        errors.append("Reference path node/edge cardinality is invalid")
    else:
        for index, edge_id in enumerate(reference_edges):
            if directed_edges.get(edge_id) != (
                reference_nodes[index],
                reference_nodes[index + 1],
            ):
                errors.append(f"Reference path is discontinuous at edge: {edge_id}")

    scenarios = endpoints.get("scenarios", {})
    expected_scenarios = {f"S{number}" for number in range(1, 7)}
    if set(scenarios) != expected_scenarios:
        errors.append("Scenario endpoint file must define exactly S1 through S6")
    for scenario_id, scenario in scenarios.items():
        if scenario.get("fixture_state") != "to_be_created_in_T005":
            errors.append(f"{scenario_id} incorrectly claims a built fixture")
        for key, value in scenario.items():
            if key.endswith("_node_id") and value not in node_by_id:
                errors.append(f"{scenario_id} references a missing node: {key}")
            if key.endswith("_segment_id") and value not in segment_ids:
                errors.append(f"{scenario_id} references a missing segment: {key}")

    warnings.append(
        f"{missing_speeds} directed edges have no sourced maxspeed; no default was invented."
    )
    warnings.append(
        f"{bridge_edges_without_weight} of {bridge_edges} bridge edges have unknown weight limits."
    )
    warnings.append(
        f"{len(facilities) - eligible_facilities} facilities exceed the 500 m routing snap threshold."
    )

    report = {
        "check": "validate_pilot_graph",
        "status": "passed" if not errors else "failed",
        "graph_version": graph_version,
        "checks": {
            "raw_sha256": raw_sha,
            "query_sha256": query_sha,
            "nodes": len(nodes),
            "directed_edges": len(edges),
            "weak_components": components,
            "facilities": len(facilities),
            "eligible_facilities": eligible_facilities,
            "reference_path_edges": len(reference_edges),
            "reference_path_length_m": round(
                sum(path_edge_lengths.get(edge_id, 0) for edge_id in reference_edges),
                3,
            ),
            "minimum_edge_length_m": min(lengths) if lengths else None,
            "maximum_edge_length_m": max(lengths) if lengths else None,
        },
        "errors": errors,
        "warnings": warnings,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
