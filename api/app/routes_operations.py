"""Operational routes: data health, risk recomputation and risk explanation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from app.audit import MAX_PAGE, AuditExportResponse, AuditRepository, to_csv
from app.data_health import DataHealthRepository, DataHealthResponse
from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.push import (
    PushRepository,
    PushRevokeRequest,
    PushStatusResponse,
    PushSubscriptionRequest,
    PushSubscriptionResponse,
    PushTestResponse,
)
from app.ratelimit import rate_limit
from app.risk_outcomes import RiskOutcomesResponse, outcome_report
from app.risk_pipeline import run_pipeline
from app.scope import WorkspaceScope, require_workspace_scope
from app.sources import connect

RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
    403: {"model": ErrorEnvelope, "description": "Capability or district denied."},
    404: {"model": ErrorEnvelope, "description": "Missing or out of scope."},
    503: {"model": ErrorEnvelope, "description": "Database not configured."},
}


class RiskRecomputeResponse(BaseModel):
    district_id: str
    model_version: str
    risk_snapshot_version: str
    computed_at: str
    segments_scored: int
    segments_unscored: int
    levels: dict[str, int]
    runs: list[dict[str, Any]]
    #: Stated on every response so no caller has to infer it.
    changes_passability: bool = False


def _push(request: Request) -> PushRepository:
    repository = getattr(request.app.state, "push_repository", None)
    if repository is None:
        raise ApiError(503, "push_unavailable", "Push registration is not configured.")
    return repository


def _health(request: Request) -> DataHealthRepository:
    repository = getattr(request.app.state, "data_health_repository", None)
    if repository is None:
        raise ApiError(503, "data_health_unavailable", "Data health is not configured.")
    return repository


def _audit(request: Request) -> AuditRepository:
    repository = getattr(request.app.state, "audit_repository", None)
    if repository is None:
        raise ApiError(503, "audit_unavailable", "The audit trail is not configured.")
    return repository


def build_operations_router(prefix: str = "/v1") -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.get(
        "/audit-events",
        response_model=AuditExportResponse,
        responses={
            **RESPONSES,
            200: {
                "description": "The events, oldest first; CSV when format=csv.",
                "content": {"text/csv": {}},
            },
            422: {"model": ErrorEnvelope, "description": "Invalid filter or cursor."},
        },
        tags=["operations"],
        summary="Export the audit trail, oldest first, for an administrator",
    )
    async def audit_events(
        request: Request,
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
        action: Annotated[str | None, Query(max_length=80)] = None,
        entity_type: Annotated[str | None, Query(max_length=80)] = None,
        after: Annotated[str | None, Query(max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
        format: Annotated[Literal["json", "csv"], Query()] = "json",
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> Any:
        # The trail names people and what they decided; it is an administrator's
        # view, and the database policy says the same (audit_events_select_admin).
        scope.require("audit:read")
        export = await _audit(request).export(
            scope=scope,
            since=since,
            until=until,
            action=action,
            entity_type=entity_type,
            after=after,
            limit=limit,
        )
        if format == "csv":
            headers = {"Content-Disposition": 'attachment; filename="rasta-audit.csv"'}
            if export.next_cursor:
                headers["X-Next-Cursor"] = export.next_cursor
            return Response(
                to_csv(export.events), media_type="text/csv", headers=headers
            )
        return export

    @router.get(
        "/data-health",
        response_model=DataHealthResponse,
        responses=RESPONSES,
        tags=["operations"],
        summary="Source, coverage and queue health, with no secrets in it",
    )
    async def data_health(
        request: Request,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> DataHealthResponse:
        # Operational health is an operator's view. It names sources, versions,
        # coverage denominators and queue depth; it is not for a driver.
        scope.require("data_health:read")

        settings = request.app.state.settings
        report = await _health(request).report(
            scope=scope,
            app_mode=settings.app_mode,
            ephemeral_credentials=bool(
                getattr(request.app.state, "telemetry_token_secret_is_ephemeral", False)
            ),
        )
        # How often roads are re-scored is this deployment's setting, not the
        # database's, so it is added here.
        every = settings.risk_recompute_minutes or None
        report.risk_model["scheduled_recompute_minutes"] = every
        if every is None:
            report.notes.append(
                "Risk is re-scored only when someone asks for it: no schedule is set."
            )
        return report

    @router.post(
        "/risk/recompute",
        response_model=RiskRecomputeResponse,
        responses={
            **RESPONSES,
            400: {"model": ErrorEnvelope, "description": "Missing Idempotency-Key."},
        },
        tags=["operations"],
        summary="Run the sources and re-score one district's roads",
    )
    async def recompute(
        request: Request,
        district_id: Annotated[str, Query()],
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("risk.recompute")),
    ) -> RiskRecomputeResponse:
        # Scoring is a planning input, so it is gated on the planning capability
        # rather than on a role: a dispatcher may refresh their own district.
        scope.require("route:plan")
        scope.require_district(district_id)

        settings = request.app.state.settings
        if not settings.database_url:
            raise ApiError(
                503, "database_unavailable", "The database is not configured."
            )

        fixture_root = settings.resolved_source_fixture_root
        with connect(settings.database_url) as connection, connection.transaction():
            result = run_pipeline(
                connection,
                organization_id=scope.organization_id,
                district_id=district_id,
                imd_base_url=settings.imd_api_base_url,
                cap_base_url=settings.sachet_cap_base_url,
                fixture_root=fixture_root,
                terrain_file=settings.resolved_terrain_file,
            )
        return RiskRecomputeResponse(**result.as_dict())

    @router.get(
        "/risk/outcomes",
        response_model=RiskOutcomesResponse,
        responses=RESPONSES,
        tags=["operations"],
        summary="What the risk score said about each road, against what was confirmed",
    )
    async def risk_outcomes(
        request: Request,
        district_id: Annotated[str, Query()],
        days: Annotated[int, Query(ge=1, le=366)] = 30,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> RiskOutcomesResponse:
        # The same audience as data health: it describes how the model is doing.
        scope.require("data_health:read")
        scope.require_district(district_id)

        settings = request.app.state.settings
        if not settings.database_url:
            raise ApiError(
                503, "database_unavailable", "The database is not configured."
            )
        try:
            with connect(settings.database_url) as connection:
                return outcome_report(
                    connection,
                    organization_id=scope.organization_id,
                    district_id=district_id,
                    days=days,
                    today=datetime.now(tz=UTC).date(),
                )
        except psycopg.errors.UndefinedTable as error:
            raise ApiError(
                503,
                "outcome_log_unavailable",
                "The outcome log is not set up on this database yet.",
            ) from error

    @router.get(
        "/push-subscriptions",
        response_model=PushStatusResponse,
        responses=RESPONSES,
        tags=["operations"],
        summary="Whether push is configured, and this caller's registrations",
    )
    async def push_status(
        request: Request,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> PushStatusResponse:
        return await _push(request).status(scope=scope)

    @router.post(
        "/push-subscriptions",
        response_model=PushSubscriptionResponse,
        status_code=201,
        responses={
            **RESPONSES,
            400: {"model": ErrorEnvelope, "description": "Missing Idempotency-Key."},
        },
        tags=["operations"],
        summary="Register a browser or device for notifications",
    )
    async def push_subscribe(
        request: Request,
        body: PushSubscriptionRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("push.subscribe")),
    ) -> PushSubscriptionResponse:
        return await _push(request).subscribe(scope=scope, request=body)

    @router.post(
        "/push-subscriptions/revoke",
        status_code=204,
        responses=RESPONSES,
        tags=["operations"],
        summary="Revoke one of this caller's registrations",
    )
    async def push_revoke(
        request: Request,
        body: PushRevokeRequest,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> None:
        await _push(request).revoke(scope=scope, endpoint=body.endpoint)

    @router.post(
        "/push-subscriptions/test",
        response_model=PushTestResponse,
        responses=RESPONSES,
        tags=["operations"],
        summary="Send a test notification to this caller's own registrations",
    )
    async def push_test(
        request: Request,
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("push.test")),
    ) -> PushTestResponse:
        return await _push(request).send_test(scope=scope)

    return router


__all__ = ["build_operations_router"]
