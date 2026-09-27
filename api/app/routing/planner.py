"""Turns an eligible graph into a dispatcher-facing route plan."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.routing.core import (
    CostPolicy,
    Edge,
    EdgeCost,
    EligibleGraph,
    VehicleProfile,
    build_eligible_graph,
    difference_ratio,
    eta_band,
    search,
    stable_hash,
)

#: Categories, in the order they are attempted. The first is the plain answer;
#: the others exist so a dispatcher has something to choose between.
CATEGORY_FASTEST = "fastest_feasible"
CATEGORY_LOWER_EXPOSURE = "lower_exposure"
CATEGORY_ALTERNATE = "alternate"

#: Added to every edge already shown, to push the next search somewhere else.
DIVERSION_PENALTY_SECONDS = 600.0

MAX_ALTERNATIVES = 3


@dataclass(frozen=True)
class RouteRequest:
    origin_node_id: str
    destination_node_id: str
    vehicle: VehicleProfile
    priority: str = "normal"
    requested_alternatives: int = MAX_ALTERNATIVES


@dataclass(frozen=True)
class Snapshot:
    """What the plan was computed against, frozen at request time."""

    network_version: str
    risk_snapshot_version: str
    cost_policy_version: str
    graph_edge_count: int
    computed_at: str


@dataclass
class Alternative:
    id: str
    rank: int
    category: str
    edge_ids: list[str]
    segment_ids: list[str]
    node_ids: list[str]
    distance_m: float
    travel_time_seconds: float
    cost_seconds: float
    eta_range_seconds: tuple[int, int]
    risk_summary: dict[str, Any]
    constraint_warnings: list[dict[str, Any]]
    reasons: list[str]
    edge_costs: list[dict[str, Any]] = field(default_factory=list)
    requires_review: bool = False


@dataclass
class RoutePlanResult:
    status: str
    snapshot: Snapshot
    alternatives: list[Alternative]
    exclusions: dict[str, list[dict[str, Any]]]
    coverage_warnings: list[dict[str, Any]]
    reason_codes: list[str]
    actions: list[str]
    safe_route_claim: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "network_version": self.snapshot.network_version,
            "risk_snapshot_version": self.snapshot.risk_snapshot_version,
            "cost_policy_version": self.snapshot.cost_policy_version,
            "graph_edge_count": self.snapshot.graph_edge_count,
            "computed_at": self.snapshot.computed_at,
            "alternatives": [
                {
                    "id": item.id,
                    "rank": item.rank,
                    "category": item.category,
                    "segment_ids": item.segment_ids,
                    "node_ids": item.node_ids,
                    "distance_m": item.distance_m,
                    "travel_time_seconds": item.travel_time_seconds,
                    "cost_seconds": item.cost_seconds,
                    "eta_range_seconds": list(item.eta_range_seconds),
                    "risk_summary": item.risk_summary,
                    "constraint_warnings": item.constraint_warnings,
                    "reasons": item.reasons,
                    "requires_review": item.requires_review,
                }
                for item in self.alternatives
            ],
            "exclusions": self.exclusions,
            "coverage_warnings": self.coverage_warnings,
            "reason_codes": self.reason_codes,
            "actions": self.actions,
            "safe_route_claim": self.safe_route_claim,
        }


def _summarise_exclusions(graph: EligibleGraph) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in graph.exclusions:
        grouped.setdefault(item.reason, []).append(
            {"segment_id": item.segment_id, **item.detail}
        )
    return grouped


def _describe_path(
    graph: EligibleGraph,
    edge_ids: Sequence[str],
    origin: str,
    policy: CostPolicy,
    rank: int,
    category: str,
    previously_shown: set[str],
) -> Alternative:
    costs: list[EdgeCost] = [graph.costs_by_id[edge_id] for edge_id in edge_ids]
    edges: list[Edge] = [graph.edges_by_id[edge_id] for edge_id in edge_ids]

    node_ids = [origin] + [edge.to_node for edge in edges]
    segment_ids = [edge.segment_id for edge in edges]

    distance = sum(cost.length_m for cost in costs)
    travel = sum(cost.travel_seconds for cost in costs)
    total_cost = sum(cost.cost_seconds for cost in costs)

    risk_scores = [edge.risk_score for edge in edges if edge.risk_score is not None]
    segments_without_risk = sum(1 for edge in edges if edge.risk_score is None)
    risk_summary: dict[str, Any] = {
        "max_score": round(max(risk_scores), 4) if risk_scores else None,
        "mean_score": round(sum(risk_scores) / len(risk_scores), 4)
        if risk_scores
        else None,
        "scored_segments": len(risk_scores),
        # Named so nobody reads "max_score: null" as "no risk".
        "segments_without_a_risk_score": segments_without_risk,
    }

    unknown_constraints = sorted(
        {
            edge.segment_id
            for edge in edges
            if edge.segment_id in graph.unknown_constraint_segments
        }
    )
    unknown_state = sorted(
        {
            edge.segment_id
            for edge in edges
            if edge.segment_id in graph.unknown_state_segments
        }
    )
    restricted = sorted(
        {edge.segment_id for edge in edges if edge.passability == "restricted"}
    )

    warnings: list[dict[str, Any]] = []
    if unknown_constraints:
        warnings.append(
            {
                "code": "unknown_constraint",
                "segment_ids": unknown_constraints,
                "message": "A load limit on this route has never been established.",
                "requires_review": True,
            }
        )
    if unknown_state:
        warnings.append(
            {
                "code": "unobserved_segment_state",
                "segment_ids": unknown_state,
                "message": "Nobody has reported on these segments; their state is unknown, not open.",
                "requires_review": False,
            }
        )
    if restricted:
        warnings.append(
            {
                "code": "restricted_segment_used",
                "segment_ids": restricted,
                "message": "This route uses road that is passable under restriction.",
                "requires_review": True,
            }
        )

    reasons: list[str] = []
    if category == CATEGORY_FASTEST:
        reasons.append("Least total cost under the vehicle's hard constraints.")
    elif category == CATEGORY_LOWER_EXPOSURE:
        reasons.append("Trades time for lower risk exposure than the fastest option.")
    else:
        novel = difference_ratio(edge_ids, previously_shown)
        reasons.append(
            f"Differs from the options above on {round(novel * 100)}% of its segments."
        )
    if unknown_constraints:
        reasons.append(
            f"{len(unknown_constraints)} segment(s) carry a limit nobody has verified."
        )

    identity = stable_hash(
        {"edges": list(edge_ids), "category": category, "rank": rank}
    )
    return Alternative(
        id=f"alt-{identity[:20]}",
        rank=rank,
        category=category,
        edge_ids=list(edge_ids),
        segment_ids=segment_ids,
        node_ids=node_ids,
        distance_m=round(distance, 3),
        travel_time_seconds=round(travel, 3),
        cost_seconds=round(total_cost, 3),
        eta_range_seconds=eta_band(travel, policy, bool(unknown_state)),
        risk_summary=risk_summary,
        constraint_warnings=warnings,
        reasons=reasons,
        edge_costs=[
            cost.__dict__ | {"travel_seconds": cost.travel_seconds} for cost in costs
        ],
        requires_review=bool(unknown_constraints or restricted),
    )


def _no_route_reasons(graph: EligibleGraph) -> tuple[list[str], list[str]]:
    grouped = {item.reason for item in graph.exclusions}
    reasons: list[str] = []
    actions: list[str] = []

    if "segment_closed" in grouped:
        reasons.append("closed_segments_disconnect_route")
        actions.append("Review the confirmed closures listed under exclusions.")
    if "vehicle_exceeds_max_weight" in grouped:
        reasons.append("vehicle_constraints_disconnect_route")
        actions.append(
            "Consider a lighter vehicle, or verify the excluded weight limits."
        )
    if "vehicle_exceeds_max_height" in grouped:
        if "vehicle_constraints_disconnect_route" not in reasons:
            reasons.append("vehicle_constraints_disconnect_route")
        actions.append(
            "Consider a lower vehicle, or verify the excluded height limits."
        )
    if not reasons:
        reasons.append("no_path_in_directed_graph")
        actions.append(
            "The graph has no directed path between these points even with nothing excluded."
        )

    actions.append("Assign an inspection to establish the state of the excluded road.")
    return reasons, actions


def plan_routes(
    edges: Iterable[Edge],
    request: RouteRequest,
    snapshot: Snapshot,
    policy: CostPolicy | None = None,
) -> RoutePlanResult:
    """Produce the primary route and up to two meaningfully different options."""

    active_policy = policy or CostPolicy()
    graph = build_eligible_graph(
        edges, request.vehicle, active_policy, request.priority
    )
    exclusions = _summarise_exclusions(graph)

    origin = request.origin_node_id
    destination = request.destination_node_id

    coverage: list[dict[str, Any]] = []
    if graph.unknown_state_segments:
        coverage.append(
            {
                "code": "unobserved_network",
                "segment_count": len(graph.unknown_state_segments),
                "message": (
                    "Part of this district has no observed passability. "
                    "Routes across it are computed, not confirmed."
                ),
            }
        )

    if origin not in graph.adjacency and origin != destination:
        return RoutePlanResult(
            status="no_route",
            snapshot=snapshot,
            alternatives=[],
            exclusions=exclusions,
            coverage_warnings=coverage,
            reason_codes=["origin_has_no_usable_road"],
            actions=[
                "Every road leaving the origin is excluded or absent; check the origin node and the exclusions.",
            ],
        )

    wanted = max(1, min(request.requested_alternatives, MAX_ALTERNATIVES))
    alternatives: list[Alternative] = []
    shown_edges: set[str] = set()
    penalties: dict[str, float] = {}

    categories = [CATEGORY_FASTEST, CATEGORY_LOWER_EXPOSURE, CATEGORY_ALTERNATE]
    for index in range(wanted):
        path = search(graph, origin, destination, penalties)
        if path is None:
            break
        if alternatives:
            novel = difference_ratio(path, shown_edges)
            # A near-identical line is not a choice. Dropping it is more useful
            # than labelling the same road a second option.
            if novel < active_policy.minimum_alternative_difference:
                break
        alternative = _describe_path(
            graph,
            path,
            origin,
            active_policy,
            rank=len(alternatives) + 1,
            category=categories[min(index, len(categories) - 1)],
            previously_shown=set(shown_edges),
        )
        alternatives.append(alternative)
        shown_edges.update(path)
        for edge_id in path:
            penalties[edge_id] = penalties.get(edge_id, 0.0) + DIVERSION_PENALTY_SECONDS

    if not alternatives:
        reasons, actions = _no_route_reasons(graph)
        return RoutePlanResult(
            status="no_route",
            snapshot=snapshot,
            alternatives=[],
            exclusions=exclusions,
            coverage_warnings=coverage,
            reason_codes=reasons,
            actions=actions,
        )

    return RoutePlanResult(
        status="feasible",
        snapshot=snapshot,
        alternatives=alternatives,
        exclusions=exclusions,
        coverage_warnings=coverage,
        reason_codes=[],
        actions=[],
    )


__all__ = [
    "CATEGORY_ALTERNATE",
    "CATEGORY_FASTEST",
    "CATEGORY_LOWER_EXPOSURE",
    "DIVERSION_PENALTY_SECONDS",
    "MAX_ALTERNATIVES",
    "Alternative",
    "RoutePlanResult",
    "RouteRequest",
    "Snapshot",
    "plan_routes",
]
