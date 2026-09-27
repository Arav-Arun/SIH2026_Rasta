#!/usr/bin/env python3
"""Database and row-level security check against a local Supabase/PostGIS stack."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "database_check.json"
SCHEMA_CONTRACT = REPOSITORY_ROOT / "supabase" / "tests" / "schema_contract.sql"

ORG_A = "a2600002-0000-4000-8000-000000000001"
DISTRICT_A = "a2600002-0000-4000-8000-000000000002"
ORG_B = "b2600002-0000-4000-8000-0000000000b1"
DISTRICT_B = "b2600002-0000-4000-8000-0000000000b2"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def run_command(command: list[str], *, cwd: Path = REPOSITORY_ROOT) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required tool not found: {name}")


def supabase_status_env() -> dict[str, str]:
    output = run_command(["supabase", "status", "-o", "env"])
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
        json={
            "email": email,
            "password": password,
            "email_confirm": True,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload["id"])


def sign_in(
    *,
    supabase_url: str,
    publishable_key: str,
    email: str,
    password: str,
) -> str:
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password",
        headers={
            "apikey": publishable_key,
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password},
        timeout=30.0,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def bootstrap_tenant_b(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.organizations (id, name, mode)
                values (%s::uuid, %s, 'local_demo')
                on conflict (id) do nothing
                """,
                (ORG_B, "RASTA synthetic tenant B"),
            )
            cursor.execute(
                """
                insert into public.districts (id, state_code, code, name)
                values (%s::uuid, 'DEMO', 'SYNTH-B1', 'Synthetic tenant B district')
                on conflict (id) do nothing
                """,
                (DISTRICT_B,),
            )
            cursor.execute(
                """
                insert into public.organization_districts (organization_id, district_id, active)
                values (%s::uuid, %s::uuid, true)
                on conflict (organization_id, district_id) do nothing
                """,
                (ORG_B, DISTRICT_B),
            )
        connection.commit()


