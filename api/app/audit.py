"""The audit trail, exported for an administrator."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.errors import ApiError
from app.scope import WorkspaceScope

MAX_PAGE = 500


class AuditEvent(BaseModel):
    id: str
    occurred_at: datetime
    actor_id: str | None
    actor_display_name: str | None
    action: str
    entity_type: str
    entity_id: str | None
    before_hash: str | None
    after_hash: str | None
    metadata: dict[str, Any]


class AuditExportResponse(BaseModel):
    organization_id: str
    events: list[AuditEvent]
    #: Pass back as `after` for the next page; null on the last page.
    next_cursor: str | None
    #: Stated so a reader never mistakes this for an editable log.
    append_only: bool = True


def encode_cursor(event: AuditEvent) -> str:
    return f"{event.occurred_at.isoformat()}|{event.id}"


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, event_id = cursor.split("|", 1)
        return datetime.fromisoformat(stamp), event_id
    except ValueError:
        raise ApiError(422, "invalid_cursor", "The page cursor is not valid.") from None


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    """A CSV cell a spreadsheet will not execute."""

    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def to_csv(events: list[AuditEvent]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "occurred_at",
            "id",
            "action",
            "entity_type",
            "entity_id",
            "actor_id",
            "actor_display_name",
            "before_hash",
            "after_hash",
            "metadata",
        ]
    )
    for event in events:
        writer.writerow(
            [
                _cell(event.occurred_at.isoformat()),
                _cell(event.id),
                _cell(event.action),
                _cell(event.entity_type),
                _cell(event.entity_id),
                _cell(event.actor_id),
                _cell(event.actor_display_name),
                _cell(event.before_hash),
                _cell(event.after_hash),
                _cell(json.dumps(event.metadata, sort_keys=True, ensure_ascii=False)),
            ]
        )
    return buffer.getvalue()


class AuditRepository(Protocol):
    async def export(
        self,
        *,
        scope: WorkspaceScope,
        since: datetime | None,
        until: datetime | None,
        action: str | None,
        entity_type: str | None,
        after: str | None,
        limit: int,
    ) -> AuditExportResponse: ...


class PostgresAuditRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def export(
        self,
        *,
        scope: WorkspaceScope,
        since: datetime | None,
        until: datetime | None,
        action: str | None,
        entity_type: str | None,
        after: str | None,
        limit: int,
    ) -> AuditExportResponse:
        cursor_at, cursor_id = decode_cursor(after) if after else (None, None)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                select
                  e.id::text as id, e.occurred_at, e.actor_id::text as actor_id,
                  p.display_name as actor_display_name, e.action, e.entity_type,
                  e.entity_id::text as entity_id, e.before_hash, e.after_hash, e.metadata
                from public.audit_events as e
                left join public.profiles as p
                  on p.id = e.actor_id and p.organization_id = e.organization_id
                where e.organization_id = %(org)s::uuid
                  and (%(since)s::timestamptz is null or e.occurred_at >= %(since)s)
                  and (%(until)s::timestamptz is null or e.occurred_at < %(until)s)
                  and (%(action)s::text is null or e.action = %(action)s)
                  and (%(entity_type)s::text is null or e.entity_type = %(entity_type)s)
                  and (
                    %(cursor_at)s::timestamptz is null
                    or (e.occurred_at, e.id) > (%(cursor_at)s, %(cursor_id)s::uuid)
                  )
                order by e.occurred_at, e.id
                limit %(limit)s
                """,
                {
                    "org": scope.organization_id,
                    "since": since,
                    "until": until,
                    "action": action,
                    "entity_type": entity_type,
                    "cursor_at": cursor_at,
                    "cursor_id": cursor_id,
                    "limit": limit + 1,
                },
            ).fetchall()
        events = [AuditEvent(**row) for row in rows[:limit]]
        return AuditExportResponse(
            organization_id=scope.organization_id,
            events=events,
            next_cursor=encode_cursor(events[-1])
            if len(rows) > limit and events
            else None,
        )


def build_audit_repository(database_url: str | None) -> AuditRepository | None:
    return PostgresAuditRepository(database_url) if database_url else None


__all__ = [
    "MAX_PAGE",
    "AuditEvent",
    "AuditExportResponse",
    "AuditRepository",
    "build_audit_repository",
    "decode_cursor",
    "encode_cursor",
    "to_csv",
]
