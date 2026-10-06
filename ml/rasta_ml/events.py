"""Landslide events from an inventory file, kept or set aside by the manifest's rules.

Reads the NASA Global Landslide Catalog export as published, or any CSV that has a
date, a latitude and a longitude (an authority's closure log, say). Every row that
is set aside is counted with its reason, so the audit can say what the labels are
made of and what was left out.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from rasta_ml.grid import Grid
from rasta_ml.manifest import Manifest

#: Column names in the order they are looked for: the catalogue's first.
COLUMNS: dict[str, tuple[str, ...]] = {
    "day": ("event_date", "date"),
    "lat": ("latitude", "lat"),
    "lon": ("longitude", "lon", "lng"),
    "accuracy": ("location_accuracy", "location_error_km"),
    "trigger": ("landslide_trigger", "trigger"),
    "country": ("country_name", "country"),
    "state": ("admin_division_name", "state"),
    "source": ("source_name", "source"),
    "ref": ("event_id", "id"),
}

DATE_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
)


@dataclass(frozen=True, slots=True)
class Event:
    ref: str
    day: date
    lat: float
    lon: float
    #: None when the inventory does not say how well the place is known.
    location_error_km: float | None
    trigger: str
    country: str
    state: str
    source: str


@dataclass(slots=True)
class Inventory:
    events: list[Event]
    rows: int
    unreadable: Counter = field(default_factory=Counter)
    columns: dict[str, str | None] = field(default_factory=dict)


@dataclass(slots=True)
class Selection:
    kept: list[Event]
    excluded: Counter = field(default_factory=Counter)


def parse_day(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_location_error_km(text: str) -> float | None:
    """'exact', '5km', '25 km' or a bare number of kilometres; None if unknown."""

    cleaned = text.strip().lower().replace(" ", "")
    if cleaned == "exact":
        return 0.0
    if cleaned.endswith("km"):
        cleaned = cleaned[:-2]
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def normalise_trigger(text: str) -> str:
    cleaned = "_".join(text.strip().lower().replace("-", " ").split())
    return cleaned or "unknown"


def _same(left: str, right: str) -> bool:
    return " ".join(left.lower().split()) == " ".join(right.lower().split())


def read_inventory(path: Path) -> Inventory:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = {name.strip().lower(): name for name in reader.fieldnames or []}
        columns = {
            key: next((header[alias] for alias in aliases if alias in header), None)
            for key, aliases in COLUMNS.items()
        }
        missing = [key for key in ("day", "lat", "lon") if columns[key] is None]
        if missing:
            raise ValueError(
                f"{path.name} has no column for {', '.join(missing)}; expected one "
                "of " + "; ".join(", ".join(COLUMNS[key]) for key in missing)
            )

        def cell(row: dict[str, str], key: str) -> str:
            name = columns[key]
            return (row.get(name) or "").strip() if name else ""

        inventory = Inventory(events=[], rows=0, columns=columns)
        for row in reader:
            inventory.rows += 1
            day = parse_day(cell(row, "day"))
            if day is None:
                inventory.unreadable["date"] += 1
                continue
            try:
                lat, lon = float(cell(row, "lat")), float(cell(row, "lon"))
            except ValueError:
                inventory.unreadable["coordinates"] += 1
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                inventory.unreadable["coordinates"] += 1
                continue
            accuracy = cell(row, "accuracy")
            inventory.events.append(
                Event(
                    ref=cell(row, "ref") or f"row-{inventory.rows}",
                    day=day,
                    lat=lat,
                    lon=lon,
                    location_error_km=(
                        parse_location_error_km(accuracy) if accuracy else None
                    ),
                    trigger=normalise_trigger(cell(row, "trigger")),
                    country=cell(row, "country"),
                    state=cell(row, "state"),
                    source=cell(row, "source") or "unnamed",
                )
            )
    return inventory


def select_events(events: list[Event], manifest: Manifest, grid: Grid) -> Selection:
    """The events that count as labels, and why each of the others does not.

    Reasons are checked in a fixed order and each event is counted once, under the
    first rule it fails.
    """

    region = manifest.region
    rules = manifest.labels
    selection = Selection(kept=[])
    for event in events:
        if not (manifest.start <= event.day <= manifest.end):
            reason = "outside the period"
        elif grid.cell_of(event.lat, event.lon) is None:
            reason = "outside the region"
        elif (
            region.country
            and event.country
            and not _same(event.country, region.country)
        ):
            reason = "another country"
        elif (
            region.states
            and event.state
            and not any(_same(event.state, state) for state in region.states)
        ):
            reason = "another state"
        elif event.trigger not in rules.triggers:
            reason = "not triggered by rain"
        elif event.location_error_km is None:
            reason = "location accuracy not stated"
        elif event.location_error_km > rules.max_location_error_km:
            reason = f"located less precisely than {rules.max_location_error_km:g} km"
        else:
            selection.kept.append(event)
            continue
        selection.excluded[reason] += 1
    return selection


__all__ = [
    "COLUMNS",
    "Event",
    "Inventory",
    "Selection",
    "normalise_trigger",
    "parse_day",
    "parse_location_error_km",
    "read_inventory",
    "select_events",
]
