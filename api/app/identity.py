"""Load server-verified identity documents from the operational database."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app import db
from app.capabilities import capabilities_for_roles
from app.errors import ApiError
from app.schemas import (
    DistrictIdentity,
    MeResponse,
    OrganizationIdentity,
    ProfileIdentity,
    RoleGrantIdentity,
)
from app.types import OperationalRole


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    """Database-backed identity for one authenticated subject."""

    profile: ProfileIdentity
    organization: OrganizationIdentity
    roles: list[RoleGrantIdentity]
    districts: list[DistrictIdentity] = field(default_factory=list)


class IdentityRepository(Protocol):
    """Lookup active profile and grants for an authenticated subject."""

    async def load_identity(self, user_id: str) -> IdentityRecord | None:
        """Return the active identity or ``None`` when no profile exists."""


class PostgresIdentityRepository:
    """Read identity using a direct database connection with service credentials."""

    _PROFILE_QUERY = """
        select
          p.id::text as profile_id,
          p.user_id::text as user_id,
          p.display_name,
          p.locale,
          p.active,
          o.id::text as organization_id,
          o.name as organization_name,
          o.mode::text as organization_mode
        from public.profiles as p
        join public.organizations as o
          on o.id = p.organization_id
        where p.user_id = %s::uuid
        limit 1
    """

    _ROLES_QUERY = """
        select
          ra.id::text as grant_id,
          ra.role::text as role,
          ra.district_id::text as district_id,
          ra.valid_from,
          ra.valid_to
        from public.role_assignments as ra
        join public.profiles as p
          on p.id = ra.profile_id
         and p.organization_id = ra.organization_id
        where p.user_id = %s::uuid
          and ra.revoked_at is null
          and ra.valid_from <= now()
          and (ra.valid_to is null or ra.valid_to > now())
          and (
            ra.district_id is null
            or exists (
              select 1
              from public.organization_districts as od
              where od.organization_id = ra.organization_id
                and od.district_id = ra.district_id
                and od.active = true
            )
          )
        order by ra.role, ra.district_id nulls first, ra.valid_from
    """

    _DISTRICTS_QUERY = """
        select d.id::text as id, d.state_code, d.code, d.name
        from public.organization_districts as od
        join public.districts as d on d.id = od.district_id
        join public.profiles as p on p.organization_id = od.organization_id
        where p.user_id = %s::uuid
          and od.active = true
        order by d.state_code, d.name
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def load_identity(self, user_id: str) -> IdentityRecord | None:
        with db.connect(self._database_url) as connection:
            profile_row = connection.execute(
                self._PROFILE_QUERY,
                (user_id,),
            ).fetchone()
            if profile_row is None:
                return None

            role_rows = connection.execute(self._ROLES_QUERY, (user_id,)).fetchall()
            district_rows = connection.execute(
                self._DISTRICTS_QUERY, (user_id,)
            ).fetchall()

        profile = ProfileIdentity(
            id=profile_row["profile_id"],
            user_id=profile_row["user_id"],
            display_name=profile_row["display_name"],
            locale=profile_row["locale"],
            active=profile_row["active"],
        )
        organization = OrganizationIdentity(
            id=profile_row["organization_id"],
            name=profile_row["organization_name"],
            mode=profile_row["organization_mode"],
        )
        roles = [
            RoleGrantIdentity(
                id=row["grant_id"],
                role=row["role"],
                district_id=row["district_id"],
                valid_from=row["valid_from"],
                valid_to=row["valid_to"],
            )
            for row in role_rows
        ]
        # An organization-wide grant sees every active district; a scoped grant
        # sees only the districts it names.
        granted = {row["district_id"] for row in role_rows if row["district_id"]}
        organization_wide = any(row["district_id"] is None for row in role_rows)
        districts = [
            DistrictIdentity(**row)
            for row in district_rows
            if organization_wide or row["id"] in granted
        ]
        return IdentityRecord(
            profile=profile,
            organization=organization,
            roles=roles,
            districts=districts,
        )


def build_me_response(record: IdentityRecord) -> MeResponse:
    """Convert a repository record into the published `/v1/me` response."""

    if not record.profile.active:
        raise ApiError(
            403,
            "scope_denied",
            "You do not have access to this action.",
        )

    active_roles: list[OperationalRole] = []
    for grant in record.roles:
        if grant.role not in active_roles:
            active_roles.append(grant.role)

    if not active_roles:
        raise ApiError(
            403,
            "scope_denied",
            "You do not have access to this action.",
        )

    return MeResponse(
        profile=record.profile,
        organization=record.organization,
        roles=record.roles,
        capabilities=capabilities_for_roles(active_roles),
        districts=record.districts,
        server_time=datetime.now(UTC),
    )


def build_identity_repository(database_url: str | None) -> IdentityRepository | None:
    """Return a repository when database configuration is present."""

    if not database_url:
        return None
    return PostgresIdentityRepository(database_url)
