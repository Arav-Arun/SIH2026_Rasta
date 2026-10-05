"""Operational data health: what is live, recorded, stale, missing or failed."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg
from pydantic import BaseModel

from app import db
from app.risk_engine import MODEL_VERSION, WEIGHTS
from app.scope import WorkspaceScope
from app.sources import DEFAULT_FRESHNESS

SourceState = Literal["live", "recorded", "stale", "failed", "never_run", "disabled"]


class SourceHealth(BaseModel):
    source: str
    state: SourceState
    source_mode: str | None
    last_attempt_at: datetime | None
    #: The last run that actually produced or confirmed content. An operator
    #: needs "last successful", not "last tried".
    last_success_at: datetime | None
    last_status: str | None
    #: A reason code, never an exception or a URL.
    error_code: str | None
    record_count: int
    consecutive_failures: int
    freshness_seconds: int


class CoverageReport(BaseModel):
    """Always states its denominator. "32 reachable" alone means nothing."""

    district_id: str
    district_name: str | None
    segments_total: int
    segments_with_observed_state: int
    segments_with_risk_score: int
    facilities_total: int
    facilities_on_routing_graph: int
    graph_version: str | None
    risk_model_version: str | None
    risk_computed_at: datetime | None


class QueueHealth(BaseModel):
    unprocessed_outbox: int
    oldest_unprocessed_at: datetime | None
    idempotency_ledger_rows: int
    trips_reporting: int
    trips_stale: int
    trips_never_reported: int


class DataHealthResponse(BaseModel):
    app_mode: str
    server_time: datetime
    #: True when tracking credentials are signed with a per-process key, which
    #: means a restart invalidates them. Reported because it changes behaviour.
    tracking_credentials_ephemeral: bool
    sources: list[SourceHealth]
    coverage: list[CoverageReport]
    queues: QueueHealth
    risk_model: dict[str, Any]
    notes: list[str]


class DataHealthRepository(Protocol):
    async def report(
        self, *, scope: WorkspaceScope, app_mode: str, ephemeral_credentials: bool
    ) -> DataHealthResponse: ...


class PostgresDataHealthRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    async def report(
        self, *, scope: WorkspaceScope, app_mode: str, ephemeral_credentials: bool
    ) -> DataHealthResponse:
        scope.require("data_health:read")
        now = datetime.now(tz=UTC)
        districts = None if scope.district_ids is None else list(scope.district_ids)

        with self._connect() as connection:
            sources = self._sources(
                connection, organization_id=scope.organization_id, now=now
            )
            coverage = self._coverage(
                connection, organization_id=scope.organization_id, districts=districts
            )
            queues = self._queues(
                connection,
                organization_id=scope.organization_id,
                districts=districts,
                now=now,
            )

        notes: list[str] = []
        if any(item.state == "disabled" for item in sources):
            notes.append(
                "One or more official sources are not configured for this "
                "deployment. Their inputs are reported as missing, never as zero."
            )
        if any(item.source_mode == "recorded" for item in sources):
            notes.append(
                "Some sources are reading recorded fixtures rather than a live "
                "feed. Anything derived from them is labelled recorded."
            )
        if ephemeral_credentials:
            notes.append(
                "Tracking credentials are signed with a per-process key, so a "
                "restart invalidates every credential already issued."
            )
        if all(item.segments_with_risk_score == 0 for item in coverage) and coverage:
            notes.append(
                "No segment in scope has a risk score, so route comparison is "
                "driven by distance and time alone."
            )

        return DataHealthResponse(
            app_mode=app_mode,
            server_time=now,
            tracking_credentials_ephemeral=ephemeral_credentials,
            sources=sources,
            coverage=coverage,
            queues=queues,
            risk_model={
                "version": MODEL_VERSION,
                "kind": "documented_weighting",
                "trained_on_outcomes": False,
                "features": sorted(WEIGHTS),
                "weights": WEIGHTS,
            },
            notes=notes,
        )

    def _sources(
        self, connection: psycopg.Connection, *, organization_id: str, now: datetime
    ) -> list[SourceHealth]:
        rows = connection.execute(
            """
            with ranked as (
              select
                source, source_mode::text as source_mode, status::text as status,
                started_at, finished_at, error_code, record_count,
                row_number() over (partition by source order by started_at desc) as recency
              from public.source_runs
              where organization_id = %(org)s::uuid
            ),
            latest as (select * from ranked where recency = 1),
            succeeded as (
              select source, max(started_at) as last_success_at
              from public.source_runs
              where organization_id = %(org)s::uuid
                and status in ('success', 'unchanged')
              group by source
            ),
            failures as (
              -- Consecutive failures, counted back from the newest run.
              select source, count(*)::int as streak
              from ranked
              where status = 'failed'
                and recency <= coalesce((
                  select min(recency) from ranked as ok
                  where ok.source = ranked.source and ok.status <> 'failed'
                ) - 1, recency)
              group by source
            )
            select
              latest.source, latest.source_mode, latest.status, latest.started_at,
              latest.error_code, latest.record_count,
              succeeded.last_success_at,
              coalesce(failures.streak, 0) as streak
            from latest
            left join succeeded on succeeded.source = latest.source
            left join failures on failures.source = latest.source
            order by latest.source
            """,
            {"org": organization_id},
        ).fetchall()

        health: list[SourceHealth] = []
        for row in rows:
            freshness = DEFAULT_FRESHNESS
            state: SourceState
            if row["status"] == "disabled":
                state = "disabled"
            elif row["status"] == "failed":
                state = "failed"
            elif row["last_success_at"] is None:
                state = "never_run"
            elif now - row["last_success_at"] > freshness:
                # Ran, succeeded, and has since gone out of date. Distinct from
                # failing: the content is real but no longer current.
                state = "stale"
            elif row["source_mode"] == "recorded":
                state = "recorded"
            else:
                state = "live"

            health.append(
                SourceHealth(
                    source=row["source"],
                    state=state,
                    source_mode=row["source_mode"],
                    last_attempt_at=row["started_at"],
                    last_success_at=row["last_success_at"],
                    last_status=row["status"],
                    error_code=row["error_code"],
                    record_count=row["record_count"],
                    consecutive_failures=row["streak"],
                    freshness_seconds=int(freshness.total_seconds()),
                )
            )
        return health

    def _coverage(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        districts: list[str] | None,
    ) -> list[CoverageReport]:
        rows = connection.execute(
            """
            select
              d.id::text as district_id,
              d.name as district_name,
              count(distinct s.id)::int as segments_total,
              count(distinct case
                when scs.passability is not null and scs.passability <> 'unknown'
                then s.id end)::int as segments_with_observed_state,
              count(distinct case when scs.risk_score is not null then s.id end)::int
                as segments_with_risk_score,
              max(s.network_version) as graph_version,
              max(scs.risk_model_version) as risk_model_version,
              max(scs.risk_computed_at) as risk_computed_at
            from public.districts as d
            join public.organization_districts as od
              on od.district_id = d.id and od.organization_id = %(org)s::uuid
            left join public.road_segments as s
              on s.district_id = d.id and s.organization_id = %(org)s::uuid
            left join public.segment_current_state as scs
              on scs.segment_id = s.id and scs.organization_id = s.organization_id
            where (%(districts)s::uuid[] is null or d.id = any(%(districts)s::uuid[]))
            group by d.id, d.name
            order by d.name
            """,
            {"org": organization_id, "districts": districts},
        ).fetchall()

        facilities = connection.execute(
            """
            select
              district_id::text as district_id,
              count(*)::int as total,
              count(metadata ->> 'routing_node_id')::int as on_graph
            from public.facilities
            where organization_id = %(org)s::uuid
              and (%(districts)s::uuid[] is null
                   or district_id = any(%(districts)s::uuid[]))
            group by district_id
            """,
            {"org": organization_id, "districts": districts},
        ).fetchall()
        by_district = {row["district_id"]: row for row in facilities}

        return [
            CoverageReport(
                district_id=row["district_id"],
                district_name=row["district_name"],
                segments_total=row["segments_total"],
                segments_with_observed_state=row["segments_with_observed_state"],
                segments_with_risk_score=row["segments_with_risk_score"],
                facilities_total=by_district.get(row["district_id"], {}).get(
                    "total", 0
                ),
                facilities_on_routing_graph=by_district.get(row["district_id"], {}).get(
                    "on_graph", 0
                ),
                graph_version=row["graph_version"],
                risk_model_version=row["risk_model_version"],
                risk_computed_at=row["risk_computed_at"],
            )
            for row in rows
        ]

    def _queues(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        districts: list[str] | None,
        now: datetime,
    ) -> QueueHealth:
        outbox = connection.execute(
            """
            select count(*)::int as pending, min(available_at) as oldest
            from public.event_outbox
            where organization_id = %(org)s::uuid and processed_at is null
            """,
            {"org": organization_id},
        ).fetchone()
        ledger = connection.execute(
            """
            select count(*)::int as rows
            from public.sync_mutations
            where organization_id = %(org)s::uuid
            """,
            {"org": organization_id},
        ).fetchone()
        trips = connection.execute(
            """
            select
              count(*)::int as running,
              count(case when l.stale_after >= %(now)s then 1 end)::int as reporting,
              count(case when l.stale_after < %(now)s then 1 end)::int as stale,
              count(case when l.trip_id is null then 1 end)::int as silent
            from public.trips as t
            left join public.trip_current_location as l
              on l.trip_id = t.id and l.organization_id = t.organization_id
            where t.organization_id = %(org)s::uuid
              and t.status in ('active', 'paused')
              and (%(districts)s::uuid[] is null
                   or t.district_id = any(%(districts)s::uuid[]))
            """,
            {"org": organization_id, "districts": districts, "now": now},
        ).fetchone()

        return QueueHealth(
            unprocessed_outbox=outbox["pending"],
            oldest_unprocessed_at=outbox["oldest"],
            idempotency_ledger_rows=ledger["rows"],
            trips_reporting=trips["reporting"],
            trips_stale=trips["stale"],
            trips_never_reported=trips["silent"],
        )


def build_data_health_repository(
    database_url: str | None,
) -> DataHealthRepository | None:
    if not database_url:
        return None
    return PostgresDataHealthRepository(database_url)


__all__ = [
    "CoverageReport",
    "DataHealthRepository",
    "DataHealthResponse",
    "PostgresDataHealthRepository",
    "QueueHealth",
    "SourceHealth",
    "build_data_health_repository",
]
