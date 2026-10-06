"""HTTP routes for people and their roles (``admin:settings`` only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.admin import (
    GrantRequest,
    GrantResponse,
    InviteRequest,
    InviteResponse,
    PeopleResponse,
    grant_role,
    invite_person,
    list_people,
    revoke_grant,
    supabase_inviter,
)
from app.db import connect
from app.errors import ApiError, ErrorEnvelope
from app.idempotency import require_idempotency_key
from app.ratelimit import rate_limit
from app.scope import WorkspaceScope, require_workspace_scope

RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Missing or invalid Idempotency-Key."},
    401: {"model": ErrorEnvelope, "description": "Missing or invalid session."},
    403: {
        "model": ErrorEnvelope,
        "description": "Not an admin, or a change to the caller's own roles.",
    },
    404: {"model": ErrorEnvelope, "description": "Missing or in another organisation."},
    409: {"model": ErrorEnvelope, "description": "The grant or person already exists."},
    422: {"model": ErrorEnvelope, "description": "A district or validity not allowed."},
    503: {
        "model": ErrorEnvelope,
        "description": "Database or invitations unavailable.",
    },
}


def _database(request: Request) -> str:
    settings = request.app.state.settings
    if not settings.database_url:
        raise ApiError(503, "database_unavailable", "The database is not configured.")
    return settings.database_url


def _inviter(request: Request):
    # Tests set their own; otherwise invitations go through Supabase auth.
    if hasattr(request.app.state, "inviter"):
        return request.app.state.inviter
    return supabase_inviter(request.app.state.settings)


def build_admin_router(prefix: str = "/v1") -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.get(
        "/admin/people",
        response_model=PeopleResponse,
        responses=RESPONSES,
        tags=["admin"],
        summary="List the organisation's people with every grant they hold or held",
    )
    async def people(
        request: Request, scope: WorkspaceScope = Depends(require_workspace_scope)
    ) -> PeopleResponse:
        scope.require("admin:settings")
        with connect(_database(request)) as connection:
            return list_people(
                connection,
                scope=scope,
                now=datetime.now(tz=UTC),
                invitations_available=_inviter(request) is not None,
            )

    @router.post(
        "/admin/people",
        response_model=InviteResponse,
        status_code=201,
        responses=RESPONSES,
        tags=["admin"],
        summary="Invite a person by email with their first role",
        dependencies=[Depends(rate_limit("admin.invite"))],
    )
    async def invite(
        request: Request,
        body: InviteRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> InviteResponse:
        scope.require("admin:settings")
        with connect(_database(request)) as connection:
            return invite_person(
                connection,
                scope=scope,
                request=body,
                idempotency_key=idempotency_key,
                inviter=_inviter(request),
                now=datetime.now(tz=UTC),
            )

    @router.post(
        "/admin/people/{profile_id}/grants",
        response_model=GrantResponse,
        status_code=201,
        responses=RESPONSES,
        tags=["admin"],
        summary="Grant a person a role, in a district where the role needs one",
    )
    async def grant(
        request: Request,
        profile_id: str,
        body: GrantRequest,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> GrantResponse:
        scope.require("admin:settings")
        with connect(_database(request)) as connection:
            return grant_role(
                connection,
                scope=scope,
                profile_id=profile_id,
                request=body,
                idempotency_key=idempotency_key,
                now=datetime.now(tz=UTC),
            )

    @router.post(
        "/admin/grants/{grant_id}/revoke",
        response_model=GrantResponse,
        responses=RESPONSES,
        tags=["admin"],
        summary="End a grant now",
    )
    async def revoke(
        request: Request,
        grant_id: str,
        idempotency_key: str = Depends(require_idempotency_key),
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> GrantResponse:
        scope.require("admin:settings")
        with connect(_database(request)) as connection:
            return revoke_grant(
                connection,
                scope=scope,
                grant_id=grant_id,
                idempotency_key=idempotency_key,
                now=datetime.now(tz=UTC),
            )

    return router


__all__ = ["build_admin_router"]
