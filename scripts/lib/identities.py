#!/usr/bin/env python3
"""Bootstrap local Auth identities for the browser end-to-end tests."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ORG_ID = "a2600002-0000-4000-8000-000000000001"
DISTRICT_ID = "a2600002-0000-4000-8000-000000000002"

# The real pilot geography imported by scripts/pipeline/import_pilot_network.py.
PILOT_ID_NAMESPACE = uuid.UUID("5d4e6c1a-2b7f-4d3a-9c8e-26002000c0de")
PILOT_DISTRICT_ID = str(uuid.uuid5(PILOT_ID_NAMESPACE, "district:ML:EAST-KHASI-HILLS"))
DEFAULT_OUTPUT = REPOSITORY_ROOT / "artifacts" / "e2e" / "e2e_identities.json"


def supabase_env() -> dict[str, str]:
    output = subprocess.check_output(
        ["supabase", "status", "-o", "env"],
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


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


def ensure_active_district(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                update public.organization_districts
                set active = true
                where organization_id = %s::uuid
                  and district_id = %s::uuid
                """,
                (ORG_ID, DISTRICT_ID),
            )
        connection.commit()


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
    district_id: str | None = DISTRICT_ID,
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


def bootstrap_identity(
    *,
    database_url: str,
    supabase_url: str,
    service_role_key: str,
    suffix: str,
    key: str,
    display_name: str,
    role: str | None,
    active: bool = True,
    district_id: str | None = DISTRICT_ID,
) -> dict[str, Any]:
    email = f"{key}-{suffix}@example.test"
    password = f"{key.title()}-{suffix}!"
    user_id = create_auth_user(
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        email=email,
        password=password,
    )
    profile_id = create_profile(
        database_url,
        user_id=user_id,
        display_name=display_name,
        active=active,
    )
    if role:
        create_role_grant(
            database_url, profile_id=profile_id, role=role, district_id=district_id
        )

    return {
        "key": key,
        "email": email,
        "password": password,
        "user_id": user_id,
        "profile_id": profile_id,
        "role": role,
        "active": active,
        "district_id": district_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Write the identity fixture JSON to this path.",
    )
    args = parser.parse_args()

    env = supabase_env()
    database_url = env["DB_URL"]
    supabase_url = env["API_URL"]
    service_role_key = env["SERVICE_ROLE_KEY"]

    suffix = uuid.uuid4().hex[:8]
    ensure_active_district(database_url)

    identities = [
        bootstrap_identity(
            database_url=database_url,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            suffix=suffix,
            key="dispatcher",
            display_name="E2E district dispatcher",
            role="district_dispatcher",
        ),
        bootstrap_identity(
            database_url=database_url,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            suffix=suffix,
            key="driver",
            display_name="E2E driver",
            role="driver",
        ),
        bootstrap_identity(
            database_url=database_url,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            suffix=suffix,
            key="no-scope",
            display_name="E2E no-scope account",
            role=None,
        ),
    ]

    if pilot_district_present(database_url):
        identities.append(
            bootstrap_identity(
                database_url=database_url,
                supabase_url=supabase_url,
                service_role_key=service_role_key,
                suffix=suffix,
                key="pilot-dispatcher",
                display_name="E2E pilot district dispatcher",
                role="district_dispatcher",
                district_id=PILOT_DISTRICT_ID,
            )
        )
        # A report has to come from somebody who is allowed to file one and not
        # to decide on it. The review flow cannot be exercised without them.
        identities.append(
            bootstrap_identity(
                database_url=database_url,
                supabase_url=supabase_url,
                service_role_key=service_role_key,
                suffix=suffix,
                key="pilot-officer",
                display_name="E2E pilot district field officer",
                role="field_officer",
                district_id=PILOT_DISTRICT_ID,
            )
        )

    payload = {
        "organization_id": ORG_ID,
        "district_id": DISTRICT_ID,
        "pilot_district_id": PILOT_DISTRICT_ID,
        "identities": identities,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
