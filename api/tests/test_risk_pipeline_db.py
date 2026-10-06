"""The risk pipeline against a real PostGIS database, inside a rolled-back transaction.

Skipped when no DATABASE_URL is configured (as in CI). Locally it runs against the
Supabase stack and leaves nothing behind.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from app.config import Settings
from app.recorded_samples import redate_recorded_sources
from app.risk_pipeline import run_pipeline
from psycopg.rows import dict_row

DATABASE_URL = Settings().database_url
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")

NS = "urn:oasis:names:tc:emergency:cap:1.2"


@pytest.fixture
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def district(db) -> dict:
    row = db.execute(
        """
        select s.organization_id::text as org, s.district_id::text as district,
               d.name, d.code, count(*) as segments,
               extensions.st_xmin(extensions.st_extent(s.geometry)) as west,
               extensions.st_xmax(extensions.st_extent(s.geometry)) as east,
               extensions.st_ymin(extensions.st_extent(s.geometry)) as south,
               extensions.st_ymax(extensions.st_extent(s.geometry)) as north
        from public.road_segments as s
        join public.districts as d on d.id = s.district_id
        group by 1, 2, 3, 4
        order by count(*) desc
        limit 1
        """
    ).fetchone()
    if row is None or row["segments"] < 10:
        pytest.skip("no imported road network")
    # Warnings already in this database would mix with the ones under test.
    db.execute(
        "delete from public.source_records where organization_id = %s::uuid "
        "and kind like 'official_warning%%'",
        (row["org"],),
    )
    return row


def cap(
    identifier: str,
    *,
    sent: datetime,
    msg_type: str = "Alert",
    references: str = "",
    severity: str = "Severe",
    area: str = "<area><areaDesc>Somewhere else</areaDesc></area>",
    expires: datetime | None = None,
    body: bool = True,
) -> str:
    refs = f"<references>{references}</references>" if references else ""
    until = expires or sent + timedelta(days=1)
    info = (
        f"<info><category>Met</category><event>Heavy Rainfall Warning</event>"
        f"<urgency>Expected</urgency><severity>{severity}</severity><certainty>Likely</certainty>"
        f"<expires>{until.isoformat()}</expires>{area}</info>"
        if body
        else ""
    )
    return (
        f'<alert xmlns="{NS}"><identifier>{identifier}</identifier>'
        f"<sender>imd@example.test</sender><sent>{sent.isoformat()}</sent>"
        f"<status>Actual</status><msgType>{msg_type}</msgType><scope>Public</scope>"
        f"{refs}{info}</alert>"
    )


def run(
    db, district: dict, tmp_path: Path, *alerts: str, now: datetime
) -> dict[str, float]:
    """Run the pipeline; return each segment's official_warning value, if it has one."""

    redate_recorded_sources(tmp_path, now=now)
    (tmp_path / "sachet_cap.xml").write_text(f"<feed>{''.join(alerts)}</feed>")
    run_pipeline(
        db,
        organization_id=district["org"],
        district_id=district["district"],
        imd_base_url=None,
        cap_base_url=None,
        fixture_root=tmp_path,
        now=now,
    )
    rows = db.execute(
        """
        select scs.segment_id::text as id, scs.risk_explanation as explanation
        from public.segment_current_state as scs
        join public.road_segments as s
          on s.id = scs.segment_id and s.organization_id = scs.organization_id
        where s.organization_id = %s::uuid and s.district_id = %s::uuid
        """,
        (district["org"], district["district"]),
    ).fetchall()
    warned = {}
    for row in rows:
        for item in (row["explanation"] or {}).get("contributions", []):
            if item["name"] == "official_warning":
                warned[row["id"]] = item["value"]
    return warned


def tag() -> str:
    return f"TEST-{uuid.uuid4().hex[:10]}"


