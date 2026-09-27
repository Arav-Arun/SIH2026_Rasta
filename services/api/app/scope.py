"""Server-evaluated workspace scope for operational endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from app.auth import AuthenticatedSubject, require_authenticated_subject
from app.capabilities import capabilities_for_roles
from app.errors import ApiError
from app.identity import IdentityRecord
from app.types import OperationalRole


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """What the authenticated subject may read or change."""

    organization_id: str
    profile_id: str
    roles: tuple[OperationalRole, ...]
    capabilities: frozenset[str]
    # ``None`` means every active district of the organization (an
    # organization-wide grant exists); otherwise the explicit district IDs.
    district_ids: frozenset[str] | None = field(default=None)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def allows_district(self, district_id: str) -> bool:
        return self.district_ids is None or district_id in self.district_ids

    def require(self, capability: str) -> None:
        if not self.has(capability):
            raise ApiError(
                403, "scope_denied", "You do not have access to this action."
            )

    def require_district(self, district_id: str | None) -> None:
        if district_id is not None and not self.allows_district(district_id):
            raise ApiError(
                403, "scope_denied", "You do not have access to this action."
            )


def scope_from_identity(record: IdentityRecord) -> WorkspaceScope:
    if not record.profile.active:
        raise ApiError(403, "scope_denied", "You do not have access to this action.")

    roles: list[OperationalRole] = []
    district_ids: set[str] = set()
    organization_wide = False
    for grant in record.roles:
        if grant.role not in roles:
            roles.append(grant.role)
        if grant.district_id is None:
            organization_wide = True
        else:
            district_ids.add(grant.district_id)

    if not roles:
        raise ApiError(403, "scope_denied", "You do not have access to this action.")

    return WorkspaceScope(
        organization_id=record.organization.id,
        profile_id=record.profile.id,
        roles=tuple(roles),
        capabilities=frozenset(capabilities_for_roles(roles)),
        district_ids=None if organization_wide else frozenset(district_ids),
    )


async def require_workspace_scope(
    subject: Annotated[AuthenticatedSubject, Depends(require_authenticated_subject)],
    request: Request,
) -> WorkspaceScope:
    """Load the caller's server-side scope or refuse the request."""

    repository = request.app.state.identity_repository
    if repository is None:
        raise ApiError(
            503,
            "identity_bootstrap_unavailable",
            "Identity bootstrap is not configured.",
        )
    record = await repository.load_identity(subject.subject_id)
    if record is None:
        raise ApiError(403, "scope_denied", "You do not have access to this action.")
    return scope_from_identity(record)


def require_capability(capability: str):
    """Dependency factory: a scope that holds ``capability``."""

    async def dependency(
        scope: Annotated[WorkspaceScope, Depends(require_workspace_scope)],
    ) -> WorkspaceScope:
        scope.require(capability)
        return scope

    return dependency
