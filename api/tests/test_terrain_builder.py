"""The terrain builder's arithmetic, checked against surfaces with a known slope."""

from __future__ import annotations

import array
import importlib.util
import math
from pathlib import Path

import pytest

SCRIPT = next(
    parent / "data" / "sources" / "build_terrain.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "data" / "sources" / "build_terrain.py").is_file()
)
spec = importlib.util.spec_from_file_location("build_terrain", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def plane_window(rise_per_metre: float, dx_m: float, direction: str) -> list[float]:
    """A 3x3 window (north row first) of a plane rising east or south."""

    window = []
    for row in (-1, 0, 1):
        for col in (-1, 0, 1):
            east = col * dx_m if direction == "east" else 0.0
            south = row * builder.ARC_SECOND_M if direction == "south" else 0.0
            window.append((east + south) * rise_per_metre)
    return window


def test_level_ground_has_no_slope() -> None:
    assert builder.horn_slope_deg([500] * 9, 28.0, 30.9) == 0.0


def test_a_one_in_one_plane_is_forty_five_degrees_in_either_direction() -> None:
    for direction in ("east", "south"):
        window = plane_window(1.0, 28.0, direction)
        slope = builder.horn_slope_deg(window, 28.0, builder.ARC_SECOND_M)
        assert slope == pytest.approx(45.0, abs=1e-9)


def test_a_void_in_the_window_gives_no_answer() -> None:
    window = [100] * 9
    window[4] = builder.VOID
    assert builder.horn_slope_deg(window, 28.0, 30.9) is None


def test_nearest_rank_percentile() -> None:
    values = [float(n) for n in range(1, 11)]
    assert builder.percentile_nearest_rank(values, 0.9) == 9.0
    assert builder.percentile_nearest_rank(values, 1.0) == 10.0
    assert builder.percentile_nearest_rank([4.0], 0.9) == 4.0


def test_densify_keeps_points_close_and_ends_on_the_last_vertex() -> None:
    geometry = [[91.880, 25.570], [91.881, 25.570]]  # about 100 m due east
    points = builder.densify(geometry, step_m=15.0)
    assert points[0] == (25.570, 91.880)
    assert points[-1] == (25.570, 91.881)
    gaps = [
        math.hypot(
            (b[1] - a[1]) * math.cos(math.radians(25.57)) * 111_195,
            (b[0] - a[0]) * 111_195,
        )
        for a, b in zip(points, points[1:], strict=False)
    ]
    assert max(gaps) <= 15.5
    assert len(points) >= 7


def make_tile(rise_per_metre: float) -> object:
    """A tile that is flat except a patch around 25.570N 91.880E rising to the east."""

    side = builder.SAMPLES_PER_SIDE
    samples = array.array("h", [1000]) * (side * side)
    tile = builder.Tile(samples, builder.TILE_SOUTH, builder.TILE_WEST)
    centre_row, centre_col = tile.row_col(25.570, 91.880)
    dx_m = builder.ARC_SECOND_M * math.cos(math.radians(25.570))
    for row in range(centre_row - 25, centre_row + 26):
        for col in range(centre_col - 25, centre_col + 26):
            metres_east = (col - centre_col) * dx_m
            samples[row * side + col] = round(1000 + metres_east * rise_per_metre)
    return tile


def test_a_road_across_a_known_slope_reads_that_slope() -> None:
    rise = 0.5  # 26.57 degrees
    tile = make_tile(rise)
    geometry = [[91.8795, 25.5700], [91.8805, 25.5700]]
    p90, mean, samples = builder.edge_terrain(tile, geometry, {})
    expected = math.degrees(math.atan(rise))
    # Elevations are whole metres, so allow for the rounding of a 28 m step.
    assert p90 == pytest.approx(expected, abs=2.0)
    assert mean == pytest.approx(expected, abs=2.0)
    assert samples >= 4


def test_a_road_on_level_ground_reads_zero() -> None:
    tile = make_tile(0.0)
    p90, mean, _ = builder.edge_terrain(
        tile, [[91.8795, 25.5700], [91.8805, 25.5700]], {}
    )
    assert (p90, mean) == (0.0, 0.0)
