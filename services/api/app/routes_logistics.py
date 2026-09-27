"""HTTP routes for consignments, vehicles, trips and delivery receipts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.logistics import (
    Consignment,
    ConsignmentCreateRequest,
    ConsignmentListResponse,
    DriverListResponse,
    LogisticsRepository,
    ReceiptCreateRequest,
    ReceiptResponse,
    SupplyRequest,
    SupplyRequestCreateRequest,
    SupplyRequestListResponse,
    TripCreateRequest,
    TripListResponse,
    TripMutationResponse,
    TripReceiptResponse,
    TripRoutePlanRequest,
    TripStatus,
    VehicleListResponse,
)
from app.scope import WorkspaceScope, require_workspace_scope

WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Missing or invalid Idempotency-Key."},
    401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
    403: {
        "model": ErrorEnvelope,
        "description": "Capability or district scope denied.",
    },
    404: {"model": ErrorEnvelope, "description": "Missing or out of scope."},
    409: {"model": ErrorEnvelope, "description": "Invalid state transition."},
    422: {"model": ErrorEnvelope, "description": "Invalid request."},
    503: {"model": ErrorEnvelope, "description": "Database not configured."},
}
READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: WRITE_RESPONSES[401],
    403: WRITE_RESPONSES[403],
    404: WRITE_RESPONSES[404],
    503: WRITE_RESPONSES[503],
}


def _logistics(request: Request) -> LogisticsRepository:
    repository = getattr(request.app.state, "logistics_repository", None)
    if repository is None:
        raise ApiError(
            503, "logistics_unavailable", "The logistics database is not configured."
        )
    return repository


def build_logistics_router(prefix: str = "/v1") -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.get(
        "/supply-requests",
        response_model=SupplyRequestListResponse,
        responses=READ_RESPONSES,
        tags=["logistics"],
        summary="List facility supply requests with how far they have been fulfilled",
    )
    async def list_supply_requests(
        request: Request,
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> SupplyRequestListResponse:
        return await _logistics(request).list_supply_requests(
            scope=scope, status=status, limit=limit
        )

    @router.post(
        "/supply-requests",
        response_model=SupplyRequest,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["logistics"],
        summary="Raise a supply request for a facility",
    )
    async def create_supply_request(
        request: Request,
        body: SupplyRequestCreateRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> SupplyRequest:
        return await _logistics(request).create_supply_request(
            scope=scope, request=body, idempotency_key=idempotency_key
        )

    @router.get(
        "/consignments",
        response_model=ConsignmentListResponse,
        responses=READ_RESPONSES,
        tags=["logistics"],
        summary="List consignments in the caller's districts",
    )
    async def list_consignments(
        request: Request,
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> ConsignmentListResponse:
        return await _logistics(request).list_consignments(
            scope=scope, status=status, limit=limit
        )

    @router.post(
        "/consignments",
        response_model=Consignment,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["logistics"],
        summary="Create a consignment with its items",
    )
    async def create_consignment(
        request: Request,
        body: ConsignmentCreateRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> Consignment:
        return await _logistics(request).create_consignment(
            scope=scope, request=body, idempotency_key=idempotency_key
        )

    @router.post(
        "/consignments/{consignment_id}/planned",
        response_model=Consignment,
        responses=WRITE_RESPONSES,
        tags=["logistics"],
        summary="Release a draft consignment for planning once its lines are right",
    )
    async def plan_consignment(
        request: Request,
        consignment_id: str,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> Consignment:
        return await _logistics(request).plan_consignment(
            scope=scope,
            consignment_id=consignment_id,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/districts/{district_id}/drivers",
        response_model=DriverListResponse,
        responses=READ_RESPONSES,
        tags=["logistics"],
        summary="Drivers this dispatcher may assign a trip to, for one district",
    )
    async def list_drivers(
        request: Request,
        district_id: str,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> DriverListResponse:
        return await _logistics(request).list_drivers(
            scope=scope, district_id=district_id
        )

    @router.get(
        "/vehicles",
        response_model=VehicleListResponse,
        responses=READ_RESPONSES,
        tags=["logistics"],
        summary="List the organisation's vehicles with their declared limits",
    )
    async def list_vehicles(
        request: Request,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> VehicleListResponse:
        return await _logistics(request).list_vehicles(scope=scope)

    @router.get(
        "/trips",
        response_model=TripListResponse,
        responses=READ_RESPONSES,
        tags=["logistics"],
        summary="List trips: the district's for a dispatcher, their own for a driver",
    )
    async def list_trips(
        request: Request,
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> TripListResponse:
        return await _logistics(request).list_trips(
            scope=scope, status=status, limit=limit
        )

    @router.post(
        "/trips",
        response_model=TripMutationResponse,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["logistics"],
        summary="Assign a vehicle and driver to a consignment",
    )
    async def create_trip(
        request: Request,
        body: TripCreateRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> TripMutationResponse:
        return await _logistics(request).create_trip(
            scope=scope, request=body, idempotency_key=idempotency_key
        )

    def _transition_route(action: TripStatus, summary: str) -> None:
        @router.post(
            f"/trips/{{trip_id}}/{action}",
            response_model=TripMutationResponse,
            responses=WRITE_RESPONSES,
            tags=["logistics"],
            summary=summary,
            name=f"trip_{action}",
        )
        async def transition(
            request: Request,
            trip_id: str,
            idempotency_key: str = Depends(require_idempotency_key),
            scope: WorkspaceScope = Depends(require_workspace_scope),
        ) -> TripMutationResponse:
            return await _logistics(request).transition_trip(
                scope=scope,
                trip_id=trip_id,
                to_status=action,
                idempotency_key=idempotency_key,
            )

    # Exposed as named actions rather than a PATCH on status, so the allowed
    # moves are visible in the contract instead of hidden in a body field.
    _transition_route("awaiting_driver", "Offer the trip to its driver")
    _transition_route("active", "Driver starts the trip")
    _transition_route("paused", "Driver pauses the trip")
    _transition_route("cancelled", "Dispatcher cancels the trip")
    _transition_route("failed", "Record that the trip could not be completed")

    @router.post(
        "/trips/{trip_id}/route-plan",
        response_model=TripMutationResponse,
        responses=WRITE_RESPONSES,
        tags=["logistics"],
        summary="Give a trip the approved route its driver should follow",
    )
    async def bind_route_plan(
        request: Request,
        trip_id: str,
        body: TripRoutePlanRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> TripMutationResponse:
        return await _logistics(request).bind_route_plan(
            scope=scope,
            trip_id=trip_id,
            request=body,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/trips/{trip_id}/receipt",
        response_model=TripReceiptResponse,
        responses=READ_RESPONSES,
        tags=["logistics"],
        summary="Read back what the receiving end said arrived, line by line",
    )
    async def get_trip_receipt(
        request: Request,
        trip_id: str,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> TripReceiptResponse:
        return await _logistics(request).get_trip_receipt(scope=scope, trip_id=trip_id)

    @router.post(
        "/trips/{trip_id}/receipt",
        response_model=ReceiptResponse,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["logistics"],
        summary="Record what actually arrived, stated by a person and not inferred",
    )
    async def record_receipt(
        request: Request,
        trip_id: str,
        body: ReceiptCreateRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> ReceiptResponse:
        return await _logistics(request).record_receipt(
            scope=scope,
            trip_id=trip_id,
            request=body,
            idempotency_key=idempotency_key,
        )

    return router


__all__ = ["build_logistics_router"]
