"""Granting and revoking roles against the local database.

Skipped without DATABASE_URL. Everything runs inside a transaction that is rolled
back, so no grant, person or audit row is left behind.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.admin import (
    GrantRequest,
    InviteRequest,
    grant_role,
    invite_person,
    list_people,
    revoke_grant,
)
from app.capabilities import capabilities_for_roles
from app.config import Settings
from app.errors import ApiError
from app.scope import WorkspaceScope
from psycopg.rows import dict_row

DATABASE_URL = Settings().database_url
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")


@pytest.fixture
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def org(db) -> dict:
    admin = db.execute(
        """
        select p.organization_id::text as org, p.id::text as admin
        from public.profiles as p
        join public.role_assignments as ra
          on ra.profile_id = p.id and ra.organization_id = p.organization_id
        where ra.role = 'admin' and ra.revoked_at is null and p.active
        order by p.created_at
        limit 1
        """
    ).fetchone()
    if admin is None:
        pytest.skip("needs the demo administrator")
    district = db.execute(
        "select district_id::text as id from public.organization_districts "
        "where organization_id = %s::uuid and active limit 1",
        (admin["org"],),
    ).fetchone()
    other = db.execute(
        """
        select p.id::text as id from public.profiles as p
        where p.organization_id = %s::uuid and p.active and p.id <> %s::uuid
          and not exists (
            select 1 from public.role_assignments as ra
            where ra.profile_id = p.id and ra.role = 'field_officer'
              and ra.revoked_at is null
          )
        order by p.created_at
        limit 1
        """,
        (admin["org"], admin["admin"]),
    ).fetchone()
    if district is None or other is None:
        pytest.skip("needs a district and another person")
    return {**admin, "district": district["id"], "person": other["id"]}


def scope_for(org: dict, *roles: str, profile: str | None = None) -> WorkspaceScope:
    return WorkspaceScope(
        organization_id=org["org"],
        profile_id=profile or org["admin"],
        roles=tuple(roles),
        capabilities=frozenset(capabilities_for_roles(list(roles))),
        district_ids=None,
    )


def key() -> str:
    return str(uuid.uuid4())


def officer(org: dict, **changes) -> GrantRequest:
    return GrantRequest(role="field_officer", district_id=org["district"], **changes)


def audit_actions(db, entity_id: str) -> list[str]:
    return [
        row["action"]
        for row in db.execute(
            "select action from public.audit_events where entity_id = %s::uuid "
            "order by occurred_at",
            (entity_id,),
        )
    ]


def test_an_admin_grants_a_role_once_and_it_is_audited(db, org) -> None:
    now = datetime.now(UTC)
    admin = scope_for(org, "admin")
    request_key = key()
    first = grant_role(
        db,
        scope=admin,
        profile_id=org["person"],
        request=officer(org),
        idempotency_key=request_key,
        now=now,
    )
    assert first.grant.state == "active"
    assert first.grant.role == "field_officer"
    assert audit_actions(db, first.grant.id) == ["role.granted"]

    # The same request again is the same answer, not a second grant.
    again = grant_role(
        db,
        scope=admin,
        profile_id=org["person"],
        request=officer(org),
        idempotency_key=request_key,
        now=now,
    )
    assert again.replayed and again.grant.id == first.grant.id

    # A new request for a grant that already holds is refused.
    with pytest.raises(ApiError) as clash:
        grant_role(
            db,
            scope=admin,
            profile_id=org["person"],
            request=officer(org),
            idempotency_key=key(),
            now=now,
        )
    assert (clash.value.status_code, clash.value.code) == (409, "grant_exists")

    listed = list_people(db, scope=admin, now=now, invitations_available=False)
    person = next(p for p in listed.people if p.profile_id == org["person"])
    assert first.grant.id in {g.id for g in person.grants}
    assert next(p for p in listed.people if p.is_you).profile_id == org["admin"]


def test_a_revoked_grant_stops_counting_at_once(db, org) -> None:
    now = datetime.now(UTC)
    admin = scope_for(org, "admin")
    granted = grant_role(
        db,
        scope=admin,
        profile_id=org["person"],
        request=officer(org),
        idempotency_key=key(),
        now=now,
    )
    revoked = revoke_grant(
        db, scope=admin, grant_id=granted.grant.id, idempotency_key=key(), now=now
    )
    assert revoked.grant.state == "revoked"
    # The identity check every request makes no longer finds it.
    live = db.execute(
        """
        select count(*)::int as n from public.role_assignments
        where id = %s::uuid and revoked_at is null and valid_from <= now()
          and (valid_to is null or valid_to > now())
        """,
        (granted.grant.id,),
    ).fetchone()["n"]
    assert live == 0
    # Revoking it again changes nothing, and is still recorded.
    twice = revoke_grant(
        db, scope=admin, grant_id=granted.grant.id, idempotency_key=key(), now=now
    )
    assert twice.grant.revoked_at == revoked.grant.revoked_at
    assert audit_actions(db, granted.grant.id) == [
        "role.granted",
        "role.revoked",
        "role.revoked",
    ]


def test_nobody_changes_their_own_roles(db, org) -> None:
    now = datetime.now(UTC)
    admin = scope_for(org, "admin")
    with pytest.raises(ApiError) as grant:
        grant_role(
            db,
            scope=admin,
            profile_id=org["admin"],
            request=GrantRequest(role="state_coordinator"),
            idempotency_key=key(),
            now=now,
        )
    assert (grant.value.status_code, grant.value.code) == (403, "self_change_refused")

    own = db.execute(
        "select id::text as id from public.role_assignments where profile_id = %s::uuid "
        "and role = 'admin' and revoked_at is null limit 1",
        (org["admin"],),
    ).fetchone()
    with pytest.raises(ApiError) as revoke:
        revoke_grant(
            db, scope=admin, grant_id=own["id"], idempotency_key=key(), now=now
        )
    assert (revoke.value.status_code, revoke.value.code) == (403, "self_change_refused")


def test_only_an_admin_may_grant(db, org) -> None:
    dispatcher = scope_for(org, "district_dispatcher", profile=org["person"])
    with pytest.raises(ApiError) as denied:
        grant_role(
            db,
            scope=dispatcher,
            profile_id=org["admin"],
            request=officer(org),
            idempotency_key=key(),
            now=datetime.now(UTC),
        )
    assert denied.value.status_code == 403
    with pytest.raises(ApiError):
        list_people(
            db, scope=dispatcher, now=datetime.now(UTC), invitations_available=False
        )


@pytest.mark.parametrize(
    ("request_for", "status", "code"),
    [
        (lambda org: GrantRequest(role="field_officer"), 422, "district_required"),
        (
            lambda org: GrantRequest(role="admin", district_id=org["district"]),
            422,
            "district_not_allowed",
        ),
        (
            lambda org: GrantRequest(role="driver", district_id=str(uuid.uuid4())),
            404,
            "not_found",
        ),
        (
            lambda org: officer(org, valid_to=datetime.now(UTC) - timedelta(minutes=5)),
            422,
            "invalid_validity",
        ),
        (
            lambda org: officer(org, valid_from=datetime.now(UTC) - timedelta(days=1)),
            422,
            "backdated_grant",
        ),
    ],
)
def test_a_grant_the_rules_do_not_allow_is_refused(
    db, org, request_for, status, code
) -> None:
    with pytest.raises(ApiError) as refused:
        grant_role(
            db,
            scope=scope_for(org, "admin"),
            profile_id=org["person"],
            request=request_for(org),
            idempotency_key=key(),
            now=datetime.now(UTC),
        )
    assert (refused.value.status_code, refused.value.code) == (status, code)


def test_a_person_in_another_organisation_is_not_found(db, org) -> None:
    with pytest.raises(ApiError) as missing:
        grant_role(
            db,
            scope=scope_for(org, "admin"),
            profile_id=str(uuid.uuid4()),
            request=officer(org),
            idempotency_key=key(),
            now=datetime.now(UTC),
        )
    assert missing.value.status_code == 404


def test_an_invitation_creates_the_person_with_their_first_role(db, org) -> None:
    sent: list[str] = []

    def inviter(email: str, display_name: str) -> str:
        sent.append(email)
        # What the sign-in service does: a user row for the address.
        return db.execute(
            "insert into auth.users (id, email) values (gen_random_uuid(), %s) "
            "returning id::text as id",
            (email,),
        ).fetchone()["id"]

    email = f"invited-{uuid.uuid4().hex[:8]}@example.test"
    invited = invite_person(
        db,
        scope=scope_for(org, "admin"),
        request=InviteRequest(
            email=email, display_name="Invited officer", grant=officer(org)
        ),
        idempotency_key=key(),
        inviter=inviter,
        now=datetime.now(UTC),
    )
    assert sent == [email]
    assert invited.person.email == email
    assert [g.role for g in invited.person.grants] == ["field_officer"]
    assert audit_actions(db, invited.person.profile_id) == ["person.invited"]


def test_a_bad_grant_is_refused_before_anyone_is_emailed(db, org) -> None:
    sent: list[str] = []
    with pytest.raises(ApiError) as refused:
        invite_person(
            db,
            scope=scope_for(org, "admin"),
            request=InviteRequest(
                email="x@example.test",
                display_name="X",
                grant=GrantRequest(role="driver"),
            ),
            idempotency_key=key(),
            inviter=lambda email, name: sent.append(email) or str(uuid.uuid4()),
            now=datetime.now(UTC),
        )
    assert refused.value.code == "district_required"
    assert sent == []


def test_without_the_sign_in_service_there_are_no_invitations(db, org) -> None:
    with pytest.raises(ApiError) as unavailable:
        invite_person(
            db,
            scope=scope_for(org, "admin"),
            request=InviteRequest(
                email="y@example.test", display_name="Y", grant=officer(org)
            ),
            idempotency_key=key(),
            inviter=None,
            now=datetime.now(UTC),
        )
    assert (unavailable.value.status_code, unavailable.value.code) == (
        503,
        "invitations_unavailable",
    )


def test_an_email_that_already_has_a_person_is_not_invited_again(db, org) -> None:
    taken = db.execute(
        """
        select u.email from auth.users as u
        join public.profiles as p on p.user_id = u.id
        where u.email is not null
        limit 1
        """
    ).fetchone()
    if taken is None:
        pytest.skip("needs a person with a sign-in")
    sent: list[str] = []
    with pytest.raises(ApiError) as exists:
        invite_person(
            db,
            scope=scope_for(org, "admin"),
            request=InviteRequest(
                email=taken["email"].upper(), display_name="Again", grant=officer(org)
            ),
            idempotency_key=key(),
            inviter=lambda email, name: sent.append(email) or str(uuid.uuid4()),
            now=datetime.now(UTC),
        )
    assert (exists.value.status_code, exists.value.code) == (409, "person_exists")
    assert sent == []
