"""People and their roles, administered by an organisation's admins.

Rules, each enforced here and each tested:

- Only a holder of ``admin:settings`` may read or change anything.
- Everything stays inside the caller's organisation: a person, a grant or a district
  from another one is "not found".
- Nobody changes their own grants, so no admin can raise or extend their own access
  (and nobody locks themselves out by accident). Another admin has to do it.
- A grant has the district its role needs: organisation-wide for admins and state
  coordinators, one active district for everyone else (the database enforces the
  same). It starts now or later and, if it ends, ends in the future.
- An active grant is not granted twice.
- Every change is written to ``audit_events``, and every write is idempotent.

An invitation goes through Supabase auth with the server key: Supabase emails the
person a link to set a password. Their profile and first grant are created at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app.capabilities import ROLE_CAPABILITIES
from app.config import Settings
from app.errors import ApiError
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.scope import WorkspaceScope
from app.types import OperationalRole

#: Roles that cover a whole organisation and carry no district.
ORGANISATION_ROLES = frozenset({"admin", "state_coordinator"})

GrantState = Literal["active", "scheduled", "expired", "revoked"]

#: A start a minute in the past is "now" from a browser whose clock is behind.
CLOCK_SKEW = timedelta(minutes=1)


class Grant(BaseModel):
    id: str
    role: OperationalRole
    district_id: str | None
    district_name: str | None
    valid_from: datetime
    valid_to: datetime | None
    revoked_at: datetime | None
    granted_by_profile_id: str | None
    granted_by_name: str | None
    state: GrantState


class Person(BaseModel):
    profile_id: str
    display_name: str
    email: str | None
    active: bool
    #: The caller: their own grants are shown but cannot be changed here.
    is_you: bool
    grants: list[Grant]


class DistrictOption(BaseModel):
    id: str
    name: str


class RoleOption(BaseModel):
    role: OperationalRole
    organisation_wide: bool
    capabilities: list[str]


class PeopleResponse(BaseModel):
    people: list[Person]
    roles: list[RoleOption]
    districts: list[DistrictOption]
    invitations_available: bool
    as_of: datetime


class GrantRequest(BaseModel):
    role: OperationalRole
    district_id: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class InviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=120)
    grant: GrantRequest

    @field_validator("email")
    @classmethod
    def plausible_email(cls, value: str) -> str:
        value = value.strip().lower()
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            raise ValueError("not an email address")
        return value

    @field_validator("display_name")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a name is needed")
        return value


class GrantResponse(BaseModel):
    profile_id: str
    grant: Grant
    audit_event_id: str
    replayed: bool = False


class InviteResponse(BaseModel):
    person: Person
    audit_event_id: str
    replayed: bool = False


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "The requested resource was not found.")


def _state(row: dict[str, Any], now: datetime) -> GrantState:
    if row["revoked_at"] is not None:
        return "revoked"
    if row["valid_from"] > now:
        return "scheduled"
    if row["valid_to"] is not None and row["valid_to"] <= now:
        return "expired"
    return "active"


_GRANTS = """
    select ra.id::text as id, ra.profile_id::text as profile_id, ra.role::text as role,
           ra.district_id::text as district_id, d.name as district_name,
           ra.valid_from, ra.valid_to, ra.revoked_at,
           ra.granted_by_profile_id::text as granted_by_profile_id,
           granter.display_name as granted_by_name
    from public.role_assignments as ra
    left join public.districts as d on d.id = ra.district_id
    left join public.profiles as granter
      on granter.id = ra.granted_by_profile_id
     and granter.organization_id = ra.organization_id
    where ra.organization_id = %(org)s::uuid
