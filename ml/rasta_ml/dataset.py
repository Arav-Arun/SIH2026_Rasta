"""The cell-day dataset: features, labels, the observed area, the label audit, the split.

One row is one grid cell on one day. Its label is 1 when a kept event falls in that
cell on that day. Rows come only from the observed area: cells where the inventory
recorded at least one event in the training years. A cell nobody has ever reported
from says nothing about whether landslides happen there, so its quiet days are not
used as negatives; the audit counts the events this leaves out.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from rasta_ml.events import Event, Inventory, Selection
from rasta_ml.grid import Grid
from rasta_ml.manifest import Manifest
from rasta_ml.sources import LEAD_DAYS, TERRAIN_FEATURES

#: Rainfall totals over the days ending on the row's day, inclusive.
RAIN_WINDOWS: dict[str, int] = {
    "rain_1d": 1,
    "rain_2d": 2,
    "rain_3d": 3,
    "rain_7d": 7,
    "rain_30d": 30,
}
FEATURES: tuple[str, ...] = (*RAIN_WINDOWS, *TERRAIN_FEATURES)
PARTS: tuple[str, ...] = ("train", "validate", "test")
EPOCH = date(1970, 1, 1)


@dataclass(slots=True)
class Dataset:
    X: np.ndarray  # (rows, features) float32
    y: np.ndarray  # (rows,) int8
    cell: np.ndarray  # (rows,) int32
    day: np.ndarray  # (rows,) int32, days since 1970-01-01
    part: np.ndarray  # (rows,) int8, an index into PARTS
    features: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.y.size)

    def mask(self, part: str) -> np.ndarray:
        return self.part == PARTS.index(part)

    def column(self, name: str) -> np.ndarray:
        return self.X[:, self.features.index(name)]

    def months(self) -> np.ndarray:
        days = self.day.astype("datetime64[D]")
        return (days.astype("datetime64[M]").astype(np.int64) % 12 + 1).astype(np.int8)

    def sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(json.dumps(self.features).encode())
        for array in (self.X, self.y, self.cell, self.day, self.part):
            digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()

    def save(self, path: Path) -> None:
        np.savez_compressed(
            path,
            X=self.X,
            y=self.y,
            cell=self.cell,
            day=self.day,
            part=self.part,
            features=np.array(self.features),
        )

    @classmethod
    def load(cls, path: Path) -> Dataset:
        with np.load(path, allow_pickle=False) as saved:
            return cls(
                X=saved["X"],
                y=saved["y"],
                cell=saved["cell"],
                day=saved["day"],
                part=saved["part"],
                features=tuple(str(name) for name in saved["features"]),
            )


def window_sums(rain: np.ndarray, window: int) -> np.ndarray:
    """Totals over the ``window`` days ending on each day, inclusive.

    NaN where any day in the window is missing, and for the first ``window - 1`` days.
    """

    filled = np.nan_to_num(rain, nan=0.0).astype(np.float64)
    gaps = np.isnan(rain).astype(np.int64)
    zero = np.zeros((1, *rain.shape[1:]))
    totals = np.concatenate([zero, np.cumsum(filled, axis=0)])
    gap_counts = np.concatenate([zero, np.cumsum(gaps, axis=0)])
    out = np.full(rain.shape, np.nan)
    out[window - 1 :] = totals[window:] - totals[:-window]
    missing = (gap_counts[window:] - gap_counts[:-window]) > 0
    out[window - 1 :][missing] = np.nan
    return out


def observed_cells(kept: list[Event], grid: Grid, manifest: Manifest) -> set[int]:
    """Cells with at least one kept event in the training years, and only those.

    Later years must not decide where the model looks, or the test would leak.
    """

    first, last = manifest.split.train
    return {
        cell
        for event in kept
        if first <= event.day.year <= last
        and (cell := grid.cell_of(event.lat, event.lon)) is not None
    }


def build_dataset(
    rain: np.ndarray,
    rain_first_day: date,
    terrain: dict[int, dict[str, float] | None],
    kept: list[Event],
    manifest: Manifest,
    grid: Grid,
) -> tuple[Dataset, dict]:
    """Rows for every observed cell and every day of the period, and what was dropped."""

    offset = (manifest.start - rain_first_day).days
    if offset < LEAD_DAYS:
        raise ValueError(f"the rainfall record must start {LEAD_DAYS} days early")
    days = (manifest.end - manifest.start).days + 1
    if offset + days > rain.shape[0]:
        raise ValueError("the rainfall record ends before the period does")

    sums = {
        name: window_sums(rain, window)[offset : offset + days]
        for name, window in RAIN_WINDOWS.items()
    }
    observed = observed_cells(kept, grid, manifest)
    positive: set[tuple[int, int]] = set()
    for event in kept:
        cell = grid.cell_of(event.lat, event.lon)
        if cell in observed:
            positive.add((cell, (event.day - manifest.start).days))

    first_day = (manifest.start - EPOCH).days
    day_numbers = np.arange(first_day, first_day + days, dtype=np.int32)
    years = (
        day_numbers.astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970
    )
    parts = np.array(
        [
            PARTS.index(part) if (part := manifest.split.part_of(int(year))) else -1
            for year in years
        ],
        dtype=np.int8,
    )

    blocks: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    cells: list[np.ndarray] = []
    dropped = Counter()
    for cell in sorted(observed):
        stats = terrain.get(cell)
        if stats is None:
            dropped["cell without usable terrain"] += days
            continue
        row, col = grid.row_col(cell)
        block = np.empty((days, len(FEATURES)), dtype=np.float32)
        for index, name in enumerate(RAIN_WINDOWS):
            block[:, index] = sums[name][:, row, col]
        for index, name in enumerate(TERRAIN_FEATURES, start=len(RAIN_WINDOWS)):
            block[:, index] = stats[name]
        label = np.zeros(days, dtype=np.int8)
        for day_index in (d for c, d in positive if c == cell):
            label[day_index] = 1
        blocks.append(block)
        labels.append(label)
        cells.append(np.full(days, cell, dtype=np.int32))

    if not blocks:
        raise ValueError("no observed cell has usable terrain: nothing to build")
    X = np.concatenate(blocks)
    y = np.concatenate(labels)
    cell_ids = np.concatenate(cells)
    day_ids = np.tile(day_numbers, len(blocks))
    part_ids = np.tile(parts, len(blocks))

    complete = np.isfinite(X).all(axis=1)
    dropped["missing rainfall"] = int((~complete).sum())
    dropped["positives lost to missing rainfall"] = int(((~complete) & (y == 1)).sum())
    in_split = part_ids >= 0
    dropped["outside the split years"] = int((complete & ~in_split).sum())
    keep = complete & in_split
    dataset = Dataset(
        X=X[keep],
        y=y[keep],
        cell=cell_ids[keep],
        day=day_ids[keep],
        part=part_ids[keep],
        features=FEATURES,
    )
    return dataset, {"observed_cells": sorted(observed), "dropped": dict(dropped)}


def _top(counter: Counter, limit: int = 15) -> dict[str, int]:
    return {str(key): count for key, count in counter.most_common(limit)}


def label_audit(
    inventory: Inventory,
    selection: Selection,
    dataset: Dataset,
    build: dict,
    manifest: Manifest,
    grid: Grid,
    inventory_sha256: str,
) -> dict:
    """What the labels are made of, what was left out, and where bias is visible."""

    kept = selection.kept
    observed = set(build["observed_cells"])
    cell_of = {id(event): grid.cell_of(event.lat, event.lon) for event in kept}

    seen: Counter = Counter((cell_of[id(e)], e.day) for e in kept)
    outside: Counter = Counter()
    for event in kept:
        if cell_of[id(event)] not in observed:
            outside[manifest.split.part_of(event.day.year) or "outside the split"] += 1

    per_cell = Counter(cell_of[id(event)] for event in kept)
    counts = sorted(per_cell.values(), reverse=True)
    busiest = max(1, len(counts) // 10)

    rows = {part: int(dataset.mask(part).sum()) for part in PARTS}
    positives = {part: int(dataset.y[dataset.mask(part)].sum()) for part in PARTS}
    return {
        "inventory": {
            "sha256": inventory_sha256,
            "rows": inventory.rows,
            "unreadable": dict(inventory.unreadable),
            "columns_used": inventory.columns,
        },
        "excluded": dict(selection.excluded),
        "kept": {
            "events": len(kept),
            "by_year": dict(sorted(Counter(str(e.day.year) for e in kept).items())),
            "by_state": _top(Counter(e.state or "not given" for e in kept)),
            "by_source": _top(Counter(e.source for e in kept)),
            "by_location_accuracy_km": dict(
                sorted(Counter(f"{e.location_error_km:g}" for e in kept).items())
            ),
            "by_trigger": _top(Counter(e.trigger for e in kept)),
        },
        "reporting_bias": {
            "cells_with_events": len(per_cell),
            "most_events_in_one_cell": counts[0] if counts else 0,
            "median_events_per_cell": statistics.median(counts) if counts else 0,
            "share_of_events_in_busiest_tenth_of_cells": (
                round(sum(counts[:busiest]) / len(kept), 3) if kept else 0.0
            ),
            "events_outside_the_observed_area": dict(outside),
            "note": (
                "Catalogues record landslides that reach the news or an office, so "
                "events cluster near roads and towns. Rows come only from cells with "
                "a training-year event; events elsewhere cannot be scored and are "
                "counted above."
            ),
        },
        "location": {
            "kept_with_error_10km_or_more": sum(
                1 for e in kept if (e.location_error_km or 0) >= 10
            ),
        },
        "same_cell_same_day_merged": sum(n - 1 for n in seen.values() if n > 1),
        "dataset": {
            "rows": rows,
            "positives": positives,
            "cells": len({int(c) for c in dataset.cell}),
            "dropped": build["dropped"],
        },
    }


def split_record(dataset: Dataset, manifest: Manifest) -> dict:
    """The split, written down before any model is fitted, tied to this exact dataset."""

    return {
        "written_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "manifest_sha256": manifest.sha256,
        "dataset_sha256": dataset.sha256(),
        "years": {
            "train": list(manifest.split.train),
            "validate": list(manifest.split.validate),
            "test": list(manifest.split.test),
        },
        "rows": {part: int(dataset.mask(part).sum()) for part in PARTS},
        "positives": {part: int(dataset.y[dataset.mask(part)].sum()) for part in PARTS},
        "features": list(dataset.features),
    }


def period_days(manifest: Manifest) -> tuple[date, date]:
    """First and last day of rainfall the period needs, lead days included."""

    return manifest.start - timedelta(days=LEAD_DAYS), manifest.end


__all__ = [
    "FEATURES",
    "PARTS",
    "RAIN_WINDOWS",
    "Dataset",
    "build_dataset",
    "label_audit",
    "observed_cells",
    "period_days",
    "split_record",
    "window_sums",
]
