"""Idempotent write ledger backed by ``public.sync_mutations``."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

import psycopg
from fastapi import Header
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.errors import ApiError

LEDGER_TTL = timedelta(days=7)


def canonical_hash(payload: Any) -> str:
    """Stable SHA-256 over a JSON payload regardless of key order."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """Validate the mandatory write header; the client generates the UUID."""

    if idempotency_key is None or not idempotency_key.strip():
        raise ApiError(
            400,
            "idempotency_key_required",
            "Every write must carry an Idempotency-Key header with a UUID.",
        )
    try:
        parsed = uuid.UUID(idempotency_key.strip())
    except ValueError as error:
        raise ApiError(
            400,
            "idempotency_key_invalid",
            "Idempotency-Key must be a UUID.",
        ) from error
    return str(parsed)


@dataclass(frozen=True, slots=True)
class LedgerHit:
    result_code: str
    result_payload: dict[str, Any]


def lookup_ledger(
    connection: psycopg.Connection[Any],
    *,
    organization_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
) -> LedgerHit | None:
    """Return a stored result for an exact replay, or refuse a key reuse."""

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "select pg_advisory_xact_lock(hashtextextended(%(key)s, 0))",
            {"key": f"idempotency:{organization_id}:{actor_id}:{idempotency_key}"},
        )
        row = cursor.execute(
            """
            select request_hash, result_code, result_payload
            from public.sync_mutations
            where organization_id = %(organization_id)s::uuid
              and actor_id = %(actor_id)s::uuid
              and idempotency_key = %(idempotency_key)s::uuid
              and expires_at > now()
            """,
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "idempotency_key": idempotency_key,
            },
        ).fetchone()
    if row is None:
        return None
    if row["request_hash"] != request_hash:
        raise ApiError(
            409,
            "idempotency_key_reused",
            "This Idempotency-Key was already used with a different request body.",
        )
    return LedgerHit(
        result_code=row["result_code"], result_payload=row["result_payload"]
    )


def record_ledger(
    connection: psycopg.Connection[Any],
    *,
    organization_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    result_code: str,
    result_payload: dict[str, Any],
) -> None:
    """Store the result inside the caller's transaction."""

    # Expired rows for the same key are replaced; live rows are never
    # overwritten (the caller looked the key up first).
    connection.execute(
        """
        insert into public.sync_mutations (
          organization_id, actor_id, idempotency_key, request_hash,
          result_code, result_payload, expires_at
        )
        values (
          %(organization_id)s::uuid, %(actor_id)s::uuid, %(idempotency_key)s::uuid,
          %(request_hash)s, %(result_code)s, %(result_payload)s, now() + %(ttl)s::interval
        )
        on conflict (organization_id, actor_id, idempotency_key) do update
        set request_hash = excluded.request_hash,
            result_code = excluded.result_code,
            result_payload = excluded.result_payload,
            expires_at = excluded.expires_at,
            created_at = now()
        where public.sync_mutations.expires_at <= now()
        """,
        {
            "organization_id": organization_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "result_code": result_code,
            "result_payload": Jsonb(result_payload),
            "ttl": f"{int(LEDGER_TTL.total_seconds())} seconds",
        },
    )
