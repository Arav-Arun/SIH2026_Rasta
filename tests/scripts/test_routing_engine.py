import unittest

from services.api.app.routing import route_scenario


def edge(
    edge_id: str,
    segment_id: str,
    source: str,
    target: str,
    *,
    bridge: bool = False,
) -> dict:
    return {
        "edge_id": edge_id,
        "segment_id": segment_id,
        "from_node": source,
        "to_node": target,
        "length_m": 100,
        "road_class": "primary",
        "base_speed_kph": None,
        "base_speed_source": None,
        "bridge": bridge,
        "max_weight_t": None,
    }


def graph(edges: list[dict]) -> dict:
    node_ids = sorted(
        {item["from_node"] for item in edges} | {item["to_node"] for item in edges}
    )
    return {
        "graph_version": "test-graph-v1",
        "operational_status": "not_live",
        "source": {"name": "test"},
        "nodes": [{"node_id": node_id} for node_id in node_ids],
        "edges": edges,
    }


def fixture(scenario: dict, *, vehicle_weight: float = 3.5) -> dict:
    return {
        "graph_version": "test-graph-v1",
        "fixture_set_id": "test-fixtures",
        "mode": "synthetic",
        "ui_label": "SIMULATED SCENARIO",
        "simulation_epoch": "2000-01-01T00:00:00Z",
        "network_policy": {
            "mode": "synthetic",
            "road_class_speed_kph": {"primary": 30},
        },
        "vehicles": {
            "vehicle": {
                "synthetic": True,
                "gross_weight_t": vehicle_weight,
            }
        },
        "scenarios": [scenario],
    }


def scenario(
    *,
    states: list[dict] | None = None,
    constraints: list[dict] | None = None,
) -> dict:
    return {
        "id": "S",
        "route_request": {
            "origin_node_id": "a",
            "destination_node_id": "b",
            "vehicle_id": "vehicle",
        },
        "overlays": {
            "segment_states": states or [],
            "segment_constraints": constraints or [],
        },
    }


class RoutingEngineTest(unittest.TestCase):
    def test_closed_segment_is_excluded_and_alternate_is_used(self) -> None:
        network = graph(
            [
                edge("direct", "direct-segment", "a", "b"),
                edge("alternate-1", "alternate-1", "a", "c"),
                edge("alternate-2", "alternate-2", "c", "b"),
            ]
        )
        case = scenario(
            states=[
                {
                    "segment_id": "direct-segment",
                    "passability": "closed",
                }
            ]
        )
        result = route_scenario(network, fixture(case), "S")
        self.assertEqual(result["status"], "feasible")
        self.assertNotIn("direct-segment", result["path"]["segment_ids"])
        self.assertEqual(result["exclusions"]["closed_segment_ids"], ["direct-segment"])

    def test_incompatible_weight_returns_explained_no_route(self) -> None:
        network = graph([edge("bridge", "bridge-segment", "a", "b", bridge=True)])
        case = scenario(
            constraints=[
                {
                    "segment_id": "bridge-segment",
                    "max_weight_t": 5,
                }
            ]
        )
        result = route_scenario(network, fixture(case, vehicle_weight=8), "S")
        self.assertEqual(result["status"], "no_route")
        self.assertIn("vehicle_constraints_disconnect_route", result["reason_codes"])
        self.assertEqual(
            result["exclusions"]["vehicle_constraint_segment_ids"],
            ["bridge-segment"],
        )

    def test_unknown_bridge_limit_is_a_warning_not_a_pass_claim(self) -> None:
        network = graph([edge("bridge", "bridge-segment", "a", "b", bridge=True)])
        result = route_scenario(network, fixture(scenario()), "S")
        self.assertEqual(result["status"], "feasible")
        self.assertFalse(result["safe_route_claim"])
        self.assertEqual(result["warnings"][0]["code"], "unknown_bridge_weight_limit")


if __name__ == "__main__":
    unittest.main()
