"""The data manifest: what a dataset is built from, and the rules fixed before fitting.

Everything that could be tuned after seeing results (the region, the period, which
events count, the split years, the gate) is read from one committed file, and every
output records that file's SHA-256. Changing a rule therefore changes every output's
provenance, visibly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCHEMA_VERSION = "rasta-ml-manifest-v1"

#: The rainfall record's grid. A different cell size would need a different source.
CELL_DEG = 0.25


@dataclass(frozen=True, slots=True)
class Region:
    name: str
    west: float
    south: float
    east: float
    north: float
    country: str
    states: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Split:
    train: tuple[int, int]
    validate: tuple[int, int]
    test: tuple[int, int]
    spatial_block_deg: float
    spatial_folds: int

    def part_of(self, year: int) -> str | None:
        """Which part of the data a year belongs to, or None if it is in none."""

        for name, (first, last) in (
            ("train", self.train),
            ("validate", self.validate),
            ("test", self.test),
        ):
            if first <= year <= last:
                return name
        return None


@dataclass(frozen=True, slots=True)
class LabelRules:
    triggers: frozenset[str]
    max_location_error_km: float
    min_test_positives: int


@dataclass(frozen=True, slots=True)
class Source:
    key: str
    title: str
    url: str
    licence: str
    licence_confirmed: bool


@dataclass(frozen=True, slots=True)
class Gate:
    interval: float
    bootstrap_resamples: int
    rule: str


@dataclass(frozen=True, slots=True)
class Manifest:
    name: str
    question: str
    cell_deg: float
    region: Region
    start: date
    end: date
    split: Split
    labels: LabelRules
    sources: dict[str, Source]
    seed: int
    gate: Gate
    #: SHA-256 of the manifest file as committed.
    sha256: str

    def unconfirmed_licences(self) -> list[str]:
        return [
            key for key, source in self.sources.items() if not source.licence_confirmed
        ]


def _years(value: list[int], name: str) -> tuple[int, int]:
    first, last = (int(item) for item in value)
    if first > last:
        raise ValueError(f"{name} runs backwards: {first} to {last}")
    return first, last


def parse_manifest(raw: bytes) -> Manifest:
    document = json.loads(raw)
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"not a {SCHEMA_VERSION} manifest")

    cell = float(document["unit"]["cell_deg"])
    if cell != CELL_DEG:
        raise ValueError(f"the rainfall record is gridded at {CELL_DEG} degrees")

    region_doc = document["region"]
    west, south, east, north = (float(value) for value in region_doc["bbox"])
    if not (west < east and south < north):
        raise ValueError("the region's bounding box is empty")
    for edge in (west, south, east, north):
        if abs(edge / cell - round(edge / cell)) > 1e-9:
            raise ValueError("the region's edges must fall on the rainfall grid")
    region = Region(
        name=str(region_doc["name"]),
        west=west,
        south=south,
        east=east,
        north=north,
        country=str(region_doc.get("country", "")),
        states=tuple(str(state) for state in region_doc.get("states", [])),
    )

    start, end = (date.fromisoformat(value) for value in document["period"])
    split_doc = document["split"]
    split = Split(
        train=_years(split_doc["train_years"], "train_years"),
        validate=_years(split_doc["validate_years"], "validate_years"),
        test=_years(split_doc["test_years"], "test_years"),
        spatial_block_deg=float(split_doc["spatial_block_deg"]),
        spatial_folds=int(split_doc["spatial_folds"]),
    )
    # Train on the past, choose on the next years, judge on the latest: never the
    # other way round, and never overlapping.
    if not (split.train[1] < split.validate[0] <= split.validate[1] < split.test[0]):
        raise ValueError("the split must run train, then validate, then test")
    if split.train[0] < start.year or split.test[1] > end.year:
        raise ValueError("the split years must lie inside the period")
    if split.spatial_folds < 2:
        raise ValueError("a spatial check needs at least two folds")

    labels_doc = document["labels"]
    labels = LabelRules(
        triggers=frozenset(str(item).lower() for item in labels_doc["triggers"]),
        max_location_error_km=float(labels_doc["max_location_error_km"]),
        min_test_positives=int(labels_doc.get("min_test_positives", 20)),
    )

    sources = {
        key: Source(
            key=key,
            title=str(item["title"]),
            url=str(item.get("url", "")),
            licence=str(item.get("licence", "")),
            licence_confirmed=bool(item.get("licence_confirmed", False)),
        )
        for key, item in document["sources"].items()
    }
    for required in ("rainfall", "terrain", "events"):
        if required not in sources:
            raise ValueError(f"the manifest names no {required} source")

    gate_doc = document["gate"]
    gate = Gate(
        interval=float(gate_doc["interval"]),
        bootstrap_resamples=int(gate_doc["bootstrap_resamples"]),
        rule=str(gate_doc["rule"]),
    )
    if not 0.5 < gate.interval < 1.0:
        raise ValueError("the gate's interval must be a probability above one half")

    return Manifest(
        name=str(document["name"]),
        question=str(document["question"]),
        cell_deg=cell,
        region=region,
        start=start,
        end=end,
        split=split,
        labels=labels,
        sources=sources,
        seed=int(document["seed"]),
        gate=gate,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_manifest(path: Path) -> Manifest:
    return parse_manifest(path.read_bytes())


__all__ = [
    "CELL_DEG",
    "Gate",
    "LabelRules",
    "Manifest",
    "Region",
    "Source",
    "Split",
    "load_manifest",
    "parse_manifest",
]
