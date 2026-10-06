"""Fetch and read the rainfall record and the terrain, for the cells a dataset needs.

Both sources are public files on AWS that need no account. Every file read is hashed,
and the hashes go into the inputs lock, so a rebuild can show it used the same bytes.
Only the region's cells are kept from each file; the raw downloads are not stored.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import h5py
import numpy as np

from rasta_ml.grid import Grid

Getter = Callable[[str], bytes]

PERSIANN_BASE = "https://s3.amazonaws.com/noaa-cdr-precip-persiann-pds"
PERSIANN_FILE = re.compile(r"PERSIANN-CDR_v01r01_(\d{8})_c\d{8}\.nc$")
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

SKADI_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
SKADI_VOID = -32768

#: Days of rainfall needed before the first labelled day, for the 30-day total.
LEAD_DAYS = 29

#: Every third SRTM sample, about 90 m apart: plenty to describe a 27 km cell.
DECIMATE = 3
#: Slopes steeper than this count towards a cell's steep fraction.
STEEP_DEG = 30.0
EARTH_RADIUS_M = 6_371_008.8
ARC_SECOND_M = math.pi / 180 / 3600 * EARTH_RADIUS_M


def http_get(url: str, *, attempts: int = 3, timeout: float = 90.0) -> bytes:
    """GET with a few retries; raises after the last failure."""

    request = urllib.request.Request(url, headers={"User-Agent": "rasta-ml/0.1"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except OSError:
            if attempt == attempts:
                raise
    raise AssertionError("unreachable")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Rainfall ---------------------------------------------------------------------------


def list_rainfall_files(year: int, get: Getter = http_get) -> dict[date, str]:
    """The record's file for each day of a year, by listing the public bucket."""

    keys: dict[date, str] = {}
    token: str | None = None
    while True:
        query = {"list-type": "2", "prefix": f"data/{year}/"}
        if token:
            query["continuation-token"] = token
        root = ElementTree.fromstring(
            get(f"{PERSIANN_BASE}/?{urllib.parse.urlencode(query)}")
        )
        for item in root.findall("s3:Contents", S3_NS):
            key = item.findtext("s3:Key", default="", namespaces=S3_NS)
            match = PERSIANN_FILE.search(key)
            if match:
                day = datetime.strptime(match.group(1), "%Y%m%d").date()
                # Reprocessed files carry a later creation stamp: keep the newest.
                if day not in keys or key > keys[day]:
                    keys[day] = key
        if root.findtext("s3:IsTruncated", namespaces=S3_NS) != "true":
            return keys
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)


def _index_of(values: np.ndarray, wanted: float) -> int:
    index = int(np.argmin(np.abs(values - wanted)))
    if abs(float(values[index]) - wanted) > 1e-3:
        raise ValueError(f"no grid line at {wanted}")
    return index


def read_rainfall_day(raw: bytes, grid: Grid, day: date) -> np.ndarray:
    """One day of rainfall in millimetres, as (rows north to south, columns west to east).

    Missing values become NaN. The file's own date must be the day asked for.
    """

    with h5py.File(io.BytesIO(raw), "r") as handle:
        stamp = handle.attrs.get("datetime")
        if isinstance(stamp, bytes):
            stamp = stamp.decode()
        if stamp is not None and str(stamp)[:10] != day.isoformat():
            raise ValueError(f"file dated {stamp}, expected {day.isoformat()}")
        lat = np.asarray(handle["lat"][:], dtype=np.float64)
        lon = np.asarray(handle["lon"][:], dtype=np.float64)
        rain = handle["precipitation"]
        if rain.shape != (1, lon.size, lat.size):
            raise ValueError(f"unexpected precipitation layout {rain.shape}")
        units = rain.attrs.get("units")
        if isinstance(units, bytes):
            units = units.decode()
        if units != "mm":
            raise ValueError(f"precipitation in {units!r}, expected mm")

        half = grid.step / 2
        rows = [
            _index_of(lat, grid.north - half - r * grid.step) for r in range(grid.ny)
        ]
        cols = [
            _index_of(lon, (grid.west + half + c * grid.step) % 360)
            for c in range(grid.nx)
        ]
        if rows != list(range(rows[0], rows[0] + grid.ny)) or cols != list(
            range(cols[0], cols[0] + grid.nx)
        ):
            raise ValueError("the region is not one block of the record's grid")
        block = np.asarray(
            rain[0, cols[0] : cols[-1] + 1, rows[0] : rows[-1] + 1], dtype=np.float32
        ).T
    block = block.copy()
    block[~np.isfinite(block) | (block < 0)] = np.nan
    return block


