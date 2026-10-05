"""Inspection assignment and execution with enforced state transitions."""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db
from app.errors import ApiError
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.scope import WorkspaceScope

InspectionStatus = Literal[
    "assigned",
    "accepted",
    "in_progress",
    "submitted",
    "reviewed",
    "cancelled",
    "overdue",
]
TargetType = Literal["incident", "segment", "bridge", "facility"]

TRANSITIONS: dict[str, dict[str, str]] = {
    # action -> {from_status: to_status}
    "accept": {"assigned": "accepted"},
    "start": {"accepted": "in_progress"},
    "complete": {"accepted": "submitted", "in_progress": "submitted"},
    "cancel": {
        "assigned": "cancelled",
        "accepted": "cancelled",
        "in_progress": "cancelled",
    },
}


class InspectionCreateRequest(BaseModel):
    target_type: TargetType
    target_id: str
    assignee_profile_id: str
    due_at: datetime | None = None
    instructions: str | None = Field(default=None, max_length=4000)


class InspectionCompleteRequest(BaseModel):
    result_incident_id: str | None = Field(
        default=None,
        description="The report filed for this inspection; it must belong to the assignee.",
    )
    note: str | None = Field(default=None, max_length=2000)


class InspectionSummary(BaseModel):
    id: str
    district_id: str
    target_type: TargetType
    target_id: str
    target_label: str | None = None
    assignee_profile_id: str
    assignee_display_name: str | None = None
    status: InspectionStatus
    due_at: datetime | None
    instructions: str | None
    result_incident_id: str | None
    created_by_profile_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class InspectionListResponse(BaseModel):
    inspections: list[InspectionSummary]
    total: int
    as_of: datetime
    scope: Literal["district", "assigned_to_me"]


class InspectionMutationResponse(BaseModel):
    inspection: InspectionSummary
    audit_event_id: str
    replayed: bool = False


class AssignableOfficer(BaseModel):
    """A field officer a dispatcher may assign an inspection to."""

    profile_id: str
    display_name: str


class AssignableOfficerListResponse(BaseModel):
    officers: list[AssignableOfficer]
    district_id: str
    as_of: datetime


class InspectionRepository(Protocol):
    async def create(
        self,
        *,
        scope: WorkspaceScope,
        request: InspectionCreateRequest,
        idempotency_key: str,
    ) -> InspectionMutationResponse: ...

    async def transition(
        self,
        *,
        scope: WorkspaceScope,
        inspection_id: str,
        action: Literal["accept", "start", "complete", "cancel"],
        request: InspectionCompleteRequest | None,
        idempotency_key: str,
    ) -> InspectionMutationResponse: ...

    async def list(
        self, *, scope: WorkspaceScope, status: InspectionStatus | None, limit: int
    ) -> InspectionListResponse: ...

    async def get(
        self, *, scope: WorkspaceScope, inspection_id: str
    ) -> InspectionSummary | None: ...

    async def eligible_assignees(
        self, *, scope: WorkspaceScope, district_id: str
    ) -> list[AssignableOfficer]: ...


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError):
        return False
    return True