def test_a_drawn_polygon_covers_only_the_roads_it_touches(
    db, district, tmp_path
) -> None:
    now = datetime.now(UTC)
    middle = (district["west"] + district["east"]) / 2
    ring = [
        (district["south"] - 0.001, district["west"] - 0.001),
        (district["south"] - 0.001, middle),
        (district["north"] + 0.001, middle),
        (district["north"] + 0.001, district["west"] - 0.001),
    ]
    polygon = " ".join(f"{lat},{lon}" for lat, lon in [*ring, ring[0]])
    area = f"<area><areaDesc>West of the pilot</areaDesc><polygon>{polygon}</polygon></area>"
    warned = run(
        db,
        district,
        tmp_path,
        cap(tag(), sent=now - timedelta(minutes=20), area=area),
        now=now,
    )

    wkt = (
        "POLYGON((" + ", ".join(f"{lon} {lat}" for lat, lon in [*ring, ring[0]]) + "))"
    )
    inside = {
        row["id"]
        for row in db.execute(
            """
            select id::text as id from public.road_segments
            where organization_id = %s::uuid and district_id = %s::uuid
              and extensions.st_intersects(
                geometry, extensions.st_setsrid(extensions.st_geomfromtext(%s), 4326))
            """,
            (district["org"], district["district"], wkt),
        ).fetchall()
    }
    assert 0 < len(inside) < district["segments"]
    assert set(warned) == inside


def test_a_warning_that_names_the_district_covers_all_of_it(
    db, district, tmp_path
) -> None:
    now = datetime.now(UTC)
    area = f"<area><areaDesc>{district['name']}</areaDesc></area>"
    warned = run(
        db,
        district,
        tmp_path,
        cap(tag(), sent=now - timedelta(minutes=20), area=area),
        now=now,
    )
    assert len(warned) == district["segments"]


def test_a_geocode_for_the_district_covers_all_of_it(db, district, tmp_path) -> None:
    now = datetime.now(UTC)
    area = (
        "<area><areaDesc>District code only</areaDesc><geocode><valueName>LGD</valueName>"
        f"<value>{district['code']}</value></geocode></area>"
    )
    warned = run(
        db,
        district,
        tmp_path,
        cap(tag(), sent=now - timedelta(minutes=20), area=area),
        now=now,
    )
    assert len(warned) == district["segments"]


def test_a_cancel_withdraws_the_warning(db, district, tmp_path) -> None:
    now = datetime.now(UTC)
    first, sent = tag(), now - timedelta(minutes=40)
    area = f"<area><areaDesc>{district['name']}</areaDesc></area>"
    cancel = cap(
        tag(),
        sent=now - timedelta(minutes=10),
        msg_type="Cancel",
        references=f"imd@example.test,{first},{sent.isoformat()}",
        body=False,
    )
    warned = run(
        db, district, tmp_path, cap(first, sent=sent, area=area), cancel, now=now
    )
    assert warned == {}


def test_an_update_replaces_the_warning_it_references(db, district, tmp_path) -> None:
    now = datetime.now(UTC)
    first, sent = tag(), now - timedelta(minutes=40)
    area = f"<area><areaDesc>{district['name']}</areaDesc></area>"
    update = cap(
        tag(),
        sent=now - timedelta(minutes=10),
        msg_type="Update",
        references=f"imd@example.test,{first},{sent.isoformat()}",
        severity="Moderate",
        area=area,
    )
    warned = run(
        db, district, tmp_path, cap(first, sent=sent, area=area), update, now=now
    )
    assert set(warned.values()) == {0.5}


def test_an_expired_warning_is_not_used(db, district, tmp_path) -> None:
    now = datetime.now(UTC)
    area = f"<area><areaDesc>{district['name']}</areaDesc></area>"
    expired = cap(
        tag(),
        sent=now - timedelta(hours=3),
        expires=now - timedelta(hours=1),
        area=area,
    )
    assert run(db, district, tmp_path, expired, now=now) == {}
