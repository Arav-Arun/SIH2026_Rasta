#!/usr/bin/env python3
"""Build the per-segment terrain slope for the Shillong pilot from SRTM elevation.

    python data/sources/build_terrain.py                 # downloads the tile, checks its hash
    python data/sources/build_terrain.py --tile N25E091.hgt.gz

Reads the pilot road graph and one SRTM 1 arc-second tile, computes the slope of
the terrain around every road segment and writes

    data/pilot/shillong_terrain.json
    data/manifests/terrain_shillong_srtm.json

Standard library only. The same tile and graph always give the same numbers; only
the ``generated_at`` stamp changes unless it is pinned with ``--generated-at``.

Method, in short: Horn's 3x3 finite-difference slope at every elevation sample, then,
for each directed edge, the samples within BUFFER_M of its centre line (points taken
every STEP_M along the line). The segment's value is the 90th percentile (nearest
rank) of those samples' slopes, so one steep bank beside a road counts even when the
carriageway itself is level. SRTM is a 30 m model of the surface, buildings and trees
included: it says how steep the ground around a road is, not whether a slope will fail.
"""

from __future__ import annotations

import argparse
import array
import gzip
import hashlib
import json
import math
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPOSITORY_ROOT / "data" / "pilot" / "shillong_graph.json"
OUTPUT_PATH = REPOSITORY_ROOT / "data" / "pilot" / "shillong_terrain.json"
MANIFEST_PATH = REPOSITORY_ROOT / "data" / "manifests" / "terrain_shillong_srtm.json"

TRANSFORM_VERSION = "terrain-slope-v1"
SCHEMA_VERSION = "terrain-v1"

TILE_NAME = "N25E091"
TILE_URL = f"https://s3.amazonaws.com/elevation-tiles-prod/skadi/N25/{TILE_NAME}.hgt.gz"
TILE_GZ_SHA256 = "ccb45d61537cb5c40b8ac3ef5e12f23ff9e5ff2ffc158ee50789229eb61406b8"
TILE_HGT_SHA256 = "c6e18332e0dabf5ae60b2d7cf0a6487f818ecb3f5694a4adcfb35ca12bd17191"
SAMPLES_PER_SIDE = 3601  # 1 arc-second: 3600 intervals across one degree
TILE_SOUTH, TILE_WEST = 25, 91  # the tile's south-west corner, in degrees
VOID = -32768

EARTH_RADIUS_M = 6_371_008.8
ARC_SECOND_M = math.pi / 180 / 3600 * EARTH_RADIUS_M  # ~30.9 m of latitude

BUFFER_M = 45.0  # elevation samples this close to the road centre line count
STEP_M = 15.0  # spacing of the points taken along the centre line
MIN_SAMPLES = 1
#: A slope this steep reads as 1.0 in the risk score. An engineering assumption,
#: not a fitted value; the API applies it (app/terrain.py) and the file records it.
SLOPE_FULL_SCALE_DEG = 35.0


# --- the elevation model ----------------------------------------------------


class Tile:
    """One SRTM HGT tile: big-endian signed 16-bit metres, north row first."""

    def __init__(self, samples: array.array, south: int, west: int) -> None:
        if len(samples) != SAMPLES_PER_SIDE * SAMPLES_PER_SIDE:
            raise ValueError("not a 1 arc-second HGT tile")
        self.samples = samples
        self.south = south
        self.west = west

    @classmethod
    def from_bytes(cls, raw: bytes, south: int, west: int) -> Tile:
        samples = array.array("h")
        samples.frombytes(raw)
        if sys.byteorder == "little":
            samples.byteswap()
        return cls(samples, south, west)

    def row_col(self, lat: float, lon: float) -> tuple[int, int]:
        row = round((self.south + 1 - lat) * 3600)
        col = round((lon - self.west) * 3600)
        return row, col

    def lat_lon(self, row: int, col: int) -> tuple[float, float]:
        return self.south + 1 - row / 3600, self.west + col / 3600

    def elevation(self, row: int, col: int) -> int:
        return self.samples[row * SAMPLES_PER_SIDE + col]


def horn_slope_deg(window: list[int], dx_m: float, dy_m: float) -> float | None:
    """Slope in degrees from a 3x3 window of elevations, north row first.

    Horn (1981). ``dx_m`` and ``dy_m`` are the spacings between neighbouring samples.
    Returns None when any sample is a void.
    """

    if any(value == VOID for value in window):
        return None
    z1, z2, z3, z4, _, z6, z7, z8, z9 = window
    dz_dx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * dx_m)
    dz_dy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8 * dy_m)
    return math.degrees(math.atan(math.hypot(dz_dx, dz_dy)))


