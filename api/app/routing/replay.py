"""Replay the fixed SYNTHETIC scenarios through the production route planner.

Route decisions are scored on fixed scenarios (requirement R16), including ones
where the right answer is to change nothing. Every scenario is synthetic
(``data/fixtures/synthetic_scenarios.json``): the pilot's OSM topology with
invented closures, limits, weather and vehicles. A pass means the planner kept to
its rules on these cases; it says nothing about real roads.

    PYTHONPATH=api python -m app.routing.replay
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.routing.core import CostPolicy, Edge, VehicleProfile, eta_band
from app.routing.planner import (
    Alternative,
    RoutePlanResult,
    RouteRequest,
    Snapshot,
    plan_routes,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = REPOSITORY_ROOT / "data" / "fixtures" / "synthetic_scenarios.json"
GRAPH = REPOSITORY_ROOT / "data" / "pilot" / "shillong_graph.json"

#: The scenarios carry a warning colour, not a score. The replay reads a colour as
#: this risk score: an assumption of the replay, not the risk engine's own mapping.
WARNING_RISK = {"yellow": 0.5, "orange": 0.7, "red": 0.9}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    scenario_id: str
    title: str
    routing: bool = True
    checks: list[Check] = field(default_factory=list)
    status: str | None = None
    distance_m: float | None = None
    #: What a reader should know that is not a pass or a fail, such as the cost of
    #: steering around a road that was only forecast to be risky.
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def load(
    graph_path: Path = GRAPH, scenarios_path: Path = SCENARIOS
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    fixture = json.loads(scenarios_path.read_text(encoding="utf-8"))
    if fixture.get("mode") != "synthetic":
        raise ValueError("the replay runs synthetic scenarios only")
    if fixture.get("graph_version") != graph.get("graph_version"):
        raise ValueError("the scenarios were written for a different graph")
    return graph, fixture


def edges_for(
    graph: dict[str, Any],
    fixture: dict[str, Any],
    scenario: dict[str, Any],
    *,
    with_overlays: bool = True,
) -> list[Edge]:
    """The pilot graph as the scenario's synthetic world says it is."""

    default = fixture["network_policy"].get("default_passability", "unknown")
    overlays = scenario.get("overlays", {}) if with_overlays else {}
    states = {
        item["segment_id"]: item["passability"]
        for item in overlays.get("segment_states", [])
    }
    limits = {
        item["segment_id"]: item.get("max_weight_t")
        for item in overlays.get("segment_constraints", [])
    }
    risks: dict[str, float] = {}
    for item in overlays.get("risk_observations", []):
        score = WARNING_RISK.get(item.get("warning_severity", ""), 0.0)
        risks[item["segment_id"]] = max(risks.get(item["segment_id"], 0.0), score)
    return [
        Edge(
            edge_id=edge["edge_id"],
            segment_id=edge["segment_id"],
            from_node=edge["from_node"],
            to_node=edge["to_node"],
            length_m=float(edge["length_m"]),
            road_class=edge["road_class"],
            base_speed_kph=edge.get("base_speed_kph"),
            max_weight_t=limits.get(edge["segment_id"], edge.get("max_weight_t")),
            max_height_m=edge.get("max_height_m"),
            is_bridge=bool(edge.get("bridge")),
            passability=states.get(edge["segment_id"], default),
            risk_score=risks.get(edge["segment_id"]),
        )
        for edge in graph["edges"]
    ]


def policy_for(fixture: dict[str, Any]) -> CostPolicy:
    speeds = fixture["network_policy"]["road_class_speed_kph"]
    return CostPolicy(
        road_class_speed_kph={name: float(kph) for name, kph in speeds.items()}
    )


