"""The segment page cache, against the local database.

Skipped without DATABASE_URL. Runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext

import psycopg
import pytest
from app.config import Settings
from app.network import PostgresNetworkRepository, SegmentQuery
from app.schemas import BBox
from psycopg.rows import dict_row

DATABASE_URL = Settings().database_url
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")


@pytest.fixture
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def test_a_page_is_reused_until_a_road_in_scope_changes(db) -> None:
    segment = db.execute(
        """
        select s.organization_id::text as org, s.id::text as id,
               extensions.st_x(extensions.st_startpoint(s.geometry)) as lon,
               extensions.st_y(extensions.st_startpoint(s.geometry)) as lat
        from public.road_segments as s
        join public.segment_current_state as scs
          on scs.segment_id = s.id and scs.organization_id = s.organization_id
        limit 1
        """
    ).fetchone()
    if segment is None:
        pytest.skip("needs an imported road network")

    repository = PostgresNetworkRepository(DATABASE_URL)
    repository._connect = lambda: nullcontext(db)  # the test's own transaction
    query = SegmentQuery(
        organization_id=segment["org"],
        district_ids=None,
        bbox=BBox(
            min_lon=segment["lon"] - 0.01,
            min_lat=segment["lat"] - 0.01,
            max_lon=segment["lon"] + 0.01,
            max_lat=segment["lat"] + 0.01,
        ),
        passability=None,
        since=None,
        simplify_m=None,
        limit=5000,
    )

    first = asyncio.run(repository.list_segments(query))
    assert asyncio.run(repository.list_segments(query)) is first

    # A closure written without touching updated_at still shows at once.
    db.execute(
        "update public.segment_current_state set passability = 'closed' "
        "where segment_id = %s::uuid",
        (segment["id"],),
    )
    after = asyncio.run(repository.list_segments(query))
    assert after is not first
    closed = {
        feature.id
        for feature in after.features
        if feature.properties.passability == "closed"
    }
    assert segment["id"] in closed
