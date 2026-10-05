"""Incident lifecycle: field report shell, evidence attachments, dispatcher review."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app import db
from app.errors import ApiError
from app.evidence import (
    ALLOWED_MIME_TYPES,
    EVIDENCE_BUCKET,
    MAX_ATTACHMENT_BYTES,
    UPLOAD_WINDOW_SECONDS,
    EvidenceStore,
)
from app.exposure import SegmentChange, react_to_state_change
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.push import PushSender, deliver_alerts_for
from app.reducer import (
    IMPACT_TO_PASSABILITY,
    CurrentState,
    ReviewedDecision,
    reduce_segment_state,
)
from app.scope import WorkspaceScope

# --- contract ---------------------------------------------------------------

IncidentType = Literal[
    "landslide_debris",
    "flooding",
    "bridge_damage",
    "road_damage",
    "congestion",
    "road_reopened",
    "other",
]
IncidentStatus = Literal[
    "draft",
    "queued",
    "submitted",
    "under_review",
    "confirmed",
    "rejected",
    "superseded",
]
UploadStatus = Literal["pending", "uploaded", "verified", "rejected", "expired"]
Impact = Literal["monitor", "restriction", "closure"]
ReviewDecision = Literal[
    "confirm_restriction",
    "confirm_closure",
    "request_clarification",
    "reject",
    "reopen",
]
LocationSource = Literal["device_gps", "manual_pin"]

SEGMENT_SUGGESTION_RADIUS_M = 250.0
MAX_ATTACHMENTS_PER_INCIDENT = 5


class GeoPoint(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class AttachmentDraft(BaseModel):
    local_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    mime_type: str
    bytes: int = Field(gt=0, le=MAX_ATTACHMENT_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: datetime | None = None

    @field_validator("mime_type")
    @classmethod
    def _allowed_mime(cls, value: str) -> str:
        if value not in ALLOWED_MIME_TYPES:
            raise ValueError(f"mime_type must be one of {sorted(ALLOWED_MIME_TYPES)}")
        return value


class IncidentCreateRequest(BaseModel):
    type: IncidentType
    captured_at: datetime
    location: GeoPoint
    location_source: LocationSource = "device_gps"
    accuracy_m: float | None = Field(default=None, gt=0, le=100_000)
    note: str | None = Field(default=None, max_length=4000)
    district_id: str | None = None
    proposed_segment_ids: list[str] = Field(default_factory=list, max_length=10)
    attachments: list[AttachmentDraft] = Field(
        default_factory=list, max_length=MAX_ATTACHMENTS_PER_INCIDENT
    )


class UploadInstruction(BaseModel):
    attachment_id: str
    local_id: str
    method: Literal["supabase_storage_upload"] = "supabase_storage_upload"
    bucket: str = EVIDENCE_BUCKET
    path: str
    max_bytes: int = MAX_ATTACHMENT_BYTES
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: sorted(ALLOWED_MIME_TYPES)
    )
    expires_at: datetime


class AttachmentInfo(BaseModel):
    id: str
    storage_key: str
    mime_type: str
    size_bytes: int | None
    sha256: str
    upload_status: UploadStatus
    captured_at: datetime | None
    uploaded_at: datetime | None
    uploader_profile_id: str | None


class IncidentSegmentInfo(BaseModel):
    segment_id: str
    impact: Impact
    reviewed_by_profile_id: str | None
    reviewed_at: datetime | None
    name: str | None = None
    road_class: str | None = None


class SuggestedSegment(BaseModel):
    segment_id: str
    name: str | None
    road_class: str
    distance_m: float
    passability: str


class IncidentSummary(BaseModel):
    id: str
    district_id: str
    type: IncidentType | str
    status: IncidentStatus
    location: GeoPoint | None
    location_source: LocationSource | None = None
    accuracy_m: float | None
    captured_at: datetime | None
    reported_at: datetime
    reporter_profile_id: str | None
    reporter_role: str | None = None
    note: str | None
    source_mode: str
    version: int
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    segments: list[IncidentSegmentInfo] = Field(default_factory=list)
    review: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class IncidentCreateResponse(BaseModel):
    incident: IncidentSummary
    upload_instructions: list[UploadInstruction]
    suggested_segments: list[SuggestedSegment] = Field(
        description="Nearest segments within 250 m. These are proposals for the reviewer, never applied automatically."
    )
    replayed: bool = False


class AttachmentAddResponse(BaseModel):
    attachment: AttachmentInfo
    upload_instruction: UploadInstruction
    replayed: bool = False


class AttachmentCompleteRequest(BaseModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_ATTACHMENT_BYTES)


class AttachmentVerification(BaseModel):
    status: Literal["verified", "rejected", "expired", "missing", "already_verified"]
    reason: str | None = None


class AttachmentCompleteResponse(BaseModel):
    attachment: AttachmentInfo
    verification: AttachmentVerification
    replayed: bool = False


class IncidentReviewRequest(BaseModel):
    decision: ReviewDecision
    affected_segment_ids: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=3, max_length=2000)
    expected_version: int = Field(ge=1)


class SegmentStateChange(BaseModel):
    segment_id: str
    previous_passability: str
    passability: str
    network_version: str
    changed: bool
    outcome: str


class IncidentReviewResponse(BaseModel):
    incident: IncidentSummary
    segment_changes: list[SegmentStateChange]
    network_version: str | None = Field(
        default=None, description="New network version when routable state changed."
    )
    audit_event_ids: list[str]
    outbox_event_ids: list[str]
    replayed: bool = False


class IncidentListResponse(BaseModel):
    incidents: list[IncidentSummary]
    total: int
    as_of: datetime
    scope: Literal["district", "own_reports"]


# --- repository -------------------------------------------------------------


class IncidentRepository(Protocol):
    async def create_incident(
        self,
        *,
        scope: WorkspaceScope,
        request: IncidentCreateRequest,
        idempotency_key: str,
    ) -> IncidentCreateResponse: ...

    async def add_attachment(
        self,
        *,
        scope: WorkspaceScope,
        incident_id: str,
        draft: AttachmentDraft,
        idempotency_key: str,
    ) -> AttachmentAddResponse: ...

    async def complete_attachment(
        self,
        *,
        scope: WorkspaceScope,
        incident_id: str,
        attachment_id: str,
        request: AttachmentCompleteRequest,
        idempotency_key: str,
    ) -> AttachmentCompleteResponse: ...

    async def review_incident(
        self,
        *,
        scope: WorkspaceScope,
        incident_id: str,
        request: IncidentReviewRequest,
        idempotency_key: str,
    ) -> IncidentReviewResponse: ...

    async def list_incidents(
        self, *, scope: WorkspaceScope, status: IncidentStatus | None, limit: int
    ) -> IncidentListResponse: ...

    async def get_incident(
        self, *, scope: WorkspaceScope, incident_id: str
    ) -> IncidentSummary | None: ...


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def storage_key_for(
    organization_id: str,
    incident_id: str,
    attachment_id: str,
    local_id: str,
    mime_type: str,
) -> str:
    stem = _SAFE_FILENAME.sub("-", local_id)[:100] or "evidence"
    return f"{organization_id}/{incident_id}/{attachment_id}/{stem}.{_EXTENSIONS[mime_type]}"


def _hash_state(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


@dataclass(slots=True)
class _IncidentRow:
    row: dict[str, Any]
    attachments: list[dict[str, Any]]
    segments: list[dict[str, Any]]


class PostgresIncidentRepository:
    def __init__(
        self,
        database_url: str,
        evidence_store: EvidenceStore,
        push_sender: PushSender | None = None,
    ) -> None:
        self._database_url = database_url
        self._evidence_store = evidence_store
        # Optional by design: a deployment with no push sender still raises
        # every alert, it just does not ring anybody's phone.
        self._push_sender = push_sender

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    # -- reads ---------------------------------------------------------------

    _INCIDENT_SELECT = """
        select
          i.id::text as id,
          i.district_id::text as district_id,
          i.type,
          i.status::text as status,
          extensions.st_x(i.location)::float8 as longitude,
          extensions.st_y(i.location)::float8 as latitude,
          i.accuracy_m::float8 as accuracy_m,
          i.captured_at,
          i.reported_at,
          i.reporter_id::text as reporter_profile_id,
          i.note,
          i.source_mode::text as source_mode,
          i.version,
          i.created_at,
          i.updated_at,
          (
            select ra.role::text
            from public.role_assignments as ra
            where ra.profile_id = i.reporter_id
              and ra.organization_id = i.organization_id
              and ra.revoked_at is null
            order by ra.valid_from
            limit 1
          ) as reporter_role,
          (
            select ae.metadata
            from public.audit_events as ae
            where ae.entity_type = 'incident'
              and ae.entity_id = i.id
              and ae.action in ('incident.reviewed', 'incident.created')
            order by ae.occurred_at desc
            limit 1
          ) as last_audit
        from public.incidents as i
    """

    def _load_incident(
        self,
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        incident_id: str,
        for_update: bool = False,
    ) -> _IncidentRow | None:
        if not _is_uuid(incident_id):
            return None
        row = connection.execute(
            self._INCIDENT_SELECT
            + " where i.organization_id = %(organization_id)s::uuid and i.id = %(incident_id)s::uuid"
            + (" for update of i" if for_update else ""),
            {"organization_id": organization_id, "incident_id": incident_id},
        ).fetchone()
        if row is None:
            return None
        attachments = connection.execute(
            """
            select
              a.id::text as id, a.storage_key, a.mime_type, a.size_bytes, a.sha256,
              a.upload_status::text as upload_status, a.captured_at, a.uploaded_at,
              a.uploader_id::text as uploader_profile_id, a.created_at
            from public.attachments as a
            where a.organization_id = %(organization_id)s::uuid
              and a.incident_id = %(incident_id)s::uuid
            order by a.created_at
            """,
            {"organization_id": organization_id, "incident_id": incident_id},
        ).fetchall()
        segments = connection.execute(
            """
            select
              s.segment_id::text as segment_id,
              s.impact::text as impact,
              s.reviewed_by_profile_id::text as reviewed_by_profile_id,
              s.reviewed_at,
              rs.metadata ->> 'name' as name,
              rs.road_class
            from public.incident_segments as s
            join public.road_segments as rs
              on rs.id = s.segment_id and rs.organization_id = s.organization_id
            where s.organization_id = %(organization_id)s::uuid
              and s.incident_id = %(incident_id)s::uuid
            order by s.created_at
            """,
            {"organization_id": organization_id, "incident_id": incident_id},
        ).fetchall()
        return _IncidentRow(
            row=dict(row),
            attachments=[dict(a) for a in attachments],
            segments=[dict(s) for s in segments],
        )

    @staticmethod
    def _to_summary(loaded: _IncidentRow) -> IncidentSummary:
        row = loaded.row
        last_audit = row.get("last_audit") or {}
        return IncidentSummary(
            id=row["id"],
            district_id=row["district_id"],
            type=row["type"],
            status=row["status"],
            location=(
                GeoPoint(longitude=row["longitude"], latitude=row["latitude"])
                if row.get("longitude") is not None
                else None
            ),
            location_source=last_audit.get("location_source"),
            accuracy_m=row.get("accuracy_m"),
            captured_at=row.get("captured_at"),
            reported_at=row["reported_at"],
            reporter_profile_id=row.get("reporter_profile_id"),
            reporter_role=row.get("reporter_role"),
            note=row.get("note"),
            source_mode=row["source_mode"],
            version=row["version"],
            attachments=[
                AttachmentInfo(**{k: v for k, v in a.items() if k != "created_at"})
                for a in loaded.attachments
            ],
            segments=[IncidentSegmentInfo(**s) for s in loaded.segments],
            review=last_audit.get("review"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _assert_visible(self, scope: WorkspaceScope, loaded: _IncidentRow) -> None:
        row = loaded.row
        if scope.has("incident:review") and scope.allows_district(row["district_id"]):
            return
        if (
            scope.has("incident:create")
            and row.get("reporter_profile_id") == scope.profile_id
        ):
            return
        raise ApiError(404, "not_found", "The requested resource was not found.")

    async def get_incident(
        self, *, scope: WorkspaceScope, incident_id: str
    ) -> IncidentSummary | None:
        with self._connect() as connection:
            loaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
            )
        if loaded is None:
            return None
        self._assert_visible(scope, loaded)
        return self._to_summary(loaded)

    async def list_incidents(
        self, *, scope: WorkspaceScope, status: IncidentStatus | None, limit: int
    ) -> IncidentListResponse:
        params: dict[str, Any] = {
            "organization_id": scope.organization_id,
            "status": status,
            "limit": limit,
            "district_ids": list(scope.district_ids)
            if scope.district_ids is not None
            else None,
            "profile_id": scope.profile_id,
        }
        if scope.has("incident:review"):
            where = """
                where i.organization_id = %(organization_id)s::uuid
                  and (%(district_ids)s::uuid[] is null or i.district_id = any(%(district_ids)s::uuid[]))
            """
            visibility: Literal["district", "own_reports"] = "district"
        elif scope.has("incident:create"):
            where = """
                where i.organization_id = %(organization_id)s::uuid
                  and i.reporter_id = %(profile_id)s::uuid
            """
            visibility = "own_reports"
        else:
            raise ApiError(
                403, "scope_denied", "You do not have access to this action."
            )
        where += " and (%(status)s::text is null or i.status::text = %(status)s::text)"

        with self._connect() as connection:
            rows = connection.execute(
                self._INCIDENT_SELECT
                + where
                + " order by case i.status when 'submitted' then 0 when 'under_review' then 1 else 2 end, i.reported_at desc limit %(limit)s",
                params,
            ).fetchall()
            total = connection.execute(
                "select count(*) as total from public.incidents as i " + where, params
            ).fetchone()
            summaries = []
            for row in rows:
                loaded = self._load_incident(
                    connection,
                    organization_id=scope.organization_id,
                    incident_id=row["id"],
                )
                if loaded is not None:
                    summaries.append(self._to_summary(loaded))
        return IncidentListResponse(
            incidents=summaries,
            total=int(total["total"]) if total else 0,
            as_of=datetime.now(UTC),
            scope=visibility,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _nearest_segments(
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        district_ids: frozenset[str] | None,
        point: GeoPoint,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return connection.execute(
            """
            select
              rs.id::text as segment_id,
              rs.district_id::text as district_id,
              rs.metadata ->> 'name' as name,
              rs.road_class,
              coalesce(scs.passability::text, 'unknown') as passability,
              extensions.st_distance(
                rs.geometry::extensions.geography,
                extensions.st_setsrid(extensions.st_makepoint(%(lon)s, %(lat)s), 4326)::extensions.geography
              )::float8 as distance_m
            from public.road_segments as rs
            left join public.segment_current_state as scs
              on scs.segment_id = rs.id and scs.organization_id = rs.organization_id
            where rs.organization_id = %(organization_id)s::uuid
              and (%(district_ids)s::uuid[] is null or rs.district_id = any(%(district_ids)s::uuid[]))
              and extensions.st_dwithin(
                rs.geometry::extensions.geography,
                extensions.st_setsrid(extensions.st_makepoint(%(lon)s, %(lat)s), 4326)::extensions.geography,
                %(radius)s
              )
            order by distance_m
            limit %(limit)s
            """,
            {
                "organization_id": organization_id,
                "district_ids": list(district_ids)
                if district_ids is not None
                else None,
                "lon": point.longitude,
                "lat": point.latitude,
                "radius": SEGMENT_SUGGESTION_RADIUS_M,
                "limit": limit,
            },
        ).fetchall()

    @staticmethod
    def _segments_in_scope(
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        segment_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not segment_ids:
            return []
        if any(not _is_uuid(segment_id) for segment_id in segment_ids):
            raise ApiError(422, "validation_error", "Segment IDs must be UUIDs.")
        rows = connection.execute(
            """
            select rs.id::text as id, rs.district_id::text as district_id, rs.network_version,
                   rs.metadata ->> 'name' as name, rs.road_class
            from public.road_segments as rs
            where rs.organization_id = %(organization_id)s::uuid
              and rs.id = any(%(segment_ids)s::uuid[])
            """,
            {"organization_id": organization_id, "segment_ids": segment_ids},
        ).fetchall()
        found = {row["id"] for row in rows}
        missing = [segment_id for segment_id in segment_ids if segment_id not in found]
        if missing:
            raise ApiError(
                422,
                "unknown_segment",
                "One or more segments do not exist in this organization.",
                details={"segment_ids": missing},
            )
        return [dict(row) for row in rows]

    @staticmethod
    def _audit(
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        actor_id: str | None,
        action: str,
        entity_type: str,
        entity_id: str | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        metadata: dict[str, Any],
    ) -> str:
        row = connection.execute(
            """
            insert into public.audit_events (
              organization_id, actor_id, action, entity_type, entity_id,
              before_hash, after_hash, metadata
            )
            values (
              %(organization_id)s::uuid, %(actor_id)s::uuid, %(action)s, %(entity_type)s,
              %(entity_id)s::uuid, %(before_hash)s, %(after_hash)s, %(metadata)s
            )
            returning id::text as id
            """,
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before_hash": _hash_state(before) if before is not None else None,
                "after_hash": _hash_state(after) if after is not None else None,
                "metadata": Jsonb(metadata),
            },
        ).fetchone()
        return row["id"]

    @staticmethod
    def _outbox(
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        event_type: str,
        aggregate_id: str | None,
        payload: dict[str, Any],
    ) -> str:
        row = connection.execute(
            """
            insert into public.event_outbox (organization_id, event_type, aggregate_id, payload)
            values (%(organization_id)s::uuid, %(event_type)s, %(aggregate_id)s::uuid, %(payload)s)
            returning id::text as id
            """,
            {
                "organization_id": organization_id,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "payload": Jsonb(payload),
            },
        ).fetchone()
        return row["id"]

    def _insert_attachment(
        self,
        connection: psycopg.Connection[Any],
        *,
        organization_id: str,
        incident_id: str,
        uploader_id: str,
        draft: AttachmentDraft,
    ) -> tuple[AttachmentInfo, UploadInstruction]:
        attachment_id = str(uuid.uuid4())
        storage_key = storage_key_for(
            organization_id, incident_id, attachment_id, draft.local_id, draft.mime_type
        )
        row = connection.execute(
            """
            insert into public.attachments (
              id, organization_id, incident_id, uploader_id, storage_key, sha256,
              mime_type, size_bytes, captured_at, upload_status
            )
            values (
              %(id)s::uuid, %(organization_id)s::uuid, %(incident_id)s::uuid, %(uploader_id)s::uuid,
              %(storage_key)s, %(sha256)s, %(mime_type)s, %(size_bytes)s, %(captured_at)s, 'pending'
            )
            returning id::text as id, storage_key, mime_type, size_bytes, sha256,
                      upload_status::text as upload_status, captured_at, uploaded_at,
                      uploader_id::text as uploader_profile_id, created_at
            """,
            {
                "id": attachment_id,
                "organization_id": organization_id,
                "incident_id": incident_id,
                "uploader_id": uploader_id,
                "storage_key": storage_key,
                "sha256": draft.sha256,
                "mime_type": draft.mime_type,
                "size_bytes": draft.bytes,
                "captured_at": draft.captured_at,
            },
        ).fetchone()
        info = AttachmentInfo(**{k: v for k, v in row.items() if k != "created_at"})
        instruction = UploadInstruction(
            attachment_id=attachment_id,
            local_id=draft.local_id,
            path=storage_key,
            expires_at=row["created_at"] + timedelta(seconds=UPLOAD_WINDOW_SECONDS),
        )
        return info, instruction

    # -- writes --------------------------------------------------------------

    async def create_incident(
        self,
        *,
        scope: WorkspaceScope,
        request: IncidentCreateRequest,
        idempotency_key: str,
    ) -> IncidentCreateResponse:
        scope.require("incident:create")
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
                return IncidentCreateResponse(
                    **{**hit.result_payload, "replayed": True}
                )

            proposed = self._segments_in_scope(
                connection,
                organization_id=scope.organization_id,
                segment_ids=request.proposed_segment_ids,
            )
            nearest = self._nearest_segments(
                connection,
                organization_id=scope.organization_id,
                district_ids=scope.district_ids,
                point=request.location,
            )

            # District: proposed segments > explicit district > nearest segment.
            if proposed:
                districts = {row["district_id"] for row in proposed}
                if len(districts) != 1:
                    raise ApiError(
                        422,
                        "validation_error",
                        "Proposed segments must belong to a single district.",
                    )
                district_id = next(iter(districts))
            elif request.district_id:
                district_id = request.district_id
            elif nearest:
                district_id = nearest[0]["district_id"]
            else:
                raise ApiError(
                    422,
                    "district_unresolved",
                    "No road segment lies within 250 m of the location; supply district_id or proposed_segment_ids.",
                )
            if not _is_uuid(district_id):
                raise ApiError(422, "validation_error", "district_id must be a UUID.")
            scope.require_district(district_id)

            incident_id = str(uuid.uuid4())
            connection.execute(
                """
                insert into public.incidents (
                  id, organization_id, district_id, type, status, location, accuracy_m,
                  captured_at, reporter_id, source_mode, note, idempotency_key
                )
                values (
                  %(id)s::uuid, %(organization_id)s::uuid, %(district_id)s::uuid, %(type)s, 'submitted',
                  extensions.st_setsrid(extensions.st_makepoint(%(lon)s, %(lat)s), 4326),
                  %(accuracy_m)s, %(captured_at)s, %(reporter_id)s::uuid, 'recorded', %(note)s,
                  %(idempotency_key)s::uuid
                )
                """,
                {
                    "id": incident_id,
                    "organization_id": scope.organization_id,
                    "district_id": district_id,
                    "type": request.type,
                    "lon": request.location.longitude,
                    "lat": request.location.latitude,
                    "accuracy_m": request.accuracy_m,
                    "captured_at": request.captured_at,
                    "reporter_id": scope.profile_id,
                    "note": request.note,
                    "idempotency_key": idempotency_key,
                },
            )
            for row in proposed:
                connection.execute(
                    """
                    insert into public.incident_segments (organization_id, incident_id, segment_id, impact)
                    values (%(organization_id)s::uuid, %(incident_id)s::uuid, %(segment_id)s::uuid, 'monitor')
                    """,
                    {
                        "organization_id": scope.organization_id,
                        "incident_id": incident_id,
                        "segment_id": row["id"],
                    },
                )

            instructions: list[UploadInstruction] = []
            for draft in request.attachments:
                _info, instruction = self._insert_attachment(
                    connection,
                    organization_id=scope.organization_id,
                    incident_id=incident_id,
                    uploader_id=scope.profile_id,
                    draft=draft,
                )
                instructions.append(instruction)

            self._audit(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                action="incident.created",
                entity_type="incident",
                entity_id=incident_id,
                before=None,
                after={"status": "submitted", "type": request.type},
                metadata={
                    "location_source": request.location_source,
                    "accuracy_m": request.accuracy_m,
                    "proposed_segment_ids": [row["id"] for row in proposed],
                    "attachment_count": len(request.attachments),
                    "idempotency_key": idempotency_key,
                },
            )
            self._outbox(
                connection,
                organization_id=scope.organization_id,
                event_type="incident.submitted",
                aggregate_id=incident_id,
                payload={
                    "incident_id": incident_id,
                    "district_id": district_id,
                    "type": request.type,
                    "reporter_profile_id": scope.profile_id,
                    "suggested_segment_ids": [row["segment_id"] for row in nearest],
                },
            )

            loaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
            )
            assert loaded is not None
            response = IncidentCreateResponse(
                incident=self._to_summary(loaded),
                upload_instructions=instructions,
                suggested_segments=[
                    SuggestedSegment(
                        segment_id=row["segment_id"],
                        name=row["name"],
                        road_class=row["road_class"],
                        distance_m=round(float(row["distance_m"]), 1),
                        passability=row["passability"],
                    )
                    for row in nearest
                ],
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

    async def add_attachment(
        self,
        *,
        scope: WorkspaceScope,
        incident_id: str,
        draft: AttachmentDraft,
        idempotency_key: str,
    ) -> AttachmentAddResponse:
        scope.require("incident:create")
        request_hash = canonical_hash(
            {"incident_id": incident_id, **draft.model_dump(mode="json")}
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
                return AttachmentAddResponse(**{**hit.result_payload, "replayed": True})

            loaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
                for_update=True,
            )
            if (
                loaded is None
                or loaded.row.get("reporter_profile_id") != scope.profile_id
            ):
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if loaded.row["status"] in ("confirmed", "rejected", "superseded"):
                raise ApiError(
                    409,
                    "incident_closed",
                    "This incident is no longer accepting evidence.",
                )
            live = [
                a
                for a in loaded.attachments
                if a["upload_status"] in ("pending", "uploaded", "verified")
            ]
            if len(live) >= MAX_ATTACHMENTS_PER_INCIDENT:
                raise ApiError(
                    422,
                    "too_many_attachments",
                    f"An incident may carry at most {MAX_ATTACHMENTS_PER_INCIDENT} evidence files.",
                )
            info, instruction = self._insert_attachment(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
                uploader_id=scope.profile_id,
                draft=draft,
            )
            response = AttachmentAddResponse(
                attachment=info, upload_instruction=instruction
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

    async def complete_attachment(
        self,
        *,
        scope: WorkspaceScope,
        incident_id: str,
        attachment_id: str,
        request: AttachmentCompleteRequest,
        idempotency_key: str,
    ) -> AttachmentCompleteResponse:
        scope.require("incident:create")
        request_hash = canonical_hash(
            {
                "incident_id": incident_id,
                "attachment_id": attachment_id,
                **request.model_dump(mode="json"),
            }
        )
        if not _is_uuid(attachment_id):
            raise ApiError(404, "not_found", "The requested resource was not found.")

        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return AttachmentCompleteResponse(
                    **{**hit.result_payload, "replayed": True}
                )

            loaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
                for_update=True,
            )
            if (
                loaded is None
                or loaded.row.get("reporter_profile_id") != scope.profile_id
            ):
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            attachment = next(
                (a for a in loaded.attachments if a["id"] == attachment_id), None
            )
            if attachment is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )

            if attachment["upload_status"] == "verified":
                verification = AttachmentVerification(status="already_verified")
            elif attachment["upload_status"] in ("rejected", "expired"):
                verification = AttachmentVerification(
                    status=attachment["upload_status"],
                    reason="This upload target is closed; request a new attachment.",
                )
            elif datetime.now(UTC) > attachment["created_at"] + timedelta(
                seconds=UPLOAD_WINDOW_SECONDS
            ):
                self._set_upload_status(
                    connection,
                    scope.organization_id,
                    attachment_id,
                    "expired",
                    uploaded_at=None,
                )
                verification = AttachmentVerification(
                    status="expired",
                    reason="The upload window has passed; request a new attachment.",
                )
            else:
                stored = await self._evidence_store.fetch(attachment["storage_key"])
                if stored is None:
                    verification = AttachmentVerification(
                        status="missing",
                        reason="No object was found at the authorised path.",
                    )
                elif stored.size_bytes > MAX_ATTACHMENT_BYTES:
                    self._set_upload_status(
                        connection,
                        scope.organization_id,
                        attachment_id,
                        "rejected",
                        uploaded_at=None,
                    )
                    verification = AttachmentVerification(
                        status="rejected", reason="Object exceeds the size limit."
                    )
                elif (
                    stored.sha256 != request.sha256
                    or stored.sha256 != attachment["sha256"]
                ):
                    self._set_upload_status(
                        connection,
                        scope.organization_id,
                        attachment_id,
                        "rejected",
                        uploaded_at=None,
                    )
                    verification = AttachmentVerification(
                        status="rejected",
                        reason="Checksum does not match the declared evidence.",
                    )
                elif stored.size_bytes != request.size_bytes:
                    self._set_upload_status(
                        connection,
                        scope.organization_id,
                        attachment_id,
                        "rejected",
                        uploaded_at=None,
                    )
                    verification = AttachmentVerification(
                        status="rejected",
                        reason="Content length does not match the declared size.",
                    )
                else:
                    self._set_upload_status(
                        connection,
                        scope.organization_id,
                        attachment_id,
                        "verified",
                        uploaded_at=datetime.now(UTC),
                        size_bytes=stored.size_bytes,
                    )
                    verification = AttachmentVerification(status="verified")

            self._audit(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                action="attachment.completed",
                entity_type="attachment",
                entity_id=attachment_id,
                before={"upload_status": attachment["upload_status"]},
                after={"upload_status": verification.status},
                metadata={"incident_id": incident_id, "reason": verification.reason},
            )
            reloaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
            )
            assert reloaded is not None
            current = next(a for a in reloaded.attachments if a["id"] == attachment_id)
            response = AttachmentCompleteResponse(
                attachment=AttachmentInfo(
                    **{k: v for k, v in current.items() if k != "created_at"}
                ),
                verification=verification,
            )
            record_ledger(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result_code=verification.status,
                result_payload=response.model_dump(mode="json"),
            )
            connection.commit()
        return response

    @staticmethod
    def _set_upload_status(
        connection: psycopg.Connection[Any],
        organization_id: str,
        attachment_id: str,
        status: str,
        *,
        uploaded_at: datetime | None,
        size_bytes: int | None = None,
    ) -> None:
        connection.execute(
            """
            update public.attachments
            set upload_status = %(status)s::public.attachment_upload_status,
                uploaded_at = %(uploaded_at)s,
                size_bytes = coalesce(%(size_bytes)s, size_bytes)
            where organization_id = %(organization_id)s::uuid and id = %(attachment_id)s::uuid
            """,
            {
                "status": status,
                "uploaded_at": uploaded_at,
                "size_bytes": size_bytes,
                "organization_id": organization_id,
                "attachment_id": attachment_id,
            },
        )

    async def review_incident(
        self,
        *,
        scope: WorkspaceScope,
        incident_id: str,
        request: IncidentReviewRequest,
        idempotency_key: str,
    ) -> IncidentReviewResponse:
        scope.require("incident:review")
        request_hash = canonical_hash(
            {"incident_id": incident_id, **request.model_dump(mode="json")}
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
                return IncidentReviewResponse(
                    **{**hit.result_payload, "replayed": True}
                )

            loaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
                for_update=True,
            )
            if loaded is None or not scope.allows_district(loaded.row["district_id"]):
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            row = loaded.row
            if row["version"] != request.expected_version:
                raise ApiError(
                    409,
                    "version_conflict",
                    "The incident changed since you loaded it; review the current state.",
                    details={
                        "current_version": row["version"],
                        "current_status": row["status"],
                    },
                )
            if row["status"] in ("confirmed", "rejected", "superseded"):
                raise ApiError(
                    409,
                    "incident_already_decided",
                    "This incident already has a final decision; create a new report to change the road state.",
                    details={"current_status": row["status"]},
                )
            if row.get("captured_at") is None or row.get("longitude") is None:
                raise ApiError(
                    422,
                    "insufficient_evidence",
                    "A decision requires a captured time and location on the report.",
                )

            decision = request.decision
            reviewed_at = datetime.now(UTC)
            impact: str | None = {
                "confirm_closure": "closure",
                "confirm_restriction": "restriction",
                "reopen": "monitor",
            }.get(decision)
            affected: list[dict[str, Any]] = []
            if impact is not None:
                if not request.affected_segment_ids:
                    raise ApiError(
                        422,
                        "validation_error",
                        "affected_segment_ids is required for this decision.",
                    )
                affected = self._segments_in_scope(
                    connection,
                    organization_id=scope.organization_id,
                    segment_ids=request.affected_segment_ids,
                )
                for segment in affected:
                    if segment["district_id"] != row["district_id"]:
                        raise ApiError(
                            422,
                            "validation_error",
                            "Affected segments must be in the incident's district.",
                            details={"segment_id": segment["id"]},
                        )
                if decision == "reopen" and row["type"] != "road_reopened":
                    raise ApiError(
                        422,
                        "validation_error",
                        "A reopen decision requires a road_reopened observation.",
                    )

            new_status = {
                "confirm_closure": "confirmed",
                "confirm_restriction": "confirmed",
                "reopen": "confirmed",
                "request_clarification": "under_review",
                "reject": "rejected",
            }[decision]

            audit_ids: list[str] = []
            outbox_ids: list[str] = []
            changes: list[SegmentStateChange] = []
            new_version: str | None = None

            for segment in affected:
                state_row = connection.execute(
                    """
                    select passability::text as passability, network_version, source_summary, as_of
                    from public.segment_current_state
                    where organization_id = %(organization_id)s::uuid and segment_id = %(segment_id)s::uuid
                    for update
                    """,
                    {
                        "organization_id": scope.organization_id,
                        "segment_id": segment["id"],
                    },
                ).fetchone()
                current = CurrentState(
                    passability=state_row["passability"] if state_row else "unknown",
                    network_version=state_row["network_version"]
                    if state_row
                    else segment["network_version"],
                    source_summary=dict(state_row["source_summary"])
                    if state_row
                    else {},
                    as_of=state_row["as_of"] if state_row else None,
                )
                reduced = reduce_segment_state(
                    current,
                    ReviewedDecision(
                        incident_id=incident_id,
                        incident_type=row["type"],
                        impact=impact,  # type: ignore[arg-type]
                        captured_at=row["captured_at"],
                        reviewed_at=reviewed_at,
                        reviewer_profile_id=scope.profile_id,
                        reason=request.reason,
                        base_graph_version=segment["network_version"],
                    ),
                )
                connection.execute(
                    """
                    insert into public.incident_segments (
                      organization_id, incident_id, segment_id, impact, reviewed_by_profile_id, reviewed_at
                    )
                    values (
                      %(organization_id)s::uuid, %(incident_id)s::uuid, %(segment_id)s::uuid,
                      %(impact)s::public.incident_impact, %(reviewer)s::uuid, %(reviewed_at)s
                    )
                    on conflict (organization_id, incident_id, segment_id) do update
                    set impact = excluded.impact,
                        reviewed_by_profile_id = excluded.reviewed_by_profile_id,
                        reviewed_at = excluded.reviewed_at
                    """,
                    {
                        "organization_id": scope.organization_id,
                        "incident_id": incident_id,
                        "segment_id": segment["id"],
                        "impact": impact,
                        "reviewer": scope.profile_id,
                        "reviewed_at": reviewed_at,
                    },
                )
                connection.execute(
                    """
                    insert into public.network_observations (
                      organization_id, segment_id, kind, passability, observed_at, source_id,
                      source_mode, confidence, payload
                    )
                    values (
                      %(organization_id)s::uuid, %(segment_id)s::uuid, 'field_report_reviewed',
                      %(passability)s::public.road_passability, %(observed_at)s, %(source_id)s,
                      'recorded', null, %(payload)s
                    )
                    """,
                    {
                        "organization_id": scope.organization_id,
                        "segment_id": segment["id"],
                        "passability": IMPACT_TO_PASSABILITY[impact],  # type: ignore[index]
                        "observed_at": row["captured_at"],
                        "source_id": f"incident:{incident_id}",
                        "payload": Jsonb(
                            {
                                "decision": decision,
                                "impact": impact,
                                "reason": request.reason,
                                "reviewer_profile_id": scope.profile_id,
                                "outcome": reduced.outcome,
                            }
                        ),
                    },
                )
                if reduced.outcome != "superseded_by_later_closure":
                    connection.execute(
                        """
                        insert into public.segment_current_state (
                          organization_id, segment_id, passability, risk_level, risk_score,
                          as_of, source_summary, network_version
                        )
                        values (
                          %(organization_id)s::uuid, %(segment_id)s::uuid,
                          %(passability)s::public.road_passability, 'unknown', null,
                          %(as_of)s, %(source_summary)s, %(network_version)s
                        )
                        on conflict (organization_id, segment_id) do update
                        set passability = excluded.passability,
                            as_of = excluded.as_of,
                            source_summary = excluded.source_summary,
                            network_version = excluded.network_version
                        """,
                        {
                            "organization_id": scope.organization_id,
                            "segment_id": segment["id"],
                            "passability": reduced.passability,
                            "as_of": reduced.as_of,
                            "source_summary": Jsonb(reduced.source_summary),
                            "network_version": reduced.network_version,
                        },
                    )
                else:
                    connection.execute(
                        """
                        update public.segment_current_state
                        set source_summary = %(source_summary)s
                        where organization_id = %(organization_id)s::uuid and segment_id = %(segment_id)s::uuid
                        """,
                        {
                            "organization_id": scope.organization_id,
                            "segment_id": segment["id"],
                            "source_summary": Jsonb(reduced.source_summary),
                        },
                    )
                if reduced.changed:
                    new_version = reduced.network_version
                    audit_ids.append(
                        self._audit(
                            connection,
                            organization_id=scope.organization_id,
                            actor_id=scope.profile_id,
                            action="segment.state_changed",
                            entity_type="road_segment",
                            entity_id=segment["id"],
                            before={
                                "passability": current.passability,
                                "network_version": current.network_version,
                            },
                            after={
                                "passability": reduced.passability,
                                "network_version": reduced.network_version,
                            },
                            metadata={
                                "incident_id": incident_id,
                                "impact": impact,
                                "reason": request.reason,
                            },
                        )
                    )
                    outbox_ids.append(
                        self._outbox(
                            connection,
                            organization_id=scope.organization_id,
                            event_type="segment.state_changed",
                            aggregate_id=segment["id"],
                            payload={
                                "segment_id": segment["id"],
                                "district_id": row["district_id"],
                                "incident_id": incident_id,
                                "previous_passability": current.passability,
                                "passability": reduced.passability,
                                "network_version": reduced.network_version,
                                "reviewed_at": reviewed_at.isoformat(),
                            },
                        )
                    )
                changes.append(
                    SegmentStateChange(
                        segment_id=segment["id"],
                        previous_passability=current.passability,
                        passability=reduced.passability,
                        network_version=reduced.network_version,
                        changed=reduced.changed,
                        outcome=reduced.outcome,
                    )
                )

            # A road nobody can drive is only half the decision.
            exposure = react_to_state_change(
                connection,
                organization_id=scope.organization_id,
                changes=[
                    SegmentChange(
                        segment_id=change.segment_id,
                        district_id=row["district_id"],
                        passability=change.passability,
                        previous_passability=change.previous_passability,
                        network_version=change.network_version,
                        incident_id=incident_id,
                    )
                    for change in changes
                    if change.changed
                ],
            )

            connection.execute(
                """
                update public.incidents
                set status = %(status)s::public.incident_status
                where organization_id = %(organization_id)s::uuid and id = %(incident_id)s::uuid
                """,
                {
                    "status": new_status,
                    "organization_id": scope.organization_id,
                    "incident_id": incident_id,
                },
            )
            review_record = {
                "decision": decision,
                "reason": request.reason,
                "reviewer_profile_id": scope.profile_id,
                "reviewed_at": reviewed_at.isoformat(),
                "affected_segment_ids": [segment["id"] for segment in affected],
                "network_version": new_version,
                "exposure": exposure.as_dict(),
            }
            audit_ids.insert(
                0,
                self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="incident.reviewed",
                    entity_type="incident",
                    entity_id=incident_id,
                    before={"status": row["status"], "version": row["version"]},
                    after={"status": new_status, "version": row["version"] + 1},
                    metadata={
                        "review": review_record,
                        "location_source": (row.get("last_audit") or {}).get(
                            "location_source"
                        ),
                    },
                ),
            )
            outbox_ids.insert(
                0,
                self._outbox(
                    connection,
                    organization_id=scope.organization_id,
                    event_type="incident.reviewed",
                    aggregate_id=incident_id,
                    payload={
                        "incident_id": incident_id,
                        "district_id": row["district_id"],
                        "reporter_profile_id": row.get("reporter_profile_id"),
                        **review_record,
                    },
                ),
            )

            reloaded = self._load_incident(
                connection,
                organization_id=scope.organization_id,
                incident_id=incident_id,
            )
            assert reloaded is not None
            response = IncidentReviewResponse(
                incident=self._to_summary(reloaded),
                segment_changes=changes,
                network_version=new_version,
                audit_event_ids=audit_ids,
                outbox_event_ids=outbox_ids,
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

            # After the commit, never inside it: a push goes to a third party and a
            # failed one must not be able to roll back a review or hold a lock.
            if self._push_sender is not None and exposure.alert_ids:
                try:
                    deliver_alerts_for(
                        connection,
                        organization_id=scope.organization_id,
                        sender=self._push_sender,
                        alert_ids=exposure.alert_ids,
                    )
                    connection.commit()
                except Exception:
                    # Attempt records are a convenience; losing them must not
                    # turn a completed review into an error for the reviewer.
                    connection.rollback()
        return response


def build_incident_repository(
    database_url: str | None,
    evidence_store: EvidenceStore,
    push_sender: PushSender | None = None,
) -> IncidentRepository | None:
    if not database_url:
        return None
    return PostgresIncidentRepository(database_url, evidence_store, push_sender)
