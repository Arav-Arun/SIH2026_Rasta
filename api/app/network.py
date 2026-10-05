"""Network-state reads: bounded segment queries, segment detail and reachability."""

from __future__ import annotations

from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg

from app import db
from app.errors import ApiError
from app.schemas import (
    BBox,
    BridgeInfo,
    ConnectivityFacility,
    ConnectivitySummaryResponse,
    FacilityCounts,
    NetworkCoverage,
    NetworkObservation,
    SegmentDetailResponse,
    SegmentFeature,
    SegmentProperties,
    SegmentRisk,
    SupplyHub,
)

DEGREES_PER_METRE_AT_EQUATOR = 1.0 / 111_320.0
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"

Passability = Literal["open", "restricted", "closed", "unknown"]


@dataclass(frozen=True, slots=True)
class SegmentQuery:
    organization_id: str
    district_ids: frozenset[str] | None
    bbox: BBox
    passability: Passability | None
    since: datetime | None
    simplify_m: float | None
    limit: int


@dataclass(slots=True)
class SegmentPage:
    features: list[SegmentFeature]
    total_in_bbox: int
    coverage: NetworkCoverage


@dataclass(slots=True)
class DistrictNetwork:
    district_id: str
    network_versions: list[str]
    baseline_as_of: datetime | None
    # (segment_id, from_node, to_node, passability)
    edges: list[tuple[str, str, str, str]]
    facilities: list[dict[str, Any]]
    passability_counts: dict[str, int] = field(default_factory=dict)


class NetworkRepository(Protocol):
    async def list_segments(self, query: SegmentQuery) -> SegmentPage: ...

    async def get_segment(
        self,
        *,
        organization_id: str,
        district_ids: frozenset[str] | None,
        segment_id: str,
    ) -> SegmentDetailResponse | None: ...

    async def district_network(
        self,
        *,
        organization_id: str,
        district_id: str,
    ) -> DistrictNetwork | None: ...