def _read_year(path: Path) -> dict[date, tuple[np.ndarray, str]]:
    """A finished year from the fetch cache, by day; empty when there is none."""

    if not path.is_file():
        return {}
    with np.load(path, allow_pickle=False) as saved:
        return {
            date.fromisoformat(str(day)): (layer, str(digest))
            for day, layer, digest in zip(
                saved["days"], saved["rain"], saved["sha256"], strict=True
            )
        }


def fetch_rainfall(
    grid: Grid,
    first: date,
    last: date,
    cache: Path,
    *,
    get: Getter = http_get,
    workers: int = 8,
    log: Callable[[str], None] = print,
) -> tuple[np.ndarray, dict]:
    """Daily rainfall for every grid cell from ``first`` to ``last`` inclusive.

    Each year is kept in ``cache`` once complete, so an interrupted fetch resumes.
    Returns (rain[day, row, col], provenance).
    """

    cache.mkdir(parents=True, exist_ok=True)
    days: list[date] = [
        first + timedelta(days=offset) for offset in range((last - first).days + 1)
    ]
    layers: list[np.ndarray] = []
    files: dict[str, str] = {}
    missing: list[str] = []
    area = f"{grid.west:g}_{grid.south:g}_{grid.east:g}_{grid.north:g}"
    for year in sorted({day.year for day in days}):
        wanted = [day for day in days if day.year == year]
        stored = cache / f"rainfall_{year}_{area}.npz"
        cached = _read_year(stored)
        if set(wanted) <= cached.keys():
            results = [(day, *cached[day]) for day in wanted]
        else:
            keys = list_rainfall_files(year, get)
            log(
                f"rainfall {year}: {len(wanted)} days, {sum(d in keys for d in wanted)} files"
            )

            def one(
                day: date, keys: dict[date, str] = keys
            ) -> tuple[date, np.ndarray, str]:
                key = keys.get(day)
                if key is None:
                    return day, np.full((grid.ny, grid.nx), np.nan, np.float32), ""
                raw = get(f"{PERSIANN_BASE}/{key}")
                return day, read_rainfall_day(raw, grid, day), sha256(raw)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, wanted))
            np.savez_compressed(
                stored,
                days=np.array([day.isoformat() for day, _, _ in results]),
                rain=np.stack([layer for _, layer, _ in results]),
                sha256=np.array([digest for _, _, digest in results]),
            )
        for day, layer, digest in results:
            layers.append(layer)
            if digest:
                files[day.isoformat()] = digest
            else:
                missing.append(day.isoformat())

    lines = "\n".join(f"{day} {digest}" for day, digest in sorted(files.items()))
    provenance = {
        "source": PERSIANN_BASE,
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "files": len(files),
        "missing_days": sorted(missing),
        # One digest over every file's digest, in date order.
        "sha256_of_file_digests": sha256(lines.encode()),
    }
    return np.stack(layers), provenance


# Terrain ----------------------------------------------------------------------------


def tile_name(lat_floor: int, lon_floor: int) -> str:
    north_south = "N" if lat_floor >= 0 else "S"
    east_west = "E" if lon_floor >= 0 else "W"
    return f"{north_south}{abs(lat_floor):02d}{east_west}{abs(lon_floor):03d}"


def read_hgt(raw_gz: bytes) -> np.ndarray:
    """A gzipped SRTM tile as metres, north row first, voids as NaN."""

    data = gzip.decompress(raw_gz)
    side = math.isqrt(len(data) // 2)
    if side * side * 2 != len(data):
        raise ValueError("not a square SRTM tile")
    elevation = np.frombuffer(data, dtype=">i2").reshape(side, side).astype(np.float32)
    elevation[elevation == SKADI_VOID] = np.nan
    return elevation


def slope_deg(
    elevation: np.ndarray, top_lat: float, spacing_arcsec: float
) -> np.ndarray:
    """Horn's 3x3 slope in degrees; NaN on the border and beside voids."""

    z = elevation.astype(np.float64)
    dy = spacing_arcsec * ARC_SECOND_M
    lats = top_lat - np.arange(z.shape[0]) * spacing_arcsec / 3600
    dx = (dy * np.cos(np.radians(lats)))[1:-1, None]
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * dy)
    slope = np.full(z.shape, np.nan, dtype=np.float32)
    slope[1:-1, 1:-1] = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return slope


