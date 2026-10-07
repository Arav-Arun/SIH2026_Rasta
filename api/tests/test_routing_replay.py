"""The production planner on the fixed SYNTHETIC scenarios (requirement R16).

The scenarios are invented closures, limits, weather and vehicles on the pilot's
OSM topology. Passing them shows the planner keeps its rules; it is not evidence
about real roads.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from app.routing.replay import REPOSITORY_ROOT, SCENARIOS, judge, load, plan, replay

GRAPH, FIXTURE = load()


def _scenario(scenario_id: str) -> dict[str, Any]:
    return copy.deepcopy(
        next(item for item in FIXTURE["scenarios"] if item["id"] == scenario_id)
    )


def _failed(scenario: dict[str, Any]) -> set[str]:
    return {
        check.name
        for check in judge(GRAPH, FIXTURE, scenario).checks
        if not check.passed
    }


def test_every_routing_scenario_meets_its_stated_expectation() -> None:
    results = replay()
    failures = {
        result.scenario_id: [check.name for check in result.checks if not check.passed]
        for result in results
        if not result.passed
    }
    assert failures == {}
    assert sum(result.routing for result in results) >= 10


def test_the_replay_scores_cases_where_the_right_answer_is_to_change_nothing() -> None:
    checks = {check.name for result in replay() for check in result.checks}
    assert "a forecast alone excludes no road" in checks
    assert "a lighter vehicle may still use it" in checks
    assert "an unverified limit keeps no vehicle off the bridge" in checks


def test_a_closure_scenario_is_only_counted_if_it_changed_the_route() -> None:
    (closure,) = [result for result in replay() if result.scenario_id == "S2"]
    names = [check.name for check in closure.checks]
    assert any(name.startswith("without the closure") for name in names)


def test_every_closure_in_a_multi_closure_scenario_must_have_mattered() -> None:
    scenario = _scenario("S9")
    assert "each closure lay on the best route the earlier ones left" not in _failed(
        scenario
    )
    # A closure far from the route changes nothing, so the scenario stops counting.
    scenario["overlays"]["segment_states"][-1]["segment_id"] = (
        "osm-way-132319025-segment-0"
    )
    assert "each closure lay on the best route the earlier ones left" in _failed(
        scenario
    )


def test_a_restriction_no_route_uses_does_not_pass_as_handled() -> None:
    scenario = _scenario("S7")
    road = "osm-way-122094967-segment-2"
    scenario["overlays"]["segment_states"][0]["segment_id"] = road
    scenario["expected"]["restricted_segment_ids"] = [road]
    assert f"{road} is restricted, not closed: a route still uses it" in _failed(
        scenario
    )


def test_a_made_up_bridge_limit_is_caught() -> None:
    scenario = _scenario("S8")
    (bridge,) = scenario["expected"]["unverified_limit_segment_ids"]
    scenario["overlays"]["segment_constraints"] = [
        {"segment_id": bridge, "max_weight_t": 10.0, "source_mode": "synthetic"}
    ]
    failed = _failed(scenario)
    assert (
        f"{bridge} is a bridge with no limit on record, and none is made up" in failed
    )
    assert "every route over it says the limit is unverified and needs review" in failed


def test_a_risk_warning_moves_cost_and_never_the_eta_of_the_same_route() -> None:
    clear = plan(GRAPH, FIXTURE, _scenario("S1")).alternatives[0]
    priced = plan(GRAPH, FIXTURE, _scenario("S10")).alternatives[0]
    assert priced.segment_ids == clear.segment_ids
    assert priced.eta_range_seconds == clear.eta_range_seconds
    assert priced.travel_time_seconds == clear.travel_time_seconds
    penalty = sum(cost["risk_penalty_seconds"] for cost in priced.edge_costs)
    assert penalty > 0
    assert abs(priced.cost_seconds - clear.cost_seconds - penalty) < 0.01


def test_a_red_warning_lengthens_the_eta_only_by_the_detour_driven() -> None:
    clear = plan(GRAPH, FIXTURE, _scenario("S1")).alternatives[0]
    detour = plan(GRAPH, FIXTURE, _scenario("S11")).alternatives[0]
    (warned,) = {
        item["segment_id"] for item in _scenario("S11")["overlays"]["risk_observations"]
    }
    assert warned in clear.segment_ids and warned not in detour.segment_ids
    assert detour.travel_time_seconds > clear.travel_time_seconds
    assert sum(cost["risk_penalty_seconds"] for cost in detour.edge_costs) == 0


def test_the_manifest_describes_the_scenario_file_as_committed() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "data" / "manifests" / "synthetic_scenarios.json").read_text(
            encoding="utf-8"
        )
    )
    digest = hashlib.sha256(SCENARIOS.read_bytes()).hexdigest()
    assert manifest["output_checksum"] == f"sha256:{digest}"
    assert manifest["fixture_set_id"] == FIXTURE["fixture_set_id"]
    authored = set(manifest["hand_authored"]["scenario_ids"])
    assert authored <= {item["id"] for item in FIXTURE["scenarios"]}
