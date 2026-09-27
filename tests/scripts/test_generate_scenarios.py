import unittest

from scripts.pipeline.generate_scenarios import find_bridges, reachable_nodes


class ScenarioGraphHelpersTest(unittest.TestCase):
    def test_bridge_detection_handles_cycle_and_tail(self) -> None:
        segments = {
            "a": ("1", "2"),
            "b": ("2", "3"),
            "c": ("3", "1"),
            "tail": ("3", "4"),
        }
        self.assertEqual(find_bridges(segments), {"tail"})

    def test_reachability_respects_physical_segment_closure(self) -> None:
        graph = {
            "edges": [
                {
                    "edge_id": "a-forward",
                    "segment_id": "a",
                    "from_node": "1",
                    "to_node": "2",
                },
                {
                    "edge_id": "a-reverse",
                    "segment_id": "a",
                    "from_node": "2",
                    "to_node": "1",
                },
            ]
        }
        self.assertEqual(reachable_nodes(graph, "1"), {"1", "2"})
        self.assertEqual(reachable_nodes(graph, "1", {"a"}), {"1"})


if __name__ == "__main__":
    unittest.main()
