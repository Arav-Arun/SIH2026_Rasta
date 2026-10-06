"""CAP semantics that decide whether a warning is in force, and where."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.risk_pipeline import _matches_area, _polygon_wkt
from app.sources import (
    CapWarningAdapter,
    SourceRecord,
    parse_cap_circle,
    parse_cap_polygon,
    parse_cap_references,
)

NS = "urn:oasis:names:tc:emergency:cap:1.2"


def alert(
    identifier: str = "IMD-1",
    *,
    status: str = "Actual",
    msg_type: str = "Alert",
    references: str = "",
    infos: str | None = None,
    sent: str = "2026-10-06T08:00:00+05:30",
) -> str:
    body = infos if infos is not None else info()
    refs = f"<references>{references}</references>" if references else ""
    return (
        f'<alert xmlns="{NS}"><identifier>{identifier}</identifier>'
        f"<sender>imd@example.test</sender><sent>{sent}</sent>"
        f"<status>{status}</status><msgType>{msg_type}</msgType><scope>Public</scope>"
        f"{refs}{body}</alert>"
    )


def info(
    severity: str = "Severe",
    *,
    areas: str = "<area><areaDesc>East Khasi Hills</areaDesc></area>",
    expires: str = "2026-10-07T08:00:00+05:30",
    event: str = "Heavy Rainfall Warning",
) -> str:
    return (
        f"<info><language>en-IN</language><category>Met</category><event>{event}</event>"
        f"<urgency>Expected</urgency><severity>{severity}</severity>"
        f"<certainty>Likely</certainty><expires>{expires}</expires>"
        f"<headline>{event}</headline>{areas}</info>"
    )


def parse(xml: str) -> list[SourceRecord]:
    return CapWarningAdapter(None).parse(xml.encode())


@pytest.mark.parametrize("status", ["Exercise", "Test", "Draft", "System"])
def test_only_actual_messages_become_warnings(status: str) -> None:
    assert parse(alert(status=status)) == []
    assert len(parse(alert(status="Actual"))) == 1


def test_acknowledgements_and_errors_are_not_warnings() -> None:
    assert parse(alert(msg_type="Ack")) == []
    assert parse(alert(msg_type="Error")) == []


def test_a_feed_with_alerts_but_none_in_force_reads_as_empty_not_broken() -> None:
    feed = f"<feed>{alert(status='Test')}{alert('IMD-2', status='Draft')}</feed>"
    assert parse(feed) == []


def test_a_cancel_withdraws_what_it_references_even_without_an_info_block() -> None:
    cancel = alert(
        "IMD-2",
        msg_type="Cancel",
        references="imd@example.test,IMD-1,2026-10-06T08:00:00+05:30",
        infos="",
    )
    [record] = parse(cancel)
    assert record.kind == "official_warning_cancel"
    assert record.value["supersedes"] == ["IMD-1"]


def test_an_update_is_a_warning_that_replaces_the_one_it_references() -> None:
    update = alert(
        "IMD-3",
        msg_type="Update",
        references=(
            "imd@example.test,IMD-1,2026-10-06T08:00:00+05:30 "
            "imd@example.test,IMD-2,2026-10-06T09:00:00+05:30"
        ),
        infos=info("Moderate"),
    )
    [record] = parse(update)
    assert record.kind == "official_warning"
    assert record.value["msg_type"] == "Update"
    assert record.value["supersedes"] == ["IMD-1", "IMD-2"]
    assert record.value["severity_normalised"] == 0.5


def test_the_most_serious_info_block_speaks_for_the_alert_and_all_areas_count() -> None:
    blocks = info("Moderate", areas="<area><areaDesc>Ri Bhoi</areaDesc></area>") + info(
        "Extreme", areas="<area><areaDesc>East Khasi Hills</areaDesc></area>"
    )
    [record] = parse(alert(infos=blocks))
    assert record.value["severity"] == "Extreme"
    assert record.value["areas"] == ["Ri Bhoi", "East Khasi Hills"]


def test_geometry_and_geocodes_are_kept() -> None:
    areas = (
        "<area><areaDesc>Shillong area</areaDesc>"
        "<polygon>25.56,91.87 25.56,91.89 25.58,91.89 25.58,91.87 25.56,91.87</polygon>"
        "<circle>25.57,91.88 2.5</circle>"
        "<geocode><valueName>LGD</valueName><value>EAST-KHASI-HILLS</value></geocode>"
        "</area>"
    )
    [record] = parse(alert(infos=info(areas=areas)))
    assert record.value["polygons"][0][0] == [25.56, 91.87]
    assert record.value["circles"] == [{"lat": 25.57, "lon": 91.88, "radius_km": 2.5}]
    assert record.value["geocodes"] == [{"name": "LGD", "value": "EAST-KHASI-HILLS"}]


def test_a_validity_that_ends_before_the_message_was_sent_is_dropped() -> None:
    [record] = parse(alert(infos=info(expires="2026-10-05T08:00:00+05:30")))
    assert record.valid_until is None


def test_validity_is_kept_when_it_makes_sense() -> None:
    [record] = parse(alert())
    assert record.valid_until == datetime(2026, 10, 7, 2, 30, tzinfo=UTC)


def test_references_are_read_as_identifiers() -> None:
    assert parse_cap_references("a@b,ID-1,2026-10-06T08:00:00+05:30") == ["ID-1"]
    assert parse_cap_references("") == []
    assert parse_cap_references("only-two,parts") == []


def test_polygons_are_closed_and_validated() -> None:
    ring = parse_cap_polygon("25.5,91.8 25.5,91.9 25.6,91.9")
    assert ring[0] == ring[-1] and len(ring) == 4
    assert parse_cap_polygon("25.5,91.8 25.5,91.9") is None
    assert parse_cap_polygon("95,91.8 25.5,91.9 25.6,91.9") is None
    assert parse_cap_polygon("not numbers") is None


def test_circles_are_validated() -> None:
    assert parse_cap_circle("25.57,91.88 2.5") == {
        "lat": 25.57,
        "lon": 91.88,
        "radius_km": 2.5,
    }
    assert parse_cap_circle("25.57,91.88 0") is None
    assert parse_cap_circle("25.57,91.88") is None


def test_polygon_wkt_puts_longitude_first() -> None:
    assert _polygon_wkt(
        [[25.5, 91.8], [25.5, 91.9], [25.6, 91.9], [25.5, 91.8]]
    ).startswith("POLYGON((91.8000000 25.5000000, 91.9000000 25.5000000")


def record_with(**value) -> SourceRecord:
    return SourceRecord(
        subject_type="alert",
        subject_ref="IMD-1",
        kind="official_warning",
        value=value,
        observed_at=datetime(2026, 10, 6, tzinfo=UTC),
    )


def test_a_warning_names_the_district_by_area_or_by_geocode() -> None:
    assert _matches_area(record_with(areas=["east khasi hills"]), "East Khasi Hills")
    assert _matches_area(
        record_with(areas=[], geocodes=[{"name": "LGD", "value": "EAST-KHASI-HILLS"}]),
        "East Khasi Hills",
        "EAST-KHASI-HILLS",
    )
    assert not _matches_area(record_with(areas=["Ri Bhoi"]), "East Khasi Hills", "EKH")
    assert not _matches_area(record_with(areas=["East Khasi Hills"]), None, None)
