"""Requests, consignments, vehicles, trips and delivery receipts."""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app import db
from app.errors import ApiError
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.scope import WorkspaceScope

SupplyRequestStatus = Literal[
    "open", "planned", "partially_fulfilled", "fulfilled", "cancelled"
]
ConsignmentStatus = Literal[
    "draft",
    "planned",
    "assigned",
    "in_transit",
    "delivered",
    "partially_delivered",
    "failed",
    "cancelled",
]
TripStatus = Literal[
    "planned", "awaiting_driver", "active", "paused", "completed", "failed", "cancelled"
]
ReceiptStatus = Literal["delivered", "partially_delivered", "failed"]

# --- state machines ---------------------------------------------------------

CONSIGNMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"planned", "cancelled"}),
    "planned": frozenset({"assigned", "cancelled"}),
    "assigned": frozenset({"in_transit", "planned", "cancelled"}),
    "in_transit": frozenset({"delivered", "partially_delivered", "failed"}),
    # Terminal: a delivered consignment is corrected by a superseding receipt,
    # not by moving the consignment backwards.
    "delivered": frozenset(),
    "partially_delivered": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

TRIP_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"awaiting_driver", "cancelled"}),
    "awaiting_driver": frozenset({"active", "cancelled"}),
    "active": frozenset({"paused", "completed", "failed"}),
    "paused": frozenset({"active", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

# A receipt on a trip decides what its consignment becomes.
RECEIPT_TO_CONSIGNMENT: dict[str, str] = {
    "delivered": "delivered",
    "partially_delivered": "partially_delivered",
    "failed": "failed",
}


# --- contracts --------------------------------------------------------------


class ConsignmentItemInput(BaseModel):
    commodity: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=32)
    weight_kg: Decimal | None = Field(default=None, ge=0)
    expiry_at: datetime | None = None


class ConsignmentItem(BaseModel):
    id: str
    commodity: str
    quantity: Decimal
    unit: str
    weight_kg: Decimal | None
    expiry_at: datetime | None


class ConsignmentCreateRequest(BaseModel):
    district_id: str
    reference: str = Field(min_length=1, max_length=64)
    origin_facility_id: str
    destination_facility_id: str
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    deadline_at: datetime | None = None
    supply_request_id: str | None = None
    items: list[ConsignmentItemInput] = Field(min_length=1)


class Consignment(BaseModel):
    id: str
    district_id: str
    reference: str
    supply_request_id: str | None
    origin_facility_id: str
    origin_facility_name: str | None
    destination_facility_id: str
    destination_facility_name: str | None
    priority: str
    deadline_at: datetime | None
    status: ConsignmentStatus
    items: list[ConsignmentItem]
    total_weight_kg: Decimal | None
    """Null when any item omits a weight: the total is unknown, not zero."""
    weight_known_for_all_items: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ConsignmentListResponse(BaseModel):
    consignments: list[Consignment]
    total: int
    as_of: datetime


class Vehicle(BaseModel):
    id: str
    registration_ref: str
    vehicle_class: str
    capacity_kg: Decimal | None
    max_height_m: Decimal | None
    active: bool
    source_mode: str


class VehicleListResponse(BaseModel):
    vehicles: list[Vehicle]
    total: int
    as_of: datetime


class DriverOption(BaseModel):
    profile_id: str
    display_name: str


class DriverListResponse(BaseModel):
    drivers: list[DriverOption]
    total: int
    as_of: datetime


class TripCreateRequest(BaseModel):
    consignment_id: str
    vehicle_id: str
    driver_profile_id: str


class TripRoutePlanRequest(BaseModel):
    route_plan_id: str


class Trip(BaseModel):
    id: str
    district_id: str
    consignment_id: str
    consignment_reference: str | None
    vehicle_id: str
    vehicle_registration: str | None
    driver_profile_id: str
    driver_display_name: str | None
    status: TripStatus
    route_plan_id: str | None = None
    route_plan_status: str | None = None
    started_at: datetime | None
    ended_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class TripListResponse(BaseModel):
    trips: list[Trip]
    total: int
    as_of: datetime
    scope: Literal["district", "assigned_to_me"]


class ReceiptItemInput(BaseModel):
    consignment_item_id: str
    delivered_quantity: Decimal = Field(ge=0)
    note: str | None = Field(default=None, max_length=1000)


class ReceiptCreateRequest(BaseModel):
    status: ReceiptStatus
    received_by_ref: str | None = Field(default=None, max_length=256)
    notes: str | None = Field(default=None, max_length=4000)
    items: list[ReceiptItemInput] = Field(default_factory=list, max_length=500)

    @field_validator("items")
    @classmethod
    def one_line_per_item(cls, items: list[ReceiptItemInput]) -> list[ReceiptItemInput]:
        # Two lines for one item would leave only the last one's quantity on
        # record, and the signer would never know which number counted.
        seen: set[str] = set()
        for line in items:
            if line.consignment_item_id in seen:
                raise ValueError(
                    f"item {line.consignment_item_id} appears on more than one line"
                )
            seen.add(line.consignment_item_id)
        return items


class ReceiptItem(BaseModel):
    consignment_item_id: str
    commodity: str
    ordered_quantity: Decimal
    delivered_quantity: Decimal
    unit: str
    note: str | None


class DeliveryReceipt(BaseModel):
    id: str
    trip_id: str
    status: ReceiptStatus
    received_at: datetime
    received_by_ref: str | None
    notes: str | None
    items: list[ReceiptItem]
    created_by_profile_id: str | None
    version: int


class TripReceiptResponse(BaseModel):
    """Always 200. A trip without a receipt is an ordinary state, not an error."""

    trip_id: str
    receipt: DeliveryReceipt | None


class ReceiptResponse(BaseModel):
    receipt: DeliveryReceipt
    trip: Trip
    consignment_status: ConsignmentStatus
    audit_event_ids: list[str]
    replayed: bool = False


class CapacityCheck(BaseModel):
    """Result of comparing a consignment's weight with a vehicle's capacity."""

    performed: bool
    """False when either side of the comparison is unknown."""
    reason: str | None
    consignment_weight_kg: Decimal | None
    vehicle_capacity_kg: Decimal | None
    within_capacity: bool | None


class TripMutationResponse(BaseModel):
    trip: Trip
    capacity_check: CapacityCheck
    audit_event_ids: list[str]
    replayed: bool = False


# --- repository -------------------------------------------------------------


class LogisticsRepository(Protocol):
    async def list_consignments(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> ConsignmentListResponse: ...

    async def create_consignment(
        self,
        *,
        scope: WorkspaceScope,
        request: ConsignmentCreateRequest,
        idempotency_key: str,
    ) -> Consignment: ...

    async def plan_consignment(
        self, *, scope: WorkspaceScope, consignment_id: str, idempotency_key: str
    ) -> Consignment: ...

    async def list_vehicles(self, *, scope: WorkspaceScope) -> VehicleListResponse: ...

    async def list_drivers(
        self, *, scope: WorkspaceScope, district_id: str
    ) -> DriverListResponse: ...

    async def list_trips(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> TripListResponse: ...

    async def create_trip(
        self, *, scope: WorkspaceScope, request: TripCreateRequest, idempotency_key: str
    ) -> TripMutationResponse: ...

    async def transition_trip(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        to_status: TripStatus,
        idempotency_key: str,
    ) -> TripMutationResponse: ...

    async def bind_route_plan(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        request: TripRoutePlanRequest,
        idempotency_key: str,
    ) -> TripMutationResponse: ...

    async def record_receipt(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        request: ReceiptCreateRequest,
        idempotency_key: str,
    ) -> ReceiptResponse: ...

    async def get_trip_receipt(
        self, *, scope: WorkspaceScope, trip_id: str
    ) -> TripReceiptResponse: ...

    async def list_supply_requests(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> SupplyRequestListResponse: ...

    async def create_supply_request(
        self,
        *,
        scope: WorkspaceScope,
        request: SupplyRequestCreateRequest,
        idempotency_key: str,
    ) -> SupplyRequest: ...


def _now() -> datetime:
    return datetime.now(UTC)


# A dispatcher holds `consignment:manage`; a coordinator or reviewer holds only
# `consignment:read`.
CONSIGNMENT_READ = ("consignment:read", "consignment:manage")


def require_any(scope: WorkspaceScope, capabilities: tuple[str, ...]) -> None:
    if not any(scope.has(capability) for capability in capabilities):
        raise ApiError(
            403,
            "forbidden",
            "You do not have access to this resource.",
        )


def refuse_transition(entity: str, current: str, requested: str) -> ApiError:
    return ApiError(
        409,
        "invalid_transition",
        f"A {entity} cannot move from {current} to {requested}.",
        details={"current": current, "requested": requested},
    )


def check_capacity(
    *, consignment_weight_kg: Decimal | None, vehicle_capacity_kg: Decimal | None
) -> CapacityCheck:
    """Compare declared weight with declared capacity."""

    if consignment_weight_kg is None:
        return CapacityCheck(
            performed=False,
            reason="At least one item has no declared weight, so the load is unknown.",
            consignment_weight_kg=None,
            vehicle_capacity_kg=vehicle_capacity_kg,
            within_capacity=None,
        )
    if vehicle_capacity_kg is None:
        return CapacityCheck(
            performed=False,
            reason="This vehicle has no declared capacity.",
            consignment_weight_kg=consignment_weight_kg,
            vehicle_capacity_kg=None,
            within_capacity=None,
        )

    return CapacityCheck(
        performed=True,
        reason=None,
        consignment_weight_kg=consignment_weight_kg,
        vehicle_capacity_kg=vehicle_capacity_kg,
        within_capacity=consignment_weight_kg <= vehicle_capacity_kg,
    )


def classify_receipt(
    *, ordered: dict[str, Decimal], delivered: dict[str, Decimal]
) -> ReceiptStatus:
    """Derive the receipt status from the quantities actually recorded."""

    if not ordered:
        raise ApiError(422, "consignment_has_no_items", "The consignment has no items.")

    total_delivered = sum(delivered.get(item_id, Decimal(0)) for item_id in ordered)
    if total_delivered <= 0:
        return "failed"
    if all(
        delivered.get(item_id, Decimal(0)) >= quantity
        for item_id, quantity in ordered.items()
    ):
        return "delivered"
    return "partially_delivered"


class PostgresLogisticsRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    # -- reads ---------------------------------------------------------------

    _CONSIGNMENT_SELECT = """
        select
          c.id::text as id,
          c.district_id::text as district_id,
          c.reference,
          c.supply_request_id::text as supply_request_id,
          c.origin_facility_id::text as origin_facility_id,
          origin.name as origin_facility_name,
          c.destination_facility_id::text as destination_facility_id,
          destination.name as destination_facility_name,
          c.priority,
          c.deadline_at,
          c.status,
          c.version,
          c.created_at,
          c.updated_at
        from public.consignments as c
        left join public.facilities as origin
          on origin.id = c.origin_facility_id and origin.organization_id = c.organization_id
        left join public.facilities as destination
          on destination.id = c.destination_facility_id
          and destination.organization_id = c.organization_id
    """

    def _load_items(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        consignment_ids: list[str],
    ) -> dict[str, list[ConsignmentItem]]:
        if not consignment_ids:
            return {}
        rows = connection.execute(
            """
            select
              id::text as id,
              consignment_id::text as consignment_id,
              commodity,
              quantity,
              unit,
              weight_kg,
              expiry_at
            from public.consignment_items
            where organization_id = %(org)s::uuid
              and consignment_id = any(%(ids)s::uuid[])
            order by commodity
            """,
            {"org": organization_id, "ids": consignment_ids},
        ).fetchall()

        grouped: dict[str, list[ConsignmentItem]] = {}
        for row in rows:
            consignment_id = row.pop("consignment_id")
            grouped.setdefault(consignment_id, []).append(ConsignmentItem(**row))
        return grouped

    @staticmethod
    def _total_weight(items: list[ConsignmentItem]) -> tuple[Decimal | None, bool]:
        """Sum declared weights, or report that the total is unknown."""

        if any(item.weight_kg is None for item in items):
            return None, False
        return sum((item.weight_kg or Decimal(0)) for item in items), True

    def _build_consignment(
        self, row: dict[str, Any], items: list[ConsignmentItem]
    ) -> Consignment:
        total, known = self._total_weight(items)
        return Consignment(
            **row,
            items=items,
            total_weight_kg=total,
            weight_known_for_all_items=known,
        )

    async def list_consignments(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> ConsignmentListResponse:
        # A dispatcher or reviewer reads the district's consignments; a driver
        # reads only what is loaded on their own trips, the manifest they
        # deliver and sign a receipt against.
        if any(scope.has(capability) for capability in CONSIGNMENT_READ):
            where = """
                where c.organization_id = %(org)s::uuid
                  and (%(districts)s::uuid[] is null
                       or c.district_id = any(%(districts)s::uuid[]))
            """
        elif scope.has("trip:read"):
            where = """
                where c.organization_id = %(org)s::uuid
                  and exists (
                    select 1
                    from public.trips as t
                    where t.organization_id = c.organization_id
                      and t.consignment_id = c.id
                      and t.driver_id = %(profile_id)s::uuid
                  )
            """
        else:
            raise ApiError(403, "forbidden", "You do not have access to this resource.")
        with self._connect() as connection:
            rows = connection.execute(
                self._CONSIGNMENT_SELECT
                + where
                + """
                  and (%(status)s::text is null or c.status::text = %(status)s)
                order by c.created_at desc
                limit %(limit)s
                """,
                {
                    "org": scope.organization_id,
                    "districts": list(scope.district_ids)
                    if scope.district_ids is not None
                    else None,
                    "profile_id": scope.profile_id,
                    "status": status,
                    "limit": limit,
                },
            ).fetchall()

            items = self._load_items(
                connection,
                organization_id=scope.organization_id,
                consignment_ids=[row["id"] for row in rows],
            )

        return ConsignmentListResponse(
            consignments=[
                self._build_consignment(row, items.get(row["id"], [])) for row in rows
            ],
            total=len(rows),
            as_of=_now(),
        )

    async def list_vehicles(self, *, scope: WorkspaceScope) -> VehicleListResponse:
        scope.require("fleet:read")
        with self._connect() as connection:
            rows = connection.execute(
                """
                select
                  id::text as id,
                  registration_ref,
                  class as vehicle_class,
                  capacity_kg,
                  max_height_m,
                  active,
                  source_mode::text as source_mode
                from public.vehicles
                where organization_id = %(org)s::uuid
                order by registration_ref
                """,
                {"org": scope.organization_id},
            ).fetchall()

        return VehicleListResponse(
            vehicles=[Vehicle(**row) for row in rows], total=len(rows), as_of=_now()
        )

    async def list_drivers(
        self, *, scope: WorkspaceScope, district_id: str
    ) -> DriverListResponse:
        """Drivers the assign call would accept."""

        scope.require("consignment:manage")
        if not scope.allows_district(district_id):
            raise ApiError(
                403, "district_not_in_scope", "That district is outside your grant."
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                select p.id::text as profile_id, p.display_name
                from public.profiles as p
                where p.organization_id = %(org)s::uuid
                  and p.active = true
                  and exists (
                    select 1 from public.role_assignments as ra
                    where ra.profile_id = p.id
                      and ra.organization_id = p.organization_id
                      and ra.role = 'driver'
                      and ra.revoked_at is null
                      and ra.valid_from <= now()
                      and (ra.valid_to is null or ra.valid_to > now())
                      and (ra.district_id is null
                           or ra.district_id = %(district_id)s::uuid)
                  )
                order by p.display_name
                """,
                {"org": scope.organization_id, "district_id": district_id},
            ).fetchall()

        return DriverListResponse(
            drivers=[DriverOption(**row) for row in rows],
            total=len(rows),
            as_of=_now(),
        )

    async def plan_consignment(
        self, *, scope: WorkspaceScope, consignment_id: str, idempotency_key: str
    ) -> Consignment:
        """Release a draft consignment for planning."""

        scope.require("consignment:manage")
        request_hash = canonical_hash(
            {"consignment_id": consignment_id, "to_status": "planned"}
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
                return Consignment(**hit.result_payload)

            row = connection.execute(
                self._CONSIGNMENT_SELECT
                + " where c.id = %(id)s::uuid and c.organization_id = %(org)s::uuid",
                {"id": consignment_id, "org": scope.organization_id},
            ).fetchone()
            if row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if not scope.allows_district(row["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )

            current = row["status"]
            if "planned" not in CONSIGNMENT_TRANSITIONS[current]:
                raise refuse_transition("consignment", current, "planned")

            items = self._load_items(
                connection,
                organization_id=scope.organization_id,
                consignment_ids=[consignment_id],
            ).get(consignment_id, [])
            # A manifest with no lines cannot be checked against a receipt, so
            # it must not reach a driver.
            if not items:
                raise ApiError(
                    422,
                    "consignment_has_no_items",
                    "A consignment must list at least one item before it is planned.",
                )

            with connection.transaction():
                connection.execute(
                    """
                    update public.consignments
                    set status = 'planned'
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {"id": consignment_id, "org": scope.organization_id},
                )
                updated = connection.execute(
                    self._CONSIGNMENT_SELECT
                    + " where c.id = %(id)s::uuid and c.organization_id = %(org)s::uuid",
                    {"id": consignment_id, "org": scope.organization_id},
                ).fetchone()
                self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="consignment.planned",
                    entity_type="consignment",
                    entity_id=consignment_id,
                    before={"status": current},
                    after={"status": "planned"},
                    metadata={"item_count": len(items)},
                )
                # Planning a consignment moves its request off "open"; the
                # rollup is recomputed rather than set, so the request's status
                # always reflects every consignment raised against it.
                if row["supply_request_id"]:
                    self._refresh_request_status(
                        connection,
                        organization_id=scope.organization_id,
                        request_id=row["supply_request_id"],
                    )

                consignment = self._build_consignment(updated, items)
                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=consignment.model_dump(mode="json"),
                )

        return consignment

    _TRIP_SELECT = """
        select
          t.id::text as id,
          t.district_id::text as district_id,
          t.consignment_id::text as consignment_id,
          c.reference as consignment_reference,
          t.vehicle_id::text as vehicle_id,
          v.registration_ref as vehicle_registration,
          t.driver_id::text as driver_profile_id,
          p.display_name as driver_display_name,
          t.status,
          t.route_plan_id::text as route_plan_id,
          rp.status::text as route_plan_status,
          t.started_at,
          t.ended_at,
          t.version,
          t.created_at,
          t.updated_at
        from public.trips as t
        left join public.consignments as c
          on c.id = t.consignment_id and c.organization_id = t.organization_id
        left join public.vehicles as v
          on v.id = t.vehicle_id and v.organization_id = t.organization_id
        left join public.profiles as p
          on p.id = t.driver_id and p.organization_id = t.organization_id
        left join public.route_plans as rp
          on rp.id = t.route_plan_id and rp.organization_id = t.organization_id
    """

    async def list_trips(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> TripListResponse:
        # A driver sees their own trips; a dispatcher sees the district's.
        if scope.has("consignment:manage"):
            where = """
                where t.organization_id = %(org)s::uuid
                  and (%(districts)s::uuid[] is null
                       or t.district_id = any(%(districts)s::uuid[]))
            """
            listing: Literal["district", "assigned_to_me"] = "district"
        elif scope.has("trip:read"):
            where = """
                where t.organization_id = %(org)s::uuid
                  and t.driver_id = %(profile_id)s::uuid
            """
            listing = "assigned_to_me"
        else:
            raise ApiError(403, "forbidden", "You do not have access to trips.")

        with self._connect() as connection:
            rows = connection.execute(
                self._TRIP_SELECT
                + where
                + """
                  and (%(status)s::text is null or t.status::text = %(status)s)
                order by t.created_at desc
                limit %(limit)s
                """,
                {
                    "org": scope.organization_id,
                    "districts": list(scope.district_ids)
                    if scope.district_ids is not None
                    else None,
                    "profile_id": scope.profile_id,
                    "status": status,
                    "limit": limit,
                },
            ).fetchall()

        return TripListResponse(
            trips=[Trip(**row) for row in rows],
            total=len(rows),
            as_of=_now(),
            scope=listing,
        )

    # -- writes --------------------------------------------------------------

    async def create_consignment(
        self,
        *,
        scope: WorkspaceScope,
        request: ConsignmentCreateRequest,
        idempotency_key: str,
    ) -> Consignment:
        scope.require("consignment:manage")
        if not scope.allows_district(request.district_id):
            raise ApiError(
                403, "district_not_in_scope", "That district is outside your grant."
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
                return Consignment(**hit.result_payload)

            consignment_id = str(uuid.uuid4())
            with connection.transaction():
                connection.execute(
                    """
                    insert into public.consignments (
                      id, organization_id, district_id, supply_request_id, reference,
                      origin_facility_id, destination_facility_id, priority,
                      deadline_at, status, created_by_profile_id
                    )
                    values (
                      %(id)s::uuid, %(org)s::uuid, %(district)s::uuid,
                      %(supply_request)s::uuid, %(reference)s,
                      %(origin)s::uuid, %(destination)s::uuid, %(priority)s,
                      %(deadline)s, 'draft', %(profile)s::uuid
                    )
                    """,
                    {
                        "id": consignment_id,
                        "org": scope.organization_id,
                        "district": request.district_id,
                        "supply_request": request.supply_request_id,
                        "reference": request.reference,
                        "origin": request.origin_facility_id,
                        "destination": request.destination_facility_id,
                        "priority": request.priority,
                        "deadline": request.deadline_at,
                        "profile": scope.profile_id,
                    },
                )

                for item in request.items:
                    connection.execute(
                        """
                        insert into public.consignment_items (
                          organization_id, consignment_id, commodity, quantity,
                          unit, weight_kg, expiry_at
                        )
                        values (
                          %(org)s::uuid, %(consignment)s::uuid, %(commodity)s,
                          %(quantity)s, %(unit)s, %(weight)s, %(expiry)s
                        )
                        """,
                        {
                            "org": scope.organization_id,
                            "consignment": consignment_id,
                            "commodity": item.commodity,
                            "quantity": item.quantity,
                            "unit": item.unit,
                            "weight": item.weight_kg,
                            "expiry": item.expiry_at,
                        },
                    )

                row = connection.execute(
                    self._CONSIGNMENT_SELECT
                    + " where c.id = %(id)s::uuid and c.organization_id = %(org)s::uuid",
                    {"id": consignment_id, "org": scope.organization_id},
                ).fetchone()
                items = self._load_items(
                    connection,
                    organization_id=scope.organization_id,
                    consignment_ids=[consignment_id],
                ).get(consignment_id, [])
                consignment = self._build_consignment(row, items)

                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=consignment.model_dump(mode="json"),
                )

        return consignment

    # -- trips ---------------------------------------------------------------

    def _audit(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        actor_id: str | None,
        action: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        metadata: dict[str, Any],
    ) -> str:
        row = connection.execute(
            """
            insert into public.audit_events (
              organization_id, actor_id, action, entity_type, entity_id,
              before_hash, after_hash, metadata
            )
            values (
              %(organization_id)s::uuid, %(actor_id)s::uuid, %(action)s, %(entity_type)s,
              %(entity_id)s::uuid, %(before_hash)s, %(after_hash)s, %(metadata)s
            )
            returning id::text as id
            """,
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before_hash": canonical_hash(before) if before is not None else None,
                "after_hash": canonical_hash(after) if after is not None else None,
                "metadata": Jsonb(metadata),
            },
        ).fetchone()
        return row["id"]

    def _read_trip(
        self, connection: psycopg.Connection, *, organization_id: str, trip_id: str
    ) -> dict[str, Any] | None:
        return connection.execute(
            self._TRIP_SELECT
            + " where t.id = %(id)s::uuid and t.organization_id = %(org)s::uuid",
            {"id": trip_id, "org": organization_id},
        ).fetchone()

    async def create_trip(
        self, *, scope: WorkspaceScope, request: TripCreateRequest, idempotency_key: str
    ) -> TripMutationResponse:
        scope.require("consignment:manage")
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
                return TripMutationResponse(**{**hit.result_payload, "replayed": True})

            consignment_row = connection.execute(
                self._CONSIGNMENT_SELECT
                + " where c.id = %(id)s::uuid and c.organization_id = %(org)s::uuid",
                {"id": request.consignment_id, "org": scope.organization_id},
            ).fetchone()
            if consignment_row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if not scope.allows_district(consignment_row["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )

            # A trip may only be raised against a consignment that is ready for
            # one. Anything else is a planning mistake, not a routing problem.
            if consignment_row["status"] not in ("planned", "assigned"):
                raise refuse_transition(
                    "consignment", consignment_row["status"], "assigned"
                )

            vehicle = connection.execute(
                """
                select id::text as id, capacity_kg, active
                from public.vehicles
                where id = %(id)s::uuid and organization_id = %(org)s::uuid
                """,
                {"id": request.vehicle_id, "org": scope.organization_id},
            ).fetchone()
            if vehicle is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if not vehicle["active"]:
                raise ApiError(
                    422, "vehicle_not_active", "That vehicle is not in service."
                )

            driver = connection.execute(
                """
                select p.id::text as id
                from public.profiles as p
                where p.organization_id = %(org)s::uuid
                  and p.id = %(profile_id)s::uuid
                  and p.active = true
                  and exists (
                    select 1 from public.role_assignments as ra
                    where ra.profile_id = p.id
                      and ra.organization_id = p.organization_id
                      and ra.role = 'driver'
                      and ra.revoked_at is null
                      and ra.valid_from <= now()
                      and (ra.valid_to is null or ra.valid_to > now())
                  )
                """,
                {"org": scope.organization_id, "profile_id": request.driver_profile_id},
            ).fetchone()
            if driver is None:
                raise ApiError(
                    422,
                    "driver_not_eligible",
                    "The driver must be an active profile holding a driver grant.",
                )

            items = self._load_items(
                connection,
                organization_id=scope.organization_id,
                consignment_ids=[request.consignment_id],
            ).get(request.consignment_id, [])
            total_weight, _ = self._total_weight(items)
            capacity = check_capacity(
                consignment_weight_kg=total_weight,
                vehicle_capacity_kg=vehicle["capacity_kg"],
            )
            # A load we know exceeds capacity is refused.
            if capacity.within_capacity is False:
                raise ApiError(
                    422,
                    "vehicle_over_capacity",
                    "The consignment is heavier than this vehicle's declared capacity.",
                    details={
                        "consignment_weight_kg": str(capacity.consignment_weight_kg),
                        "vehicle_capacity_kg": str(capacity.vehicle_capacity_kg),
                    },
                )

            trip_id = str(uuid.uuid4())
            with connection.transaction():
                connection.execute(
                    """
                    insert into public.trips (
                      id, organization_id, district_id, consignment_id,
                      vehicle_id, driver_id, status
                    )
                    values (
                      %(id)s::uuid, %(org)s::uuid, %(district)s::uuid,
                      %(consignment)s::uuid, %(vehicle)s::uuid, %(driver)s::uuid, 'planned'
                    )
                    """,
                    {
                        "id": trip_id,
                        "org": scope.organization_id,
                        "district": consignment_row["district_id"],
                        "consignment": request.consignment_id,
                        "vehicle": request.vehicle_id,
                        "driver": request.driver_profile_id,
                    },
                )
                connection.execute(
                    """
                    update public.consignments
                    set status = 'assigned'
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {"id": request.consignment_id, "org": scope.organization_id},
                )

                trip_row = self._read_trip(
                    connection, organization_id=scope.organization_id, trip_id=trip_id
                )
                audit_id = self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="trip.created",
                    entity_type="trip",
                    entity_id=trip_id,
                    before=None,
                    after={"status": "planned"},
                    metadata={
                        "consignment_id": request.consignment_id,
                        "capacity_check": capacity.model_dump(mode="json"),
                    },
                )

                response = TripMutationResponse(
                    trip=Trip(**trip_row),
                    capacity_check=capacity,
                    audit_event_ids=[audit_id],
                )
                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=response.model_dump(mode="json"),
                )

        return response

    async def transition_trip(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        to_status: TripStatus,
        idempotency_key: str,
    ) -> TripMutationResponse:
        request_hash = canonical_hash({"trip_id": trip_id, "to_status": to_status})

        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return TripMutationResponse(**{**hit.result_payload, "replayed": True})

            row = self._read_trip(
                connection, organization_id=scope.organization_id, trip_id=trip_id
            )
            if row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )

            # A driver drives their own trip; a dispatcher manages any trip in
            # their districts. Nobody else may move one.
            is_driver = row["driver_profile_id"] == scope.profile_id and scope.has(
                "trip:read"
            )
            is_manager = scope.has("consignment:manage") and scope.allows_district(
                row["district_id"]
            )
            if not (is_driver or is_manager):
                raise ApiError(403, "forbidden", "You cannot change this trip.")

            current = row["status"]
            if to_status not in TRIP_TRANSITIONS[current]:
                raise refuse_transition("trip", current, to_status)

            # Starting is where the route matters.
            if (
                to_status == "active"
                and row["route_plan_id"] is not None
                and row["route_plan_status"]
                in ("invalidated", "superseded", "rejected")
            ):
                raise ApiError(
                    409,
                    "route_plan_not_current",
                    "This trip's route plan is "
                    f"{row['route_plan_status']}. Plan and approve a route "
                    "again before the driver sets off.",
                )

            sets = ["status = %(status)s"]
            if to_status == "active" and row["started_at"] is None:
                sets.append("started_at = now()")
            if to_status in ("completed", "failed", "cancelled"):
                sets.append("ended_at = now()")

            with connection.transaction():
                connection.execute(
                    f"""
                    update public.trips
                    set {", ".join(sets)}
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {"id": trip_id, "org": scope.organization_id, "status": to_status},
                )
                if to_status == "active":
                    connection.execute(
                        """
                        update public.consignments
                        set status = 'in_transit'
                        where id = %(id)s::uuid and organization_id = %(org)s::uuid
                          and status = 'assigned'
                        """,
                        {"id": row["consignment_id"], "org": scope.organization_id},
                    )

                updated = self._read_trip(
                    connection, organization_id=scope.organization_id, trip_id=trip_id
                )
                audit_id = self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="trip.status_changed",
                    entity_type="trip",
                    entity_id=trip_id,
                    before={"status": current},
                    after={"status": to_status},
                    metadata={"consignment_id": row["consignment_id"]},
                )

                response = TripMutationResponse(
                    trip=Trip(**updated),
                    capacity_check=CapacityCheck(
                        performed=False,
                        reason="Capacity is checked when the trip is created.",
                        consignment_weight_kg=None,
                        vehicle_capacity_kg=None,
                        within_capacity=None,
                    ),
                    audit_event_ids=[audit_id],
                )
                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=response.model_dump(mode="json"),
                )

        return response

    async def bind_route_plan(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        request: TripRoutePlanRequest,
        idempotency_key: str,
    ) -> TripMutationResponse:
        """Give a trip the approved route its driver should follow."""

        scope.require("consignment:manage")
        request_hash = canonical_hash(
            {"trip_id": trip_id, **request.model_dump(mode="json")}
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
                return TripMutationResponse(**{**hit.result_payload, "replayed": True})

            row = self._read_trip(
                connection, organization_id=scope.organization_id, trip_id=trip_id
            )
            if row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if not scope.allows_district(row["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )
            if row["status"] in ("completed", "failed", "cancelled"):
                raise ApiError(
                    409,
                    "trip_finished",
                    "A finished trip does not take a new route.",
                )

            plan = connection.execute(
                """
                select id::text as id, status::text as status,
                       district_id::text as district_id
                from public.route_plans
                where id = %(id)s::uuid and organization_id = %(org)s::uuid
                """,
                {"id": request.route_plan_id, "org": scope.organization_id},
            ).fetchone()
            if plan is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if plan["district_id"] != row["district_id"]:
                raise ApiError(
                    409,
                    "route_plan_other_district",
                    "That route plan was made for a different district.",
                )
            if plan["status"] != "approved":
                raise ApiError(
                    409,
                    "route_plan_not_approved",
                    f"That route plan is {plan['status']}. Only an approved route "
                    "can be given to a driver.",
                )

            with connection.transaction():
                connection.execute(
                    """
                    update public.trips
                    set route_plan_id = %(plan)s::uuid,
                        updated_at = now(),
                        version = version + 1
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {
                        "plan": plan["id"],
                        "id": trip_id,
                        "org": scope.organization_id,
                    },
                )
                connection.execute(
                    """
                    update public.route_plans
                    set trip_id = %(id)s::uuid, updated_at = now()
                    where id = %(plan)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {
                        "plan": plan["id"],
                        "id": trip_id,
                        "org": scope.organization_id,
                    },
                )
                updated = self._read_trip(
                    connection, organization_id=scope.organization_id, trip_id=trip_id
                )
                audit_id = self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="trip.route_plan_bound",
                    entity_type="trip",
                    entity_id=trip_id,
                    before={"route_plan_id": row["route_plan_id"]},
                    after={"route_plan_id": plan["id"]},
                    metadata={"route_plan_id": plan["id"]},
                )
                response = TripMutationResponse(
                    trip=Trip(**updated),
                    capacity_check=CapacityCheck(
                        performed=False,
                        reason="Capacity is checked when the trip is created.",
                        consignment_weight_kg=None,
                        vehicle_capacity_kg=None,
                        within_capacity=None,
                    ),
                    audit_event_ids=[audit_id],
                )
                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=response.model_dump(mode="json"),
                )

        return response

    # -- receipts ------------------------------------------------------------

    async def record_receipt(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        request: ReceiptCreateRequest,
        idempotency_key: str,
    ) -> ReceiptResponse:
        request_hash = canonical_hash(
            {"trip_id": trip_id, **request.model_dump(mode="json")}
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
                return ReceiptResponse(**{**hit.result_payload, "replayed": True})

            trip_row = self._read_trip(
                connection, organization_id=scope.organization_id, trip_id=trip_id
            )
            if trip_row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )

            is_driver = trip_row["driver_profile_id"] == scope.profile_id and scope.has(
                "trip:read"
            )
            is_manager = scope.has("consignment:manage") and scope.allows_district(
                trip_row["district_id"]
            )
            if not (is_driver or is_manager):
                raise ApiError(
                    403, "forbidden", "You cannot record a receipt for this trip."
                )

            # Arrival is only meaningful for a trip that was actually running.
            if trip_row["status"] not in ("active", "paused"):
                raise refuse_transition("trip", trip_row["status"], "completed")

            items = self._load_items(
                connection,
                organization_id=scope.organization_id,
                consignment_ids=[trip_row["consignment_id"]],
            ).get(trip_row["consignment_id"], [])
            if not items:
                raise ApiError(
                    422, "consignment_has_no_items", "The consignment has no items."
                )

            by_id = {item.id: item for item in items}
            delivered: dict[str, Decimal] = {}
            for line in request.items:
                item = by_id.get(line.consignment_item_id)
                if item is None:
                    raise ApiError(
                        422,
                        "item_not_in_consignment",
                        "A receipt line refers to an item that is not on this consignment.",
                        details={"consignment_item_id": line.consignment_item_id},
                    )
                # Over-delivery is a counting error, not a happy surprise: the
                # dispatch record says how much was loaded.
                if line.delivered_quantity > item.quantity:
                    raise ApiError(
                        422,
                        "delivered_exceeds_ordered",
                        "More was recorded as delivered than was dispatched.",
                        details={
                            "consignment_item_id": item.id,
                            "ordered": str(item.quantity),
                            "delivered": str(line.delivered_quantity),
                        },
                    )
                delivered[item.id] = line.delivered_quantity

            ordered = {item.id: item.quantity for item in items}
            derived = classify_receipt(ordered=ordered, delivered=delivered)
            # The label follows the numbers. A claim that disagrees with the
            # quantities recorded would make the audit trail useless.
            if derived != request.status:
                raise ApiError(
                    422,
                    "status_does_not_match_quantities",
                    "The recorded quantities describe a "
                    f"{derived.replace('_', ' ')} delivery, not "
                    f"{request.status.replace('_', ' ')}.",
                    details={"derived": derived, "declared": request.status},
                )

            notes_by_item = {
                line.consignment_item_id: line.note for line in request.items
            }
            receipt_id = str(uuid.uuid4())

            with connection.transaction():
                connection.execute(
                    """
                    insert into public.delivery_receipts (
                      id, organization_id, trip_id, status, received_by_ref,
                      notes, created_by_profile_id, idempotency_key
                    )
                    values (
                      %(id)s::uuid, %(org)s::uuid, %(trip)s::uuid, %(status)s,
                      %(received_by)s, %(notes)s, %(profile)s::uuid, %(key)s::uuid
                    )
                    """,
                    {
                        "id": receipt_id,
                        "org": scope.organization_id,
                        "trip": trip_id,
                        "status": request.status,
                        "received_by": request.received_by_ref,
                        "notes": request.notes,
                        "profile": scope.profile_id,
                        "key": idempotency_key,
                    },
                )

                # Every item gets a line, including the ones that arrived with
                # nothing: a silent omission and a zero look identical later.
                for item in items:
                    connection.execute(
                        """
                        insert into public.delivery_receipt_items (
                          organization_id, delivery_receipt_id, consignment_item_id,
                          delivered_quantity, unit, note
                        )
                        values (
                          %(org)s::uuid, %(receipt)s::uuid, %(item)s::uuid,
                          %(quantity)s, %(unit)s, %(note)s
                        )
                        """,
                        {
                            "org": scope.organization_id,
                            "receipt": receipt_id,
                            "item": item.id,
                            "quantity": delivered.get(item.id, Decimal(0)),
                            "unit": item.unit,
                            "note": notes_by_item.get(item.id),
                        },
                    )

                connection.execute(
                    """
                    update public.trips
                    set status = %(status)s, ended_at = now()
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {
                        "id": trip_id,
                        "org": scope.organization_id,
                        "status": "completed"
                        if request.status != "failed"
                        else "failed",
                    },
                )
                connection.execute(
                    """
                    update public.consignments
                    set status = %(status)s
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {
                        "id": trip_row["consignment_id"],
                        "org": scope.organization_id,
                        "status": RECEIPT_TO_CONSIGNMENT[request.status],
                    },
                )

                # A request is a facility's need, not a shipment.
                supply_request_id = connection.execute(
                    """
                    select supply_request_id::text as id
                    from public.consignments
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {"id": trip_row["consignment_id"], "org": scope.organization_id},
                ).fetchone()
                if supply_request_id and supply_request_id["id"]:
                    self._refresh_request_status(
                        connection,
                        organization_id=scope.organization_id,
                        request_id=supply_request_id["id"],
                    )

                receipt_row = connection.execute(
                    """
                    select
                      id::text as id, trip_id::text as trip_id, status, received_at,
                      received_by_ref, notes,
                      created_by_profile_id::text as created_by_profile_id, version
                    from public.delivery_receipts
                    where id = %(id)s::uuid and organization_id = %(org)s::uuid
                    """,
                    {"id": receipt_id, "org": scope.organization_id},
                ).fetchone()

                receipt = DeliveryReceipt(
                    **receipt_row,
                    items=[
                        ReceiptItem(
                            consignment_item_id=item.id,
                            commodity=item.commodity,
                            ordered_quantity=item.quantity,
                            delivered_quantity=delivered.get(item.id, Decimal(0)),
                            unit=item.unit,
                            note=notes_by_item.get(item.id),
                        )
                        for item in items
                    ],
                )

                audit_id = self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="delivery.receipt_recorded",
                    entity_type="delivery_receipt",
                    entity_id=receipt_id,
                    before={"trip_status": trip_row["status"]},
                    after={"receipt_status": request.status},
                    metadata={
                        "trip_id": trip_id,
                        "consignment_id": trip_row["consignment_id"],
                        "declared_by": request.received_by_ref,
                    },
                )

                updated_trip = self._read_trip(
                    connection, organization_id=scope.organization_id, trip_id=trip_id
                )
                response = ReceiptResponse(
                    receipt=receipt,
                    trip=Trip(**updated_trip),
                    consignment_status=RECEIPT_TO_CONSIGNMENT[request.status],
                    audit_event_ids=[audit_id],
                )
                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=response.model_dump(mode="json"),
                )

        return response

    # -- supply requests -----------------------------------------------------

    _REQUEST_SELECT = """
        select
          r.id::text as id,
          r.facility_id::text as facility_id,
          f.name as facility_name,
          r.priority,
          r.needed_by,
          r.status,
          r.note,
          r.version,
          r.created_at,
          r.updated_at,
          coalesce(counts.total, 0)::int as consignment_count,
          coalesce(counts.delivered, 0)::int as delivered_consignment_count
        from public.supply_requests as r
        left join public.facilities as f
          on f.id = r.facility_id and f.organization_id = r.organization_id
        left join lateral (
          select
            count(*) as total,
            count(*) filter (where c.status = 'delivered') as delivered
          from public.consignments as c
          where c.supply_request_id = r.id
            and c.organization_id = r.organization_id
            and c.status <> 'cancelled'
        ) as counts on true
    """

    async def get_trip_receipt(
        self, *, scope: WorkspaceScope, trip_id: str
    ) -> TripReceiptResponse:
        """What the receiving end said arrived, read back line by line."""

        require_any(scope, CONSIGNMENT_READ + ("trip:read",))

        with self._connect() as connection:
            trip_row = self._read_trip(
                connection, organization_id=scope.organization_id, trip_id=trip_id
            )
            if trip_row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            is_driver = trip_row["driver_profile_id"] == scope.profile_id
            if not is_driver and not scope.allows_district(trip_row["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )

            receipt_row = connection.execute(
                """
                select
                  id::text as id, trip_id::text as trip_id, status, received_at,
                  received_by_ref, notes,
                  created_by_profile_id::text as created_by_profile_id, version
                from public.delivery_receipts
                where trip_id = %(trip)s::uuid and organization_id = %(org)s::uuid
                order by received_at desc
                limit 1
                """,
                {"trip": trip_id, "org": scope.organization_id},
            ).fetchone()
            if receipt_row is None:
                return TripReceiptResponse(trip_id=trip_id, receipt=None)

            items = self._load_items(
                connection,
                organization_id=scope.organization_id,
                consignment_ids=[trip_row["consignment_id"]],
            ).get(trip_row["consignment_id"], [])

            lines = connection.execute(
                """
                select consignment_item_id::text as consignment_item_id,
                       delivered_quantity, note
                from public.delivery_receipt_items
                where delivery_receipt_id = %(receipt)s::uuid
                  and organization_id = %(org)s::uuid
                """,
                {"receipt": receipt_row["id"], "org": scope.organization_id},
            ).fetchall()
            delivered = {
                row["consignment_item_id"]: row["delivered_quantity"] for row in lines
            }
            notes = {row["consignment_item_id"]: row["note"] for row in lines}

        return TripReceiptResponse(
            trip_id=trip_id,
            receipt=DeliveryReceipt(
                **receipt_row,
                items=[
                    ReceiptItem(
                        consignment_item_id=item.id,
                        commodity=item.commodity,
                        ordered_quantity=item.quantity,
                        delivered_quantity=delivered.get(item.id, Decimal(0)),
                        unit=item.unit,
                        note=notes.get(item.id),
                    )
                    for item in items
                ],
            ),
        )

    async def list_supply_requests(
        self, *, scope: WorkspaceScope, status: str | None, limit: int
    ) -> SupplyRequestListResponse:
        require_any(scope, CONSIGNMENT_READ)
        with self._connect() as connection:
            rows = connection.execute(
                self._REQUEST_SELECT
                + """
                where r.organization_id = %(org)s::uuid
                  and (%(districts)s::uuid[] is null
                       or f.district_id = any(%(districts)s::uuid[]))
                  and (%(status)s::text is null or r.status::text = %(status)s)
                order by
                  case r.priority
                    when 'critical' then 0 when 'high' then 1
                    when 'normal' then 2 else 3
                  end,
                  r.needed_by nulls last,
                  r.created_at desc
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

        return SupplyRequestListResponse(
            requests=[SupplyRequest(**row) for row in rows],
            total=len(rows),
            as_of=_now(),
        )

    async def create_supply_request(
        self,
        *,
        scope: WorkspaceScope,
        request: SupplyRequestCreateRequest,
        idempotency_key: str,
    ) -> SupplyRequest:
        scope.require("consignment:manage")
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
                return SupplyRequest(**hit.result_payload)

            facility = connection.execute(
                """
                select district_id::text as district_id
                from public.facilities
                where id = %(id)s::uuid and organization_id = %(org)s::uuid
                """,
                {"id": request.facility_id, "org": scope.organization_id},
            ).fetchone()
            if facility is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if not scope.allows_district(facility["district_id"]):
                raise ApiError(
                    403, "district_not_in_scope", "That district is outside your grant."
                )

            request_id = str(uuid.uuid4())
            with connection.transaction():
                connection.execute(
                    """
                    insert into public.supply_requests (
                      id, organization_id, facility_id, priority, needed_by,
                      status, note, created_by_profile_id
                    )
                    values (
                      %(id)s::uuid, %(org)s::uuid, %(facility)s::uuid, %(priority)s,
                      %(needed_by)s, 'open', %(note)s, %(profile)s::uuid
                    )
                    """,
                    {
                        "id": request_id,
                        "org": scope.organization_id,
                        "facility": request.facility_id,
                        "priority": request.priority,
                        "needed_by": request.needed_by,
                        "note": request.note,
                        "profile": scope.profile_id,
                    },
                )
                row = connection.execute(
                    self._REQUEST_SELECT
                    + " where r.id = %(id)s::uuid and r.organization_id = %(org)s::uuid",
                    {"id": request_id, "org": scope.organization_id},
                ).fetchone()
                created = SupplyRequest(**row)

                record_ledger(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=created.model_dump(mode="json"),
                )

        return created

    def _refresh_request_status(
        self, connection: psycopg.Connection, *, organization_id: str, request_id: str
    ) -> None:
        """Recompute a request's status from its consignments."""

        current = connection.execute(
            """
            select status::text as status from public.supply_requests
            where id = %(id)s::uuid and organization_id = %(org)s::uuid
            """,
            {"id": request_id, "org": organization_id},
        ).fetchone()
        if current is None:
            return

        rows = connection.execute(
            """
            select status::text as status from public.consignments
            where supply_request_id = %(id)s::uuid and organization_id = %(org)s::uuid
            """,
            {"id": request_id, "org": organization_id},
        ).fetchall()

        next_status = roll_up_request_status(
            consignment_statuses=[row["status"] for row in rows],
            current=current["status"],
        )
        if next_status != current["status"]:
            connection.execute(
                """
                update public.supply_requests set status = %(status)s
                where id = %(id)s::uuid and organization_id = %(org)s::uuid
                """,
                {"id": request_id, "org": organization_id, "status": next_status},
            )


def build_logistics_repository(database_url: str | None) -> LogisticsRepository | None:
    if not database_url:
        return None
    return PostgresLogisticsRepository(database_url)


# --- supply requests --------------------------------------------------------


class SupplyRequestCreateRequest(BaseModel):
    facility_id: str
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    needed_by: datetime | None = None
    note: str | None = Field(default=None, max_length=4000)


class SupplyRequest(BaseModel):
    id: str
    facility_id: str
    facility_name: str | None
    priority: str
    needed_by: datetime | None
    status: SupplyRequestStatus
    note: str | None
    consignment_count: int
    """How many consignments have been raised to fulfil this request."""
    delivered_consignment_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class SupplyRequestListResponse(BaseModel):
    requests: list[SupplyRequest]
    total: int
    as_of: datetime


def roll_up_request_status(
    *, consignment_statuses: list[str], current: str
) -> SupplyRequestStatus:
    """Derive a request's status from the consignments raised against it."""

    if current == "cancelled":
        return "cancelled"
    if not consignment_statuses:
        return "open"

    live = [status for status in consignment_statuses if status != "cancelled"]
    if not live:
        return "open"

    if all(status == "delivered" for status in live):
        return "fulfilled"
    if any(status in ("delivered", "partially_delivered") for status in live):
        return "partially_fulfilled"
    return "planned"
