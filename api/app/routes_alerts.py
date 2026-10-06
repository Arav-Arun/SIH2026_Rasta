"""HTTP routes for the alert inbox."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.alerts import (
    AlertAcknowledgeResponse,
    AlertListResponse,
    AlertRepository,
)
from app.db import connect
from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.push import deliver_alerts_for
from app.ratelimit import rate_limit
from app.scope import WorkspaceScope, require_workspace_scope
from app.sos import SosRequest, SosResponse, raise_sos

logger = logging.getLogger("rasta.api.sos")

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

    @router.post(
        "/sos",
        response_model=SosResponse,
        status_code=201,
        responses={
            **RESPONSES,
            400: {"model": ErrorEnvelope, "description": "Missing Idempotency-Key."},
            404: {"model": ErrorEnvelope, "description": "No such trip here."},
        },
        tags=["alerts"],
        summary="Tell the control room someone needs help (this does not call 112)",
    )
    async def sos(
        request: Request,
        body: SosRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("sos.raise")),
    ) -> SosResponse:
        # Every operational role may ask for help.
        scope.require("alert:read")
        settings = request.app.state.settings
        if not settings.database_url:
            raise ApiError(
                503, "database_unavailable", "The database is not configured."
            )
        with connect(settings.database_url) as connection:
            response = raise_sos(
                connection, scope=scope, request=body, idempotency_key=idempotency_key
            )

        # After the commit: a push goes to a third party, and a failed one must
        # not undo an SOS the control room can already see.
        sender = getattr(request.app.state, "push_sender", None)
        if sender is not None:
            try:
                with connect(settings.database_url) as connection:
                    deliver_alerts_for(
                        connection,
                        organization_id=scope.organization_id,
                        sender=sender,
                        alert_ids=[response.alert_id],
                    )
            except Exception:
                logger.warning("SOS raised; push delivery failed", exc_info=True)
        return response

    return router


__all__ = ["build_alert_router"]
