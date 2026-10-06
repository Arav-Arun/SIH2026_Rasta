"""The database-to-graph translation, where an honest mistake is expensive.

Reading a bridge's limit from the wrong column, or treating an unobserved
segment as open, would produce a plan that looks correct and routes a truck
over a bridge nobody has weighed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.routing.graph import (
    PILOT_ROAD_CLASS_SPEED_KPH,
    _version_of,
    load_district_graph,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params or {}))
        return FakeCursor(self._rows)


def row(**over):
    base = {
        "segment_id": "seg-1",
        "from_node_id": "n1",
        "to_node_id": "n2",
        "length_m": 1000.0,
        "road_class": "trunk",
        "base_speed_kph": None,
        "segment_max_weight_t": None,
        "network_version": "net-1",
        "passability": "unknown",
        "risk_score": None,
        "risk_level": "unknown",
        "state_as_of": None,
        "state_network_version": None,
        "bridge_id": None,
        "bridge_max_weight_t": None,
    }
    base.update(over)
    return base


def load(rows):
    connection = FakeConnection(rows)
    return load_district_graph(
        connection, organization_id="org-1", district_id="district-1"
    )


class TestGraphLoading:
    def test_a_segment_becomes_one_directed_edge_keyed_by_its_own_id(self) -> None:
        graph = load([row()])
        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.edge_id == edge.segment_id == "seg-1"
        assert (edge.from_node, edge.to_node) == ("n1", "n2")

    def test_nodes_are_counted_without_double_counting_shared_ones(self) -> None:
        graph = load(
            [row(), row(segment_id="seg-2", from_node_id="n2", to_node_id="n3")]
        )
        assert graph.node_count == 3

    def test_a_bridge_limit_overrides_the_segment_limit(self) -> None:
        """Both describe the same road; the bridge is the narrower statement."""

        graph = load(
            [row(segment_max_weight_t=20.0, bridge_id="b-1", bridge_max_weight_t=5.0)]
        )
        assert graph.edges[0].max_weight_t == 5.0
        assert graph.edges[0].is_bridge is True

    def test_a_bridge_with_no_recorded_limit_stays_unknown_not_zero(self) -> None:
        graph = load([row(bridge_id="b-1", bridge_max_weight_t=None)])
        assert graph.edges[0].max_weight_t is None
        assert graph.edges[0].is_bridge is True

    def test_a_segment_limit_is_used_where_there_is_no_bridge(self) -> None:
        graph = load([row(segment_max_weight_t=12.0)])
        assert graph.edges[0].max_weight_t == 12.0
        assert graph.edges[0].is_bridge is False

    def test_a_missing_state_row_reads_as_unknown_and_is_counted(self) -> None:
        # The join produces 'unknown' rather than null, and the count is what
        # the plan's coverage warning is built from.
        graph = load([row(passability="unknown", state_as_of=None)])
        assert graph.edges[0].passability == "unknown"
        assert graph.unobserved_segment_count == 1

    def test_an_observed_segment_is_not_counted_as_unobserved(self) -> None:
        graph = load(
            [
                row(
                    passability="open",
                    state_as_of=datetime(2026, 9, 25, tzinfo=UTC),
                    state_network_version="net-1",
                )
            ]
        )
        assert graph.unobserved_segment_count == 0
        assert graph.edges[0].state_as_of is not None


class TestSnapshotVersions:
    def test_one_shared_version_is_passed_through_unchanged(self) -> None:
        assert _version_of(["net-7", "net-7"]) == "net-7"

    def test_mixed_versions_collapse_to_a_single_token_that_still_changes(self) -> None:
        mixed = _version_of(["net-7", "net-8"])
        assert mixed.startswith("mixed-")
        assert mixed != _version_of(["net-7", "net-9"])

    def test_the_token_does_not_depend_on_row_order(self) -> None:
        assert _version_of(["b", "a"]) == _version_of(["a", "b"])

    def test_an_entirely_unobserved_district_says_so_in_its_risk_token(self) -> None:
        graph = load([row()])
        assert graph.risk_snapshot_version.startswith("state-unobserved+")
        assert load([]).risk_snapshot_version == "state-unobserved"

    def test_the_risk_token_moves_when_a_score_does_and_not_when_it_is_rewritten(
        self,
    ) -> None:
        scored = load(
            [row(risk_score=0.41, risk_level="moderate")]
        ).risk_snapshot_version
        rewritten = load(
            [row(risk_score=0.41, risk_level="moderate")]
        ).risk_snapshot_version
        changed = load([row(risk_score=0.62, risk_level="high")]).risk_snapshot_version
        assert scored == rewritten
        assert scored != changed

    def test_the_risk_token_moves_when_a_segment_state_does(self) -> None:
        earlier = load(
            [
                row(
                    state_as_of=datetime(2026, 9, 1, tzinfo=UTC),
                    state_network_version="net-1",
                )
            ]
        ).risk_snapshot_version
        later = load(
            [
                row(
                    state_as_of=datetime(2026, 9, 2, tzinfo=UTC),
                    state_network_version="net-1",
                )
            ]
        ).risk_snapshot_version
        assert earlier != later

    def test_an_empty_district_is_reported_rather_than_crashing(self) -> None:
        graph = load([])
        assert graph.edges == []
        assert graph.network_version == "empty"


class TestRoadClassPolicy:
    def test_every_speed_is_positive(self) -> None:
        for road_class, speed in PILOT_ROAD_CLASS_SPEED_KPH.items():
            assert speed > 0, road_class

    def test_the_hierarchy_is_ordered_as_a_road_network_actually_is(self) -> None:
        speeds = PILOT_ROAD_CLASS_SPEED_KPH
        assert speeds["motorway"] > speeds["trunk"] > speeds["primary"]
        assert speeds["primary"] > speeds["residential"] > speeds["path"]


class TestVersionHelperEdgeCases:
    def test_a_single_unknown_version_is_not_hashed_away(self) -> None:
        assert _version_of(["none"]) == "none"

    @pytest.mark.parametrize("values", [["a"], ["a", "a", "a"]])
    def test_duplicates_do_not_make_a_version_look_mixed(self, values) -> None:
        assert _version_of(values) == "a"
