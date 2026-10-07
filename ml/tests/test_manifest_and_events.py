"""The committed manifest, and reading and selecting inventory events."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import synthetic
from rasta_ml.events import (
    Event,
    parse_day,
    parse_location_error_km,
    read_inventory,
    select_events,
)
from rasta_ml.grid import Grid
from rasta_ml.manifest import load_manifest, parse_manifest

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


def test_the_committed_manifest_is_valid_and_fixes_a_forward_split() -> None:
    manifest = load_manifest(MANIFEST)
    split = manifest.split
    assert split.train[1] < split.validate[0] <= split.validate[1] < split.test[0]
    assert manifest.split.part_of(2016) == "test"
    assert len(manifest.sha256) == 64
    # Every source's terms are recorded before anything is fetched from it.
    assert manifest.unconfirmed_licences() == []
    assert all(source.licence.strip() for source in manifest.sources.values())


def test_a_split_that_looks_back_is_refused() -> None:
    document = synthetic.manifest_document()
    document["split"]["validate_years"] = [2007, 2007]
    with pytest.raises(ValueError, match="train, then validate, then test"):
        parse_manifest(json.dumps(document).encode())


def test_a_region_off_the_rainfall_grid_is_refused() -> None:
    document = synthetic.manifest_document()
    document["region"]["bbox"] = [91.1, 25.0, 92.0, 26.0]
    with pytest.raises(ValueError, match="rainfall grid"):
        parse_manifest(json.dumps(document).encode())


def test_dates_and_accuracies_in_the_forms_inventories_use() -> None:
    assert parse_day("03/15/2010 12:00:00 AM") == date(2010, 3, 15)
    assert parse_day("2010-03-15T00:00:00.000") == date(2010, 3, 15)
    assert parse_day("2010-03-15") == date(2010, 3, 15)
    assert parse_day("sometime in March") is None
    assert parse_location_error_km("exact") == 0.0
    assert parse_location_error_km("25km") == 25.0
    assert parse_location_error_km("5 km") == 5.0
    assert parse_location_error_km("unknown") is None


GLC_HEADER = (
    "source_name,event_id,event_date,event_title,location_accuracy,"
    "landslide_category,landslide_trigger,country_name,admin_division_name,"
    "longitude,latitude\n"
)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_catalogue_format_is_read_and_bad_rows_are_counted(tmp_path) -> None:
    path = write(
        tmp_path / "glc.csv",
        GLC_HEADER
        + "Paper A,1,06/12/2014 12:00:00 AM,Slide,5km,landslide,Downpour,India,"
        "Meghalaya,91.88,25.57\n"
        + "Paper B,2,not a date,Slide,5km,landslide,rain,India,Assam,91.7,26.1\n"
        + "Paper C,3,06/13/2014 12:00:00 AM,Slide,1km,landslide,rain,India,Assam,"
        "north,26.1\n",
    )
    inventory = read_inventory(path)
    assert inventory.rows == 3
    assert inventory.unreadable == {"date": 1, "coordinates": 1}
    (event,) = inventory.events
    assert (event.ref, event.day, event.trigger, event.state) == (
        "1",
        date(2014, 6, 12),
        "downpour",
        "Meghalaya",
    )
    assert event.location_error_km == 5.0


def test_a_plain_csv_with_date_and_coordinates_is_enough(tmp_path) -> None:
    path = write(tmp_path / "log.csv", "date,lat,lon\n2014-06-12,25.57,91.88\n")
    (event,) = read_inventory(path).events
    assert event.location_error_km is None and event.trigger == "unknown"


def test_a_file_without_coordinates_is_refused(tmp_path) -> None:
    path = write(tmp_path / "bad.csv", "date,place\n2014-06-12,Shillong\n")
    with pytest.raises(ValueError, match="lat"):
        read_inventory(path)


def event(**changes) -> Event:
    values = {
        "ref": "x",
        "day": date(2008, 7, 1),
        "lat": 25.57,
        "lon": 91.88,
        "location_error_km": 5.0,
        "trigger": "rain",
        "country": "India",
        "state": "Meghalaya",
        "source": "test",
    }
    values.update(changes)
    return Event(**values)


def test_each_set_aside_event_is_counted_under_its_first_failed_rule() -> None:
    manifest = synthetic.manifest()
    grid = Grid.for_region(manifest.region, manifest.cell_deg)
    events = [
        event(),
        event(day=date(2006, 7, 1)),
        event(lat=10.0),
        event(country="Bhutan"),
        event(state="Assam"),
        event(trigger="earthquake"),
        event(location_error_km=None),
        event(location_error_km=50.0),
        event(state=""),
    ]
    selection = select_events(events, manifest, grid)
    assert len(selection.kept) == 2  # the first, and the one with no state given
    assert selection.excluded == {
        "outside the period": 1,
        "outside the region": 1,
        "another country": 1,
        "another state": 1,
        "not triggered by rain": 1,
        "location accuracy not stated": 1,
        "located less precisely than 25 km": 1,
    }


def test_nothing_is_fetched_before_the_inventorys_terms_are_confirmed(
    tmp_path, capsys
) -> None:
    from rasta_ml.__main__ import main

    document = synthetic.manifest_document()
    document["sources"]["events"]["licence_confirmed"] = False
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document))
    (tmp_path / "events.csv").write_text("date,lat,lon\n2008-07-01,25.57,91.88\n")
    assert main(["fetch", "--work", str(tmp_path), "--manifest", str(manifest)]) == 2
    assert "licence_confirmed" in capsys.readouterr().err
    assert not (tmp_path / "cache").exists()
