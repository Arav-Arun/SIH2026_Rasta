#!/usr/bin/env python3
"""Build a bounded, versioned routing graph from a saved Overpass response."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
import re
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IMPORTER_VERSION = "osm-pilot-v1"
ALLOWED_HIGHWAYS = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "service",
}
ALLOWED_AMENITIES = {"hospital", "clinic", "pharmacy", "marketplace"}
QUERY_BBOX = {
    "south": 25.560,
    "west": 91.875,
    "north": 25.585,
    "east": 91.900,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/sources/osm_shillong_pilot.raw.json"),
    )
    parser.add_argument(
        "--query",
        type=Path,
        default=Path("data/sources/osm_shillong_pilot.overpassql"),
    )
    parser.add_argument(
        "--graph-output",
        type=Path,
        default=Path("data/pilot/shillong_graph.json"),
    )
    parser.add_argument(
        "--facilities-output",
        type=Path,
        default=Path("data/pilot/shillong_facilities.json"),
    )
    parser.add_argument(
        "--endpoints-output",
        type=Path,
        default=Path("data/pilot/scenario_endpoints.json"),
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=Path("data/manifests/osm_shillong_pilot.json"),
    )
    return parser.parse_args()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def inside_query_bbox(point: tuple[float, float]) -> bool:
    latitude, longitude = point
    return (
        QUERY_BBOX["south"] <= latitude <= QUERY_BBOX["north"]
        and QUERY_BBOX["west"] <= longitude <= QUERY_BBOX["east"]
    )


def parse_metric_number(raw: Any, unit: str) -> float | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if value in {"", "none", "unsigned", "default", "variable", "unknown"}:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([a-z/]*)", value)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if unit == "speed":
        return round(number * 1.609344, 3) if suffix == "mph" else number
    if unit == "weight":
        return round(number * 0.90718474, 3) if suffix in {"st", "shortton"} else number
    if unit == "length":
        if suffix in {"ft", "feet"}:
            return round(number * 0.3048, 3)
        return number
    return None


def way_direction(tags: dict[str, Any]) -> str:
    oneway = str(tags.get("oneway", "")).lower()
    if oneway == "-1":
        return "reverse"
    if oneway in {"yes", "true", "1"} or tags.get("junction") == "roundabout":
        return "forward"
    return "both"


def largest_component(
    edges: list[dict[str, Any]],
) -> tuple[set[str], int, list[int]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        source = edge["from_node"]
        target = edge["to_node"]
        adjacency[source].add(target)
        adjacency[target].add(source)

    components: list[set[str]] = []
    unseen = set(adjacency)
    while unseen:
        start = min(unseen)
        component: set[str] = set()
        queue = deque([start])
        unseen.remove(start)
        while queue:
            node = queue.popleft()
            component.add(node)
            for neighbor in adjacency[node]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(component)

    components.sort(key=lambda item: (-len(item), min(item)))
    return components[0], len(components), [len(item) for item in components[:10]]


def shortest_path(
    edges: list[dict[str, Any]], origin: str, destination: str
) -> tuple[list[str], list[str]] | None:
    adjacency: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["from_node"]].append(
            (edge["to_node"], edge["length_m"], edge["edge_id"])
        )
    for choices in adjacency.values():
        choices.sort()

    distance: dict[str, float] = {origin: 0.0}
    previous: dict[str, tuple[str, str]] = {}
    queue: list[tuple[float, str]] = [(0.0, origin)]
    while queue:
        current_distance, node = heapq.heappop(queue)
        if current_distance != distance.get(node):
            continue
        if node == destination:
            break
        for next_node, edge_length, edge_id in adjacency.get(node, []):
            candidate = current_distance + edge_length
            if candidate < distance.get(next_node, math.inf):
                distance[next_node] = candidate
                previous[next_node] = (node, edge_id)
                heapq.heappush(queue, (candidate, next_node))

    if destination not in distance:
        return None
    nodes = [destination]
    edge_ids: list[str] = []
    current = destination
    while current != origin:
        prior, edge_id = previous[current]
        nodes.append(prior)
        edge_ids.append(edge_id)
        current = prior
    nodes.reverse()
    edge_ids.reverse()
    return nodes, edge_ids


def facility_point(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if isinstance(center, dict) and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def main() -> None:
    args = parse_args()
    raw_bytes = args.input.read_bytes()
    query_bytes = args.query.read_bytes()
    payload = json.loads(raw_bytes)
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise SystemExit("Overpass response has no elements array")

    coordinates: dict[int, tuple[float, float]] = {}
    for element in elements:
        if element.get("type") == "node" and "lat" in element and "lon" in element:
            coordinates[int(element["id"])] = (
                float(element["lat"]),
                float(element["lon"]),
            )

    highway_elements = [
        element
        for element in elements
        if element.get("type") == "way"
        and (element.get("tags") or {}).get("highway") in ALLOWED_HIGHWAYS
    ]
    node_way_use: Counter[int] = Counter()
    for element in highway_elements:
        node_way_use.update(int(value) for value in element.get("nodes", []))

    raw_edges: list[dict[str, Any]] = []
    missing_coordinate_ways: list[int] = []
    skipped_zero_length = 0
    skipped_outside_bbox = 0
    highway_way_count = len(highway_elements)
    for element in highway_elements:
        tags = element.get("tags") or {}
        highway = tags.get("highway")
        way_nodes = [int(value) for value in element.get("nodes", [])]
        if len(way_nodes) < 2 or any(node not in coordinates for node in way_nodes):
            missing_coordinate_ways.append(int(element["id"]))
            continue

        direction = way_direction(tags)
        way_id = int(element["id"])
        split_nodes = {node for node in way_nodes if node_way_use[node] > 1} | {
            way_nodes[0],
            way_nodes[-1],
        }
        if way_nodes[0] == way_nodes[-1] and len(split_nodes) == 1:
            split_nodes.add(way_nodes[len(way_nodes) // 2])

        segments: list[list[int]] = []
        current_segment = [way_nodes[0]]
        for node in way_nodes[1:]:
            current_segment.append(node)
            if node in split_nodes:
                segments.append(current_segment)
                current_segment = [node]

        for index, segment_nodes in enumerate(segments):
            left = segment_nodes[0]
            right = segment_nodes[-1]
            if left == right:
                skipped_zero_length += 1
                continue
            segment_points = [coordinates[node] for node in segment_nodes]
            if not all(inside_query_bbox(point) for point in segment_points):
                skipped_outside_bbox += 1
                continue
            length_m = sum(
                haversine_m(point_a, point_b)
                for point_a, point_b in itertools.pairwise(segment_points)
            )
            if length_m < 0.01:
                skipped_zero_length += 1
                continue
            segment_id = f"osm-way-{way_id}-segment-{index}"
            geometry = [
                [round(point[1], 7), round(point[0], 7)] for point in segment_points
            ]
            common = {
                "segment_id": segment_id,
                "source": "OpenStreetMap",
                "source_way_id": way_id,
                "geometry": geometry,
                "length_m": round(length_m, 3),
                "road_class": highway,
                "name": tags.get("name"),
                "bridge": tags.get("bridge") not in {None, "no"},
                "max_weight_t": parse_metric_number(tags.get("maxweight"), "weight"),
                "max_height_m": parse_metric_number(tags.get("maxheight"), "length"),
                "width_m": parse_metric_number(tags.get("width"), "length"),
                "base_speed_kph": parse_metric_number(tags.get("maxspeed"), "speed"),
                "base_speed_source": "osm:maxspeed" if tags.get("maxspeed") else None,
                "passability": "unknown",
                "state_as_of": None,
            }
            if direction in {"forward", "both"}:
                raw_edges.append(
                    {
                        **common,
                        "edge_id": f"{segment_id}-forward",
                        "from_node": f"osm-node-{left}",
                        "to_node": f"osm-node-{right}",
                        "source_direction": direction,
                    }
                )
            if direction in {"reverse", "both"}:
                raw_edges.append(
                    {
                        **common,
                        "edge_id": f"{segment_id}-reverse",
                        "from_node": f"osm-node-{right}",
                        "to_node": f"osm-node-{left}",
                        "geometry": list(reversed(common["geometry"])),
                        "source_direction": direction,
                    }
                )

    if not raw_edges:
        raise SystemExit("No routable edges were produced")
    retained_nodes, component_count, component_sizes = largest_component(raw_edges)
    edges = [
        edge
        for edge in raw_edges
        if edge["from_node"] in retained_nodes and edge["to_node"] in retained_nodes
    ]
    used_osm_nodes = {int(node.removeprefix("osm-node-")) for node in retained_nodes}
    nodes = [
        {
            "node_id": f"osm-node-{osm_id}",
            "source": "OpenStreetMap",
            "source_node_id": osm_id,
            "longitude": round(coordinates[osm_id][1], 7),
            "latitude": round(coordinates[osm_id][0], 7),
        }
        for osm_id in sorted(used_osm_nodes)
    ]

    graph_seed = {
        "importer_version": IMPORTER_VERSION,
        "raw_sha256": sha256_bytes(raw_bytes),
        "query_sha256": sha256_bytes(query_bytes),
        "node_ids": [node["node_id"] for node in nodes],
        "edge_ids": [edge["edge_id"] for edge in edges],
    }
    graph_version = "osm-shillong-" + sha256_bytes(canonical_bytes(graph_seed))[:16]
    graph = {
        "schema_version": "routing-graph-v1",
        "graph_version": graph_version,
        "mode": "baseline",
        "operational_status": "not_live",
        "source": {
            "name": "OpenStreetMap",
            "license": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
            "raw_sha256": sha256_bytes(raw_bytes),
            "osm_base_timestamp": (payload.get("osm3s") or {}).get(
                "timestamp_osm_base"
            ),
        },
        "query_bbox": QUERY_BBOX,
        "counts": {
            "source_highway_ways": highway_way_count,
            "source_coordinate_nodes": len(coordinates),
            "directed_edges_before_pruning": len(raw_edges),
            "nodes": len(nodes),
            "directed_edges": len(edges),
            "weak_components_before_pruning": component_count,
            "discarded_directed_edges": len(raw_edges) - len(edges),
            "missing_coordinate_ways": len(missing_coordinate_ways),
            "skipped_zero_length_pairs": skipped_zero_length,
            "skipped_outside_bbox_segments": skipped_outside_bbox,
        },
        "nodes": nodes,
        "edges": edges,
    }

    node_points = {
        node["node_id"]: (node["latitude"], node["longitude"]) for node in nodes
    }
    facilities: list[dict[str, Any]] = []
    seen_facilities: set[tuple[str, int]] = set()
    for element in elements:
        tags = element.get("tags") or {}
        is_allowed = (
            tags.get("amenity") in ALLOWED_AMENITIES
            or tags.get("building") == "warehouse"
        )
        point = facility_point(element)
        source_key = (str(element.get("type")), int(element.get("id", 0)))
        if not is_allowed or point is None or source_key in seen_facilities:
            continue
        seen_facilities.add(source_key)
        nearest_node, snap_distance = min(
            (
                (node_id, haversine_m(point, node_point))
                for node_id, node_point in node_points.items()
            ),
            key=lambda item: (item[1], item[0]),
        )
        facilities.append(
            {
                "facility_id": f"osm-{source_key[0]}-{source_key[1]}",
                "source": "OpenStreetMap",
                "source_type": source_key[0],
                "source_id": source_key[1],
                "name": tags.get("name"),
                "facility_type": tags.get("amenity") or "warehouse",
                "latitude": round(point[0], 7),
                "longitude": round(point[1], 7),
                "routing_node_id": nearest_node,
                "snap_distance_m": round(snap_distance, 3),
                "routing_eligible": snap_distance <= 500,
            }
        )
    facilities.sort(key=lambda item: item["facility_id"])
    eligible_named = [
        item
        for item in facilities
        if item["routing_eligible"]
        and item["name"]
        and item["facility_type"] in {"hospital", "clinic"}
    ]
    if len(eligible_named) < 2:
        raise SystemExit("Fewer than two named, routable health facilities were found")

    chosen: tuple[dict[str, Any], dict[str, Any], list[str], list[str]] | None = None
    chosen_separation = -1.0
    for origin in eligible_named:
        for destination in eligible_named:
            if origin["facility_id"] == destination["facility_id"]:
                continue
            result = shortest_path(
                edges, origin["routing_node_id"], destination["routing_node_id"]
            )
            if result is None:
                continue
            separation = haversine_m(
                (origin["latitude"], origin["longitude"]),
                (destination["latitude"], destination["longitude"]),
            )
            if separation > chosen_separation:
                path_nodes, path_edges = result
                chosen = (origin, destination, path_nodes, path_edges)
                chosen_separation = separation
    if chosen is None:
        raise SystemExit("No directed path between named health facilities")

    origin, destination, path_nodes, path_edges = chosen
    edge_by_id = {edge["edge_id"]: edge for edge in edges}
    middle_edge = edge_by_id[path_edges[len(path_edges) // 2]]
    risk_edge = edge_by_id[path_edges[len(path_edges) // 3]]
    bridge_edge = next((edge for edge in edges if edge["bridge"]), None)
    if bridge_edge is None:
        raise SystemExit("No OSM-sourced bridge segment exists in the retained graph")

    endpoints = {
        "schema_version": "scenario-endpoints-v1",
        "graph_version": graph_version,
        "mode": "endpoint_reference_only",
        "notice": (
            "These are OSM-sourced locations selected deterministically for later "
            "simulated scenarios. They are not real consignments, closures, or warnings."
        ),
        "common_facilities": {
            "origin": {
                "facility_id": origin["facility_id"],
                "name": origin["name"],
                "routing_node_id": origin["routing_node_id"],
            },
            "destination": {
                "facility_id": destination["facility_id"],
                "name": destination["name"],
                "routing_node_id": destination["routing_node_id"],
            },
        },
        "reference_path": {
            "node_ids": path_nodes,
            "edge_ids": path_edges,
            "straight_line_separation_m": round(chosen_separation, 3),
        },
        "scenarios": {
            "S1": {
                "origin_node_id": origin["routing_node_id"],
                "destination_node_id": destination["routing_node_id"],
                "fixture_state": "to_be_created_in_T005",
            },
            "S2": {
                "origin_node_id": origin["routing_node_id"],
                "destination_node_id": destination["routing_node_id"],
                "candidate_closure_segment_id": middle_edge["segment_id"],
                "fixture_state": "to_be_created_in_T005",
            },
            "S3": {
                "origin_node_id": origin["routing_node_id"],
                "destination_node_id": destination["routing_node_id"],
                "candidate_forecast_segment_id": risk_edge["segment_id"],
                "fixture_state": "to_be_created_in_T005",
            },
            "S4": {
                "supply_node_id": origin["routing_node_id"],
                "monitored_facility_node_id": destination["routing_node_id"],
                "fixture_state": "to_be_created_in_T005",
            },
            "S5": {
                "trip_origin_node_id": origin["routing_node_id"],
                "trip_destination_node_id": destination["routing_node_id"],
                "fixture_state": "to_be_created_in_T005",
            },
            "S6": {
                "bridge_from_node_id": bridge_edge["from_node"],
                "bridge_to_node_id": bridge_edge["to_node"],
                "candidate_bridge_segment_id": bridge_edge["segment_id"],
                "source_max_weight_t": bridge_edge["max_weight_t"],
                "constraint_note": (
                    "OSM does not provide a weight limit for this selected segment; "
                    "the synthetic scenarios add a visibly synthetic constraint overlay."
                ),
                "fixture_state": "to_be_created_in_T005",
            },
        },
    }

    facility_document = {
        "schema_version": "pilot-facilities-v1",
        "graph_version": graph_version,
        "mode": "baseline",
        "operational_status": "not_live",
        "source": {
            "name": "OpenStreetMap",
            "license": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
        },
        "facilities": facilities,
    }
    graph_checksum = sha256_bytes(canonical_bytes(graph))
    facilities_checksum = sha256_bytes(canonical_bytes(facility_document))
    endpoints_checksum = sha256_bytes(canonical_bytes(endpoints))
    retrieved_at = datetime.fromtimestamp(
        args.input.stat().st_mtime, tz=UTC
    ).isoformat()
    manifest = {
        "schema_version": "source-manifest-v1",
        "source": "OpenStreetMap Overpass API",
        "source_endpoint": "https://overpass.kumi.systems/api/interpreter",
        "accessed_at": retrieved_at,
        "license": "ODbL-1.0",
        "license_or_terms_url": "https://www.openstreetmap.org/copyright",
        "attribution": "© OpenStreetMap contributors",
        "query_file": str(args.query),
        "query_bbox": QUERY_BBOX,
        "raw_file": str(args.input),
        "raw_checksum": "sha256:" + sha256_bytes(raw_bytes),
        "query_checksum": "sha256:" + sha256_bytes(query_bytes),
        "transform_script": "scripts/pipeline/import_osm_pilot.py",
        "transform_version": IMPORTER_VERSION,
        "intended_use": "bounded pilot routing topology and facility candidates",
        "redistribution_allowed": True,
        "redistribution_conditions": "ODbL attribution and share-alike apply",
        "outputs": {
            str(args.graph_output): "sha256:" + graph_checksum,
            str(args.facilities_output): "sha256:" + facilities_checksum,
            str(args.endpoints_output): "sha256:" + endpoints_checksum,
        },
        "quality_notes": {
            "not_live_status": True,
            "largest_weak_component_only": True,
            "component_sizes_before_pruning": component_sizes,
            "missing_coordinate_way_ids": missing_coordinate_ways,
            "road_passability_unknown": True,
            "unsourced_speed_defaults_added": False,
        },
    }

    write_json(args.graph_output, graph)
    write_json(args.facilities_output, facility_document)
    write_json(args.endpoints_output, endpoints)
    write_json(args.manifest_output, manifest)
    print(
        json.dumps(
            {
                "graph_version": graph_version,
                "nodes": len(nodes),
                "directed_edges": len(edges),
                "facilities": len(facilities),
                "named_routing_facilities": len(eligible_named),
                "components_before_pruning": component_count,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
