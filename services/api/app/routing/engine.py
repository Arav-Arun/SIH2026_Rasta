"""Small deterministic routing engine used to prove SIH26002 constraints."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import defaultdict
from typing import Any

ROUTING_CONFIG_VERSION = "routing-proof-v1"
UNKNOWN_BRIDGE_PENALTY_SECONDS = 120.0


class RoutingError(ValueError):
    """Raised when a routing request or fixture is structurally invalid."""


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _scenario(fixture: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    matches = [
        scenario
        for scenario in fixture.get("scenarios", [])
        if scenario.get("id") == scenario_id
    ]
    if len(matches) != 1:
        raise RoutingError(f"Expected one scenario named {scenario_id}")
    return matches[0]


def _validate_request(
    graph: dict[str, Any],
    fixture: dict[str, Any],
    scenario: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if fixture.get("mode") != "synthetic":
        raise RoutingError("This proof engine accepts only explicit synthetic fixtures")
    if fixture.get("graph_version") != graph.get("graph_version"):
        raise RoutingError("Fixture and graph versions differ")
    request = scenario.get("route_request")
    if not isinstance(request, dict):
        raise RoutingError("Scenario has no route request")
    node_ids = {node["node_id"] for node in graph.get("nodes", [])}
    for key in ("origin_node_id", "destination_node_id"):
        if request.get(key) not in node_ids:
            raise RoutingError(f"Unknown graph node in {key}")
    vehicle = fixture.get("vehicles", {}).get(request.get("vehicle_id"))
    if not isinstance(vehicle, dict) or vehicle.get("synthetic") is not True:
        raise RoutingError("Scenario vehicle is missing or not synthetic")
    if request["origin_node_id"] == request["destination_node_id"]:
        raise RoutingError("Origin and destination must differ")
    return request, vehicle


def route_scenario(
    graph: dict[str, Any],
    fixture: dict[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    """Return one deterministic constrained route for a synthetic scenario."""

    scenario = _scenario(fixture, scenario_id)
    request, vehicle = _validate_request(graph, fixture, scenario)
    policy = fixture.get("network_policy", {})
    if policy.get("mode") != "synthetic":
        raise RoutingError("Network policy must be explicitly synthetic")
    speed_by_class = policy.get("road_class_speed_kph", {})
    if not speed_by_class:
        raise RoutingError("Synthetic road-class speed policy is missing")

    overlays = scenario.get("overlays", {})
    states = {item["segment_id"]: item for item in overlays.get("segment_states", [])}
    constraints = {
        item["segment_id"]: item for item in overlays.get("segment_constraints", [])
    }
    closed_segments = {
        segment_id
        for segment_id, state in states.items()
        if state.get("passability") == "closed"
    }

    excluded_closed: set[str] = set()
    excluded_weight: set[str] = set()
    unknown_bridge_segments: set[str] = set()
    adjacency: dict[str, list[tuple[str, str, float, float, float, str]]] = defaultdict(
        list
    )
    for edge in graph.get("edges", []):
        segment_id = edge["segment_id"]
        if segment_id in closed_segments:
            excluded_closed.add(segment_id)
            continue

        constraint = constraints.get(segment_id)
        max_weight_t = (
            constraint.get("max_weight_t")
            if constraint is not None
            else edge.get("max_weight_t")
        )
        vehicle_weight_t = vehicle.get("gross_weight_t")
        if (
            max_weight_t is not None
            and vehicle_weight_t is not None
            and float(vehicle_weight_t) > float(max_weight_t)
        ):
            excluded_weight.add(segment_id)
            continue

        unknown_penalty = 0.0
        if edge.get("bridge") and max_weight_t is None:
            unknown_bridge_segments.add(segment_id)
            unknown_penalty = UNKNOWN_BRIDGE_PENALTY_SECONDS

        speed_kph = edge.get("base_speed_kph")
        speed_source = edge.get("base_speed_source")
        if speed_kph is None:
            speed_kph = speed_by_class.get(edge.get("road_class"))
            speed_source = "synthetic_fixture:road_class_speed_kph"
        if not isinstance(speed_kph, (int, float)) or speed_kph <= 0:
            raise RoutingError(
                f"No usable speed for road class {edge.get('road_class')}"
            )

        length_m = float(edge["length_m"])
        travel_seconds = 3.6 * length_m / float(speed_kph)
        cost_seconds = travel_seconds + unknown_penalty
        adjacency[edge["from_node"]].append(
            (
                edge["to_node"],
                edge["edge_id"],
                cost_seconds,
                travel_seconds,
                length_m,
                str(speed_source),
            )
        )
    for choices in adjacency.values():
        choices.sort(key=lambda value: value[1])

    origin = request["origin_node_id"]
    destination = request["destination_node_id"]
    best: dict[str, tuple[float, str]] = {origin: (0.0, "")}
    previous: dict[str, tuple[str, str, float, float, float, str]] = {}
    queue: list[tuple[float, str]] = [(0.0, origin)]
    while queue:
        current_cost, node = heapq.heappop(queue)
        if not math.isclose(current_cost, best.get(node, (math.inf, ""))[0]):
            continue
        if node == destination:
            break
        for (
            next_node,
            edge_id,
            edge_cost,
            travel_seconds,
            length_m,
            speed_source,
        ) in adjacency.get(node, []):
            candidate_cost = current_cost + edge_cost
            candidate_key = (round(candidate_cost, 9), edge_id)
            prior_key = best.get(next_node, (math.inf, ""))
            if candidate_key < prior_key:
                best[next_node] = candidate_key
                previous[next_node] = (
                    node,
                    edge_id,
                    edge_cost,
                    travel_seconds,
                    length_m,
                    speed_source,
                )
                heapq.heappush(queue, (candidate_cost, next_node))

    exclusion_summary = {
        "closed_segment_ids": sorted(excluded_closed),
        "vehicle_constraint_segment_ids": sorted(excluded_weight),
    }
    source_context = {
        "graph_source": graph.get("source"),
        "graph_operational_status": graph.get("operational_status"),
        "fixture_mode": fixture.get("mode"),
        "fixture_label": fixture.get("ui_label"),
        "simulation_epoch": fixture.get("simulation_epoch"),
    }
    if destination not in best:
        reason_codes = []
        if excluded_closed:
            reason_codes.append("closed_segments_disconnect_route")
        if excluded_weight:
            reason_codes.append("vehicle_constraints_disconnect_route")
        if not reason_codes:
            reason_codes.append("directed_graph_has_no_path")
        result = {
            "status": "no_route",
            "scenario_id": scenario_id,
            "mode": "synthetic",
            "ui_label": "SIMULATED SCENARIO",
            "graph_version": graph["graph_version"],
            "routing_config_version": ROUTING_CONFIG_VERSION,
            "reason_codes": reason_codes,
            "exclusions": exclusion_summary,
            "path": None,
            "source_context": source_context,
            "actions": [
                "inspect excluded segments",
                "request field verification",
                "change vehicle only after dispatcher review",
            ],
            "safe_route_claim": False,
        }
        result["result_id"] = "route-" + _stable_hash(result)[:20]
        return result

    edge_by_id = {edge["edge_id"]: edge for edge in graph["edges"]}
    node_path = [destination]
    edge_path: list[str] = []
    edge_costs: list[dict[str, Any]] = []
    current = destination
    while current != origin:
        (
            prior_node,
            edge_id,
            edge_cost,
            travel_seconds,
            length_m,
            speed_source,
        ) = previous[current]
        edge = edge_by_id[edge_id]
        node_path.append(prior_node)
        edge_path.append(edge_id)
        edge_costs.append(
            {
                "edge_id": edge_id,
                "segment_id": edge["segment_id"],
                "length_m": round(length_m, 3),
                "travel_seconds": round(travel_seconds, 3),
                "unknown_constraint_penalty_seconds": round(
                    edge_cost - travel_seconds, 3
                ),
                "cost_seconds": round(edge_cost, 3),
                "speed_source": speed_source,
            }
        )
        current = prior_node
    node_path.reverse()
    edge_path.reverse()
    edge_costs.reverse()
    segment_path = [edge_by_id[edge_id]["segment_id"] for edge_id in edge_path]
    used_unknown_bridges = sorted(
        set(segment_path).intersection(unknown_bridge_segments)
    )
    travel_total = sum(item["travel_seconds"] for item in edge_costs)
    distance_total = sum(item["length_m"] for item in edge_costs)
    total_cost = sum(item["cost_seconds"] for item in edge_costs)
    warnings = []
    if used_unknown_bridges:
        warnings.append(
            {
                "code": "unknown_bridge_weight_limit",
                "segment_ids": used_unknown_bridges,
                "requires_dispatcher_review": True,
            }
        )
    if any(
        item["speed_source"] == "synthetic_fixture:road_class_speed_kph"
        for item in edge_costs
    ):
        warnings.append(
            {
                "code": "synthetic_speed_assumptions_used",
                "requires_dispatcher_review": False,
            }
        )

    result = {
        "status": "feasible",
        "scenario_id": scenario_id,
        "mode": "synthetic",
        "ui_label": "SIMULATED SCENARIO",
        "graph_version": graph["graph_version"],
        "routing_config_version": ROUTING_CONFIG_VERSION,
        "distance_m": round(distance_total, 3),
        "travel_time_seconds": round(travel_total, 3),
        "cost_seconds": round(total_cost, 3),
        "path": {
            "node_ids": node_path,
            "edge_ids": edge_path,
            "segment_ids": segment_path,
            "edge_costs": edge_costs,
        },
        "exclusions": exclusion_summary,
        "warnings": warnings,
        "source_context": source_context,
        "safe_route_claim": False,
    }
    result["result_id"] = "route-" + _stable_hash(result)[:20]
    return result