def slope_at(tile: Tile, row: int, col: int) -> float | None:
    if not (1 <= row < SAMPLES_PER_SIDE - 1 and 1 <= col < SAMPLES_PER_SIDE - 1):
        return None
    lat, _ = tile.lat_lon(row, col)
    dx_m = ARC_SECOND_M * math.cos(math.radians(lat))
    window = [
        tile.elevation(row + dr, col + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)
    ]
    return horn_slope_deg(window, dx_m, ARC_SECOND_M)


# --- from the road graph to slope samples -----------------------------------


def densify(
    geometry: list[list[float]], step_m: float = STEP_M
) -> list[tuple[float, float]]:
    """Points (lat, lon) along a polyline given as [lon, lat] pairs, at most step_m apart."""

    points: list[tuple[float, float]] = []
    for (lon_a, lat_a), (lon_b, lat_b) in zip(geometry, geometry[1:], strict=False):
        mid_lat = math.radians((lat_a + lat_b) / 2)
        length = math.hypot(
            (lon_b - lon_a) * math.pi / 180 * EARTH_RADIUS_M * math.cos(mid_lat),
            (lat_b - lat_a) * math.pi / 180 * EARTH_RADIUS_M,
        )
        pieces = max(1, math.ceil(length / step_m))
        for index in range(pieces):
            fraction = index / pieces
            points.append(
                (lat_a + (lat_b - lat_a) * fraction, lon_a + (lon_b - lon_a) * fraction)
            )
    if geometry:
        points.append((geometry[-1][1], geometry[-1][0]))
    return points


def cells_near(
    tile: Tile, points: list[tuple[float, float]], buffer_m: float = BUFFER_M
) -> set[tuple[int, int]]:
    """Elevation samples within buffer_m of any of the points."""

    reach = math.ceil(buffer_m / (ARC_SECOND_M * 0.8)) + 1
    found: set[tuple[int, int]] = set()
    for lat, lon in points:
        row0, col0 = tile.row_col(lat, lon)
        cos_lat = math.cos(math.radians(lat))
        for row in range(row0 - reach, row0 + reach + 1):
            for col in range(col0 - reach, col0 + reach + 1):
                cell_lat, cell_lon = tile.lat_lon(row, col)
                distance = math.hypot(
                    (cell_lon - lon) * math.pi / 180 * EARTH_RADIUS_M * cos_lat,
                    (cell_lat - lat) * math.pi / 180 * EARTH_RADIUS_M,
                )
                if distance <= buffer_m:
                    found.add((row, col))
    return found


