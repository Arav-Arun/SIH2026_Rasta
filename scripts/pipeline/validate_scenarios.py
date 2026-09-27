#!/usr/bin/env python3
"""Validate deterministic synthetic scenarios against the pilot graph."""

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
        "--fixture",
        type=Path,
        default=Path("data/fixtures/synthetic_scenarios.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/synthetic_scenarios.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/reports/scenario_validation.json"),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reachable(
    graph: dict[str, Any], origin: str, blocked_segments: set[str]
) -> set[str]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["segment_id"] not in blocked_segments:
            adjacency[edge["from_node"]].append(edge["to_node"])
    visited = {origin}
    queue = deque([origin])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return visited


def main() -> None:
    args = parse_args()
    graph = load_json(args.graph)
    fixture = load_json(args.fixture)
    manifest = load_json(args.manifest)
    errors: list[str] = []
    observations: list[str] = []

    if fixture.get("schema_version") != "synthetic-scenarios-v1":
        errors.append("Unexpected fixture schema")
    if fixture.get("mode") != "synthetic":
        errors.append("Fixture mode is not synthetic")
    if fixture.get("ui_label") != "SIMULATED SCENARIO":
        errors.append("Fixture UI label is not explicit")
    if fixture.get("simulation_epoch") != "2000-01-01T00:00:00Z":
        errors.append("Simulation clock origin changed unexpectedly")
    if fixture.get("graph_version") != graph.get("graph_version"):
        errors.append("Fixture graph version does not match graph")
    if manifest.get("live_data") is not False:
        errors.append("Manifest must state that no live data is present")
    if manifest.get("output_checksum") != "sha256:" + canonical_sha(fixture):
        errors.append("Fixture checksum does not match manifest")

    node_ids = {node["node_id"] for node in graph["nodes"]}
    segment_ids = {edge["segment_id"] for edge in graph["edges"]}
    vehicle_ids = set(fixture.get("vehicles", {}))
    scenarios = fixture.get("scenarios", [])
    if [scenario.get("id") for scenario in scenarios] != [
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
    ]:
        errors.append("Scenarios must be ordered exactly S1 through S6")
    scenario_by_id = {scenario["id"]: scenario for scenario in scenarios}

    for scenario in scenarios:
        scenario_id = scenario.get("id", "unknown")
        if scenario.get("mode") != "synthetic":
            errors.append(f"{scenario_id} mode is not synthetic")
        if scenario.get("ui_label") != "SIMULATED SCENARIO":
            errors.append(f"{scenario_id} has no explicit UI label")
        route_request = scenario.get("route_request", {})
        for key in ("origin_node_id", "destination_node_id"):
            if route_request.get(key) not in node_ids:
                errors.append(f"{scenario_id} references unknown node in {key}")
        if route_request.get("vehicle_id") not in vehicle_ids:
            errors.append(f"{scenario_id} references an unknown vehicle")
        commodity = route_request.get("commodity", {})
        if commodity.get("synthetic") is not True:
            errors.append(f"{scenario_id} commodity is not marked synthetic")

        overlays = scenario.get("overlays", {})
        for state in overlays.get("segment_states", []):
            if state.get("segment_id") not in segment_ids:
                errors.append(f"{scenario_id} state references unknown segment")
            if state.get("source_mode") != "synthetic":
                errors.append(f"{scenario_id} state source is not synthetic")
        for risk in overlays.get("risk_observations", []):
            if risk.get("segment_id") not in segment_ids:
                errors.append(f"{scenario_id} risk references unknown segment")
            if risk.get("source_mode") != "synthetic":
                errors.append(f"{scenario_id} risk source is not synthetic")
            if not str(risk.get("source_name", "")).startswith("SIMULATED_"):
                errors.append(f"{scenario_id} risk source could look authoritative")
        for constraint in overlays.get("segment_constraints", []):
            if constraint.get("segment_id") not in segment_ids:
                errors.append(f"{scenario_id} constraint references unknown segment")
            if constraint.get("source_mode") != "synthetic":
                errors.append(f"{scenario_id} constraint source is not synthetic")

    if len(scenario_by_id) == 6:
        s1 = scenario_by_id["S1"]
        s1_request = s1["route_request"]
        if s1_request["destination_node_id"] not in reachable(
            graph, s1_request["origin_node_id"], set()
        ):
            errors.append("S1 has no directed route in the baseline graph")

        s2 = scenario_by_id["S2"]
        s2_request = s2["route_request"]
        closed_segments = {
            state["segment_id"]
            for state in s2["overlays"]["segment_states"]
            if state["passability"] == "closed"
        }
        if len(closed_segments) != 1:
            errors.append("S2 must close exactly one physical segment")
        else:
            has_replan = s2_request["destination_node_id"] in reachable(
                graph, s2_request["origin_node_id"], closed_segments
            )
            expected_status = "feasible_replan" if has_replan else "no_route"
            if s2["expected"].get("route_status") != expected_status:
                errors.append(
                    "S2 expected route status does not match graph reachability"
                )
            if set(s2["expected"].get("excluded_segment_ids", [])) != closed_segments:
                errors.append("S2 expected exclusion does not match closure")
            observations.append(f"S2 route status after closure: {expected_status}")

        s3 = scenario_by_id["S3"]
        if s3["overlays"].get("segment_states"):
            errors.append("S3 forecast-only scenario changes passability")
        if not s3["overlays"].get("risk_observations"):
            errors.append("S3 has no risk observation")
        if s3["expected"].get("passability_change") is not False:
            errors.append("S3 expected state must keep passability unchanged")

        s4 = scenario_by_id["S4"]
        s4_request = s4["route_request"]
        s4_closed = {state["segment_id"] for state in s4["overlays"]["segment_states"]}
        if s4_request["destination_node_id"] in reachable(
            graph, s4_request["origin_node_id"], s4_closed
        ):
            errors.append("S4 closure does not isolate the target facility")
        if s4["expected"].get("reachability") != "isolated":
            errors.append("S4 expected reachability is not isolated")

        s5 = scenario_by_id["S5"]
        telemetry = s5.get("telemetry", {})
        points = telemetry.get("points", [])
        if not points or any(
            point.get("source_mode") != "synthetic" for point in points
        ):
            errors.append("S5 telemetry is missing or not explicitly synthetic")
        upload_ids = telemetry.get("buffered_upload_event_ids", [])
        duplicate_count = len(upload_ids) - len(set(upload_ids))
        if duplicate_count != 1:
            errors.append("S5 must contain exactly one duplicate upload attempt")
        if s5["expected"].get("buffered_upload_unique_inserts") != len(set(upload_ids)):
            errors.append("S5 idempotency expectation is inconsistent")

        s6 = scenario_by_id["S6"]
        constraints = s6["overlays"].get("segment_constraints", [])
        if len(constraints) != 1:
            errors.append("S6 must contain one synthetic bridge constraint")
        else:
            constraint = constraints[0]
            vehicle = fixture["vehicles"][s6["route_request"]["vehicle_id"]]
            if vehicle["gross_weight_t"] <= constraint["max_weight_t"]:
                errors.append("S6 vehicle does not exceed the simulated limit")
            if s6["expected"].get("reason_code") != "vehicle_exceeds_max_weight":
                errors.append("S6 expected reason code is incorrect")

    policy = fixture.get("network_policy", {})
    if policy.get("mode") != "synthetic":
        errors.append("Fallback speed/passability policy is not marked synthetic")
    if policy.get("default_passability") != "open":
        errors.append("Synthetic baseline routing policy changed unexpectedly")

    report = {
        "check": "validate_scenarios",
        "status": "passed" if not errors else "failed",
        "fixture_set_id": fixture.get("fixture_set_id"),
        "graph_version": graph.get("graph_version"),
        "scenario_count": len(scenarios),
        "fixture_checksum": canonical_sha(fixture),
        "errors": errors,
        "observations": observations,
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