def parse_bbox(raw: str) -> BBox:
    """Parse ``minLon,minLat,maxLon,maxLat`` with helpful validation errors."""

    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 4:
        raise ApiError(
            422,
            "invalid_bbox",
            "bbox must be four comma-separated numbers: minLon,minLat,maxLon,maxLat.",
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as error:
        raise ApiError(
            422,
            "invalid_bbox",
            "bbox must be four comma-separated numbers: minLon,minLat,maxLon,maxLat.",
        ) from error
    if not (-180 <= min_lon < max_lon <= 180) or not (-90 <= min_lat < max_lat <= 90):
        raise ApiError(
            422,
            "invalid_bbox",
            "bbox must satisfy -180 <= minLon < maxLon <= 180 and -90 <= minLat < maxLat <= 90.",
        )
    return BBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


def bbox_area_deg2(bbox: BBox) -> float:
    return (bbox.max_lon - bbox.min_lon) * (bbox.max_lat - bbox.min_lat)


def _row_properties(row: dict[str, Any]) -> SegmentProperties:
    metadata = row.get("metadata") or {}
    bridge = None
    if row.get("bridge_id"):
        bridge = BridgeInfo(
            id=row["bridge_id"],
            max_weight_t=row.get("bridge_max_weight_t"),
            status=row.get("bridge_status") or "unknown",
            verified_at=row.get("bridge_verified_at"),
            source_id=row.get("bridge_source_id"),
        )
    return SegmentProperties(
        segment_id=row["id"],
        district_id=row["district_id"],
        from_node_id=row["from_node_id"],
        to_node_id=row["to_node_id"],
        name=metadata.get("name"),
        road_class=row["road_class"],
        length_m=float(row["length_m"]),
        base_speed_kph=row.get("base_speed_kph"),
        max_weight_t=row.get("max_weight_t"),
        bridge=bridge,
        passability=row.get("passability") or "unknown",
        risk_level=row.get("risk_level") or "unknown",
        risk_score=row.get("risk_score"),
        state_as_of=row.get("as_of"),
        source_summary=row.get("source_summary") or {},
        graph_version=row["network_version"],
        network_version=row.get("state_network_version") or row["network_version"],
        source_mode=row["source_mode"],
    )


class PostgresNetworkRepository:
    """Direct PostGIS reads with explicit organization/district filtering."""

    _SEGMENT_SELECT = """
        select
          rs.id::text as id,
          rs.district_id::text as district_id,
          rs.from_node_id,
          rs.to_node_id,
          rs.length_m::float8 as length_m,
          rs.road_class,
          rs.base_speed_kph::float8 as base_speed_kph,
          rs.max_weight_t::float8 as max_weight_t,
          rs.network_version,
          rs.source_mode::text as source_mode,
          rs.metadata,
          extensions.st_asgeojson(
            case
              when %(simplify_deg)s::float8 is null then rs.geometry
              else extensions.st_simplifypreservetopology(rs.geometry, %(simplify_deg)s::float8)
            end
          )::json as geometry,
          scs.passability::text as passability,
          scs.risk_level::text as risk_level,
          scs.risk_score::float8 as risk_score,
          scs.as_of,
          scs.source_summary,
          scs.network_version as state_network_version,
          b.bridge_id,
          b.bridge_max_weight_t,
          b.bridge_status,
          b.bridge_verified_at,
          b.bridge_source_id
        from public.road_segments as rs
        left join public.segment_current_state as scs
          on scs.segment_id = rs.id
         and scs.organization_id = rs.organization_id
        left join lateral (
          select
            br.id::text as bridge_id,
            br.max_weight_t::float8 as bridge_max_weight_t,
            br.status::text as bridge_status,
            br.verified_at as bridge_verified_at,
            br.source_id as bridge_source_id
          from public.bridges as br
          where br.segment_id = rs.id
            and br.organization_id = rs.organization_id
          order by br.created_at
          limit 1
        ) as b on true
    """

    _SCOPE_WHERE = """
        where rs.organization_id = %(organization_id)s::uuid
          and (
            %(district_ids)s::uuid[] is null
            or rs.district_id = any(%(district_ids)s::uuid[])
          )
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    async def list_segments(self, query: SegmentQuery) -> SegmentPage:
        params: dict[str, Any] = {
            "organization_id": query.organization_id,
            "district_ids": list(query.district_ids)
            if query.district_ids is not None
            else None,
            "min_lon": query.bbox.min_lon,
            "min_lat": query.bbox.min_lat,
            "max_lon": query.bbox.max_lon,
            "max_lat": query.bbox.max_lat,
            "passability": query.passability,
            "since": query.since,
            "simplify_deg": (
                query.simplify_m * DEGREES_PER_METRE_AT_EQUATOR
                if query.simplify_m
                else None
            ),
            "limit": query.limit,
        }
        bbox_filter = """
              and rs.geometry && extensions.st_makeenvelope(
                %(min_lon)s::float8, %(min_lat)s::float8,
                %(max_lon)s::float8, %(max_lat)s::float8, 4326
              )
              and (%(passability)s::text is null or scs.passability::text = %(passability)s::text)
              and (%(since)s::timestamptz is null or scs.updated_at > %(since)s::timestamptz)
        """
        with self._connect() as connection:
            rows = connection.execute(
                self._SEGMENT_SELECT
                + self._SCOPE_WHERE
                + bbox_filter
                + " order by rs.id limit %(limit)s",
                params,
            ).fetchall()
            total_row = connection.execute(
                """
                select count(*) as total
                from public.road_segments as rs
                left join public.segment_current_state as scs
                  on scs.segment_id = rs.id and scs.organization_id = rs.organization_id
                """
                + self._SCOPE_WHERE
                + bbox_filter,
                params,
            ).fetchone()
            coverage_row = connection.execute(
                """
                select
                  extensions.st_xmin(extent)::float8 as min_lon,
                  extensions.st_ymin(extent)::float8 as min_lat,
                  extensions.st_xmax(extent)::float8 as max_lon,
                  extensions.st_ymax(extent)::float8 as max_lat,
                  segment_count,
                  network_versions,
                  district_ids
                from (
                  select
                    extensions.st_extent(rs.geometry)::extensions.geometry as extent,
                    count(*)::int as segment_count,
                    array_remove(array_agg(distinct rs.network_version), null) as network_versions,
                    array_remove(array_agg(distinct rs.district_id::text), null) as district_ids
                  from public.road_segments as rs
                """
                + self._SCOPE_WHERE
                + ") as scope",
                params,
            ).fetchone()

        features = [
            SegmentFeature(
                id=row["id"], geometry=row["geometry"], properties=_row_properties(row)
            )
            for row in rows
        ]
        total = int(total_row["total"]) if total_row else 0
        coverage = NetworkCoverage(
            bbox=(
                BBox(
                    min_lon=coverage_row["min_lon"],
                    min_lat=coverage_row["min_lat"],
                    max_lon=coverage_row["max_lon"],
                    max_lat=coverage_row["max_lat"],
                )
                if coverage_row and coverage_row["min_lon"] is not None
                else None
            ),
            segment_count=int(coverage_row["segment_count"]) if coverage_row else 0,
            network_versions=list(coverage_row["network_versions"] or [])
            if coverage_row
            else [],
            district_ids=list(coverage_row["district_ids"] or [])
            if coverage_row
            else [],
            returned=len(features),
            truncated=total > len(features),
        )
        return SegmentPage(features=features, total_in_bbox=total, coverage=coverage)

    async def get_segment(
        self,
        *,
        organization_id: str,
        district_ids: frozenset[str] | None,
        segment_id: str,
    ) -> SegmentDetailResponse | None:
        params: dict[str, Any] = {
            "organization_id": organization_id,
            "district_ids": list(district_ids) if district_ids is not None else None,
            "segment_id": segment_id,
            "simplify_deg": None,
        }
        with self._connect() as connection:
            row = connection.execute(
                self._SEGMENT_SELECT
                + self._SCOPE_WHERE
                + " and rs.id = %(segment_id)s::uuid",
                params,
            ).fetchone()
            if row is None:
                return None
            observation_rows = connection.execute(
                """
                select
                  o.id::text as id,
                  o.kind,
                  o.passability::text as passability,
                  o.risk_level::text as risk_level,
                  o.risk_score::float8 as risk_score,
                  o.observed_at,
                  o.valid_until,
                  o.source_id,
                  o.source_mode::text as source_mode,
                  o.confidence::float8 as confidence
                from public.network_observations as o
                where o.organization_id = %(organization_id)s::uuid
                  and o.segment_id = %(segment_id)s::uuid
                order by o.observed_at desc
                limit 20
                """,
                params,
            ).fetchall()

        properties = _row_properties(row)
        return SegmentDetailResponse(
            segment=SegmentFeature(
                id=row["id"], geometry=row["geometry"], properties=properties
            ),
            risk=SegmentRisk(
                available=False,
                level=properties.risk_level,
                score=properties.risk_score,
                reason="risk_engine_not_available",
            ),
            observations=[
                NetworkObservation(**observation) for observation in observation_rows
            ],
            affected_trips=[],
            affected_trips_available=False,
            allowed_actions=[],
            as_of=datetime.now(UTC),
            mode=properties.source_mode,
            network_version=properties.network_version,
        )

    async def district_network(
        self,
        *,
        organization_id: str,
        district_id: str,
    ) -> DistrictNetwork | None:
        params = {"organization_id": organization_id, "district_id": district_id}
        with self._connect() as connection:
            membership = connection.execute(
                """
                select 1
                from public.organization_districts as od
                where od.organization_id = %(organization_id)s::uuid
                  and od.district_id = %(district_id)s::uuid
                  and od.active = true
                """,
                params,
            ).fetchone()
            if membership is None:
                return None
            edge_rows = connection.execute(
                """
                select
                  rs.id::text as id,
                  rs.from_node_id,
                  rs.to_node_id,
                  coalesce(scs.passability::text, 'unknown') as passability,
                  rs.network_version,
                  scs.as_of
                from public.road_segments as rs
                left join public.segment_current_state as scs
                  on scs.segment_id = rs.id and scs.organization_id = rs.organization_id
                where rs.organization_id = %(organization_id)s::uuid
                  and rs.district_id = %(district_id)s::uuid
                """,
                params,
            ).fetchall()
            facility_rows = connection.execute(
                """
                select
                  f.id::text as id,
                  f.name,
                  f.type,
                  f.metadata,
                  extensions.st_x(f.location)::float8 as longitude,
                  extensions.st_y(f.location)::float8 as latitude
                from public.facilities as f
                where f.organization_id = %(organization_id)s::uuid
                  and f.district_id = %(district_id)s::uuid
                  and f.active = true
                order by f.name
                """,
                params,
            ).fetchall()

        counts: dict[str, int] = {}
        versions: set[str] = set()
        as_of: datetime | None = None
        edges: list[tuple[str, str, str, str]] = []
        for row in edge_rows:
            edges.append(
                (row["id"], row["from_node_id"], row["to_node_id"], row["passability"])
            )
            counts[row["passability"]] = counts.get(row["passability"], 0) + 1
            versions.add(row["network_version"])
            if row["as_of"] is not None and (as_of is None or row["as_of"] > as_of):
                as_of = row["as_of"]
        return DistrictNetwork(
            district_id=district_id,
            network_versions=sorted(versions),
            baseline_as_of=as_of,
            edges=edges,
            facilities=[dict(row) for row in facility_rows],
            passability_counts=counts,
        )


def build_network_repository(database_url: str | None) -> NetworkRepository | None:
    if not database_url:
        return None
    return PostgresNetworkRepository(database_url)


def compute_connectivity(network: DistrictNetwork) -> ConnectivitySummaryResponse:
    """Reachability of monitored facilities from explicit supply hubs."""

    warnings: list[str] = []
    adjacency: dict[str, list[str]] = {}
    for _segment_id, from_node, to_node, passability in network.edges:
        if passability == "closed":
            continue
        adjacency.setdefault(from_node, []).append(to_node)

    hubs: list[SupplyHub] = []
    hub_nodes: list[str] = []
    for facility in network.facilities:
        metadata = facility.get("metadata") or {}
        if metadata.get("supply_hub") or metadata.get("supply_hub_candidate"):
            hubs.append(SupplyHub(facility_id=facility["id"], name=facility["name"]))
            node = metadata.get("routing_node_id")
            if node:
                hub_nodes.append(node)

    reached: set[str] = set()
    if hub_nodes:
        queue = deque(hub_nodes)
        reached.update(hub_nodes)
        while queue:
            node = queue.popleft()
            for neighbour in adjacency.get(node, ()):
                if neighbour not in reached:
                    reached.add(neighbour)
                    queue.append(neighbour)

    statuses: list[ConnectivityFacility] = []
    counts = FacilityCounts()
    for facility in network.facilities:
        metadata = facility.get("metadata") or {}
        node = metadata.get("routing_node_id")
        eligible = bool(metadata.get("routing_eligible")) and bool(node)
        if not network.edges or not hub_nodes or not eligible:
            status = "unknown_coverage"
        elif node in reached:
            status = "reachable"
        else:
            status = "isolated"
        counts.monitored += 1
        if status == "reachable":
            counts.reachable += 1
        elif status == "isolated":
            counts.isolated += 1
        else:
            counts.unknown_coverage += 1
        statuses.append(
            ConnectivityFacility(
                facility_id=facility["id"],
                name=facility["name"],
                type=facility["type"],
                status=status,
                routing_node_id=node,
                location=(
                    {
                        "longitude": facility["longitude"],
                        "latitude": facility["latitude"],
                    }
                    if facility.get("longitude") is not None
                    else None
                ),
            )
        )

    if not network.edges:
        coverage_state: Literal["complete", "partial", "none"] = "none"
        warnings.append("no_network_imported")
    elif not hub_nodes:
        coverage_state = "partial"
        warnings.append("no_supply_hub_configured")
    elif counts.unknown_coverage:
        coverage_state = "partial"
    else:
        coverage_state = "complete"

    unknown_segments = network.passability_counts.get("unknown", 0)
    if unknown_segments:
        warnings.append("passability_unknown_segments")
    warnings.append("risk_engine_not_available")

    return ConnectivitySummaryResponse(
        district_id=network.district_id,
        graph_version=network.network_versions[0]
        if len(network.network_versions) == 1
        else None,
        graph_versions=network.network_versions,
        computed_at=datetime.now(UTC),
        baseline_as_of=network.baseline_as_of,
        coverage_state=coverage_state,
        facilities=counts,
        supply_hubs=hubs,
        facility_status=statuses,
        segments={
            "total": len(network.edges),
            "open": network.passability_counts.get("open", 0),
            "restricted": network.passability_counts.get("restricted", 0),
            "closed": network.passability_counts.get("closed", 0),
            "unknown": unknown_segments,
        },
        warnings=warnings,
    )