def percentile_nearest_rank(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def edge_terrain(
    tile: Tile, geometry: list[list[float]], cache: dict[tuple[int, int], float | None]
) -> tuple[float, float, int] | None:
    """(90th-percentile slope, mean slope, samples used) around one edge, or None."""

    slopes: list[float] = []
    for row, col in sorted(cells_near(tile, densify(geometry))):
        if (row, col) not in cache:
            cache[(row, col)] = slope_at(tile, row, col)
        value = cache[(row, col)]
        if value is not None:
            slopes.append(value)
    if len(slopes) < MIN_SAMPLES:
        return None
    return (
        round(percentile_nearest_rank(slopes, 0.9), 1),
        round(sum(slopes) / len(slopes), 1),
        len(slopes),
    )


# --- files ------------------------------------------------------------------


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_tile_bytes(path: Path | None) -> tuple[bytes, bytes]:
    """(gzip file, raw HGT bytes), both checked against the pinned hashes."""

    if path is not None:
        compressed = path.read_bytes()
    else:
        print(f"downloading {TILE_URL}", file=sys.stderr)
        with urllib.request.urlopen(TILE_URL, timeout=180) as response:
            compressed = response.read()
    raw = gzip.decompress(compressed) if compressed[:2] == b"\x1f\x8b" else compressed
    if compressed[:2] == b"\x1f\x8b" and sha256_of(compressed) != TILE_GZ_SHA256:
        raise SystemExit("the downloaded tile is not the one this file was built from")
    if sha256_of(raw) != TILE_HGT_SHA256:
        raise SystemExit(
            "the elevation data differs from the pinned tile; review it first"
        )
    return compressed, raw


def build(tile: Tile, graph: dict) -> tuple[dict[str, list[float | int]], list[str]]:
    cache: dict[tuple[int, int], float | None] = {}
    segments: dict[str, list[float | int]] = {}
    missing: list[str] = []
    for edge in graph["edges"]:
        result = edge_terrain(tile, edge["geometry"], cache)
        if result is None:
            missing.append(edge["edge_id"])
        else:
            segments[edge["edge_id"]] = list(result)
    return segments, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tile", type=Path, help="a local N25E091.hgt.gz or .hgt file")
    parser.add_argument("--generated-at", help="pin the generated_at stamp (ISO 8601)")
    args = parser.parse_args(argv)

    compressed, raw = load_tile_bytes(args.tile)
    tile = Tile.from_bytes(raw, TILE_SOUTH, TILE_WEST)
    graph_bytes = GRAPH_PATH.read_bytes()
    graph = json.loads(graph_bytes)
    segments, missing = build(tile, graph)

    stamp = args.generated_at or datetime.now(UTC).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    p90 = sorted(values[0] for values in segments.values())
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": stamp,
        "graph_version": graph["graph_version"],
        "source": {
            "name": "SRTM 1 arc-second global elevation, tile " + TILE_NAME,
            "provider": "NASA / USGS Shuttle Radar Topography Mission",
            "packaged_by": "Tilezen terrain tiles (skadi) on AWS Open Data",
            "url": TILE_URL,
            "sha256_gz": TILE_GZ_SHA256,
            "sha256_hgt": TILE_HGT_SHA256,
        },
        "method": {
            "slope": "Horn (1981) 3x3 finite differences at each elevation sample",
            "sample_spacing": "1 arc-second, about 31 m north-south and 28 m east-west here",
            "buffer_m": BUFFER_M,
            "point_spacing_m": STEP_M,
            "statistic": "90th percentile (nearest rank) of sample slopes within the buffer",
            "full_scale_deg": SLOPE_FULL_SCALE_DEG,
            "normalisation": "min(1, slope / full_scale_deg); an engineering assumption, not fitted",
            "limits": (
                "SRTM models the surface including buildings and trees at 30 m. "
                "It describes how steep the ground is, not whether it will fail."
            ),
        },
        "fields": ["slope_p90_deg", "slope_mean_deg", "samples"],
        "segments": segments,
    }
    OUTPUT_PATH.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    output_bytes = OUTPUT_PATH.read_bytes()
    manifest = {
        "accessed_at": stamp,
        "attribution": (
            "United States 3DEP (formerly NED) and global GMTED2010 and SRTM terrain "
            "data courtesy of the U.S. Geological Survey."
        ),
        "intended_use": "per-segment terrain slope input to the baseline risk score",
        "license": (
            "SRTM is U.S. Government data. The tile provider asks for the attribution "
            "above and says each user must check the terms of every data provider."
        ),
        "license_or_terms_url": (
            "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
        ),
        "outputs": {
            "data/pilot/shillong_terrain.json": f"sha256:{sha256_of(output_bytes)}",
        },
        "inputs": {
            "data/pilot/shillong_graph.json": f"sha256:{sha256_of(graph_bytes)}",
            TILE_URL: f"sha256:{TILE_GZ_SHA256}",
        },
        "quality_notes": {
            "edges": len(graph["edges"]),
            "edges_with_terrain": len(segments),
            "edges_without_terrain": missing,
            "slope_p90_deg_min": p90[0],
            "slope_p90_deg_median": p90[len(p90) // 2],
            "slope_p90_deg_max": p90[-1],
            "voids_in_tile": 0,
            "not_a_susceptibility_model": True,
        },
        "query_bbox": graph["query_bbox"],
        "redistribution_allowed": True,
        "redistribution_conditions": "Keep the attribution; re-check the terms before reuse",
        "schema_version": "source-manifest-v1",
        "source": "SRTM via Tilezen terrain tiles (AWS Open Data)",
        "source_endpoint": TILE_URL,
        "transform_script": "data/sources/build_terrain.py",
        "transform_version": TRANSFORM_VERSION,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"{len(segments)} of {len(graph['edges'])} edges; slope p90 "
        f"min {p90[0]} median {p90[len(p90) // 2]} max {p90[-1]} degrees"
    )
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
