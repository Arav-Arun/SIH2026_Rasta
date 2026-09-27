"""HTTP routes for route plans."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.ratelimit import rate_limit
from app.route_plans import (
    RoutePlan,
    RoutePlanApproveRequest,
    RoutePlanApproveResponse,
    RoutePlanCreateRequest,
    RoutePlanCreateResponse,
    RoutePlanListResponse,
    RoutePlanRepository,
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
    409: {
        "model": ErrorEnvelope,
        "description": "Stale snapshot or invalid state transition.",
    },
    422: {"model": ErrorEnvelope, "description": "Invalid request."},
    503: {"model": ErrorEnvelope, "description": "Database not configured."},
}
READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: WRITE_RESPONSES[401],
    403: WRITE_RESPONSES[403],
    404: WRITE_RESPONSES[404],
    503: WRITE_RESPONSES[503],
}


def _plans(request: Request) -> RoutePlanRepository:
    repository = getattr(request.app.state, "route_plan_repository", None)
    if repository is None:
        raise ApiError(
            503, "routing_unavailable", "The routing database is not configured."
        )
    return repository


def build_route_plan_router(prefix: str = "/v1") -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.post(
        "/route-plans",
        response_model=RoutePlanCreateResponse,
        status_code=201,
        responses=WRITE_RESPONSES,
        tags=["routing"],
        summary="Plan a constrained route against a frozen network snapshot",
    )
    async def create_route_plan(
        request: Request,
        body: RoutePlanCreateRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("route_plan.create")),
    ) -> RoutePlanCreateResponse:
        return await _plans(request).create_plan(
            scope=scope, request=body, idempotency_key=idempotency_key
        )

    @router.get(
        "/route-plans",
        response_model=RoutePlanListResponse,
        responses=READ_RESPONSES,
        tags=["routing"],
        summary="List route plans in the caller's districts",
    )
    async def list_route_plans(
        request: Request,
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> RoutePlanListResponse:
        return await _plans(request).list_plans(scope=scope, status=status, limit=limit)

    @router.get(
        "/route-plans/{plan_id}",
        response_model=RoutePlan,
        responses=READ_RESPONSES,
        tags=["routing"],
        summary="Read one route plan with its alternatives and exclusions",
    )
    async def get_route_plan(
        request: Request,
        plan_id: str,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> RoutePlan:
        return await _plans(request).get_plan(scope=scope, plan_id=plan_id)

    @router.post(
        "/route-plans/{plan_id}/approve",
        response_model=RoutePlanApproveResponse,
        responses=WRITE_RESPONSES,
        tags=["routing"],
        summary="Approve one alternative, refusing a snapshot that has moved",
    )
    async def approve_route_plan(
        request: Request,
        plan_id: str,
        body: RoutePlanApproveRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> RoutePlanApproveResponse:
        return await _plans(request).approve_plan(
            scope=scope,
            plan_id=plan_id,
            request=body,
            idempotency_key=idempotency_key,
        )

    return router


__all__ = ["build_route_plan_router"]