def create_profile_and_grant(
    database_url: str,
    *,
    user_id: str,
    organization_id: str,
    district_id: str,
    display_name: str,
) -> tuple[str, str]:
    profile_id = str(uuid.uuid4())
    grant_id = str(uuid.uuid4())
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.profiles (
                  id, user_id, organization_id, display_name, locale, active
                )
                values (%s::uuid, %s::uuid, %s::uuid, %s, 'en', true)
                """,
                (profile_id, user_id, organization_id, display_name),
            )
            cursor.execute(
                """
                insert into public.role_assignments (
                  id, organization_id, profile_id, role, district_id
                )
                values (%s::uuid, %s::uuid, %s::uuid, 'district_dispatcher', %s::uuid)
                """,
                (grant_id, organization_id, profile_id, district_id),
            )
        connection.commit()
    return profile_id, grant_id


def rest_get(
    *,
    supabase_url: str,
    publishable_key: str,
    access_token: str,
    table: str,
    query: str,
) -> httpx.Response:
    return httpx.get(
        f"{supabase_url.rstrip('/')}/rest/v1/{table}?{query}",
        headers={
            "apikey": publishable_key,
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30.0,
    )


def storage_insert(
    *,
    supabase_url: str,
    publishable_key: str,
    access_token: str,
    bucket: str,
    object_path: str,
) -> httpx.Response:
    return httpx.post(
        f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}",
        headers={
            "apikey": publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "text/plain",
        },
        content=b"synthetic acceptance payload",
        timeout=30.0,
    )


def main() -> int:
    checks: list[CheckResult] = []
    try:
        require_tool("supabase")
        require_tool("docker")
        require_tool("psql")

        if not (REPOSITORY_ROOT / "supabase" / "config.toml").exists():
            run_command(["supabase", "init"])
        run_command(["supabase", "start"])
        run_command(["supabase", "db", "reset", "--yes"])

        env = supabase_status_env()
        database_url = env.get("DB_URL", "")
        supabase_url = env.get("API_URL", "")
        publishable_key = env.get("ANON_KEY", "")
        service_role_key = env.get("SERVICE_ROLE_KEY", "")
        if not all([database_url, supabase_url, publishable_key, service_role_key]):
            raise RuntimeError(
                "Supabase status did not return required environment values"
            )

        run_command(
            [
                "psql",
                database_url,
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                str(SCHEMA_CONTRACT),
            ]
        )
        checks.append(
            CheckResult("schema_contract_sql", True, "schema_contract.sql passed")
        )

        bootstrap_tenant_b(database_url)

        suffix = uuid.uuid4().hex[:8]
        email_a = f"tenant-a-{suffix}@example.test"
        email_b = f"tenant-b-{suffix}@example.test"
        password = f"Acceptance-{suffix}!"

        user_a = create_auth_user(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            email=email_a,
            password=password,
        )
        user_b = create_auth_user(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            email=email_b,
            password=password,
        )
        profile_a, _ = create_profile_and_grant(
            database_url,
            user_id=user_a,
            organization_id=ORG_A,
            district_id=DISTRICT_A,
            display_name="Tenant A dispatcher",
        )
        profile_b, _ = create_profile_and_grant(
            database_url,
            user_id=user_b,
            organization_id=ORG_B,
            district_id=DISTRICT_B,
            display_name="Tenant B dispatcher",
        )

        token_a = sign_in(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            email=email_a,
            password=password,
        )
        # Signed in to prove the second account exists and is usable; the
        # token itself is not needed by the checks that follow.
        sign_in(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            email=email_b,
            password=password,
        )

        own_profile = rest_get(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            access_token=token_a,
            table="profiles",
            query=f"id=eq.{profile_a}&select=id,display_name",
        )
        checks.append(
            CheckResult(
                "tenant_a_reads_own_profile",
                own_profile.status_code == 200 and len(own_profile.json()) == 1,
                f"status={own_profile.status_code}",
            )
        )

        cross_profile = rest_get(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            access_token=token_a,
            table="profiles",
            query=f"id=eq.{profile_b}&select=id",
        )
        checks.append(
            CheckResult(
                "tenant_a_cannot_read_tenant_b_profile",
                cross_profile.status_code == 200 and cross_profile.json() == [],
                f"status={cross_profile.status_code}, rows={len(cross_profile.json())}",
            )
        )

        foreign_storage = storage_insert(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            access_token=token_a,
            bucket="evidence",
            object_path=f"{ORG_B}/{uuid.uuid4()}/{uuid.uuid4()}/foreign.txt",
        )
        checks.append(
            CheckResult(
                "tenant_a_cannot_write_tenant_b_storage_path",
                foreign_storage.status_code in {400, 401, 403, 404},
                f"status={foreign_storage.status_code}",
            )
        )

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            deactivated = connection.execute(
                """
                update public.organization_districts
                set active = false
                where organization_id = %s::uuid and district_id = %s::uuid
                returning organization_id
                """,
                (ORG_A, DISTRICT_A),
            ).fetchone()
            connection.commit()
        checks.append(
            CheckResult(
                "tenant_a_district_scope_can_be_deactivated",
                deactivated is not None,
                "organization_districts deactivated for tenant A",
            )
        )

        denied_facility = rest_get(
            supabase_url=supabase_url,
            publishable_key=publishable_key,
            access_token=token_a,
            table="facilities",
            query="select=id&limit=1",
        )
        checks.append(
            CheckResult(
                "deactivated_district_denies_facility_reads",
                denied_facility.status_code == 200 and denied_facility.json() == [],
                f"status={denied_facility.status_code}, rows={len(denied_facility.json())}",
            )
        )

        passed = all(check.passed for check in checks)
        report: dict[str, Any] = {
            "check": "check_database",
            "status": "passed" if passed else "failed",
            "generated_at": datetime.now(UTC).isoformat(),
            "checks": [asdict(check) for check in checks],
            "notes": [
                "Executed against local Supabase CLI stack.",
                "Throwaway Auth users were created for acceptance only.",
            ],
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if passed else 1
    except Exception as exc:  # noqa: BLE001 - acceptance runner must record failure
        report = {
            "check": "check_database",
            "status": "failed",
            "generated_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
            "checks": [asdict(check) for check in checks],
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