"""


def _grant(row: dict[str, Any], now: datetime) -> Grant:
    return Grant(
        id=row["id"],
        role=row["role"],
        district_id=row["district_id"],
        district_name=row["district_name"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        revoked_at=row["revoked_at"],
        granted_by_profile_id=row["granted_by_profile_id"],
        granted_by_name=row["granted_by_name"],
        state=_state(row, now),
    )


def _people(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    now: datetime,
    profile_id: str | None = None,
) -> list[Person]:
    rows = connection.execute(
        """
        select p.id::text as profile_id, p.display_name, p.active, u.email
        from public.profiles as p
        left join auth.users as u on u.id = p.user_id
        where p.organization_id = %(org)s::uuid
          and (%(profile)s::uuid is null or p.id = %(profile)s::uuid)
        order by p.display_name, p.id
        """,
        {"org": scope.organization_id, "profile": profile_id},
    ).fetchall()
    grants: dict[str, list[Grant]] = {}
    for row in connection.execute(
        _GRANTS
        + """
          and (%(profile)s::uuid is null or ra.profile_id = %(profile)s::uuid)
        order by ra.revoked_at nulls first, ra.valid_from desc
        """,
        {"org": scope.organization_id, "profile": profile_id},
    ).fetchall():
        grants.setdefault(row["profile_id"], []).append(_grant(row, now))
    return [
        Person(
            profile_id=row["profile_id"],
            display_name=row["display_name"],
            email=row["email"],
            active=row["active"],
            is_you=row["profile_id"] == scope.profile_id,
            grants=grants.get(row["profile_id"], []),
        )
        for row in rows
    ]


def list_people(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    now: datetime,
    invitations_available: bool,
) -> PeopleResponse:
    scope.require("admin:settings")
    districts = connection.execute(
        """
        select d.id::text as id, d.name
        from public.organization_districts as od
        join public.districts as d on d.id = od.district_id
        where od.organization_id = %(org)s::uuid and od.active
        order by d.name
        """,
        {"org": scope.organization_id},
    ).fetchall()
    return PeopleResponse(
        people=_people(connection, scope=scope, now=now),
        roles=[
            RoleOption(
                role=role,
                organisation_wide=role in ORGANISATION_ROLES,
                capabilities=list(capabilities),
            )
            for role, capabilities in ROLE_CAPABILITIES.items()
        ],
        districts=[DistrictOption(**row) for row in districts],
        invitations_available=invitations_available,
        as_of=now,
    )


def _check_grant(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    request: GrantRequest,
    now: datetime,
) -> tuple[datetime, datetime | None]:
    """Refuse a grant this caller may not make; return its validity window."""

    if request.role in ORGANISATION_ROLES:
        if request.district_id is not None:
            raise ApiError(
                422,
                "district_not_allowed",
                f"The {request.role} role covers the whole organisation; "
                "give it no district.",
            )
    else:
        if request.district_id is None:
            raise ApiError(
                422,
                "district_required",
                f"The {request.role} role needs a district.",
            )
        try:
            uuid.UUID(request.district_id)
        except ValueError as error:
            raise _not_found() from error
        known = connection.execute(
            """
            select 1 from public.organization_districts
            where organization_id = %(org)s::uuid and district_id = %(district)s::uuid
              and active
            """,
            {"org": scope.organization_id, "district": request.district_id},
        ).fetchone()
        if known is None:
            raise _not_found()
        scope.require_district(request.district_id)

    starts = request.valid_from or now
    if starts < now - CLOCK_SKEW:
        raise ApiError(422, "backdated_grant", "A grant cannot start in the past.")
    if request.valid_to is not None and request.valid_to <= max(starts, now):
        raise ApiError(
            422, "invalid_validity", "A grant must end after it starts, in the future."
        )
    return starts, request.valid_to


def _audit(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    action: str,
    entity_type: str,
    entity_id: str,
    after: dict[str, Any],
    before: dict[str, Any] | None = None,
) -> str:
    return connection.execute(
        """
        insert into public.audit_events (
          organization_id, actor_id, action, entity_type, entity_id,
          before_hash, after_hash, metadata
        )
        values (
          %(org)s::uuid, %(actor)s::uuid, %(action)s, %(entity_type)s,
          %(entity)s::uuid, %(before)s, %(after)s, %(meta)s
        )
        returning id::text as id
        """,
        {
            "org": scope.organization_id,
            "actor": scope.profile_id,
            "action": action,
            "entity_type": entity_type,
            "entity": entity_id,
            "before": canonical_hash(before) if before is not None else None,
            "after": canonical_hash(after),
            "meta": Jsonb(after),
        },
    ).fetchone()["id"]


def _insert_grant(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    profile_id: str,
    request: GrantRequest,
    starts: datetime,
    ends: datetime | None,
    now: datetime,
) -> Grant:
    clash = connection.execute(
        """
        select 1 from public.role_assignments
        where organization_id = %(org)s::uuid and profile_id = %(profile)s::uuid
          and role = %(role)s::public.app_role
          and district_id is not distinct from %(district)s::uuid
          and revoked_at is null
          and (valid_to is null or valid_to > %(starts)s)
          and (%(ends)s::timestamptz is null or valid_from < %(ends)s)
        """,
        {
            "org": scope.organization_id,
            "profile": profile_id,
            "role": request.role,
            "district": request.district_id,
            "starts": starts,
            "ends": ends,
        },
    ).fetchone()
    if clash is not None:
        raise ApiError(
            409,
            "grant_exists",
            "This person already holds that role there for some of that time.",
        )
    grant_id = connection.execute(
        """
        insert into public.role_assignments (
          organization_id, profile_id, role, district_id, valid_from, valid_to,
          granted_by_profile_id
        )
        values (
          %(org)s::uuid, %(profile)s::uuid, %(role)s::public.app_role,
          %(district)s::uuid, %(starts)s, %(ends)s, %(actor)s::uuid
        )
        returning id::text as id
        """,
        {
            "org": scope.organization_id,
            "profile": profile_id,
            "role": request.role,
            "district": request.district_id,
            "starts": starts,
            "ends": ends,
            "actor": scope.profile_id,
        },
    ).fetchone()["id"]
    row = connection.execute(
        _GRANTS + " and ra.id = %(grant)s::uuid",
        {"org": scope.organization_id, "grant": grant_id},
    ).fetchone()
    return _grant(row, now)


def _profile(
    connection: psycopg.Connection, *, scope: WorkspaceScope, profile_id: str
) -> dict[str, Any]:
    try:
        uuid.UUID(profile_id)
    except ValueError as error:
        raise _not_found() from error
    row = connection.execute(
        """
        select id::text as id, active from public.profiles
        where organization_id = %(org)s::uuid and id = %(profile)s::uuid
        """,
        {"org": scope.organization_id, "profile": profile_id},
    ).fetchone()
    if row is None:
        raise _not_found()
    return row


def _refuse_self(scope: WorkspaceScope, profile_id: str) -> None:
    if profile_id == scope.profile_id:
        raise ApiError(
            403,
            "self_change_refused",
            "You cannot change your own roles. Ask another admin.",
        )


def grant_role(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    profile_id: str,
    request: GrantRequest,
    idempotency_key: str,
    now: datetime,
) -> GrantResponse:
    scope.require("admin:settings")
    request_hash = canonical_hash(
        {"op": "grant", "profile": profile_id, **request.model_dump(mode="json")}
    )
    with connection.transaction():
        hit = lookup_ledger(
            connection,
            organization_id=scope.organization_id,
            actor_id=scope.profile_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if hit is not None:
            return GrantResponse(**hit.result_payload, replayed=True)

        person = _profile(connection, scope=scope, profile_id=profile_id)
        _refuse_self(scope, profile_id)
        if not person["active"]:
            raise ApiError(
                409, "profile_inactive", "This person's profile is deactivated."
            )
        starts, ends = _check_grant(connection, scope=scope, request=request, now=now)
        grant = _insert_grant(
            connection,
            scope=scope,
            profile_id=profile_id,
            request=request,
            starts=starts,
            ends=ends,
            now=now,
        )
        audit_id = _audit(
            connection,
            scope=scope,
            action="role.granted",
            entity_type="role_assignment",
            entity_id=grant.id,
            after={
                "profile_id": profile_id,
                "role": grant.role,
                "district_id": grant.district_id,
                "valid_from": grant.valid_from.isoformat(),
                "valid_to": grant.valid_to.isoformat() if grant.valid_to else None,
            },
        )
        response = GrantResponse(
            profile_id=profile_id, grant=grant, audit_event_id=audit_id
        )
        record_ledger(
            connection,
            organization_id=scope.organization_id,
            actor_id=scope.profile_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_code="accepted",
            result_payload=response.model_dump(mode="json", exclude={"replayed"}),
        )
    return response


def revoke_grant(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    grant_id: str,
    idempotency_key: str,
    now: datetime,
) -> GrantResponse:
    """End a grant now. Revoking one already revoked changes nothing."""

    scope.require("admin:settings")
    try:
        uuid.UUID(grant_id)
    except ValueError as error:
        raise _not_found() from error
    request_hash = canonical_hash({"op": "revoke", "grant": grant_id})
    with connection.transaction():
        hit = lookup_ledger(
            connection,
            organization_id=scope.organization_id,
            actor_id=scope.profile_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if hit is not None:
            return GrantResponse(**hit.result_payload, replayed=True)

        row = connection.execute(
            _GRANTS + " and ra.id = %(grant)s::uuid for update of ra",
            {"org": scope.organization_id, "grant": grant_id},
        ).fetchone()
        if row is None:
            raise _not_found()
        _refuse_self(scope, row["profile_id"])
        before = _grant(row, now)
        if row["revoked_at"] is None:
            # A grant not yet started ends before it begins: revoked at its start.
            connection.execute(
                """
                update public.role_assignments
                set revoked_at = greatest(%(now)s, valid_from)
                where id = %(grant)s::uuid and organization_id = %(org)s::uuid
                """,
                {"now": now, "grant": grant_id, "org": scope.organization_id},
            )
            row = connection.execute(
                _GRANTS + " and ra.id = %(grant)s::uuid",
                {"org": scope.organization_id, "grant": grant_id},
            ).fetchone()
        grant = _grant(row, now)
        audit_id = _audit(
            connection,
            scope=scope,
            action="role.revoked",
            entity_type="role_assignment",
            entity_id=grant_id,
            before={"state": before.state},
            after={
                "profile_id": row["profile_id"],
                "role": grant.role,
                "district_id": grant.district_id,
                "revoked_at": grant.revoked_at.isoformat()
                if grant.revoked_at
                else None,
                "already_revoked": before.state == "revoked",
            },
        )
        response = GrantResponse(
            profile_id=row["profile_id"], grant=grant, audit_event_id=audit_id
        )
        record_ledger(
            connection,
            organization_id=scope.organization_id,
            actor_id=scope.profile_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_code="accepted",
            result_payload=response.model_dump(mode="json", exclude={"replayed"}),
        )
    return response


#: Creates the sign-in and emails the person a link; returns the auth user id.
Inviter = Callable[[str, str], str]


def supabase_inviter(settings: Settings) -> Inviter | None:
    """Invitations through Supabase auth, or None when the server key is not set."""

    if not settings.supabase_url or not settings.supabase_secret_key:
        return None

    def invite(email: str, display_name: str) -> str:
        response = httpx.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/invite",
            headers={
                "apikey": settings.supabase_secret_key,
                "Authorization": f"Bearer {settings.supabase_secret_key}",
            },
            json={"email": email, "data": {"display_name": display_name}},
            timeout=20.0,
        )
        if response.status_code in (400, 409, 422):
            raise ApiError(
                409,
                "email_registered",
                "That email already has a sign-in. Grant a role to their profile "
                "instead.",
            )
        if response.status_code >= 300:
            raise ApiError(
                502,
                "invitation_failed",
                f"The sign-in service refused the invitation ({response.status_code}).",
            )
        return str(response.json()["id"])

    return invite


def invite_person(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    request: InviteRequest,
    idempotency_key: str,
    inviter: Inviter | None,
    now: datetime,
) -> InviteResponse:
    scope.require("admin:settings")
    if inviter is None:
        raise ApiError(
            503,
            "invitations_unavailable",
            "Invitations need the sign-in service's server key on the API.",
        )
    request_hash = canonical_hash({"op": "invite", **request.model_dump(mode="json")})
    with connection.transaction():
        hit = lookup_ledger(
            connection,
            organization_id=scope.organization_id,
            actor_id=scope.profile_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if hit is not None:
            return InviteResponse(**hit.result_payload, replayed=True)

        # Checked before anyone is emailed.
        starts, ends = _check_grant(
            connection, scope=scope, request=request.grant, now=now
        )
        known = connection.execute(
            """
            select 1
            from auth.users as u
            join public.profiles as p on p.user_id = u.id
            where lower(u.email) = %(email)s
            """,
            {"email": request.email},
        ).fetchone()
        if known is not None:
            # Whichever organisation the profile is in, the answer is the same.
            raise ApiError(
                409,
                "person_exists",
                "That email already belongs to a person here or elsewhere. Grant "
                "a role to their profile instead.",
            )
        user_id = inviter(request.email, request.display_name)
        profile_id = connection.execute(
            """
            insert into public.profiles (user_id, organization_id, display_name)
            values (%(user)s::uuid, %(org)s::uuid, %(name)s)
            returning id::text as id
            """,
            {
                "user": user_id,
                "org": scope.organization_id,
                "name": request.display_name,
            },
        ).fetchone()["id"]
        _insert_grant(
            connection,
            scope=scope,
            profile_id=profile_id,
            request=request.grant,
            starts=starts,
            ends=ends,
            now=now,
        )
        audit_id = _audit(
            connection,
            scope=scope,
            action="person.invited",
            entity_type="profile",
            entity_id=profile_id,
            after={
                "display_name": request.display_name,
                "role": request.grant.role,
                "district_id": request.grant.district_id,
            },
        )
        (person,) = _people(connection, scope=scope, now=now, profile_id=profile_id)
        response = InviteResponse(person=person, audit_event_id=audit_id)
        record_ledger(
            connection,
            organization_id=scope.organization_id,
            actor_id=scope.profile_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_code="accepted",
            result_payload=response.model_dump(mode="json", exclude={"replayed"}),
        )
    return response


__all__ = [
    "Grant",
    "GrantRequest",
    "GrantResponse",
    "InviteRequest",
    "InviteResponse",
    "PeopleResponse",
    "Person",
    "grant_role",
    "invite_person",
    "list_people",
    "revoke_grant",
    "supabase_inviter",
]
