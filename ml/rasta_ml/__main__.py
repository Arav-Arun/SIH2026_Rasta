"""Command line: python -m rasta_ml fetch | build | evaluate | all

fetch     read the inventory, download rainfall and terrain for the cells it needs,
          and write the inputs lock (file hashes)
build     the cell-day dataset, the label audit and the split, before any fitting
evaluate  fit, compare with the benchmarks, write the report and the model card
all       the three in order
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rasta_ml.dataset import (
    Dataset,
    build_dataset,
    label_audit,
    observed_cells,
    period_days,
    split_record,
)
from rasta_ml.evaluate import evaluate
from rasta_ml.events import read_inventory, select_events
from rasta_ml.grid import Grid
from rasta_ml.manifest import Manifest, load_manifest
from rasta_ml.sources import fetch_rainfall, fetch_terrain, sha256

ML_ROOT = Path(__file__).resolve().parents[1]


class Stop(Exception):
    """A reason to stop that the person running the command can act on."""


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_json(path: Path, missing: str) -> dict:
    if not path.is_file():
        raise Stop(missing)
    return json.loads(path.read_text(encoding="utf-8"))


def _terms_confirmed(manifest: Manifest) -> None:
    unconfirmed = manifest.unconfirmed_licences()
    if unconfirmed:
        raise Stop(
            "Check the terms of use of: "
            + ", ".join(manifest.sources[key].title for key in unconfirmed)
            + ". Record them in the manifest and set licence_confirmed to true before "
            "using them."
        )


def _inventory(events: Path, manifest: Manifest, grid: Grid):
    _terms_confirmed(manifest)
    if not events.is_file():
        raise Stop(
            f"No landslide inventory at {events}. Download the NASA Global Landslide "
            "Catalog export (or use an authority's CSV with date, latitude and "
            "longitude), save it there or pass --events, and record its terms in the "
            "manifest."
        )
    inventory = read_inventory(events)
    selection = select_events(inventory.events, manifest, grid)
    observed = observed_cells(selection.kept, grid, manifest)
    if not observed:
        raise Stop(
            "No kept event falls in the training years: there is nothing to learn."
        )
    return inventory, selection, observed


def fetch(manifest: Manifest, grid: Grid, work: Path, events: Path) -> dict:
    _, selection, observed = _inventory(events, manifest, grid)
    print(f"{len(selection.kept)} events kept, {len(observed)} cells observed")
    _, terrain = fetch_terrain(grid, observed, work / "cache")
    first, last = period_days(manifest)
    _, rainfall = fetch_rainfall(grid, first, last, work / "cache")
    lock = {
        "written_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "manifest_sha256": manifest.sha256,
        "events": {"file": events.name, "sha256": sha256(events.read_bytes())},
        "rainfall": rainfall,
        "terrain": terrain,
    }
    _write_json(work / "inputs.lock.json", lock)
    return lock


def build(manifest: Manifest, grid: Grid, work: Path, events: Path) -> dict:
    _terms_confirmed(manifest)
    lock = _read_json(work / "inputs.lock.json", "Run fetch first.")
    if lock["manifest_sha256"] != manifest.sha256:
        raise Stop(
            "The manifest changed since the inputs were fetched. Run fetch again."
        )
    if lock["events"]["sha256"] != sha256(events.read_bytes()):
        raise Stop(
            "The inventory changed since the inputs were fetched. Run fetch again."
        )

    inventory, selection, observed = _inventory(events, manifest, grid)
    terrain, _ = fetch_terrain(grid, observed, work / "cache")
    first, last = period_days(manifest)
    rain, _ = fetch_rainfall(grid, first, last, work / "cache")
    dataset, built = build_dataset(rain, first, terrain, selection.kept, manifest, grid)
    audit = label_audit(
        inventory, selection, dataset, built, manifest, grid, lock["events"]["sha256"]
    )
    split = split_record(dataset, manifest)
    dataset.save(work / "dataset.npz")
    _write_json(work / "audit.json", audit)
    _write_json(work / "split.json", split)
    print(
        "rows "
        + ", ".join(f"{part} {count}" for part, count in split["rows"].items())
        + "; positives "
        + ", ".join(f"{part} {count}" for part, count in split["positives"].items())
    )
    return split


def run_evaluation(manifest: Manifest, grid: Grid, work: Path) -> dict:
    dataset_path = work / "dataset.npz"
    if not dataset_path.is_file():
        raise Stop("Run build first.")
    report = evaluate(
        Dataset.load(dataset_path),
        _read_json(work / "split.json", "Run build first."),
        manifest,
        grid,
        audit=_read_json(work / "audit.json", "Run build first."),
        inputs=_read_json(work / "inputs.lock.json", "Run fetch first."),
        out=work / "report",
    )
    for name, decision in report["decision"].items():
        verdict = "passed" if decision["passed"] else "not passed"
        print(f"{name}: {verdict}")
    print(f"report and model card in {work / 'report'}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rasta_ml", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("fetch", "build", "evaluate", "all"))
    parser.add_argument("--manifest", type=Path, default=ML_ROOT / "manifest.json")
    parser.add_argument("--work", type=Path, default=ML_ROOT / "work")
    parser.add_argument(
        "--events",
        type=Path,
        help="the landslide inventory (default: <work>/events.csv)",
    )
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    grid = Grid.for_region(manifest.region, manifest.cell_deg)
    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    events = args.events or work / "events.csv"
    try:
        if args.command in ("fetch", "all"):
            fetch(manifest, grid, work, events)
        if args.command in ("build", "all"):
            build(manifest, grid, work, events)
        if args.command in ("evaluate", "all"):
            run_evaluation(manifest, grid, work)
    except Stop as reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