class PostgresInspectionRepository:
    _SELECT = """
        select
          i.id::text as id,
          i.district_id::text as district_id,
          i.target_type,
          i.target_id::text as target_id,
          case i.target_type
            when 'incident' then (select inc.type from public.incidents as inc where inc.id = i.target_id)
            when 'segment' then (select coalesce(rs.metadata ->> 'name', rs.road_class) from public.road_segments as rs where rs.id = i.target_id)
            when 'bridge' then (select b.source_id from public.bridges as b where b.id = i.target_id)
            when 'facility' then (select f.name from public.facilities as f where f.id = i.target_id)
          end as target_label,
          i.assignee_id::text as assignee_profile_id,
          p.display_name as assignee_display_name,
          i.status::text as status,
          i.due_at,
          i.instructions,
          i.result_incident_id::text as result_incident_id,
          i.created_by_profile_id::text as created_by_profile_id,
          i.version,
          i.created_at,
          i.updated_at
        from public.inspections as i
        join public.profiles as p on p.id = i.assignee_id and p.organization_id = i.organization_id
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    def _load(
        self,
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        inspection_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        if not _is_uuid(inspection_id):
            return None
        row = connection.execute(
            self._SELECT
            + " where i.organization_id = %(organization_id)s::uuid and i.id = %(inspection_id)s::uuid"
            + (" for update of i" if for_update else ""),
            {"organization_id": organization_id, "inspection_id": inspection_id},
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _visible(scope: WorkspaceScope, row: dict[str, Any]) -> bool:
        if scope.has("inspection:manage") and scope.allows_district(row["district_id"]):
            return True
        return (
            scope.has("inspection:execute")
            and row["assignee_profile_id"] == scope.profile_id
        )

    async def get(
        self, *, scope: WorkspaceScope, inspection_id: str
    ) -> InspectionSummary | None:
        with self._connect() as connection:
            row = self._load(
                connection,
                organization_id=scope.organization_id,
                inspection_id=inspection_id,
            )
        if row is None or not self._visible(scope, row):
            return None
        return InspectionSummary(**row)

    async def eligible_assignees(
        self, *, scope: WorkspaceScope, district_id: str
    ) -> list[AssignableOfficer]:
        """Officers the assign call would accept for this district."""

        scope.require("inspection:manage")
        if not scope.allows_district(district_id):
            raise ApiError(
                403,
                "district_not_in_scope",
                "That district is outside your grant.",
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                select p.id::text as profile_id, p.display_name
                from public.profiles as p
                where p.organization_id = %(org)s::uuid
                  and p.active = true
                  and exists (
                    select 1
                    from public.role_assignments as ra
                    where ra.profile_id = p.id
                      and ra.organization_id = p.organization_id
                      and ra.role = 'field_officer'
                      and ra.revoked_at is null
                      and ra.valid_from <= now()
                      and (ra.valid_to is null or ra.valid_to > now())
                      and (ra.district_id is null or ra.district_id = %(district_id)s::uuid)
                  )
                order by p.display_name
                """,
                {"org": scope.organization_id, "district_id": district_id},
            ).fetchall()

        return [AssignableOfficer(**row) for row in rows]

    async def list(
        self, *, scope: WorkspaceScope, status: InspectionStatus | None, limit: int
    ) -> InspectionListResponse:
        params: dict[str, Any] = {
            "organization_id": scope.organization_id,
            "district_ids": list(scope.district_ids)
            if scope.district_ids is not None
            else None,
            "profile_id": scope.profile_id,
            "status": status,
            "limit": limit,
        }
        if scope.has("inspection:manage"):
            where = """
                where i.organization_id = %(organization_id)s::uuid
                  and (%(district_ids)s::uuid[] is null or i.district_id = any(%(district_ids)s::uuid[]))
            """
            visibility: Literal["district", "assigned_to_me"] = "district"
        elif scope.has("inspection:execute"):
            where = """
                where i.organization_id = %(organization_id)s::uuid
                  and i.assignee_id = %(profile_id)s::uuid
            """
            visibility = "assigned_to_me"
        else:
            raise ApiError(
                403, "scope_denied", "You do not have access to this action."
            )
        where += " and (%(status)s::text is null or i.status::text = %(status)s::text)"
        with self._connect() as connection:
            rows = connection.execute(
                self._SELECT
                + where
                + " order by i.due_at nulls last, i.created_at desc limit %(limit)s",
                params,
            ).fetchall()
            total = connection.execute(
                "select count(*) as total from public.inspections as i " + where, params
            ).fetchone()
        return InspectionListResponse(
            inspections=[InspectionSummary(**dict(row)) for row in rows],
            total=int(total["total"]) if total else 0,
            as_of=datetime.now(UTC),
            scope=visibility,
        )

    @staticmethod
    def _audit(
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        actor_id: str,
        action: str,
        inspection_id: str,
        metadata: dict[str, Any],
    ) -> str:
        row = connection.execute(
            """
            insert into public.audit_events (organization_id, actor_id, action, entity_type, entity_id, metadata)
            values (%(organization_id)s::uuid, %(actor_id)s::uuid, %(action)s, 'inspection', %(entity_id)s::uuid, %(metadata)s)
            returning id::text as id
            """,
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "action": action,
                "entity_id": inspection_id,
                "metadata": Jsonb(metadata),
            },
        ).fetchone()
        return row["id"]

    async def create(
        self,
        *,
        scope: WorkspaceScope,
        request: InspectionCreateRequest,
        idempotency_key: str,
    ) -> InspectionMutationResponse:
        scope.require("inspection:manage")
        if not _is_uuid(request.target_id) or not _is_uuid(request.assignee_profile_id):
            raise ApiError(
                422,
                "validation_error",
                "target_id and assignee_profile_id must be UUIDs.",
            )
        request_hash = canonical_hash(request.model_dump(mode="json"))
        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return InspectionMutationResponse(
                    **{**hit.result_payload, "replayed": True}
                )

            target = connection.execute(
                {
                    "incident": "select district_id::text as district_id from public.incidents where organization_id = %(org)s::uuid and id = %(id)s::uuid",
                    "segment": "select district_id::text as district_id from public.road_segments where organization_id = %(org)s::uuid and id = %(id)s::uuid",
                    "bridge": "select rs.district_id::text as district_id from public.bridges as b join public.road_segments as rs on rs.id = b.segment_id and rs.organization_id = b.organization_id where b.organization_id = %(org)s::uuid and b.id = %(id)s::uuid",
                    "facility": "select district_id::text as district_id from public.facilities where organization_id = %(org)s::uuid and id = %(id)s::uuid",
                }[request.target_type],
                {"org": scope.organization_id, "id": request.target_id},
            ).fetchone()
            if target is None:
                raise ApiError(
                    422,
                    "unknown_target",
                    "The inspection target does not exist in this organization.",
                )
            district_id = target["district_id"]
            scope.require_district(district_id)

            assignee = connection.execute(
                """
                select p.id::text as id
                from public.profiles as p
                where p.organization_id = %(org)s::uuid
                  and p.id = %(profile_id)s::uuid
                  and p.active = true
                  and exists (
                    select 1
                    from public.role_assignments as ra
                    where ra.profile_id = p.id
                      and ra.organization_id = p.organization_id
                      and ra.role = 'field_officer'
                      and ra.revoked_at is null
                      and ra.valid_from <= now()
                      and (ra.valid_to is null or ra.valid_to > now())
                      and (ra.district_id is null or ra.district_id = %(district_id)s::uuid)
                  )
                """,
                {
                    "org": scope.organization_id,
                    "profile_id": request.assignee_profile_id,
                    "district_id": district_id,
                },
            ).fetchone()
            if assignee is None:
                raise ApiError(
                    422,
                    "assignee_not_eligible",
                    "The assignee must be an active field officer with a grant covering the target district.",
                )

            inspection_id = str(uuid.uuid4())
            connection.execute(
                """
                insert into public.inspections (
                  id, organization_id, district_id, target_type, target_id, assignee_id,
                  status, due_at, instructions, created_by_profile_id
                )
                values (
                  %(id)s::uuid, %(org)s::uuid, %(district_id)s::uuid, %(target_type)s, %(target_id)s::uuid,
                  %(assignee)s::uuid, 'assigned', %(due_at)s, %(instructions)s, %(creator)s::uuid
                )
                """,
                {
                    "id": inspection_id,
                    "org": scope.organization_id,
                    "district_id": district_id,
                    "target_type": request.target_type,
                    "target_id": request.target_id,
                    "assignee": request.assignee_profile_id,
                    "due_at": request.due_at,
                    "instructions": request.instructions,
                    "creator": scope.profile_id,
                },
            )
            audit_id = self._audit(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                action="inspection.assigned",
                inspection_id=inspection_id,
                metadata={
                    "target_type": request.target_type,
                    "target_id": request.target_id,
                    "assignee_profile_id": request.assignee_profile_id,
                    "due_at": request.due_at.isoformat() if request.due_at else None,
                },
            )
            connection.execute(
                """
                insert into public.event_outbox (organization_id, event_type, aggregate_id, payload)
                values (%(org)s::uuid, 'inspection.assigned', %(id)s::uuid, %(payload)s)
                """,
                {
                    "org": scope.organization_id,
                    "id": inspection_id,
                    "payload": Jsonb(
                        {
                            "inspection_id": inspection_id,
                            "district_id": district_id,
                            "assignee_profile_id": request.assignee_profile_id,
                            "target_type": request.target_type,
                            "target_id": request.target_id,
                        }
                    ),
                },
            )
            row = self._load(
                connection,
                organization_id=scope.organization_id,
                inspection_id=inspection_id,
            )
            assert row is not None
            response = InspectionMutationResponse(
                inspection=InspectionSummary(**row), audit_event_id=audit_id
            )
            record_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result_code="accepted",
                result_payload=response.model_dump(mode="json"),
            )
            connection.commit()
        return response

    async def transition(
        self,
        *,
        scope: WorkspaceScope,
        inspection_id: str,
        action: Literal["accept", "start", "complete", "cancel"],
        request: InspectionCompleteRequest | None,
        idempotency_key: str,
    ) -> InspectionMutationResponse:
        body = request.model_dump(mode="json") if request else {}
        request_hash = canonical_hash(
            {"inspection_id": inspection_id, "action": action, **body}
        )
        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return InspectionMutationResponse(
                    **{**hit.result_payload, "replayed": True}
                )

            row = self._load(
                connection,
                organization_id=scope.organization_id,
                inspection_id=inspection_id,
                for_update=True,
            )
            if row is None or not self._visible(scope, row):
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )

            if action == "cancel":
                scope.require("inspection:manage")
                scope.require_district(row["district_id"])
            else:
                scope.require("inspection:execute")
                if row["assignee_profile_id"] != scope.profile_id:
                    raise ApiError(
                        403, "scope_denied", "You do not have access to this action."
                    )

            next_status = TRANSITIONS[action].get(row["status"])
            if next_status is None:
                raise ApiError(
                    409,
                    "invalid_transition",
                    f"Cannot {action} an inspection that is {row['status']}.",
                    details={
                        "current_status": row["status"],
                        "allowed_from": sorted(TRANSITIONS[action]),
                    },
                )

            result_incident_id = None
            if action == "complete" and request and request.result_incident_id:
                if not _is_uuid(request.result_incident_id):
                    raise ApiError(
                        422, "validation_error", "result_incident_id must be a UUID."
                    )
                incident = connection.execute(
                    """
                    select id::text as id
                    from public.incidents
                    where organization_id = %(org)s::uuid
                      and id = %(id)s::uuid
                      and reporter_id = %(reporter)s::uuid
                      and district_id = %(district_id)s::uuid
                    """,
                    {
                        "org": scope.organization_id,
                        "id": request.result_incident_id,
                        "reporter": scope.profile_id,
                        "district_id": row["district_id"],
                    },
                ).fetchone()
                if incident is None:
                    raise ApiError(
                        422,
                        "result_incident_invalid",
                        "The result report must be your own report in the inspection's district.",
                    )
                result_incident_id = incident["id"]

            connection.execute(
                """
                update public.inspections
                set status = %(status)s::public.inspection_status,
                    result_incident_id = coalesce(%(result_incident_id)s::uuid, result_incident_id)
                where organization_id = %(org)s::uuid and id = %(id)s::uuid
                """,
                {
                    "status": next_status,
                    "result_incident_id": result_incident_id,
                    "org": scope.organization_id,
                    "id": inspection_id,
                },
            )
            audit_id = self._audit(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                action=f"inspection.{action}",
                inspection_id=inspection_id,
                metadata={
                    "from_status": row["status"],
                    "to_status": next_status,
                    "result_incident_id": result_incident_id,
                    "note": request.note if request else None,
                },
            )
            reloaded = self._load(
                connection,
                organization_id=scope.organization_id,
                inspection_id=inspection_id,
            )
            assert reloaded is not None
            response = InspectionMutationResponse(
                inspection=InspectionSummary(**reloaded), audit_event_id=audit_id
            )
            record_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result_code="accepted",
                result_payload=response.model_dump(mode="json"),
            )
            connection.commit()
        return response


def build_inspection_repository(
    database_url: str | None,
) -> InspectionRepository | None:
    if not database_url:
        return None
    return PostgresInspectionRepository(database_url)
