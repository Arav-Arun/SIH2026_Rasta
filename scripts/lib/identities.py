"""Create Auth users, profiles and role grants for the demo accounts."""

from __future__ import annotations

import uuid

import httpx
import psycopg

ORG_ID = "a2600002-0000-4000-8000-000000000001"

# The real pilot geography imported by scripts/pipeline/import_pilot_network.py.
PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))


def create_auth_user(
    *,
    supabase_url: str,
    service_role_key: str,
    email: str,
    password: str,
) -> str:
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/auth/v1/admin/users",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30.0,
    )
    response.raise_for_status()
    return str(response.json()["id"])


def pilot_district_present(database_url: str) -> bool:
    """True once the pilot network import has created the district row."""

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select 1 from public.districts where id = %s::uuid",
                (PILOT_DISTRICT_ID,),
            )
            found = cursor.fetchone() is not None
            if found:
                cursor.execute(
                    """
                    update public.organization_districts
                    set active = true
                    where organization_id = %s::uuid
                      and district_id = %s::uuid
                    """,
                    (ORG_ID, PILOT_DISTRICT_ID),
                )
        connection.commit()
    return found


def create_profile(
    database_url: str,
    *,
    user_id: str,
    display_name: str,
    active: bool = True,
) -> str:
    profile_id = str(uuid.uuid4())
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.profiles (
                  id, user_id, organization_id, display_name, locale, active
                )
                values (%s::uuid, %s::uuid, %s::uuid, %s, 'en', %s)
                returning id::text
                """,
                (profile_id, user_id, ORG_ID, display_name, active),
            )
            profile_id = cursor.fetchone()[0]
        connection.commit()
    return profile_id


def create_role_grant(
    database_url: str,
    *,
    profile_id: str,
    role: str,
    district_id: str | None,
) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.role_assignments (
                  id, organization_id, profile_id, role, district_id
                )
                values (%s::uuid, %s::uuid, %s::uuid, %s, %s::uuid)
                """,
                (
                    str(uuid.uuid4()),
                    ORG_ID,
                    profile_id,
                    role,
                    district_id,
                ),
            )
        connection.commit()
