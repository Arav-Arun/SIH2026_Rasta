#!/usr/bin/env python3
"""Statically verify RASTA's reviewed Supabase schema contract."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / "supabase" / "migrations"
SEED_PATH = REPOSITORY_ROOT / "supabase" / "seed.sql"
REPORT_PATH = REPOSITORY_ROOT / "artifacts" / "reports" / "schema_static_check.json"

MIGRATION_NAME = re.compile(r"^\d{14}_[a-z0-9_]+\.sql$")

REQUIRED_TABLES = (
    "organizations",
    "districts",
    "organization_districts",
    "profiles",
    "role_assignments",
    "facilities",
    "vehicles",
    "device_registrations",
    "road_segments",
    "bridges",
    "network_observations",
    "segment_current_state",
    "incidents",
    "incident_segments",
    "attachments",
    "inspections",
    "supply_requests",
    "consignments",
    "consignment_items",
    "trips",
    "route_plans",
    "route_alternatives",
    "telemetry_points",
    "trip_current_location",
    "delivery_receipts",
    "alerts",
    "alert_recipients",
    "source_runs",
    "model_versions",
    "sync_mutations",
    "audit_events",
    "event_outbox",
)

TENANT_TABLES = tuple(
    table for table in REQUIRED_TABLES if table not in {"organizations", "districts"}
)

REQUIRED_POLICIES = (
    "organizations_select_current_member",
    "profiles_select_self_or_admin",
    "role_assignments_select_self_or_admin",
    "facilities_select_operational_scope",
    "incidents_select_reporter_or_scope",
    "attachments_select_incident_scope",
    "trips_select_driver_or_scope",
    "telemetry_points_select_trip_scope",
    "audit_events_select_admin",
    "evidence_object_insert_exact_attachment",
    "evidence_object_select_scoped",
)


@dataclass(frozen=True)
class ValidationReport:
    """Machine-readable proof of static safeguards, not live policy behavior."""

    task: str
    validation_kind: str
    status: str
    schema_checksum: str
    migration_files: list[str]
    table_count: int
    policy_count: int
    errors: list[str]
    runtime_verification: str
    required_before_runtime_check: list[str]


def _read(path: Path) -> str:
    if not path.is_file():
        raise ValueError(
            f"Required schema artifact is missing: {path.relative_to(REPOSITORY_ROOT)}"
        )
    return path.read_text(encoding="utf-8")


def _require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _contains(text: str, phrase: str) -> bool:
    return phrase.casefold() in text.casefold()


def _table_body(schema: str, table: str) -> str | None:
    match = re.search(
        rf"^create table public\.{re.escape(table)}\s*\((.*?)^\);",
        schema,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def _validate_migrations(migration_paths: Iterable[Path], errors: list[str]) -> None:
    paths = list(migration_paths)
    _require(errors, bool(paths), "No migration files were found.")
    _require(
        errors,
        [path.name for path in paths] == sorted(path.name for path in paths),
        "Migration filenames are not in deterministic lexicographic order.",
    )
    _require(
        errors,
        len({path.name[:14] for path in paths}) == len(paths),
        "Migration version prefixes must be unique.",
    )

    for path in paths:
        _require(
            errors,
            bool(MIGRATION_NAME.fullmatch(path.name)),
            f"Migration name is not forward-migration safe: {path.name}",
        )
        text = _read(path).strip().casefold()
        _require(
            errors,
            text.startswith("--"),
            f"{path.name} needs a migration header comment.",
        )
        _require(errors, "begin;" in text, f"{path.name} does not begin a transaction.")
        _require(
            errors, text.endswith("commit;"), f"{path.name} does not end with commit."
        )


def validate_schema(repository_root: Path = REPOSITORY_ROOT) -> ValidationReport:
    """Return a static contract report for the schema rooted at ``repository_root``."""

    migrations_directory = repository_root / "supabase" / "migrations"
    seed_path = repository_root / "supabase" / "seed.sql"
    errors: list[str] = []
    migration_paths = sorted(migrations_directory.glob("*.sql"))
    _validate_migrations(migration_paths, errors)

    primitive_path = (
        migrations_directory / "20260912000000_extensions_and_primitives.sql"
    )
    core_path = migrations_directory / "20260912000100_core_schema.sql"
    rls_path = migrations_directory / "20260912000200_rls_and_private_storage.sql"
    corrections_path = (
        migrations_directory / "20260912000300_integrity_and_scope_corrections.sql"
    )

    primitives = _read(primitive_path) if primitive_path.exists() else ""
    core = _read(core_path) if core_path.exists() else ""
    rls = _read(rls_path) if rls_path.exists() else ""
    corrections = _read(corrections_path) if corrections_path.exists() else ""
    seed = _read(seed_path) if seed_path.exists() else ""

    _require(
        errors,
        _contains(
            primitives, "create extension if not exists postgis with schema extensions"
        ),
        "PostGIS must be installed in the extensions schema.",
    )
    _require(
        errors,
        _contains(
            primitives, "create extension if not exists pgcrypto with schema extensions"
        ),
        "pgcrypto must be installed in the extensions schema.",
    )
    _require(
        errors,
        _contains(core, "references auth.users (id)"),
        "profiles.user_id must be bound to auth.users.",
    )
    _require(
        errors,
        _contains(core, "create trigger audit_events_append_only"),
        "audit_events needs an append-only trigger.",
    )
    _require(
        errors,
        _contains(core, "create trigger telemetry_points_append_only"),
        "telemetry_points needs an append-only trigger.",
    )
    _require(
        errors,
        _contains(core, "unique (organization_id, idempotency_key)"),
        "Incident writes require a tenant-scoped idempotency key.",
    )
    _require(
        errors,
        _contains(core, "unique (organization_id, actor_id, idempotency_key)"),
        "Sync mutations require an actor-scoped idempotency key.",
    )

    for table in REQUIRED_TABLES:
        body = _table_body(core, table)
        _require(errors, body is not None, f"Required table public.{table} is missing.")
        if table in TENANT_TABLES and body is not None:
            _require(
                errors,
                bool(re.search(r"\borganization_id\s+uuid\s+not\s+null\b", body, re.I)),
                f"Tenant table public.{table} lacks a non-null organization_id.",
            )

    for table in REQUIRED_TABLES:
        _require(
            errors,
            _contains(rls, f"alter table public.{table} enable row level security"),
            f"RLS is not explicitly enabled for public.{table}.",
        )

    _require(
        errors,
        _contains(rls, "revoke all on all tables in schema public from anon"),
        "Anonymous application-table access is not revoked.",
    )
    _require(
        errors,
        _contains(rls, "revoke all on all tables in schema public from authenticated"),
        "Authenticated application-table access is not reset to least privilege.",
    )
    _require(
        errors,
        not bool(re.search(r"create\s+policy[\s\S]*?\bfor\s+all\b", rls, re.I)),
        "RLS must not contain a broad FOR ALL policy.",
    )

    policy_names = set(
        re.findall(r"^create policy\s+([a-z0-9_]+)\s+on\s+", rls, re.I | re.M)
    )
    for policy in REQUIRED_POLICIES:
        _require(
            errors,
            policy in policy_names,
            f"Required RLS/storage policy {policy} is missing.",
        )

    _require(
        errors,
        _contains(rls, "where p.user_id = auth.uid()")
        and _contains(rls, "and p.active")
        and _contains(rls, "ra.valid_from <= now()")
        and _contains(rls, "ra.revoked_at is null"),
        "Role helpers must derive scope from active, current auth.uid() grants.",
    )
    _require(
        errors,
        _contains(rls, "insert into storage.buckets")
        and _contains(rls, "'evidence'")
        and _contains(rls, "false")
        and _contains(rls, "5242880"),
        "The evidence bucket must be private and limited to 5 MB.",
    )
    _require(
        errors,
        _contains(rls, "public.can_write_evidence_object(name)")
        and _contains(rls, "public.can_read_evidence_object(name)"),
        "Evidence object policies must use exact attachment-path authorization.",
    )
    _require(
        errors,
        not bool(
            re.search(
                r"create\s+policy\s+\w*evidence\w*[\s\S]*?\bfor\s+(?:update|delete)\b",
                rls,
                re.I,
            )
        ),
        "Browser evidence update/delete policies are not permitted.",
    )
    required_corrections = (
        "role_assignments_district_scope_check",
        "role_assignments_require_active_scope",
        "inspections_require_existing_scoped_target",
        "trips_route_plan_same_trip",
        "telemetry_points_require_trip_bound_active_device",
        "trip_current_location_require_same_trip_telemetry",
        "attachments_require_identity_storage_key",
        "is_active_organization_district",
        "geometry_is_within_wgs84_bounds",
    )
    for correction in required_corrections:
        _require(
            errors,
            _contains(corrections, correction),
            f"Required integrity correction {correction} is missing.",
        )
    _require(
        errors,
        _contains(corrections, "join public.organization_districts as od")
        and _contains(corrections, "and od.active"),
        "District authorization must require an active organization district.",
    )
    _require(
        errors,
        _contains(
            rls,
            "create policy evidence_object_insert_exact_attachment on storage.objects",
        )
        and _contains(
            rls,
            "create policy evidence_object_select_scoped on storage.objects",
        ),
        "Storage browser isolation must use evidence_object_* RLS policies.",
    )

    _require(
        errors,
        seed.strip().casefold().startswith("--"),
        "Seed requires an explanatory header.",
    )
    _require(
        errors,
        seed.strip().casefold().endswith("commit;"),
        "Seed must end with commit.",
    )
    _require(
        errors,
        "synthetic" in seed.casefold() and "contains_operational_claims" in seed,
        "Seed must label every demo context as synthetic/non-operational.",
    )
    forbidden_seed_inserts = (
        "public.profiles",
        "auth.users",
        "public.road_segments",
        "public.network_observations",
        "public.incidents",
        "public.attachments",
        "public.telemetry_points",
    )
    for table in forbidden_seed_inserts:
        _require(
            errors,
            not bool(re.search(rf"insert\s+into\s+{re.escape(table)}\b", seed, re.I)),
            f"Seed must not insert into {table}.",
        )

    checksum_input = "\0".join(
        f"{path.relative_to(repository_root)}\0{_read(path)}"
        for path in [*migration_paths, seed_path]
        if path.is_file()
    )

    return ValidationReport(
        task="supabase_schema",
        validation_kind="static_schema_contract",
        status="passed" if not errors else "failed",
        schema_checksum=hashlib.sha256(checksum_input.encode("utf-8")).hexdigest(),
        migration_files=[path.name for path in migration_paths],
        table_count=len(REQUIRED_TABLES),
        policy_count=len(policy_names),
        errors=errors,
        runtime_verification=(
            "not executed: this static check cannot prove Supabase/PostGIS RLS, "
            "Auth, storage, or cross-tenant runtime behavior"
        ),
        required_before_runtime_check=[
            "Start Docker and a local Supabase/PostGIS stack.",
            "Run supabase db reset and supabase/tests/schema_contract.sql.",
            "Exercise two real Auth tenants/roles for allowed and denied database and storage paths.",
        ],
    )


def main() -> int:
    report = validate_schema()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
