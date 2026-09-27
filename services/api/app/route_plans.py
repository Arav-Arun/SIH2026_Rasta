"""Route plans: propose against a frozen snapshot, approve only if it still holds."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.errors import ApiError
from app.exposure import escalate_no_route
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.push import PushSender, deliver_alerts_for
from app.routing.core import CostPolicy, VehicleProfile
from app.routing.graph import PILOT_ROAD_CLASS_SPEED_KPH, load_district_graph
from app.routing.planner import RouteRequest, Snapshot, plan_routes
from app.scope import WorkspaceScope

ROUTE_READ = ("route:plan", "network:read")

PlanStatus = Literal["proposed", "approved", "superseded", "invalidated", "rejected"]


class RoutePlanCreateRequest(BaseModel):
    district_id: str
    origin_node_id: str
    destination_node_id: str
    vehicle_id: str | None = None
    #: Taken from the vehicle row when `vehicle_id` is given; either way a
    #: missing figure means unknown, never zero.
    gross_weight_t: float | None = Field(default=None, gt=0)
    height_m: float | None = Field(default=None, gt=0)
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    requested_alternatives: int = Field(default=3, ge=1, le=3)
    trip_id: str | None = None
    consignment_id: str | None = None


class RouteAlternativeModel(BaseModel):
    id: str
    # The planner's own deterministic identifier for this path. Stable across
    # replays of the same graph, and not the id used to approve an alternative.
    planner_key: str | None = None
    rank: int
    category: str
    segment_ids: list[str]
    #: GeoJSON for the whole route, assembled from its segments in path order.
    geometry: dict[str, Any] | None = None
    node_ids: list[str]
    distance_m: float
    travel_time_seconds: float
    cost_seconds: float
    eta_range_seconds: list[int]
    risk_summary: dict[str, Any]
    constraint_warnings: list[dict[str, Any]]
    reasons: list[str]
    requires_review: bool


class RoutePlan(BaseModel):
    id: str
    district_id: str
    trip_id: str | None
    status: PlanStatus
    network_version: str
    risk_snapshot_version: str
    cost_policy_version: str
    graph_edge_count: int
    computed_at: str
    result_status: Literal["feasible", "no_route"]
    alternatives: list[RouteAlternativeModel]
    exclusions: dict[str, list[dict[str, Any]]]
    coverage_warnings: list[dict[str, Any]]
    reason_codes: list[str]
    actions: list[str]
    safe_route_claim: bool
    chosen_alternative_id: str | None
    approved_at: datetime | None
    created_at: datetime
    version: int


class RoutePlanCreateResponse(BaseModel):
    plan: RoutePlan
    replayed: bool = False


class RoutePlanApproveRequest(BaseModel):
    alternative_id: str
    expected_network_version: str


class RoutePlanApproveResponse(BaseModel):
    plan: RoutePlan
    audit_event_ids: list[str]
    replayed: bool = False


class RoutePlanListResponse(BaseModel):
    plans: list[RoutePlan]
    total: int
    as_of: datetime


class RoutePlanRepository(Protocol):
    async def create_plan(
        self,
        *,
        scope: WorkspaceScope,
        request: RoutePlanCreateRequest,
        idempotency_key: str,
    ) -> RoutePlanCreateResponse: ...

    async def get_plan(self, *, scope: WorkspaceScope, plan_id: str) -> RoutePlan: ...

    async def list_plans(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> RoutePlanListResponse: ...

    async def approve_plan(
        self,
        *,
        scope: WorkspaceScope,
        plan_id: str,
        request: RoutePlanApproveRequest,
        idempotency_key: str,
    ) -> RoutePlanApproveResponse: ...


def _now() -> datetime:
    return datetime.now(UTC)


def require_any(scope: WorkspaceScope, capabilities: tuple[str, ...]) -> None:
    if not any(scope.has(capability) for capability in capabilities):
        raise ApiError(403, "forbidden", "You do not have access to this resource.")


def pilot_cost_policy() -> CostPolicy:
    return CostPolicy(road_class_speed_kph=dict(PILOT_ROAD_CLASS_SPEED_KPH))


class PostgresRoutePlanRepository:
    def __init__(
        self, database_url: str, push_sender: PushSender | None = None
    ) -> None:
        self._database_url = database_url
        self._push_sender = push_sender

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url, row_factory=dict_row)

    # -- reads ---------------------------------------------------------------

    _PLAN_SELECT = """
        select
          rp.id::text as id,
          rp.district_id::text as district_id,
          rp.trip_id::text as trip_id,
          rp.status::text as status,
          rp.network_version,
          rp.risk_snapshot_version,
          rp.request,
          rp.chosen_alternative_id::text as chosen_alternative_id,
          rp.approved_at,
          rp.created_at,
          rp.version
        from public.route_plans as rp
    """

    def _load_alternatives(
        self, connection: psycopg.Connection, *, organization_id: str, plan_id: str
    ) -> list[RouteAlternativeModel]:
        rows = connection.execute(
            """
            select id::text as id, rank, category, segment_ids, distance_m,
                   eta_seconds, risk_summary, constraint_warnings,
                   extensions.st_asgeojson(geometry)::json as geometry
            from public.route_alternatives
            where route_plan_id = %(plan)s::uuid and organization_id = %(org)s::uuid
            order by rank
            """,
            {"plan": plan_id, "org": organization_id},
        ).fetchall()

        alternatives: list[RouteAlternativeModel] = []
        for row in rows:
            # The full computed shape is kept inside risk_summary's sibling
            # payload so the stored plan can be replayed exactly; the columns
            # exist for querying, not as the source of truth.
            detail = row["risk_summary"].get("_detail", {})
            alternatives.append(
                RouteAlternativeModel(
                    # The stored row's own id, so `chosen_alternative_id` on the plan
                    # names an alternative the caller can actually find.
                    id=row["id"],
                    planner_key=detail.get("id"),
                    rank=row["rank"],
                    category=row["category"],
                    segment_ids=list(row["segment_ids"]),
                    geometry=row["geometry"] or detail.get("geometry"),
                    node_ids=detail.get("node_ids", []),
                    distance_m=float(row["distance_m"]),
                    travel_time_seconds=detail.get("travel_time_seconds", 0.0),
                    cost_seconds=detail.get("cost_seconds", 0.0),
                    eta_range_seconds=detail.get(
                        "eta_range_seconds",
                        [row["eta_seconds"] or 0, row["eta_seconds"] or 0],
                    ),
                    risk_summary={
                        key: value
                        for key, value in row["risk_summary"].items()
                        if key != "_detail"
                    },
                    constraint_warnings=list(row["constraint_warnings"]),
                    reasons=detail.get("reasons", []),
                    requires_review=detail.get("requires_review", False),
                )
            )
        return alternatives

    def _route_geometry(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        segment_ids: list[str],
    ) -> dict[str, Any] | None:
        """The route's line, assembled from its segments in path order."""

        if not segment_ids:
            return None
        row = connection.execute(
            """
            with ordered as (
              select rs.geometry, s.ordinality
              from unnest(%(ids)s::uuid[]) with ordinality as s(segment_id, ordinality)
              join public.road_segments as rs
                on rs.id = s.segment_id and rs.organization_id = %(org)s::uuid
              order by s.ordinality
            )
            select extensions.st_asgeojson(
              extensions.st_linemerge(
                extensions.st_collect(geometry order by ordinality)
              )
            )::json as geometry
            from ordered
            """,
            {"ids": segment_ids, "org": organization_id},
        ).fetchone()
        return row["geometry"] if row and row["geometry"] else None

    def _build_plan(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        row: dict[str, Any],
    ) -> RoutePlan:
        stored = row["request"]
        result = stored.get("result", {})
        return RoutePlan(
            id=row["id"],
            district_id=row["district_id"],
            trip_id=row["trip_id"],
            status=row["status"],
            network_version=row["network_version"],
            risk_snapshot_version=row["risk_snapshot_version"],
            cost_policy_version=result.get("cost_policy_version", "unknown"),
            graph_edge_count=result.get("graph_edge_count", 0),
            computed_at=result.get("computed_at", row["created_at"].isoformat()),
            result_status=result.get("status", "no_route"),
            alternatives=self._load_alternatives(
                connection, organization_id=organization_id, plan_id=row["id"]
            ),
            exclusions=result.get("exclusions", {}),
            coverage_warnings=result.get("coverage_warnings", []),
            reason_codes=result.get("reason_codes", []),
            actions=result.get("actions", []),
            safe_route_claim=False,
            chosen_alternative_id=row["chosen_alternative_id"],
            approved_at=row["approved_at"],
            created_at=row["created_at"],
            version=row["version"],
        )

    def _drives_this_plan(
        self, connection: psycopg.Connection, *, scope: WorkspaceScope, plan_id: str
    ) -> bool:
        """Whether the caller is the driver of a trip this plan was given to."""

        if not scope.has("trip:read"):
            return False
        row = connection.execute(
            """
            select 1
            from public.trips
            where organization_id = %(org)s::uuid
              and driver_id = %(driver)s::uuid
              and route_plan_id = %(plan)s::uuid
            limit 1
            """,
            {
                "org": scope.organization_id,
                "driver": scope.profile_id,
                "plan": plan_id,
            },
        ).fetchone()
        return row is not None

    async def get_plan(self, *, scope: WorkspaceScope, plan_id: str) -> RoutePlan:
        try:
            uuid.UUID(plan_id)
        except ValueError as error:
            raise ApiError(
                404, "not_found", "The requested resource was not found."
            ) from error

        with self._connect() as connection:
            own_trip = self._drives_this_plan(connection, scope=scope, plan_id=plan_id)
            if not own_trip:
                require_any(scope, ROUTE_READ)

            row = connection.execute(
                self._PLAN_SELECT
                + " where rp.id = %(id)s::uuid and rp.organization_id = %(org)s::uuid",
                {"id": plan_id, "org": scope.organization_id},
            ).fetchone()
            if row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            # District scope applies to browsing, not to the route a driver was
            # actually given: a driver's grant is organization-wide by design.
            if not own_trip and not scope.allows_district(row["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )
            return self._build_plan(
                connection, organization_id=scope.organization_id, row=row
            )

    async def list_plans(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> RoutePlanListResponse:
        require_any(scope, ROUTE_READ)
        with self._connect() as connection:
            rows = connection.execute(
                self._PLAN_SELECT
                + """
                where rp.organization_id = %(org)s::uuid
                  and (%(districts)s::uuid[] is null
                       or rp.district_id = any(%(districts)s::uuid[]))
                  and (%(status)s::text is null or rp.status::text = %(status)s)
                order by rp.created_at desc
                limit %(limit)s
                """,
                {
                    "org": scope.organization_id,
                    "districts": list(scope.district_ids)
                    if scope.district_ids is not None
                    else None,
                    "status": status,
                    "limit": limit,
                },
            ).fetchall()
            plans = [
                self._build_plan(
                    connection, organization_id=scope.organization_id, row=row
                )
                for row in rows
            ]
        return RoutePlanListResponse(plans=plans, total=len(plans), as_of=_now())

    # -- writes --------------------------------------------------------------

    def _resolve_vehicle(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        request: RoutePlanCreateRequest,
    ) -> VehicleProfile:
        """Vehicle dimensions, preferring the register over the caller."""

        weight = request.gross_weight_t
        height = request.height_m
        vehicle_id = request.vehicle_id

        if vehicle_id:
            row = connection.execute(
                """
                select capacity_kg::float8 as capacity_kg,
                       max_height_m::float8 as max_height_m,
                       active
                from public.vehicles
                where id = %(id)s::uuid and organization_id = %(org)s::uuid
                """,
                {"id": vehicle_id, "org": organization_id},
            ).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "That vehicle is not in the register.")
            if not row["active"]:
                raise ApiError(
                    422, "vehicle_not_active", "That vehicle is not in service."
                )
            if row["max_height_m"] is not None:
                height = row["max_height_m"]
            # `capacity_kg` is payload, not gross weight, so it is not silently reused
            # as one.

        return VehicleProfile(
            vehicle_id=vehicle_id or "unregistered",
            gross_weight_t=weight,
            height_m=height,
        )

    async def create_plan(
        self,
        *,
        scope: WorkspaceScope,
        request: RoutePlanCreateRequest,
        idempotency_key: str,
    ) -> RoutePlanCreateResponse:
        scope.require("route:plan")
        if not scope.allows_district(request.district_id):
            raise ApiError(
                403, "district_not_in_scope", "That district is outside your grant."
            )
        if request.origin_node_id == request.destination_node_id:
            raise ApiError(
                422,
                "origin_equals_destination",
                "Origin and destination must be different graph nodes.",
            )

        request_hash = canonical_hash(request.model_dump(mode="json"))

        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return RoutePlanCreateResponse(
                    plan=RoutePlan(**hit.result_payload["plan"]), replayed=True
                )

            vehicle = self._resolve_vehicle(
                connection, organization_id=scope.organization_id, request=request
            )
            graph = load_district_graph(
                connection,
                organization_id=scope.organization_id,
                district_id=request.district_id,
            )
            if not graph.edges:
                raise ApiError(
                    422,
                    "district_has_no_network",
                    "No road network is loaded for that district.",
                )

            node_ids = {edge.from_node for edge in graph.edges} | {
                edge.to_node for edge in graph.edges
            }
            for field_name, value in (
                ("origin_node_id", request.origin_node_id),
                ("destination_node_id", request.destination_node_id),
            ):
                if value not in node_ids:
                    raise ApiError(
                        422,
                        "unknown_graph_node",
                        "That point is not a node on this district's road graph.",
                        details={"field": field_name, "value": value},
                    )

            snapshot = Snapshot(
                network_version=graph.network_version,
                risk_snapshot_version=graph.risk_snapshot_version,
                cost_policy_version=pilot_cost_policy().version,
                graph_edge_count=len(graph.edges),
                computed_at=graph.loaded_at,
            )
            result = plan_routes(
                graph.edges,
                RouteRequest(
                    origin_node_id=request.origin_node_id,
                    destination_node_id=request.destination_node_id,
                    vehicle=vehicle,
                    priority=request.priority,
                    requested_alternatives=request.requested_alternatives,
                ),
                snapshot,
                pilot_cost_policy(),
            )
            payload = result.to_payload()

            plan_id = str(uuid.uuid4())
            escalation_alert_id: str | None = None
            with connection.transaction():
                connection.execute(
                    """
                    insert into public.route_plans (
                      id, organization_id, district_id, trip_id, request,
                      network_version, risk_snapshot_version, status,
                      created_by_profile_id
                    )
                    values (
                      %(id)s::uuid, %(org)s::uuid, %(district)s::uuid, %(trip)s::uuid,
                      %(request)s, %(network)s, %(risk)s, 'proposed', %(profile)s::uuid
                    )
                    """,
                    {
                        "id": plan_id,
                        "org": scope.organization_id,
                        "district": request.district_id,
                        "trip": request.trip_id,
                        # Stored immutably: the inputs and the computed result
                        # together, so the plan can be explained months later
                        # even after the network has moved on.
                        "request": Jsonb(
                            {
                                "input": request.model_dump(mode="json"),
                                "vehicle": {
                                    "vehicle_id": vehicle.vehicle_id,
                                    "gross_weight_t": vehicle.gross_weight_t,
                                    "height_m": vehicle.height_m,
                                },
                                "result": payload,
                            }
                        ),
                        "network": snapshot.network_version,
                        "risk": snapshot.risk_snapshot_version,
                        "profile": scope.profile_id,
                    },
                )

                for alternative in result.alternatives:
                    geometry = self._route_geometry(
                        connection,
                        organization_id=scope.organization_id,
                        segment_ids=alternative.segment_ids,
                    )
                    connection.execute(
                        """
                        insert into public.route_alternatives (
                          organization_id, route_plan_id, rank, category,
                          segment_ids, distance_m, eta_seconds, risk_summary,
                          constraint_warnings, geometry
                        )
                        values (
                          %(org)s::uuid, %(plan)s::uuid, %(rank)s, %(category)s,
                          %(segments)s, %(distance)s, %(eta)s, %(risk)s, %(warnings)s,
                          case
                            when %(line)s::text is null then null
                            else extensions.st_setsrid(
                              extensions.st_geomfromgeojson(%(line)s::text), 4326
                            )
                          end
                        )
                        """,
                        {
                            "org": scope.organization_id,
                            "plan": plan_id,
                            "rank": alternative.rank,
                            "category": alternative.category,
                            "segments": Jsonb(alternative.segment_ids),
                            "distance": alternative.distance_m,
                            "eta": int(round(alternative.travel_time_seconds)),
                            "risk": Jsonb(
                                {
                                    **alternative.risk_summary,
                                    "_detail": {
                                        "id": alternative.id,
                                        "node_ids": alternative.node_ids,
                                        "travel_time_seconds": alternative.travel_time_seconds,
                                        "cost_seconds": alternative.cost_seconds,
                                        "eta_range_seconds": list(
                                            alternative.eta_range_seconds
                                        ),
                                        "reasons": alternative.reasons,
                                        "requires_review": alternative.requires_review,
                                        "geometry": geometry,
                                    },
                                }
                            ),
                            "warnings": Jsonb(alternative.constraint_warnings),
                            # The column only accepts a LineString.
                            "line": json.dumps(geometry)
                            if geometry and geometry.get("type") == "LineString"
                            else None,
                        },
                    )

                # A destination with no route left is an escalation, not a failed
                # request.
                if payload.get("status") == "no_route":
                    escalation_alert_id = escalate_no_route(
                        connection,
                        organization_id=scope.organization_id,
                        district_id=request.district_id,
                        subject_id=request.destination_node_id,
                        facility_name=None,
                        reason_codes=list(payload.get("reason_codes", [])),
                        exclusions=dict(payload.get("exclusions", {})),
                    )

                row = connection.execute(
                    self._PLAN_SELECT + " where rp.id = %(id)s::uuid",
                    {"id": plan_id},
                ).fetchone()
                plan = self._build_plan(
                    connection, organization_id=scope.organization_id, row=row
                )

                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload={"plan": plan.model_dump(mode="json")},
                )

            # After the commit, never inside it: a push goes to a third party,
            # and the escalation is already in every recipient's inbox.
            if self._push_sender is not None and escalation_alert_id is not None:
                try:
                    deliver_alerts_for(
                        connection,
                        organization_id=scope.organization_id,
                        sender=self._push_sender,
                        alert_ids=[escalation_alert_id],
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()

        return RoutePlanCreateResponse(plan=plan)

    async def approve_plan(
        self,
        *,
        scope: WorkspaceScope,
        plan_id: str,
        request: RoutePlanApproveRequest,
        idempotency_key: str,
    ) -> RoutePlanApproveResponse:
        scope.require("route:plan")
        request_hash = canonical_hash(
            {"plan_id": plan_id, **request.model_dump(mode="json")}
        )

        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return RoutePlanApproveResponse(
                    plan=RoutePlan(**hit.result_payload["plan"]),
                    audit_event_ids=hit.result_payload.get("audit_event_ids", []),
                    replayed=True,
                )

            row = connection.execute(
                self._PLAN_SELECT
                + " where rp.id = %(id)s::uuid and rp.organization_id = %(org)s::uuid",
                {"id": plan_id, "org": scope.organization_id},
            ).fetchone()
            if row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if not scope.allows_district(row["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )
            if row["status"] != "proposed":
                raise ApiError(
                    409,
                    "invalid_transition",
                    f"A route plan cannot move from {row['status']} to approved.",
                    details={"current": row["status"], "requested": "approved"},
                )

            stored_result = row["request"].get("result", {})
            if stored_result.get("status") != "feasible":
                raise ApiError(
                    422,
                    "route_plan_has_no_route",
                    "This plan found no route, so there is nothing to approve.",
                )

            # The caller states which network they were looking at.
            if request.expected_network_version != row["network_version"]:
                raise ApiError(
                    409,
                    "route_plan_stale",
                    "Road conditions changed; request a new route plan.",
                    details={
                        "expected_network_version": request.expected_network_version,
                        "plan_network_version": row["network_version"],
                    },
                )

            # And the network itself must not have moved since the plan was
            # computed, whatever the caller believes.
            current = load_district_graph(
                connection,
                organization_id=scope.organization_id,
                district_id=row["district_id"],
            )
            if (
                current.network_version != row["network_version"]
                or current.risk_snapshot_version != row["risk_snapshot_version"]
            ):
                connection.execute(
                    """
                    update public.route_plans set status = 'invalidated'
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {"id": plan_id, "org": scope.organization_id},
                )
                connection.commit()
                raise ApiError(
                    409,
                    "route_plan_stale",
                    "Road conditions changed; request a new route plan.",
                    details={
                        "plan_network_version": row["network_version"],
                        "current_network_version": current.network_version,
                        "plan_risk_snapshot_version": row["risk_snapshot_version"],
                        "current_risk_snapshot_version": current.risk_snapshot_version,
                    },
                )

            alternatives = self._load_alternatives(
                connection, organization_id=scope.organization_id, plan_id=plan_id
            )
            chosen = next(
                (item for item in alternatives if item.id == request.alternative_id),
                None,
            )
            if chosen is None:
                raise ApiError(
                    422,
                    "unknown_alternative",
                    "That alternative does not belong to this route plan.",
                )

            with connection.transaction():
                connection.execute(
                    """
                    update public.route_plans
                    set status = 'approved',
                        chosen_alternative_id = %(alternative)s::uuid,
                        approved_by_profile_id = %(profile)s::uuid,
                        approved_at = now()
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {
                        "id": plan_id,
                        "org": scope.organization_id,
                        "alternative": chosen.id,
                        "profile": scope.profile_id,
                    },
                )

                # The approval is the auditable act: it records which network
                # the dispatcher was looking at and which warnings they were
                # shown, so "why was this route approved?" stays answerable.
                audit = connection.execute(
                    """
                    insert into public.audit_events (
                      organization_id, actor_id, action, entity_type, entity_id,
                      before_hash, after_hash, metadata
                    )
                    values (
                      %(org)s::uuid, %(actor)s::uuid, 'route_plan.approved',
                      'route_plan', %(entity)s::uuid, %(before)s, %(after)s, %(meta)s
                    )
                    returning id::text as id
                    """,
                    {
                        "org": scope.organization_id,
                        "actor": scope.profile_id,
                        "entity": plan_id,
                        "before": canonical_hash({"status": "proposed"}),
                        "after": canonical_hash(
                            {"status": "approved", "alternative_id": chosen.id}
                        ),
                        "meta": Jsonb(
                            {
                                "network_version": row["network_version"],
                                "risk_snapshot_version": row["risk_snapshot_version"],
                                "alternative_id": chosen.id,
                                "requires_review": chosen.requires_review,
                                "constraint_warnings": chosen.constraint_warnings,
                            }
                        ),
                    },
                ).fetchone()

                updated = connection.execute(
                    self._PLAN_SELECT + " where rp.id = %(id)s::uuid",
                    {"id": plan_id},
                ).fetchone()
                plan = self._build_plan(
                    connection, organization_id=scope.organization_id, row=updated
                )

                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload={
                        "plan": plan.model_dump(mode="json"),
                        "audit_event_ids": [audit["id"]],
                    },
                )

        return RoutePlanApproveResponse(plan=plan, audit_event_ids=[audit["id"]])


def build_route_plan_repository(
    database_url: str | None, push_sender: PushSender | None = None
) -> RoutePlanRepository | None:
    if not database_url:
        return None
    return PostgresRoutePlanRepository(database_url, push_sender)


__all__ = [
    "ROUTE_READ",
    "PostgresRoutePlanRepository",
    "RouteAlternativeModel",
    "RoutePlan",
    "RoutePlanApproveRequest",
    "RoutePlanApproveResponse",
    "RoutePlanCreateRequest",
    "RoutePlanCreateResponse",
    "RoutePlanListResponse",
    "RoutePlanRepository",
    "build_route_plan_repository",
    "pilot_cost_policy",
]