TERRAIN_FEATURES = ("slope_mean_deg", "slope_p90_deg", "steep_fraction", "relief_m")


def cell_terrain(
    grid: Grid,
    cells: Iterable[int],
    tile_lat: int,
    tile_lon: int,
    elevation: np.ndarray,
) -> dict[int, dict[str, float] | None]:
    """Terrain statistics for the cells inside one 1 degree tile.

    None for a cell with less than half of its samples usable.
    """

    sampled = elevation[::DECIMATE, ::DECIMATE]
    per_degree = (elevation.shape[0] - 1) // DECIMATE
    slope = slope_deg(sampled, top_lat=tile_lat + 1, spacing_arcsec=3600 / per_degree)
    out: dict[int, dict[str, float] | None] = {}
    for cell in cells:
        west, south, east, north = grid.bounds(cell)
        r0, r1 = (round((tile_lat + 1 - edge) * per_degree) for edge in (north, south))
        c0, c1 = (round((edge - tile_lon) * per_degree) for edge in (west, east))
        window = slope[r0:r1, c0:c1]
        heights = sampled[r0:r1, c0:c1]
        usable = np.isfinite(window)
        if window.size == 0 or usable.mean() < 0.5:
            out[cell] = None
            continue
        values = window[usable]
        finite_heights = heights[np.isfinite(heights)]
        out[cell] = {
            "slope_mean_deg": round(float(values.mean()), 3),
            "slope_p90_deg": round(float(np.percentile(values, 90)), 3),
            "steep_fraction": round(float((values > STEEP_DEG).mean()), 4),
            "relief_m": round(
                float(
                    np.percentile(finite_heights, 95) - np.percentile(finite_heights, 5)
                ),
                1,
            ),
        }
    return out


def fetch_terrain(
    grid: Grid,
    cells: Iterable[int],
    cache: Path,
    *,
    get: Getter = http_get,
    log: Callable[[str], None] = print,
) -> tuple[dict[int, dict[str, float] | None], dict]:
    """Terrain statistics for each cell, and the tiles they came from."""

    cache.mkdir(parents=True, exist_ok=True)
    by_tile: dict[tuple[int, int], list[int]] = {}
    for cell in sorted(set(cells)):
        west, south, _, _ = grid.bounds(cell)
        by_tile.setdefault((math.floor(south), math.floor(west)), []).append(cell)

    stats: dict[int, dict[str, float] | None] = {}
    tiles: dict[str, str] = {}
    area = f"{grid.west:g}_{grid.south:g}_{grid.east:g}_{grid.north:g}"
    for (lat, lon), members in sorted(by_tile.items()):
        name = tile_name(lat, lon)
        stored = cache / f"terrain_{name}_{area}.json"
        saved = (
            json.loads(stored.read_text(encoding="utf-8")) if stored.is_file() else {}
        )
        if all(str(cell) in saved.get("cells", {}) for cell in members):
            tiles[name] = saved["sha256"]
            stats.update({cell: saved["cells"][str(cell)] for cell in members})
            continue
        raw = get(f"{SKADI_BASE}/{name[:3]}/{name}.hgt.gz")
        tiles[name] = sha256(raw)
        computed = cell_terrain(grid, members, lat, lon, read_hgt(raw))
        stats.update(computed)
        stored.write_text(
            json.dumps(
                {
                    "sha256": tiles[name],
                    "cells": {
                        **saved.get("cells", {}),
                        **{str(cell): value for cell, value in computed.items()},
                    },
                }
            ),
            encoding="utf-8",
        )
        log(f"terrain {name}: {len(members)} cells")
    return stats, {"source": SKADI_BASE, "tiles": tiles}


__all__ = [
    "DECIMATE",
    "LEAD_DAYS",
    "STEEP_DEG",
    "TERRAIN_FEATURES",
    "cell_terrain",
    "fetch_rainfall",
    "fetch_terrain",
    "http_get",
    "list_rainfall_files",
    "read_hgt",
    "read_rainfall_day",
    "sha256",
    "slope_deg",
    "tile_name",
]
