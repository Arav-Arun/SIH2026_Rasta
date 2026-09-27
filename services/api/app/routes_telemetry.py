"""HTTP routes for device registration, tracking grants and telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request

from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.ratelimit import rate_limit, rate_limit_credential
from app.scope import WorkspaceScope, require_workspace_scope
from app.telemetry import (
    HISTORY_MAX_POINTS,
    DeviceListResponse,
    DeviceRegistration,
    DeviceRegistrationRequest,
    FleetLocationsResponse,
    TelemetryBatchRequest,
    TelemetryBatchResponse,
    TelemetryRepository,
    TrackingGrantRequest,
    TrackingGrantResponse,
    TripTelemetryResponse,
)

WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Missing or invalid Idempotency-Key."},
    401: {"model": ErrorEnvelope, "description": "Missing or invalid credential."},
    403: {
        "model": ErrorEnvelope,
        "description": "Capability, district or grant denied.",
    },
    404: {"model": ErrorEnvelope, "description": "Missing or out of scope."},
    409: {
        "model": ErrorEnvelope,
        "description": "The trip is not accepting positions.",
    },
    422: {"model": ErrorEnvelope, "description": "Invalid request."},
    503: {"model": ErrorEnvelope, "description": "Telemetry is not configured."},
}
READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: WRITE_RESPONSES[401],
    403: WRITE_RESPONSES[403],
    404: WRITE_RESPONSES[404],
    503: WRITE_RESPONSES[503],
}


def _telemetry(request: Request) -> TelemetryRepository:
    repository = getattr(request.app.state, "telemetry_repository", None)
    if repository is None:
        raise ApiError(
            503, "telemetry_unavailable", "Telemetry storage is not configured."
        )
    return repository


def require_tracking_grant(
    x_tracking_grant: Annotated[str | None, Header(alias="X-Tracking-Grant")] = None,
) -> str:
    """The batch endpoint is reached with a device credential, not a session."""

    if x_tracking_grant is None or not x_tracking_grant.strip():
        raise ApiError(
            401,
            "tracking_grant_required",
            "Telemetry is submitted with the trip's tracking credential.",
        )
    return x_tracking_grant.strip()


def build_telemetry_router(prefix: str = "/v1") -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.post(
        "/devices",
        response_model=DeviceRegistration,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["telemetry"],
        summary="Register the calling driver's own device for tracking",
    )
    async def register_device(
        request: Request,
        body: DeviceRegistrationRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("device.register")),
    ) -> DeviceRegistration:
        return await _telemetry(request).register_device(scope=scope, request=body)

    @router.get(
        "/devices",
        response_model=DeviceListResponse,
        responses=READ_RESPONSES,
        tags=["telemetry"],
        summary="List the calling driver's registered devices",
    )
    async def list_devices(
        request: Request,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> DeviceListResponse:
        return await _telemetry(request).list_devices(scope=scope)

    @router.post(
        "/trips/{trip_id}/tracking-grants",
        response_model=TrackingGrantResponse,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["telemetry"],
        summary="Issue a short-lived, trip-scoped tracking credential",
    )
    async def issue_grant(
        request: Request,
        trip_id: str,
        body: TrackingGrantRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("tracking_grant.issue")),
    ) -> TrackingGrantResponse:
        return await _telemetry(request).issue_grant(
            scope=scope, trip_id=trip_id, request=body
        )

    @router.post(
        "/telemetry/batches",
        response_model=TelemetryBatchResponse,
        responses=WRITE_RESPONSES,
        tags=["telemetry"],
        summary="Submit up to 20 position fixes for one trip",
    )
    async def ingest_batch(
        request: Request,
        body: TelemetryBatchRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        token: str = Depends(require_tracking_grant),
    ) -> TelemetryBatchResponse:
        rate_limit_credential(request, "telemetry.batch", token)
        return await _telemetry(request).ingest_batch(
            token=token, request=body, idempotency_key=idempotency_key
        )

    @router.get(
        "/trips/{trip_id}/telemetry",
        response_model=TripTelemetryResponse,
        responses=READ_RESPONSES,
        tags=["telemetry"],
        summary="Read a trip's position history, downsampled to a readable track",
    )
    async def trip_history(
        request: Request,
        trip_id: str,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=2, le=HISTORY_MAX_POINTS)] = HISTORY_MAX_POINTS,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> TripTelemetryResponse:
        return await _telemetry(request).trip_history(
            scope=scope, trip_id=trip_id, start=start, end=end, limit=limit
        )

    @router.get(
        "/fleet/locations",
        response_model=FleetLocationsResponse,
        responses=READ_RESPONSES,
        tags=["telemetry"],
        summary="Where every running trip last reported, and how old that is",
    )
    async def fleet_locations(
        request: Request,
        district_id: Annotated[str | None, Query()] = None,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> FleetLocationsResponse:
        return await _telemetry(request).fleet_locations(
            scope=scope, district_id=district_id
        )

    return router


__all__ = ["build_telemetry_router", "require_tracking_grant"]
