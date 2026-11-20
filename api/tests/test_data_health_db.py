"""Data health against the local database.

Skipped without DATABASE_URL. Runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.config import Settings
from app.data_health import PostgresDataHealthRepository
from app.scope import WorkspaceScope
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


def report(db, org: str, active_sources: frozenset[str] | None) -> list[str]:
    repository = PostgresDataHealthRepository(DATABASE_URL)
    repository._connect = lambda: nullcontext(db)  # the test's own transaction
    scope = WorkspaceScope(
        organization_id=org,
        profile_id="00000000-0000-4000-8000-000000000000",
        roles=("admin",),
        capabilities=frozenset({"data_health:read"}),
    )
    result = asyncio.run(
        repository.report(
            scope=scope,
            app_mode="local_demo",
            ephemeral_credentials=False,
            active_sources=active_sources,
        )
    )
    return [item.source for item in result.sources]


def test_a_source_this_deployment_no_longer_runs_is_not_listed(db) -> None:
    org = db.execute(
        "select id::text as id from public.organizations limit 1"
    ).fetchone()
    if org is None:
        pytest.skip("needs an organization")
    started = datetime.now(UTC) - timedelta(days=40)
    for source in ("retired_feed", "current_feed"):
        db.execute(
            """
            insert into public.source_runs (organization_id, source, source_mode,
                                            started_at, finished_at, status)
            values (%s::uuid, %s, 'recorded', %s, %s, 'success')
            """,
            (org["id"], source, started, started),
        )

    listed = report(db, org["id"], frozenset({"current_feed"}))
    assert "current_feed" in listed
    assert "retired_feed" not in listed

    # Without a list of active sources, every source that has run is shown.
    assert {"retired_feed", "current_feed"} <= set(report(db, org["id"], None))
