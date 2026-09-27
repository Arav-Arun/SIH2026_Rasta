"""Deterministic constrained-route search shared by the proof and the API."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

COST_POLICY_VERSION = "routing-cost-v1"

#: Passability values that can never appear on a returned route.
BLOCKING_PASSABILITY = frozenset({"closed"})


@dataclass(frozen=True)
class Edge:
    """One directed, traversable segment of the road graph."""

    edge_id: str
    segment_id: str
    from_node: str
    to_node: str
    length_m: float
    road_class: str
    base_speed_kph: float | None = None
    max_weight_t: float | None = None
    max_height_m: float | None = None
    is_bridge: bool = False
    passability: str = "unknown"
    risk_score: float | None = None
    state_as_of: str | None = None


@dataclass(frozen=True)
class VehicleProfile:
    """What the vehicle needs the road to allow."""

    vehicle_id: str
    gross_weight_t: float | None = None
    height_m: float | None = None


@dataclass(frozen=True)
class CostPolicy:
    """Every weight in the cost function, versioned so a plan can be explained."""

    version: str = COST_POLICY_VERSION
    road_class_speed_kph: dict[str, float] = field(default_factory=dict)
    fallback_speed_kph: float = 30.0
    restriction_delay_multiplier: float = 0.75
    risk_weight_seconds: float = 900.0
    unknown_constraint_penalty_seconds: float = 120.0
    unknown_state_penalty_seconds: float = 30.0
    priority_penalty_seconds: dict[str, float] = field(default_factory=dict)
    #: Half-width of the ETA band, as a proportion of the travel time.
    eta_band_proportion: float = 0.25
    #: Extra band applied when any edge on the route has no observed state.
    eta_unknown_state_band_proportion: float = 0.15
    #: An alternative must differ from every shown route by at least this much.
    minimum_alternative_difference: float = 0.30

    def speed_for(self, edge: Edge) -> tuple[float, str]:
        if edge.base_speed_kph is not None and edge.base_speed_kph > 0:
            return float(edge.base_speed_kph), "segment:base_speed_kph"
        configured = self.road_class_speed_kph.get(edge.road_class)
        if configured is not None and configured > 0:
            return float(configured), f"policy:road_class:{edge.road_class}"
        return self.fallback_speed_kph, "policy:fallback_speed_kph"


@dataclass(frozen=True)
class Exclusion:
    segment_id: str
    edge_id: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeCost:
    edge_id: str
    segment_id: str
    length_m: float
    base_time_seconds: float
    delay_seconds: float
    risk_penalty_seconds: float
    unknown_penalty_seconds: float
    priority_penalty_seconds: float
    cost_seconds: float
    speed_kph: float
    speed_source: str

    @property
    def travel_seconds(self) -> float:
        return self.base_time_seconds + self.delay_seconds


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _round(value: float) -> float:
    return round(value, 3)


def cost_edge(
    edge: Edge, vehicle: VehicleProfile, policy: CostPolicy, priority: str
) -> EdgeCost:
    """Cost one edge under the documented function."""

    speed_kph, speed_source = policy.speed_for(edge)
    length_km = edge.length_m / 1000.0
    base_time = 3600.0 * length_km / speed_kph

    delay = (
        base_time * policy.restriction_delay_multiplier
        if edge.passability == "restricted"
        else 0.0
    )

    risk_penalty = 0.0
    if edge.risk_score is not None:
        risk_penalty = policy.risk_weight_seconds * float(edge.risk_score) * length_km

    unknown_penalty = 0.0
    if has_unknown_constraint(edge, vehicle):
        unknown_penalty += policy.unknown_constraint_penalty_seconds
    if edge.passability == "unknown":
        unknown_penalty += policy.unknown_state_penalty_seconds

    priority_penalty = policy.priority_penalty_seconds.get(priority, 0.0) * length_km

    total = base_time + delay + risk_penalty + unknown_penalty + priority_penalty
    return EdgeCost(
        edge_id=edge.edge_id,
        segment_id=edge.segment_id,
        length_m=_round(edge.length_m),
        base_time_seconds=_round(base_time),
        delay_seconds=_round(delay),
        risk_penalty_seconds=_round(risk_penalty),
        unknown_penalty_seconds=_round(unknown_penalty),
        priority_penalty_seconds=_round(priority_penalty),
        cost_seconds=_round(total),
        speed_kph=speed_kph,
        speed_source=speed_source,
    )


def has_unknown_constraint(edge: Edge, vehicle: VehicleProfile) -> bool:
    """Whether this edge carries a limit nobody has established."""

    if (
        edge.is_bridge
        and edge.max_weight_t is None
        and vehicle.gross_weight_t is not None
    ):
        return True
    return edge.max_height_m is None and vehicle.height_m is not None and edge.is_bridge


def classify_edge(
    edge: Edge, vehicle: VehicleProfile
) -> tuple[bool, str | None, dict[str, Any]]:
    """Decide whether an edge may be used, and say why when it may not."""

    if edge.passability in BLOCKING_PASSABILITY:
        return False, "segment_closed", {"passability": edge.passability}

    if (
        edge.max_weight_t is not None
        and vehicle.gross_weight_t is not None
        and float(vehicle.gross_weight_t) > float(edge.max_weight_t)
    ):
        return (
            False,
            "vehicle_exceeds_max_weight",
            {
                "max_weight_t": float(edge.max_weight_t),
                "vehicle_weight_t": float(vehicle.gross_weight_t),
            },
        )

    if (
        edge.max_height_m is not None
        and vehicle.height_m is not None
        and float(vehicle.height_m) > float(edge.max_height_m)
    ):
        return (
            False,
            "vehicle_exceeds_max_height",
            {
                "max_height_m": float(edge.max_height_m),
                "vehicle_height_m": float(vehicle.height_m),
            },
        )

    return True, None, {}


@dataclass
class EligibleGraph:
    adjacency: dict[str, list[tuple[str, EdgeCost]]]
    edges_by_id: dict[str, Edge]
    costs_by_id: dict[str, EdgeCost]
    exclusions: list[Exclusion]
    unknown_constraint_segments: set[str]
    unknown_state_segments: set[str]


def build_eligible_graph(
    edges: Iterable[Edge],
    vehicle: VehicleProfile,
    policy: CostPolicy,
    priority: str = "normal",
) -> EligibleGraph:
    adjacency: dict[str, list[tuple[str, EdgeCost]]] = {}
    edges_by_id: dict[str, Edge] = {}
    costs_by_id: dict[str, EdgeCost] = {}
    exclusions: list[Exclusion] = []
    unknown_constraint: set[str] = set()
    unknown_state: set[str] = set()

    for edge in edges:
        allowed, reason, detail = classify_edge(edge, vehicle)
        if not allowed:
            exclusions.append(
                Exclusion(
                    segment_id=edge.segment_id,
                    edge_id=edge.edge_id,
                    reason=reason or "excluded",
                    detail=detail,
                )
            )
            continue

        cost = cost_edge(edge, vehicle, policy, priority)
        edges_by_id[edge.edge_id] = edge
        costs_by_id[edge.edge_id] = cost
        adjacency.setdefault(edge.from_node, []).append((edge.to_node, cost))
        if has_unknown_constraint(edge, vehicle):
            unknown_constraint.add(edge.segment_id)
        if edge.passability == "unknown":
            unknown_state.add(edge.segment_id)

    # Deterministic expansion order, so two identical requests return the
    # identical path even where several cost exactly the same.
    for choices in adjacency.values():
        choices.sort(key=lambda item: item[1].edge_id)

    exclusions.sort(key=lambda item: (item.reason, item.edge_id))
    return EligibleGraph(
        adjacency=adjacency,
        edges_by_id=edges_by_id,
        costs_by_id=costs_by_id,
        exclusions=exclusions,
        unknown_constraint_segments=unknown_constraint,
        unknown_state_segments=unknown_state,
    )


def search(
    graph: EligibleGraph,
    origin: str,
    destination: str,
    penalties: dict[str, float] | None = None,
) -> list[str] | None:
    """Least-cost edge path, or None when the destination is unreachable."""

    extra = penalties or {}
    best: dict[str, tuple[float, str]] = {origin: (0.0, "")}
    previous: dict[str, tuple[str, str]] = {}
    queue: list[tuple[float, str, str]] = [(0.0, "", origin)]

    while queue:
        current_cost, _, node = heapq.heappop(queue)
        recorded = best.get(node, (math.inf, ""))[0]
        if not math.isclose(current_cost, recorded, rel_tol=0, abs_tol=1e-9):
            continue
        if node == destination:
            break
        for next_node, cost in graph.adjacency.get(node, []):
            candidate = current_cost + cost.cost_seconds + extra.get(cost.edge_id, 0.0)
            key = (round(candidate, 9), cost.edge_id)
            if key < best.get(next_node, (math.inf, "")):
                best[next_node] = key
                previous[next_node] = (node, cost.edge_id)
                heapq.heappush(queue, (candidate, cost.edge_id, next_node))

    if destination not in best:
        return None

    path: list[str] = []
    current = destination
    while current != origin:
        node, edge_id = previous[current]
        path.append(edge_id)
        current = node
    path.reverse()
    return path


def difference_ratio(candidate: Sequence[str], existing: Sequence[str]) -> float:
    """How much of a candidate route is not already shown, by edge count."""

    if not candidate:
        return 0.0
    shown = set(existing)
    novel = sum(1 for edge_id in candidate if edge_id not in shown)
    return novel / len(candidate)


def eta_band(
    travel_seconds: float, policy: CostPolicy, uses_unknown_state: bool
) -> tuple[int, int]:
    """A range, not a single number."""

    proportion = policy.eta_band_proportion
    if uses_unknown_state:
        proportion += policy.eta_unknown_state_band_proportion
    low = max(0, int(round(travel_seconds * (1 - proportion))))
    high = int(round(travel_seconds * (1 + proportion)))
    return low, high


__all__ = [
    "BLOCKING_PASSABILITY",
    "COST_POLICY_VERSION",
    "CostPolicy",
    "Edge",
    "EdgeCost",
    "EligibleGraph",
    "Exclusion",
    "VehicleProfile",
    "build_eligible_graph",
    "classify_edge",
    "cost_edge",
    "difference_ratio",
    "eta_band",
    "has_unknown_constraint",
    "search",
    "stable_hash",
]