def plan(
    graph: dict[str, Any],
    fixture: dict[str, Any],
    scenario: dict[str, Any],
    *,
    vehicle_id: str | None = None,
    with_overlays: bool = True,
) -> RoutePlanResult:
    request = scenario["route_request"]
    vehicle = fixture["vehicles"][vehicle_id or request["vehicle_id"]]
    policy = policy_for(fixture)
    return plan_routes(
        edges_for(graph, fixture, scenario, with_overlays=with_overlays),
        RouteRequest(
            origin_node_id=request["origin_node_id"],
            destination_node_id=request["destination_node_id"],
            vehicle=VehicleProfile(
                vehicle_id=vehicle["vehicle_id"],
                gross_weight_t=vehicle.get("gross_weight_t"),
                height_m=vehicle.get("height_m"),
            ),
            priority=request.get("commodity", {}).get("priority", "normal"),
        ),
        Snapshot(
            network_version=graph["graph_version"],
            risk_snapshot_version="replay",
            cost_policy_version=policy.version,
            graph_edge_count=len(graph["edges"]),
            computed_at=str(fixture.get("simulation_epoch", "replay")),
        ),
        policy,
    )


def _used(result: RoutePlanResult) -> set[str]:
    return {segment for item in result.alternatives for segment in item.segment_ids}


def _excluded(result: RoutePlanResult, reason: str | None = None) -> set[str]:
    groups = (
        [result.exclusions.get(reason, [])] if reason else result.exclusions.values()
    )
    return {item["segment_id"] for group in groups for item in group}


def _warned(alternative: Alternative, code: str, segment: str | None = None) -> bool:
    return any(
        warning["code"] == code
        and (segment is None or segment in warning["segment_ids"])
        for warning in alternative.constraint_warnings
    )


def _eta(result: RoutePlanResult) -> str:
    if not result.alternatives:
        return "no route"
    low, high = result.alternatives[0].eta_range_seconds
    return f"{low / 60:.1f}-{high / 60:.1f} min"


def _with_states(
    scenario: dict[str, Any], states: list[dict[str, Any]]
) -> dict[str, Any]:
    return {**scenario, "overlays": {**scenario["overlays"], "segment_states": states}}


ROUTING_EXPECTATIONS = (
    "route_status",
    "reachability",
    "reason_code",
    "passability_change",
    "restricted_segment_ids",
    "unverified_limit_segment_ids",
    "eta_includes_risk_penalty",
)


