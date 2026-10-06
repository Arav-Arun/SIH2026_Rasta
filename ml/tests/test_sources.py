"""Reading the rainfall record and terrain tiles, on small SYNTHETIC files of the same layout."""

from __future__ import annotations

import gzip
import io
import math
from datetime import date

import h5py
import numpy as np
import pytest
import synthetic
from rasta_ml.grid import Grid
from rasta_ml.sources import (
    PERSIANN_BASE,
    cell_terrain,
    fetch_rainfall,
    read_hgt,
    read_rainfall_day,
    tile_name,
)


def persiann_file(day: date, values: dict[tuple[float, float], float]) -> bytes:
    """A day in PERSIANN-CDR's own layout: (time, lon, lat), latitude north first."""

    lat = (59.875 - 0.25 * np.arange(480)).astype(np.float32)
    lon = (0.125 + 0.25 * np.arange(1440)).astype(np.float32)
    rain = np.zeros((1, 1440, 480), dtype=np.float32)
    for (at_lat, at_lon), amount in values.items():
        rain[
            0, int(np.argmin(abs(lon - at_lon))), int(np.argmin(abs(lat - at_lat)))
        ] = amount
    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as handle:
        handle.attrs["datetime"] = day.isoformat().encode()
        handle["lat"] = lat
        handle["lon"] = lon
        dataset = handle.create_dataset("precipitation", data=rain, compression="gzip")
        dataset.attrs["units"] = b"mm"
    return buffer.getvalue()


def grid() -> Grid:
    m = synthetic.manifest()
    return Grid.for_region(m.region, m.cell_deg)


def test_one_day_is_cut_to_the_region_north_row_first() -> None:
    g = grid()
    raw = persiann_file(
        date(2016, 7, 1),
        {(25.875, 91.125): 40.0, (25.125, 91.875): 7.5, (25.375, 91.375): -1},
    )
    block = read_rainfall_day(raw, g, date(2016, 7, 1))
    assert block.shape == (g.ny, g.nx) == (4, 4)
    assert block[0, 0] == 40.0  # north-west cell
    assert block[3, 3] == 7.5  # south-east cell
    assert math.isnan(block[2, 1])  # the fill value is missing, not zero
    assert float(np.nansum(block)) == 47.5


def test_a_file_for_another_day_is_refused() -> None:
    raw = persiann_file(date(2016, 7, 2), {})
    with pytest.raises(ValueError, match="expected 2016-07-01"):
        read_rainfall_day(raw, grid(), date(2016, 7, 1))


LISTING = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    "<IsTruncated>false</IsTruncated>{keys}</ListBucketResult>"
)


def test_fetch_lists_downloads_hashes_and_then_reuses_its_cache(tmp_path) -> None:
    g = grid()
    days = [date(2016, 7, 1), date(2016, 7, 2)]
    keys = {
        day: f"data/2016/PERSIANN-CDR_v01r01_{day:%Y%m%d}_c20170119.nc" for day in days
    }
    files = {
        key: persiann_file(day, {(25.875, 91.125): day.day})
        for day, key in keys.items()
    }
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        if "?" in url:
            return LISTING.format(
                keys="".join(
                    f"<Contents><Key>{key}</Key></Contents>" for key in keys.values()
                )
            ).encode()
        return files[url.removeprefix(f"{PERSIANN_BASE}/")]

    rain, provenance = fetch_rainfall(
        g, days[0], days[-1], tmp_path, get=get, log=lambda _: None
    )
    assert rain.shape == (2, 4, 4)
    assert rain[:, 0, 0].tolist() == [1.0, 2.0]
    assert provenance["files"] == 2 and provenance["missing_days"] == []
    first_calls = len(calls)

    again, same = fetch_rainfall(
        g, days[0], days[-1], tmp_path, get=get, log=lambda _: None
    )
    assert len(calls) == first_calls, "a finished year comes from the cache"
    assert np.array_equal(again, rain, equal_nan=True)
    assert same["sha256_of_file_digests"] == provenance["sha256_of_file_digests"]


def test_a_day_the_record_lacks_is_missing_not_dry(tmp_path) -> None:
    def get(url: str) -> bytes:
        return LISTING.format(keys="").encode()

    rain, provenance = fetch_rainfall(
        grid(),
        date(2016, 7, 1),
        date(2016, 7, 1),
        tmp_path,
        get=get,
        log=lambda _: None,
    )
    assert np.isnan(rain).all()
    assert provenance["missing_days"] == ["2016-07-01"]


def test_tile_names_follow_the_srtm_convention() -> None:
    assert tile_name(25, 91) == "N25E091"
    assert tile_name(-1, -70) == "S01W070"


def test_a_uniform_slope_reads_back_as_that_slope() -> None:
    # A 1 degree tile sampled every 10 arc-seconds, rising 0.25 m per metre southward.
    side = 361
    metres_per_sample = 10 * math.pi / 180 / 3600 * 6_371_008.8
    rows = np.arange(side, dtype=np.float64)[:, None] * np.ones((1, side))
    elevation = (100 + 0.25 * metres_per_sample * rows).round().astype(">i2")
    elevation[5, 5] = -32768  # one void, which must not spread nonsense
    raw = gzip.compress(elevation.tobytes())

    terrain = cell_terrain(grid(), [0, 15], 25, 91, read_hgt(raw))
    expected = math.degrees(math.atan(0.25))
    for cell in (0, 15):
        stats = terrain[cell]
        assert stats is not None
        assert stats["slope_mean_deg"] == pytest.approx(expected, abs=0.2)
        assert stats["slope_p90_deg"] == pytest.approx(expected, abs=0.2)
        assert stats["steep_fraction"] == 0.0
