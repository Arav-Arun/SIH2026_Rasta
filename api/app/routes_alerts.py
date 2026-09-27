"""HTTP routes for the alert inbox."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.alerts import (
    AlertAcknowledgeResponse,
    AlertListResponse,
    AlertRepository,
)
from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.scope import WorkspaceScope, require_workspace_scope

RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
    403: {"model": ErrorEnvelope, "description": "Capability denied."},
    404: {"model": ErrorEnvelope, "description": "No such alert for this recipient."},
    503: {"model": ErrorEnvelope, "description": "Database not configured."},
}


def _alerts(request: Request) -> AlertRepository:
    repository = getattr(request.app.state, "alert_repository", None)
    if repository is None:
        raise ApiError(503, "alerts_unavailable", "The alert store is not configured.")
    return repository


def build_alert_router(prefix: str = "/v1") -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.get(
        "/alerts",
        response_model=AlertListResponse,
        responses=RESPONSES,
        tags=["alerts"],
        summary="Read the caller's own alert inbox",
    )
    async def list_alerts(
        request: Request,
        unacknowledged: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> AlertListResponse:
        return await _alerts(request).list_alerts(
            scope=scope, unacknowledged_only=unacknowledged, limit=limit
        )

    @router.post(
        "/alerts/{alert_id}/acknowledge",
        response_model=AlertAcknowledgeResponse,
        responses={
            **RESPONSES,
            400: {"model": ErrorEnvelope, "description": "Missing Idempotency-Key."},
        },
        tags=["alerts"],
        summary="Acknowledge one alert, for this recipient only",
    )
    async def acknowledge(
        request: Request,
        alert_id: str,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> AlertAcknowledgeResponse:
        return await _alerts(request).acknowledge(scope=scope, alert_id=alert_id)

    return router


__all__ = ["build_alert_router"]