def judge(
    graph: dict[str, Any], fixture: dict[str, Any], scenario: dict[str, Any]
) -> ScenarioResult:
    """Check one scenario's plan against what the scenario says should happen."""

    expected = scenario.get("expected", {})
    outcome = ScenarioResult(scenario["id"], scenario["title"])
    if not any(key in expected for key in ROUTING_EXPECTATIONS):
        outcome.routing = False
        return outcome

    overlays = scenario.get("overlays", {})
    result = plan(graph, fixture, scenario)
    outcome.status = result.status
    if result.alternatives:
        outcome.distance_m = result.alternatives[0].distance_m
    used = _used(result)
    closed = {
        item["segment_id"]
        for item in overlays.get("segment_states", [])
        if item.get("passability") == "closed"
    }
    status = expected.get("route_status")

    if status in ("feasible", "feasible_replan"):
        outcome.checks.append(
            Check("a route is offered", result.status == "feasible", result.status)
        )
    if "closed_segments_used" in expected or closed:
        crossed = used & closed
        outcome.checks.append(
            Check(
                "no offered route crosses a closed road",
                not crossed,
                ", ".join(sorted(crossed)),
            )
        )
    for segment in expected.get("excluded_segment_ids", []):
        outcome.checks.append(
            Check(
                f"{segment} is excluded as closed",
                segment in _excluded(result, "segment_closed") and segment not in used,
            )
        )
    if status == "feasible_replan":
        before = plan(graph, fixture, scenario, with_overlays=False)
        primary = (
            set(before.alternatives[0].segment_ids) if before.alternatives else set()
        )
        outcome.checks.append(
            Check(
                "without the closure the best route used that road, so it forced a replan",
                bool(primary & closed),
            )
        )
    if expected.get("closures_each_forced_a_replan"):
        # Applied one at a time, in the order listed, each closure must sit on the
        # best route the earlier ones left. A closure off that route changes nothing,
        # and counting it would make the scenario look harder than it is.
        states = overlays.get("segment_states", [])
        order = [
            item["segment_id"] for item in states if item.get("passability") == "closed"
        ]
        missed = []
        for index, segment in enumerate(order):
            earlier = [
                item
                for item in states
                if item.get("passability") != "closed"
                or item["segment_id"] in order[:index]
            ]
            best = plan(graph, fixture, _with_states(scenario, earlier))
            if not best.alternatives or segment not in best.alternatives[0].segment_ids:
                missed.append(segment)
        outcome.checks.append(
            Check(
                "each closure lay on the best route the earlier ones left",
                bool(order) and not missed,
                ", ".join(missed),
            )
        )
    if expected.get("reachability") == "isolated":
        outcome.checks.append(
            Check(
                "a cut-off destination gets no route, and says why",
                result.status == "no_route" and bool(result.reason_codes),
                ", ".join(result.reason_codes),
            )
        )
    if expected.get("passability_change") is False:
        forecast = {
            item["segment_id"] for item in overlays.get("risk_observations", [])
        }
        outcome.checks.append(
            Check(
                "a forecast alone excludes no road",
                result.status == "feasible" and not (forecast & _excluded(result)),
            )
        )
        before = plan(graph, fixture, scenario, with_overlays=False)
        if before.alternatives and result.alternatives:
            primary = result.alternatives[0]
            detour = primary.distance_m - before.alternatives[0].distance_m
            avoided = not (set(primary.segment_ids) & forecast)
            outcome.notes.append(
                f"the primary route {'steers around' if avoided else 'still uses'} "
                f"the forecast road, {detour / 1000:+.2f} km against the plan "
                "without the forecast; the road stays available to every route"
            )
    if expected.get("reason_code") == "vehicle_exceeds_max_weight":
        limited = {
            item["segment_id"] for item in overlays.get("segment_constraints", [])
        }
        outcome.checks.append(
            Check(
                "the heavy vehicle is kept off the weight-limited road",
                limited <= _excluded(result, "vehicle_exceeds_max_weight")
                and not (used & limited),
            )
        )
        lightest = min(
            fixture["vehicles"].values(), key=lambda item: item["gross_weight_t"]
        )
        if result.status == "no_route":
            outcome.notes.append(
                "the heavy vehicle has no route at all: "
                + ", ".join(result.reason_codes)
            )
        light = plan(graph, fixture, scenario, vehicle_id=lightest["vehicle_id"])
        outcome.checks.append(
            Check(
                "a lighter vehicle may still use it",
                not (limited & _excluded(light, "vehicle_exceeds_max_weight")),
                lightest["vehicle_id"],
            )
        )
    policy = policy_for(fixture)
    for segment in expected.get("restricted_segment_ids", []):
        over = [item for item in result.alternatives if segment in item.segment_ids]
        outcome.checks.append(
            Check(
                f"{segment} is restricted, not closed: a route still uses it",
                segment not in _excluded(result) and bool(over),
                f"{len(over)} of {len(result.alternatives)} routes",
            )
        )
        outcome.checks.append(
            Check(
                "every route over the restricted road is marked for review",
                bool(over)
                and all(
                    item.requires_review
                    and _warned(item, "restricted_segment_used", segment)
                    for item in over
                ),
            )
        )
        costs = [
            cost
            for item in over
            for cost in item.edge_costs
            if cost["segment_id"] == segment
        ]
        outcome.checks.append(
            Check(
                "its restriction delay is counted in the driving time",
                bool(costs)
                and all(
                    cost["delay_seconds"] > 0
                    and abs(
                        cost["delay_seconds"]
                        - cost["base_time_seconds"]
                        * policy.restriction_delay_multiplier
                    )
                    < 0.01
                    for cost in costs
                ),
            )
        )
    if expected.get("restricted_segment_ids"):
        before = plan(graph, fixture, scenario, with_overlays=False)
        outcome.notes.append(
            f"primary ETA {_eta(result)} with the restriction, {_eta(before)} without it"
        )
    for segment in expected.get("unverified_limit_segment_ids", []):
        edges = [
            edge
            for edge in edges_for(graph, fixture, scenario)
            if edge.segment_id == segment
        ]
        outcome.checks.append(
            Check(
                f"{segment} is a bridge with no limit on record, and none is made up",
                bool(edges)
                and all(
                    edge.is_bridge
                    and edge.max_weight_t is None
                    and edge.max_height_m is None
                    for edge in edges
                ),
            )
        )
        over = [item for item in result.alternatives if segment in item.segment_ids]
        outcome.checks.append(
            Check(
                "every route over it says the limit is unverified and needs review",
                bool(over)
                and all(
                    item.requires_review
                    and _warned(item, "unknown_constraint", segment)
                    for item in over
                ),
                f"{len(over)} of {len(result.alternatives)} routes",
            )
        )
        kept_off = [
            vehicle_id
            for vehicle_id in fixture["vehicles"]
            if segment
            in _excluded(plan(graph, fixture, scenario, vehicle_id=vehicle_id))
        ]
        outcome.checks.append(
            Check(
                "an unverified limit keeps no vehicle off the bridge",
                not kept_off,
                ", ".join(kept_off),
            )
        )
    if "primary_keeps_warned_road" in expected:
        warned = {item["segment_id"] for item in overlays.get("risk_observations", [])}
        keeps = bool(result.alternatives) and bool(
            warned & set(result.alternatives[0].segment_ids)
        )
        wanted = bool(expected["primary_keeps_warned_road"])
        outcome.checks.append(
            Check(
                "the primary route keeps the warned road"
                if wanted
                else "the primary route drives around the warned road",
                keeps == wanted,
            )
        )
    if expected.get("eta_includes_risk_penalty") is False:
        scores = {
            edge.segment_id: float(edge.risk_score)
            for edge in edges_for(graph, fixture, scenario)
            if edge.risk_score is not None
        }
        outcome.checks.append(
            Check(
                "every ETA is a band around driving time, with no risk penalty in it",
                bool(result.alternatives)
                and all(
                    abs(
                        item.travel_time_seconds
                        - sum(
                            cost["base_time_seconds"] + cost["delay_seconds"]
                            for cost in item.edge_costs
                        )
                    )
                    < 0.01
                    and item.eta_range_seconds
                    == eta_band(
                        item.travel_time_seconds,
                        policy,
                        _warned(item, "unobserved_segment_state"),
                    )
                    for item in result.alternatives
                ),
            )
        )
        priced = [
            cost
            for item in result.alternatives
            for cost in item.edge_costs
            if cost["segment_id"] in scores
        ]
        outcome.checks.append(
            Check(
                "a warned road on a route costs risk weight x score x km, in cost only",
                bool(priced)
                and all(
                    abs(
                        cost["risk_penalty_seconds"]
                        - policy.risk_weight_seconds
                        * scores[cost["segment_id"]]
                        * cost["length_m"]
                        / 1000.0
                    )
                    < 0.01
                    for cost in priced
                ),
                f"{len(priced)} warned edge(s) on the offered routes",
            )
        )
        before = plan(graph, fixture, scenario, with_overlays=False)
        outcome.notes.append(
            f"primary ETA {_eta(result)} with the warning, {_eta(before)} without it"
        )
    return outcome


def replay(
    graph_path: Path = GRAPH, scenarios_path: Path = SCENARIOS
) -> list[ScenarioResult]:
    graph, fixture = load(graph_path, scenarios_path)
    return [judge(graph, fixture, scenario) for scenario in fixture["scenarios"]]


def main() -> int:
    results = replay()
    print("SYNTHETIC scenarios through the production planner (not real roads)")
    for result in results:
        if not result.routing:
            print(f"{result.scenario_id}  {result.title}: not a routing scenario")
            continue
        verdict = "pass" if result.passed else "FAIL"
        distance = f", {result.distance_m / 1000:.2f} km" if result.distance_m else ""
        print(
            f"{result.scenario_id}  {result.title}: {verdict} ({result.status}{distance})"
        )
        for check in result.checks:
            mark = "ok  " if check.passed else "FAIL"
            detail = f" [{check.detail}]" if check.detail else ""
            print(f"      {mark} {check.name}{detail}")
        for note in result.notes:
            print(f"      note {note}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
