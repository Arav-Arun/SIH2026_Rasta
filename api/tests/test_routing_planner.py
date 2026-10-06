"""Unit proof of the routing rules that keep a plan honest.

These run on a hand-built graph rather than the pilot import, so each rule is
isolated: the acceptance script in `scripts/run_t023_runtime_acceptance.py`
proves the same rules over the real 2,860-segment network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.routing.core import (
    CostPolicy,
    Edge,
    VehicleProfile,
    build_eligible_graph,
    classify_edge,
    cost_edge,
    difference_ratio,
    eta_band,
    search,
)
from app.routing.planner import (
    CATEGORY_FASTEST,
    RouteRequest,
    Snapshot,
    plan_routes,
)

POLICY = CostPolicy(road_class_speed_kph={"trunk": 60.0, "residential": 20.0})
SNAPSHOT = Snapshot(
    network_version="net-1",
    risk_snapshot_version="risk-1",
    cost_policy_version=POLICY.version,
    graph_edge_count=0,
    computed_at=datetime(2026, 9, 25, tzinfo=UTC).isoformat(),
)
LIGHT = VehicleProfile(vehicle_id="v-light", gross_weight_t=3.5)
HEAVY = VehicleProfile(vehicle_id="v-heavy", gross_weight_t=12.0)


def edge(edge_id: str, a: str, b: str, **over) -> Edge:
    defaults = {
        "segment_id": f"seg-{edge_id}",
        "length_m": 1000.0,
        "road_class": "trunk",
        "passability": "open",
    }
    defaults.update(over)
    return Edge(edge_id=edge_id, from_node=a, to_node=b, **defaults)


def plan(edges, vehicle=LIGHT, origin="A", destination="C", **over):
    request = RouteRequest(
        origin_node_id=origin, destination_node_id=destination, vehicle=vehicle, **over
    )
    return plan_routes(edges, request, SNAPSHOT, POLICY)


class TestHardExclusions:
    """A closed road is excluded, not merely made expensive."""

    def test_a_closed_segment_appears_on_no_alternative(self) -> None:
        edges = [
            edge("direct", "A", "C", passability="closed", length_m=500.0),
            edge("long-1", "A", "B", length_m=1000.0),
            edge("long-2", "B", "C", length_m=1000.0),
        ]
        result = plan(edges)
        assert result.status == "feasible"
        for alternative in result.alternatives:
            assert "seg-direct" not in alternative.segment_ids
        assert result.exclusions["segment_closed"] == [
            {"segment_id": "seg-direct", "passability": "closed"}
        ]

    def test_a_bridge_lighter_than_the_vehicle_appears_on_no_alternative(self) -> None:
        edges = [
            edge("bridge", "A", "C", is_bridge=True, max_weight_t=5.0, length_m=500.0),
            edge("long-1", "A", "B", length_m=1000.0),
            edge("long-2", "B", "C", length_m=1000.0),
        ]
        result = plan(edges, vehicle=HEAVY)
        assert result.status == "feasible"
        for alternative in result.alternatives:
            assert "seg-bridge" not in alternative.segment_ids
        assert result.exclusions["vehicle_exceeds_max_weight"][0]["max_weight_t"] == 5.0

    def test_the_same_bridge_is_usable_by_a_vehicle_within_its_limit(self) -> None:
        # The exclusion must be about this vehicle, not about the bridge.
        edges = [edge("bridge", "A", "C", is_bridge=True, max_weight_t=5.0)]
        result = plan(edges, vehicle=LIGHT)
        assert result.status == "feasible"
        assert result.alternatives[0].segment_ids == ["seg-bridge"]

    def test_height_is_enforced_the_same_way_as_weight(self) -> None:
        tall = VehicleProfile(vehicle_id="v-tall", gross_weight_t=3.0, height_m=4.2)
        allowed, reason, detail = classify_edge(
            edge("low", "A", "B", max_height_m=3.5), tall
        )
        assert allowed is False
        assert reason == "vehicle_exceeds_max_height"
        assert detail["max_height_m"] == 3.5


class TestUnknownIsNotAPass:
    def test_an_unknown_bridge_limit_is_a_warning_that_needs_review(self) -> None:
        edges = [edge("bridge", "A", "C", is_bridge=True, max_weight_t=None)]
        result = plan(edges, vehicle=HEAVY)
        assert result.status == "feasible"
        warning = result.alternatives[0].constraint_warnings[0]
        assert warning["code"] == "unknown_constraint"
        assert warning["requires_review"] is True
        assert result.alternatives[0].requires_review is True

    def test_no_result_claims_the_route_is_safe(self) -> None:
        result = plan([edge("only", "A", "C")])
        assert result.safe_route_claim is False

    def test_an_unobserved_segment_is_reported_as_unknown_not_open(self) -> None:
        edges = [edge("only", "A", "C", passability="unknown")]
        result = plan(edges)
        codes = {item["code"] for item in result.alternatives[0].constraint_warnings}
        assert "unobserved_segment_state" in codes
        assert result.coverage_warnings[0]["code"] == "unobserved_network"

    def test_a_segment_with_no_risk_score_is_counted_not_treated_as_zero(self) -> None:
        edges = [edge("a", "A", "B", risk_score=0.4), edge("b", "B", "C")]
        summary = plan(edges).alternatives[0].risk_summary
        assert summary["scored_segments"] == 1
        assert summary["segments_without_a_risk_score"] == 1
        assert summary["max_score"] == 0.4


class TestNoRoute:
    def test_a_disconnected_destination_explains_the_cut_and_what_to_do(self) -> None:
        edges = [
            edge("direct", "A", "C", passability="closed"),
            edge("dead-end", "A", "B"),
        ]
        result = plan(edges)
        assert result.status == "no_route"
        assert result.alternatives == []
        assert "closed_segments_disconnect_route" in result.reason_codes
        assert result.actions, "a no-route must say what to do next"
        assert result.exclusions["segment_closed"]

    def test_a_graph_with_no_path_says_so_without_blaming_a_closure(self) -> None:
        result = plan([edge("elsewhere", "A", "B")], destination="Z")
        assert result.status == "no_route"
        assert result.reason_codes == ["no_path_in_directed_graph"]

    def test_an_origin_with_no_usable_road_is_named_specifically(self) -> None:
        result = plan([edge("out", "A", "C", passability="closed")])
        assert result.status == "no_route"
        assert result.reason_codes == ["origin_has_no_usable_road"]


class TestAlternatives:
    def test_a_near_identical_second_option_is_dropped_rather_than_shown(self) -> None:
        """Two names for the same road would overstate how much choice exists."""

        edges = [edge("a", "A", "B"), edge("b", "B", "C")]
        result = plan(edges)
        assert len(result.alternatives) == 1

    def test_genuinely_different_options_are_offered_and_ranked(self) -> None:
        edges = [
            edge("north-1", "A", "N", length_m=900.0),
            edge("north-2", "N", "C", length_m=900.0),
            edge("south-1", "A", "S", length_m=1100.0),
            edge("south-2", "S", "C", length_m=1100.0),
        ]
        result = plan(edges)
        assert len(result.alternatives) >= 2
        assert [item.rank for item in result.alternatives] == list(
            range(1, len(result.alternatives) + 1)
        )
        assert result.alternatives[0].category == CATEGORY_FASTEST
        first, second = result.alternatives[0], result.alternatives[1]
        assert set(first.segment_ids).isdisjoint(second.segment_ids)

    def test_the_fastest_option_really_is_the_cheapest(self) -> None:
        edges = [
            edge("north-1", "A", "N", length_m=900.0),
            edge("north-2", "N", "C", length_m=900.0),
            edge("south-1", "A", "S", length_m=1100.0),
            edge("south-2", "S", "C", length_m=1100.0),
        ]
        result = plan(edges)
        costs = [item.cost_seconds for item in result.alternatives]
        assert costs == sorted(costs)


class TestDeterminism:
    def test_the_same_request_returns_the_identical_plan(self) -> None:
        edges = [
            edge("p", "A", "M", length_m=1000.0),
            edge("q", "A", "M", length_m=1000.0),
            edge("r", "M", "C", length_m=1000.0),
        ]
        first = plan(edges).to_payload()
        second = plan(edges).to_payload()
        assert first == second

    def test_ties_break_on_edge_id_not_on_iteration_order(self) -> None:
        forward = [edge("aaa", "A", "C"), edge("zzz", "A", "C")]
        reversed_order = list(reversed(forward))
        assert (
            plan(forward).alternatives[0].segment_ids
            == plan(reversed_order).alternatives[0].segment_ids
            == ["seg-aaa"]
        )


class TestCostFunction:
    def test_every_term_is_reported_separately(self) -> None:
        cost = cost_edge(
            edge("e", "A", "B", passability="restricted", risk_score=0.5),
            LIGHT,
            POLICY,
            "normal",
        )
        assert cost.base_time_seconds == pytest.approx(60.0)
        assert cost.delay_seconds > 0
        assert cost.risk_penalty_seconds > 0
        assert cost.cost_seconds == pytest.approx(
            cost.base_time_seconds
            + cost.delay_seconds
            + cost.risk_penalty_seconds
            + cost.unknown_penalty_seconds
            + cost.priority_penalty_seconds
        )

    def test_a_segment_speed_is_preferred_over_the_class_default(self) -> None:
        cost = cost_edge(
            edge("e", "A", "B", base_speed_kph=120.0), LIGHT, POLICY, "normal"
        )
        assert cost.speed_source == "segment:base_speed_kph"
        assert cost.base_time_seconds == pytest.approx(30.0)

    def test_an_unconfigured_road_class_falls_back_and_says_so(self) -> None:
        cost = cost_edge(
            edge("e", "A", "B", road_class="track"), LIGHT, POLICY, "normal"
        )
        assert cost.speed_source == "policy:fallback_speed_kph"


class TestEtaBand:
    def test_an_eta_is_a_range_not_a_single_number(self) -> None:
        low, high = eta_band(1000.0, POLICY, uses_unknown_state=False)
        assert low < 1000 < high

    def test_the_band_widens_where_the_state_is_unobserved(self) -> None:
        known = eta_band(1000.0, POLICY, uses_unknown_state=False)
        unknown = eta_band(1000.0, POLICY, uses_unknown_state=True)
        assert unknown[1] - unknown[0] > known[1] - known[0]


class TestDifferenceRatio:
    def test_an_entirely_new_path_is_fully_different(self) -> None:
        assert difference_ratio(["x", "y"], ["a", "b"]) == 1.0

    def test_a_repeated_path_is_not_different_at_all(self) -> None:
        assert difference_ratio(["a", "b"], ["a", "b"]) == 0.0

    def test_an_empty_candidate_is_not_treated_as_novel(self) -> None:
        assert difference_ratio([], ["a"]) == 0.0


class TestSearch:
    def test_an_unreachable_destination_returns_nothing_rather_than_a_guess(
        self,
    ) -> None:
        graph = build_eligible_graph([edge("a", "A", "B")], LIGHT, POLICY)
        assert search(graph, "A", "Z") is None
