"""The production planner on the fixed SYNTHETIC scenarios (requirement R16).

The scenarios are invented closures, limits, weather and vehicles on the pilot's
OSM topology. Passing them shows the planner keeps its rules; it is not evidence
about real roads.
"""

from __future__ import annotations

from app.routing.replay import replay


def test_every_routing_scenario_meets_its_stated_expectation() -> None:
    results = replay()
    failures = {
        result.scenario_id: [check.name for check in result.checks if not check.passed]
        for result in results
        if not result.passed
    }
    assert failures == {}
    assert sum(result.routing for result in results) >= 5


def test_the_replay_scores_cases_where_the_right_answer_is_to_change_nothing() -> None:
    checks = {check.name for result in replay() for check in result.checks}
    assert "a forecast alone excludes no road" in checks
    assert "a lighter vehicle may still use it" in checks


def test_a_closure_scenario_is_only_counted_if_it_changed_the_route() -> None:
    (closure,) = [result for result in replay() if result.scenario_id == "S2"]
    names = [check.name for check in closure.checks]
    assert any(name.startswith("without the closure") for name in names)
