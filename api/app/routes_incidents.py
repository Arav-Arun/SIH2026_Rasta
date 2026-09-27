"""HTTP routes for incidents, evidence attachments and inspections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.incidents import (
    AttachmentAddResponse,
    AttachmentCompleteRequest,
    AttachmentCompleteResponse,
    AttachmentDraft,
    IncidentCreateRequest,
    IncidentCreateResponse,
    IncidentListResponse,
    IncidentRepository,
    IncidentReviewRequest,
    IncidentReviewResponse,
    IncidentStatus,
    IncidentSummary,
)
from app.inspections import (
    AssignableOfficerListResponse,
    InspectionCompleteRequest,
    InspectionCreateRequest,
    InspectionListResponse,
    InspectionMutationResponse,
    InspectionRepository,
    InspectionStatus,
    InspectionSummary,
)
from app.ratelimit import rate_limit
from app.scope import WorkspaceScope, require_workspace_scope

WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Missing or invalid Idempotency-Key."},
    401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
    403: {
        "model": ErrorEnvelope,
        "description": "Capability or district scope denied.",
    },
    409: {
        "model": ErrorEnvelope,
        "description": "Version, idempotency or state conflict.",
    },
    422: {"model": ErrorEnvelope, "description": "Invalid request."},
    503: {"model": ErrorEnvelope, "description": "Database not configured."},
}
READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: WRITE_RESPONSES[401],
    403: WRITE_RESPONSES[403],
    404: {"model": ErrorEnvelope, "description": "Missing or out of scope."},
    503: WRITE_RESPONSES[503],
}


def _incidents(request: Request) -> IncidentRepository:
    repository = request.app.state.incident_repository
    if repository is None:
        raise ApiError(
            503, "incidents_unavailable", "The incident database is not configured."
        )
    return repository


def _inspections(request: Request) -> InspectionRepository:
    repository = request.app.state.inspection_repository
    if repository is None:
        raise ApiError(
            503, "inspections_unavailable", "The inspection database is not configured."
        )
    return repository


def build_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)

    # -- incidents -----------------------------------------------------------

    @router.post(
        "/incidents",
        response_model=IncidentCreateResponse,
        responses=WRITE_RESPONSES,
        status_code=201,
        tags=["incidents"],
        summary="Submit a geo-tagged field report and receive evidence upload targets",
    )
    async def create_incident(
        request: Request,
        body: IncidentCreateRequest,
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("incident.create")),
        idempotency_key: str = Depends(require_idempotency_key),
    ) -> IncidentCreateResponse:
        return await _incidents(request).create_incident(
            scope=scope, request=body, idempotency_key=idempotency_key
        )

    @router.get(
        "/incidents",
        response_model=IncidentListResponse,
        responses=READ_RESPONSES,
        tags=["incidents"],
        summary="List reports: the review queue for dispatchers, own reports for field staff",
    )
    async def list_incidents(
        request: Request,
        status: Annotated[IncidentStatus | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> IncidentListResponse:
        return await _incidents(request).list_incidents(
            scope=scope, status=status, limit=limit
        )

    @router.get(
        "/incidents/{incident_id}",
        response_model=IncidentSummary,
        responses=READ_RESPONSES,
        tags=["incidents"],
        summary="Read one report with its evidence, segments and review record",
    )
    async def get_incident(
        request: Request,
        incident_id: str,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> IncidentSummary:
        incident = await _incidents(request).get_incident(
            scope=scope, incident_id=incident_id
        )
        if incident is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        return incident

    @router.post(
        "/incidents/{incident_id}/attachments",
        response_model=AttachmentAddResponse,
        responses=WRITE_RESPONSES,
        status_code=201,
        tags=["incidents"],
        summary="Add an evidence file to an open report and receive a fresh upload target",
    )
    async def add_attachment(
        request: Request,
        incident_id: str,
        body: AttachmentDraft,
        scope: WorkspaceScope = Depends(require_workspace_scope),
        _limit: None = Depends(rate_limit("attachment.issue")),
        idempotency_key: str = Depends(require_idempotency_key),
    ) -> AttachmentAddResponse:
        return await _incidents(request).add_attachment(
            scope=scope,
            incident_id=incident_id,
            draft=body,
            idempotency_key=idempotency_key,
        )

    @router.post(
        "/incidents/{incident_id}/attachments/{attachment_id}/complete",
        response_model=AttachmentCompleteResponse,
        responses=WRITE_RESPONSES,
        tags=["incidents"],
        summary="Verify an uploaded evidence object (path, length, checksum, scope)",
    )
    async def complete_attachment(
        request: Request,
        incident_id: str,
        attachment_id: str,
        body: AttachmentCompleteRequest,
        scope: WorkspaceScope = Depends(require_workspace_scope),
        idempotency_key: str = Depends(require_idempotency_key),
    ) -> AttachmentCompleteResponse:
        return await _incidents(request).complete_attachment(
            scope=scope,
            incident_id=incident_id,
            attachment_id=attachment_id,
            request=body,
            idempotency_key=idempotency_key,
        )

    @router.post(
        "/incidents/{incident_id}/review",
        response_model=IncidentReviewResponse,
        responses=WRITE_RESPONSES,
        tags=["incidents"],
        summary="Dispatcher decision: confirm restriction/closure, reopen, request clarification or reject",
    )
    async def review_incident(
        request: Request,
        incident_id: str,
        body: IncidentReviewRequest,
        scope: WorkspaceScope = Depends(require_workspace_scope),
        idempotency_key: str = Depends(require_idempotency_key),
    ) -> IncidentReviewResponse:
        return await _incidents(request).review_incident(
            scope=scope,
            incident_id=incident_id,
            request=body,
            idempotency_key=idempotency_key,
        )

    # -- inspections ---------------------------------------------------------

    @router.post(
        "/inspections",
        response_model=InspectionMutationResponse,
        responses=WRITE_RESPONSES,
        status_code=201,
        tags=["inspections"],
        summary="Assign a field officer to verify an incident, segment, bridge or facility",
    )
    async def create_inspection(
        request: Request,
        body: InspectionCreateRequest,
        scope: WorkspaceScope = Depends(require_workspace_scope),
        idempotency_key: str = Depends(require_idempotency_key),
    ) -> InspectionMutationResponse:
        return await _inspections(request).create(
            scope=scope, request=body, idempotency_key=idempotency_key
        )

    @router.get(
        "/inspections",
        response_model=InspectionListResponse,
        responses=READ_RESPONSES,
        tags=["inspections"],
        summary="List inspections: district queue for dispatchers, own tasks for field staff",
    )
    async def list_inspections(
        request: Request,
        status: Annotated[InspectionStatus | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> InspectionListResponse:
        return await _inspections(request).list(scope=scope, status=status, limit=limit)

    @router.get(
        "/districts/{district_id}/assignable-officers",
        response_model=AssignableOfficerListResponse,
        responses=READ_RESPONSES,
        tags=["inspections"],
        summary="List field officers a dispatcher may assign an inspection to",
    )
    async def list_assignable_officers(
        request: Request,
        district_id: str,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> AssignableOfficerListResponse:
        officers = await _inspections(request).eligible_assignees(
            scope=scope, district_id=district_id
        )
        return AssignableOfficerListResponse(
            officers=officers,
            district_id=district_id,
            as_of=datetime.now(UTC),
        )

    @router.get(
        "/inspections/{inspection_id}",
        response_model=InspectionSummary,
        responses=READ_RESPONSES,
        tags=["inspections"],
        summary="Read one inspection",
    )
    async def get_inspection(
        request: Request,
        inspection_id: str,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> InspectionSummary:
        inspection = await _inspections(request).get(
            scope=scope, inspection_id=inspection_id
        )
        if inspection is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        return inspection

    def _transition_route(
        action: Literal["accept", "start", "complete", "cancel"], summary: str
    ):
        @router.post(
            f"/inspections/{{inspection_id}}/{action}",
            response_model=InspectionMutationResponse,
            responses=WRITE_RESPONSES,
            tags=["inspections"],
            summary=summary,
            name=f"inspection_{action}",
        )
        async def transition(
            request: Request,
            inspection_id: str,
            body: InspectionCompleteRequest | None = None,
            scope: WorkspaceScope = Depends(require_workspace_scope),
            idempotency_key: str = Depends(require_idempotency_key),
        ) -> InspectionMutationResponse:
            return await _inspections(request).transition(
                scope=scope,
                inspection_id=inspection_id,
                action=action,
                request=body,
                idempotency_key=idempotency_key,
            )

        return transition

    _transition_route("accept", "Assignee accepts an assigned inspection")
    _transition_route("start", "Assignee starts an accepted inspection")
    _transition_route(
        "complete", "Assignee submits the inspection, optionally linking their report"
    )
    _transition_route("cancel", "Dispatcher cancels an open inspection")

    return router
