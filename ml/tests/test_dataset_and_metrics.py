"""Building the cell-day dataset, and the metrics, on SYNTHETIC data."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import synthetic
from rasta_ml.dataset import (
    PARTS,
    Dataset,
    build_dataset,
    observed_cells,
    window_sums,
)
from rasta_ml.events import Event
from rasta_ml.metrics import (
    at_threshold,
    average_precision,
    best_f1_threshold,
    day_bootstrap,
)
from sklearn.metrics import average_precision_score


def test_window_sums_match_a_plain_loop_and_carry_gaps() -> None:
    rng = np.random.default_rng(1)
    rain = rng.gamma(1.0, 5.0, size=(40, 2, 3))
    rain[10, 1, 2] = np.nan
    sums = window_sums(rain, 7)
    for day in range(40):
        window = rain[max(0, day - 6) : day + 1, 1, 2]
        if day < 6 or np.isnan(window).any():
            assert np.isnan(sums[day, 1, 2])
        else:
            assert sums[day, 1, 2] == pytest.approx(window.sum())


def small_world():
    m = synthetic.manifest()
    rain, first, terrain, events, grid = synthetic.world(m)
    return m, rain, first, terrain, events, grid


def test_rows_labels_and_parts_land_where_they_should() -> None:
    m, rain, first, terrain, events, grid = small_world()
    dataset, built = build_dataset(rain, first, terrain, events, m, grid)

    observed = observed_cells(events, grid, m)
    assert set(built["observed_cells"]) == observed
    days = (m.end - m.start).days + 1
    assert len(dataset) == len(observed) * days

    # Every kept event in an observed cell is a positive on its own cell and day.
    event = next(e for e in events if grid.cell_of(e.lat, e.lon) in observed)
    cell = grid.cell_of(event.lat, event.lon)
    day_number = (event.day - date(1970, 1, 1)).days
    row = np.flatnonzero((dataset.cell == cell) & (dataset.day == day_number))
    assert dataset.y[row].tolist() == [1]

    # A row's rain_3d is the three days ending on its day, from the record itself.
    r, c = grid.row_col(cell)
    offset = (event.day - first).days
    assert dataset.column("rain_3d")[row][0] == pytest.approx(
        float(rain[offset - 2 : offset + 1, r, c].sum()), rel=1e-5
    )
    years = (
        dataset.day.astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970
    )
    assert set(years[dataset.mask("test")]) == {2010}
    assert set(years[dataset.mask("train")]) == {2007, 2008}


def test_the_observed_area_comes_from_training_years_only() -> None:
    m, rain, first, terrain, _, grid = small_world()
    late = Event("late", date(2010, 7, 1), 25.9, 91.1, 1.0, "rain", "India", "", "t")
    assert observed_cells([late], grid, m) == set()
    early = Event("early", date(2008, 7, 1), 25.9, 91.1, 1.0, "rain", "India", "", "t")
    assert observed_cells([early, late], grid, m) == {grid.cell_of(25.9, 91.1)}


def test_days_with_missing_rainfall_are_dropped_and_counted() -> None:
    m, rain, first, terrain, events, grid = small_world()
    gap = (date(2008, 3, 1) - first).days
    rain[gap] = np.nan
    dataset, built = build_dataset(rain, first, terrain, events, m, grid)
    # The gap removes that day and the 29 after it from every observed cell.
    assert built["dropped"]["missing rainfall"] == 30 * len(built["observed_cells"])
    assert np.isfinite(dataset.X).all()


def test_the_dataset_hash_is_stable_and_survives_a_round_trip(tmp_path) -> None:
    m, rain, first, terrain, events, grid = small_world()
    dataset, _ = build_dataset(rain, first, terrain, events, m, grid)
    path = tmp_path / "dataset.npz"
    dataset.save(path)
    assert Dataset.load(path).sha256() == dataset.sha256()
    assert set(PARTS) == {"train", "validate", "test"}


def test_average_precision_agrees_with_scikit_learn_including_ties() -> None:
    rng = np.random.default_rng(3)
    y = (rng.random(5000) < 0.02).astype(np.int8)
    scores = np.round(rng.random(5000) + y * 0.3, 2)  # coarse, so many ties
    assert average_precision(y, scores) == pytest.approx(
        average_precision_score(y, scores), abs=1e-12
    )
    assert average_precision(np.zeros(5, dtype=np.int8), np.ones(5)) is None


def test_the_operating_point_reports_hits_misses_and_burden() -> None:
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0], dtype=np.int8)
    p = np.array([0.9, 0.8, 0.7, 0.2, 0.1, 0.1, 0.1, 0.1])
    threshold = best_f1_threshold(y, p)
    assert threshold == 0.7
    days = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    cells = np.array([1, 2, 1, 2, 1, 2, 1, 2])
    point = at_threshold(y, p, threshold, cells, days)
    assert (point["alerts"], point["hits"], point["missed"]) == (3, 2, 0)
    assert point["precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert point["alerts_per_cell_per_week"] == pytest.approx(3 / (2 * 4 / 7), abs=1e-4)


def test_the_day_bootstrap_is_seeded_and_brackets_the_point_value() -> None:
    rng = np.random.default_rng(5)
    days = np.repeat(np.arange(200), 10)
    y = (rng.random(days.size) < 0.05).astype(np.int8)
    good = y + rng.random(days.size)
    noise = rng.random(days.size)
    run = lambda seed: day_bootstrap(  # noqa: E731
        y,
        days,
        {"good": good, "noise": noise},
        {"good": good / 2},
        pairs=[("good", "noise")],
        resamples=200,
        interval=0.95,
        seed=seed,
    )
    first, again = run(1), run(1)
    assert first == again
    low, high = first["pr_auc"]["good"]
    assert low <= average_precision(y, good) <= high
    assert first["pr_auc_difference"]["good minus noise"][0] > 0
