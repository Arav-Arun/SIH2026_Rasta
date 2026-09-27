#!/usr/bin/env python3
"""Bootstrap one local demo Auth user/profile/grant for API smoke tests."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from pathlib import Path

import httpx
import psycopg

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ORG_ID = "a2600002-0000-4000-8000-000000000001"
SYNTHETIC_DISTRICT_ID = "a2600002-0000-4000-8000-000000000002"
DISTRICT_ID = SYNTHETIC_DISTRICT_ID
ROLES = (
    "state_coordinator",
    "district_dispatcher",
    "field_officer",
    "driver",
    "admin",
    "reviewer",
)


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


def default_district_id(database_url: str) -> str:
    """Prefer the imported pilot district so the demo user sees real data."""

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select d.id::text
            from public.districts as d
            join public.organization_districts as od
              on od.district_id = d.id and od.organization_id = %s::uuid and od.active
            where d.state_code = 'ML' and d.code = 'EAST-KHASI-HILLS'
            limit 1
            """,
            (ORG_ID,),
        ).fetchone()
    return row[0] if row else SYNTHETIC_DISTRICT_ID


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=ROLES, default="district_dispatcher")
    parser.add_argument(
        "--district-id", help="Defaults to the pilot district when imported"
    )
    parser.add_argument(
        "--email", help="Fixed email instead of a random one (local only)"
    )
    parser.add_argument(
        "--password", help="Fixed password instead of a random one (local only)"
    )
    args = parser.parse_args()

    env = supabase_env()
    database_url = env["DB_URL"]
    supabase_url = env["API_URL"]
    publishable_key = env.get("PUBLISHABLE_KEY") or env["ANON_KEY"]
    service_role_key = env["SERVICE_ROLE_KEY"]
    district_id = args.district_id or default_district_id(database_url)
    role = args.role

    suffix = uuid.uuid4().hex[:8]
    email = args.email or f"{role.replace('_', '-')}-{suffix}@example.test"
    password = args.password or f"Local-{uuid.uuid4().hex}"

    user_response = httpx.post(
        f"{supabase_url}/auth/v1/admin/users",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30.0,
    )
    user_response.raise_for_status()
    user_id = user_response.json()["id"]
    profile_id = str(uuid.uuid4())

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                update public.organization_districts
                set active = true
                where organization_id = %s::uuid
                  and district_id = %s::uuid
                """,
                (ORG_ID, district_id),
            )
            cursor.execute(
                """
                insert into public.profiles (
                  id, user_id, organization_id, display_name, locale, active
                )
                values (%s::uuid, %s::uuid, %s::uuid, %s, 'en', true)
                on conflict (user_id) do update
                set display_name = excluded.display_name
                returning id::text
                """,
                (profile_id, user_id, ORG_ID, f"Local demo {role.replace('_', ' ')}"),
            )
            profile_id = cursor.fetchone()[0]
            cursor.execute(
                """
                insert into public.role_assignments (
                  id, organization_id, profile_id, role, district_id
                )
                values (%s::uuid, %s::uuid, %s::uuid, %s::public.app_role, %s::uuid)
                on conflict do nothing
                """,
                (str(uuid.uuid4()), ORG_ID, profile_id, role, district_id),
            )
        connection.commit()

    token_response = httpx.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers={
            "apikey": publishable_key,
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password},
        timeout=30.0,
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    print(
        json.dumps(
            {
                "email": email,
                "password": password,
                "user_id": user_id,
                "profile_id": profile_id,
                "role": role,
                "district_id": district_id,
                "access_token": access_token,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
