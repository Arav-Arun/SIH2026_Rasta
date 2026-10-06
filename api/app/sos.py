"""An SOS from the field: a critical alert to the control room, alongside 112.

It tells the people who dispatch and coordinate the district that someone needs
help, and where and when the phone last knew its position. It does not call
emergency services: the message to 112 does that, and the person sends it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from pydantic import BaseModel, Field, model_validator

from app.alerts import AlertCandidate, raise_alert
from app.errors import ApiError
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.scope import WorkspaceScope

#: Who hears an SOS: everyone who dispatches or coordinates in the district.
RECIPIENT_CAPABILITIES = ("route:plan", "incident:review")
#: Long enough to be seen and acted on; a repeat within the hour updates it.
VALIDITY = timedelta(hours=6)
#: A phone's clock may run a little fast; one far in the future is wrong.
CLOCK_SKEW = timedelta(minutes=10)


class SosRequest(BaseModel):
    #: When the person pressed SOS, by the phone's clock. A request queued while
    #: offline arrives later and keeps this time.
    captured_at: datetime
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, gt=0, le=100_000)
    trip_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def position_is_whole(self) -> SosRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude come together or not at all")
        if self.accuracy_m is not None and self.latitude is None:
            raise ValueError("an accuracy needs a position")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at needs a time zone")
        return self


class SosResponse(BaseModel):
    alert_id: str
    #: How many people the alert reached.
    recipients: int
    received_at: datetime
    #: Stated on every response so that no screen can suggest otherwise.
    calls_emergency_services: bool = False


def _district(
    connection: psycopg.Connection, scope: WorkspaceScope, trip_id: UUID | None
) -> str | None:
    """The trip's district, else the caller's only district, else none (all)."""

    if trip_id is not None:
        row = connection.execute(
            """
            select district_id::text as district_id
            from public.trips
            where id = %s::uuid and organization_id = %s::uuid
            """,
            (str(trip_id), scope.organization_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "trip_not_found", "That trip does not exist here.")
        scope.require_district(row["district_id"])
        return row["district_id"]
    if scope.district_ids is not None and len(scope.district_ids) == 1:
        return next(iter(scope.district_ids))
    # Several districts or none in particular: better that everyone who
    # coordinates hears it than that the right person does not.
    return None


def raise_sos(
    connection: psycopg.Connection,
    *,
    scope: WorkspaceScope,
    request: SosRequest,
    idempotency_key: str,
    now: datetime | None = None,
) -> SosResponse:
    """Record the SOS as a critical alert and fan it out, once per request."""

    moment = now or datetime.now(tz=UTC)
    if request.captured_at > moment + CLOCK_SKEW:
        raise ApiError(
            422,
            "invalid_value",
            "The SOS time is in the future by this server's clock.",
        )
    body = request.model_dump(mode="json")
    request_hash = canonical_hash(body)
    replay = lookup_ledger(
        connection,
        organization_id=scope.organization_id,
        actor_id=scope.profile_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return SosResponse(**replay.result_payload)

    district_id = _district(connection, scope, request.trip_id)
    reporter = connection.execute(
        "select display_name from public.profiles where id = %s::uuid "
        "and organization_id = %s::uuid",
        (scope.profile_id, scope.organization_id),
    ).fetchone()
    raised = raise_alert(
        connection,
        organization_id=scope.organization_id,
        candidate=AlertCandidate(
            alert_type="sos",
            severity="critical",
            title_key="alert.sos",
            subject_type="profile",
            subject_id=scope.profile_id,
            district_id=district_id,
            payload={
                "reporter_name": reporter["display_name"] if reporter else None,
                "captured_at": body["captured_at"],
                "position_known": request.latitude is not None,
                "latitude": request.latitude,
                "longitude": request.longitude,
                "accuracy_m": request.accuracy_m,
                "trip_id": body["trip_id"],
                "note": request.note,
                "calls_emergency_services": False,
            },
            recipient_capabilities=RECIPIENT_CAPABILITIES,
            valid_for=VALIDITY,
        ),
        now=moment,
    )
    response = SosResponse(
        alert_id=raised["alert_id"],
        recipients=raised["recipients"],
        received_at=moment,
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
    return response


__all__ = ["SosRequest", "SosResponse", "raise_sos"]
