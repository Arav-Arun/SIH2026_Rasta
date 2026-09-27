import unittest

from scripts.pipeline.import_osm_pilot import (
    inside_query_bbox,
    parse_metric_number,
    way_direction,
)


class PilotImportHelpersTest(unittest.TestCase):
    def test_metric_parser_preserves_unknown_values(self) -> None:
        self.assertIsNone(parse_metric_number("none", "length"))
        self.assertIsNone(parse_metric_number("12;15", "weight"))
        self.assertIsNone(parse_metric_number(None, "speed"))

    def test_metric_parser_normalizes_supported_units(self) -> None:
        self.assertEqual(parse_metric_number("30", "speed"), 30)
        self.assertEqual(parse_metric_number("20 mph", "speed"), 32.187)
        self.assertEqual(parse_metric_number("8 ft", "length"), 2.438)

    def test_oneway_direction_is_explicit(self) -> None:
        self.assertEqual(way_direction({"oneway": "yes"}), "forward")
        self.assertEqual(way_direction({"oneway": "-1"}), "reverse")
        self.assertEqual(way_direction({"junction": "roundabout"}), "forward")
        self.assertEqual(way_direction({}), "both")

    def test_query_boundary_is_enforced(self) -> None:
        self.assertTrue(inside_query_bbox((25.57, 91.88)))
        self.assertTrue(inside_query_bbox((25.56, 91.875)))
        self.assertFalse(inside_query_bbox((25.59, 91.88)))


if __name__ == "__main__":
    unittest.main()
