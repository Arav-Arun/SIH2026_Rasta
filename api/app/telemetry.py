"""Trip telemetry: device grants, batch ingestion, current location, staleness."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db
from app.errors import ApiError
from app.idempotency import canonical_hash, lookup_ledger, record_ledger
from app.scope import WorkspaceScope

# --- pilot defaults ---------------------------------------------------------

MAX_BATCH_POINTS = 20
"""Upload at most 20 samples per batch."""

MAX_ACCURACY_M = Decimal("100")
"""Worse than 100 m is a diagnostic count, not a position."""

MAX_SPEED_KPH = Decimal("160")
"""A reported or implied speed above this is a bad fix, not a vehicle."""

FUTURE_TOLERANCE = timedelta(minutes=2)
"""Device clocks drift. Beyond two minutes ahead the timestamp is not usable."""

STALE_AFTER = timedelta(minutes=5)
"""A position older than this is shown as stale rather than as current.

The tracker saves at least one sample a minute while moving, so five minutes
is roughly five missed reports: long enough not to flicker on one lost fix,
short enough that a dispatcher is not shown a stale dot as if it were live.
"""

GRANT_TTL = timedelta(hours=2)
"""A tracking credential outlives a delivery leg, not a working day."""

JUMP_FLOOR_M = 50.0
"""Below this, two fixes are GPS noise about the same spot, not a jump."""

HISTORY_MAX_POINTS = 1000
"""A history response is downsampled to at most this many points."""

TRACKING_TRIP_STATUSES = frozenset({"active", "paused"})
"""Tracking runs for a trip that has begun and has not finished."""


# --- contracts --------------------------------------------------------------


PointOutcome = Literal["accepted", "duplicate", "rejected"]


class TelemetryPointInput(BaseModel):
    """One position fix as the device recorded it."""

    client_point_id: str = Field(min_length=1, max_length=64)
    idempotency_key: uuid.UUID
    captured_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: Decimal = Field(gt=0, le=Decimal("100000"))
    speed_kph: Decimal | None = Field(default=None, ge=0, le=Decimal("1000"))
    heading: Decimal | None = Field(default=None, ge=0, lt=360)
    battery_percent: Decimal | None = Field(default=None, ge=0, le=100)


class TelemetryBatchRequest(BaseModel):
    """A batch names the trip and device it claims to come from."""

    trip_id: str
    device_id: str
    points: list[TelemetryPointInput] = Field(min_length=1, max_length=MAX_BATCH_POINTS)


class PointResult(BaseModel):
    client_point_id: str
    outcome: PointOutcome
    reason: str | None = None
    detail: str | None = None
    telemetry_point_id: str | None = None


class TripLocation(BaseModel):
    trip_id: str
    latitude: float
    longitude: float
    accuracy_m: Decimal
    as_of: datetime
    stale_after: datetime
    is_stale: bool
    speed_kph: Decimal | None = None
    heading: Decimal | None = None


class TelemetryBatchResponse(BaseModel):
    trip_id: str
    accepted: int
    duplicates: int
    rejected: int
    results: list[PointResult]
    current_location: TripLocation | None
    server_time: datetime
    stale_after_seconds: int
    replayed: bool = False


class TrackingGrantRequest(BaseModel):
    device_public_id: str = Field(min_length=8, max_length=160)


class TrackingPolicy(BaseModel):
    """What the device should do, stated by the server rather than hardcoded."""

    max_batch_points: int = MAX_BATCH_POINTS
    max_accuracy_m: Decimal = MAX_ACCURACY_M
    max_speed_kph: Decimal = MAX_SPEED_KPH
    min_movement_m: int = 25
    heartbeat_seconds: int = 60
    stale_after_seconds: int = int(STALE_AFTER.total_seconds())


class TripReadiness(BaseModel):
    """What a start check found, stated rather than assumed."""

    driver_assigned: bool
    vehicle_assigned: bool
    route_plan_id: str | None
    route_plan_status: str | None
    approved_route: bool
    notes: list[str]


class TrackingGrantResponse(BaseModel):
    token: str
    trip_id: str
    device_id: str
    issued_at: datetime
    expires_at: datetime
    policy: TrackingPolicy
    readiness: TripReadiness


class DeviceRegistrationRequest(BaseModel):
    device_public_id: str = Field(min_length=8, max_length=160)
    platform: Literal["android", "web"]
    display_label: str | None = Field(default=None, max_length=200)


class DeviceRegistration(BaseModel):
    id: str
    device_public_id: str
    platform: str
    display_label: str | None
    profile_id: str | None
    vehicle_id: str | None
    approved_at: datetime
    revoked_at: datetime | None


class DeviceListResponse(BaseModel):
    devices: list[DeviceRegistration]


class TelemetryHistoryPoint(BaseModel):
    id: str
    captured_at: datetime
    received_at: datetime
    latitude: float
    longitude: float
    accuracy_m: Decimal
    speed_kph: Decimal | None
    heading: Decimal | None


class TripTelemetryResponse(BaseModel):
    trip_id: str
    points: list[TelemetryHistoryPoint]
    total_points: int
    returned_points: int
    downsampled: bool
    sample_interval: int
    current_location: TripLocation | None
    server_time: datetime


class FleetTripLocation(BaseModel):
    trip_id: str
    consignment_id: str
    consignment_reference: str
    district_id: str
    vehicle_id: str
    vehicle_registration: str
    driver_id: str
    driver_name: str | None
    status: str
    started_at: datetime | None
    location: TripLocation | None
    reporting_state: Literal["live", "stale", "never_reported"]


class FleetLocationsResponse(BaseModel):
    trips: list[FleetTripLocation]
    live: int
    stale: int
    never_reported: int
    stale_after_seconds: int
    server_time: datetime


# --- tracking credentials ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrackingGrant:
    """A short-lived, trip-and-device-scoped credential."""

    organization_id: str
    trip_id: str
    device_id: str
    driver_profile_id: str
    issued_at: datetime
    expires_at: datetime


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_tracking_grant(
    secret: str,
    *,
    organization_id: str,
    trip_id: str,
    device_id: str,
    driver_profile_id: str,
    issued_at: datetime,
    ttl: timedelta = GRANT_TTL,
) -> tuple[str, TrackingGrant]:
    expires_at = issued_at + ttl
    claims = {
        "org": organization_id,
        "trip": trip_id,
        "device": device_id,
        "driver": driver_profile_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_hex(8),
    }
    body = _b64url(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    token = f"{body}.{_b64url(signature)}"
    grant = TrackingGrant(
        organization_id=organization_id,
        trip_id=trip_id,
        device_id=device_id,
        driver_profile_id=driver_profile_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return token, grant


def verify_tracking_grant(secret: str, token: str, *, now: datetime) -> TrackingGrant:
    """Return the grant a token carries, or refuse it."""

    refusal = ApiError(
        401, "tracking_grant_invalid", "This tracking credential is not usable."
    )
    parts = token.strip().split(".")
    if len(parts) != 2:
        raise refusal
    body, signature = parts
    try:
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64url(signature)):
            raise refusal
        claims = json.loads(_unb64url(body))
    except ApiError:
        raise
    except Exception as error:  # malformed base64, malformed JSON
        raise refusal from error

    try:
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
        grant = TrackingGrant(
            organization_id=str(claims["org"]),
            trip_id=str(claims["trip"]),
            device_id=str(claims["device"]),
            driver_profile_id=str(claims["driver"]),
            issued_at=issued_at,
            expires_at=expires_at,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise refusal from error

    if now >= grant.expires_at:
        raise refusal
    return grant


# --- point validation -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TripWindow:
    """When a trip was running, as the trip record states it."""

    status: str
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class Fix:
    """The parts of an accepted point that judging the next one needs."""

    captured_at: datetime
    latitude: float
    longitude: float


REJECTION_DETAIL: dict[str, str] = {
    "accuracy_too_poor": "The fix was less accurate than the 100 m the pilot accepts.",
    "captured_before_trip_start": "The point was captured before this trip started.",
    "captured_after_trip_end": "The point was captured after this trip ended.",
    "captured_in_future": "The capture time is ahead of server time by more than two minutes.",
    "impossible_speed": "The reported speed is above 160 km/h, so the fix is not usable.",
    "implausible_jump": "Reaching this point from the previous one would need more than 160 km/h.",
    "trip_not_tracking": "This trip is not running, so it is not accepting positions.",
}


def haversine_metres(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Great-circle distance. Good enough at the scale of one district."""

    radius = 6371008.8
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def judge_point(
    point: TelemetryPointInput,
    *,
    window: TripWindow,
    previous: Fix | None,
    now: datetime,
) -> str | None:
    """Return a rejection reason, or ``None`` when the point is usable."""

    if window.status not in TRACKING_TRIP_STATUSES:
        return "trip_not_tracking"
    if point.accuracy_m > MAX_ACCURACY_M:
        return "accuracy_too_poor"
    if point.captured_at > now + FUTURE_TOLERANCE:
        return "captured_in_future"
    if window.started_at is not None and point.captured_at < window.started_at:
        return "captured_before_trip_start"
    if window.ended_at is not None and point.captured_at > window.ended_at:
        return "captured_after_trip_end"
    if point.speed_kph is not None and point.speed_kph > MAX_SPEED_KPH:
        return "impossible_speed"

    if previous is not None:
        elapsed = (point.captured_at - previous.captured_at).total_seconds()
        if elapsed > 0:
            distance = haversine_metres(
                previous.latitude, previous.longitude, point.latitude, point.longitude
            )
            # Two fixes metres apart are the same parked vehicle seen twice,
            # however little time separates them.
            if distance > JUMP_FLOOR_M:
                implied_kph = (distance / elapsed) * 3.6
                if implied_kph > float(MAX_SPEED_KPH):
                    return "implausible_jump"
    return None


def downsample(count: int, limit: int = HISTORY_MAX_POINTS) -> int:
    """Return the stride that keeps a history under ``limit`` points."""

    if count <= limit or limit <= 0:
        return 1
    return math.ceil(count / limit)


def classify_reporting_state(
    *, as_of: datetime | None, now: datetime, stale_after: datetime | None
) -> Literal["live", "stale", "never_reported"]:
    if as_of is None or stale_after is None:
        return "never_reported"
    return "stale" if now > stale_after else "live"


# --- repository -------------------------------------------------------------


TRIP_READ_CAPABILITIES = ("consignment:read", "consignment:manage", "fleet:read")


class TelemetryRepository(Protocol):
    async def register_device(
        self, *, scope: WorkspaceScope, request: DeviceRegistrationRequest
    ) -> DeviceRegistration: ...

    async def list_devices(self, *, scope: WorkspaceScope) -> DeviceListResponse: ...

    async def issue_grant(
        self, *, scope: WorkspaceScope, trip_id: str, request: TrackingGrantRequest
    ) -> TrackingGrantResponse: ...

    async def ingest_batch(
        self,
        *,
        token: str,
        request: TelemetryBatchRequest,
        idempotency_key: str,
    ) -> TelemetryBatchResponse: ...

    async def trip_history(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> TripTelemetryResponse: ...

    async def fleet_locations(
        self, *, scope: WorkspaceScope, district_id: str | None
    ) -> FleetLocationsResponse: ...


def _now() -> datetime:
    return datetime.now(tz=UTC)


class PostgresTelemetryRepository:
    def __init__(self, database_url: str, *, grant_secret: str) -> None:
        self._database_url = database_url
        self._grant_secret = grant_secret

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    def _audit(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        actor_id: str | None,
        action: str,
        entity_type: str,
        entity_id: str,
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
                "before_hash": canonical_hash(before) if before is not None else None,
                "after_hash": canonical_hash(after) if after is not None else None,
                "metadata": Jsonb(metadata),
            },
        ).fetchone()
        return row["id"]

    # -- devices -------------------------------------------------------------

    _DEVICE_SELECT = """
        select
          id::text as id,
          device_public_id,
          platform::text as platform,
          display_label,
          profile_id::text as profile_id,
          vehicle_id::text as vehicle_id,
          approved_at,
          revoked_at
        from public.device_registrations
    """

    async def register_device(
        self, *, scope: WorkspaceScope, request: DeviceRegistrationRequest
    ) -> DeviceRegistration:
        """Register the caller's own device. A device belongs to a person."""

        scope.require("telemetry:submit")
        with self._connect() as connection, connection.transaction():
            row = connection.execute(
                """
                insert into public.device_registrations (
                  organization_id, profile_id, platform, device_public_id, display_label
                )
                values (
                  %(org)s::uuid, %(profile)s::uuid, %(platform)s::public.device_platform,
                  %(public_id)s, %(label)s
                )
                on conflict (organization_id, device_public_id) do update
                  set display_label = excluded.display_label,
                      profile_id = excluded.profile_id,
                      revoked_at = null,
                      updated_at = now(),
                      version = public.device_registrations.version + 1
                returning id::text as id
                """,
                {
                    "org": scope.organization_id,
                    "profile": scope.profile_id,
                    "platform": request.platform,
                    "public_id": request.device_public_id,
                    "label": request.display_label,
                },
            ).fetchone()
            stored = connection.execute(
                self._DEVICE_SELECT + " where id = %(id)s::uuid", {"id": row["id"]}
            ).fetchone()
            self._audit(
                connection,
                organization_id=scope.organization_id,
                actor_id=scope.profile_id,
                action="device.registered",
                entity_type="device_registration",
                entity_id=row["id"],
                before=None,
                after={"platform": request.platform},
                metadata={"platform": request.platform},
            )
        return DeviceRegistration(**stored)

    async def list_devices(self, *, scope: WorkspaceScope) -> DeviceListResponse:
        scope.require("telemetry:submit")
        with self._connect() as connection:
            rows = connection.execute(
                self._DEVICE_SELECT
                + """
                where organization_id = %(org)s::uuid
                  and profile_id = %(profile)s::uuid
                order by approved_at desc
                """,
                {"org": scope.organization_id, "profile": scope.profile_id},
            ).fetchall()
        return DeviceListResponse(devices=[DeviceRegistration(**row) for row in rows])

    # -- grants --------------------------------------------------------------

    _TRIP_SELECT = """
        select
          t.id::text as id,
          t.organization_id::text as organization_id,
          t.district_id::text as district_id,
          t.consignment_id::text as consignment_id,
          t.vehicle_id::text as vehicle_id,
          t.driver_id::text as driver_id,
          t.status::text as status,
          t.route_plan_id::text as route_plan_id,
          t.started_at,
          t.ended_at,
          p.status::text as route_plan_status
        from public.trips as t
        left join public.route_plans as p
          on p.id = t.route_plan_id and p.organization_id = t.organization_id
    """

    def _read_trip(
        self, connection: psycopg.Connection, *, organization_id: str, trip_id: str
    ) -> dict[str, Any] | None:
        try:
            uuid.UUID(trip_id)
        except ValueError:
            return None
        return connection.execute(
            self._TRIP_SELECT
            + " where t.id = %(id)s::uuid and t.organization_id = %(org)s::uuid",
            {"id": trip_id, "org": organization_id},
        ).fetchone()

    @staticmethod
    def _readiness(row: dict[str, Any]) -> TripReadiness:
        notes: list[str] = []
        approved = row["route_plan_status"] == "approved"
        if row["route_plan_id"] is None:
            notes.append(
                "No route plan is bound to this trip, so the driver has no "
                "approved route to follow."
            )
        elif not approved:
            notes.append(
                f"The bound route plan is {row['route_plan_status']}, not approved."
            )
        return TripReadiness(
            driver_assigned=row["driver_id"] is not None,
            vehicle_assigned=row["vehicle_id"] is not None,
            route_plan_id=row["route_plan_id"],
            route_plan_status=row["route_plan_status"],
            approved_route=approved,
            notes=notes,
        )

    async def issue_grant(
        self, *, scope: WorkspaceScope, trip_id: str, request: TrackingGrantRequest
    ) -> TrackingGrantResponse:
        """Hand the driver's own device a credential for their own trip."""

        scope.require("telemetry:submit")
        with self._connect() as connection:
            row = self._read_trip(
                connection, organization_id=scope.organization_id, trip_id=trip_id
            )
            if row is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if row["driver_id"] != scope.profile_id:
                raise ApiError(
                    403, "forbidden", "Only the assigned driver can track this trip."
                )
            if row["status"] not in TRACKING_TRIP_STATUSES:
                raise ApiError(
                    409,
                    "trip_not_tracking",
                    "Tracking starts when the trip starts and stops when it ends.",
                )

            device = connection.execute(
                self._DEVICE_SELECT
                + """
                where organization_id = %(org)s::uuid
                  and device_public_id = %(public_id)s
                """,
                {
                    "org": scope.organization_id,
                    "public_id": request.device_public_id,
                },
            ).fetchone()
            if device is None:
                raise ApiError(
                    404, "device_not_registered", "Register this device first."
                )
            if device["revoked_at"] is not None:
                raise ApiError(
                    403, "device_revoked", "This device may no longer report."
                )
            if device["profile_id"] != scope.profile_id:
                raise ApiError(
                    403, "forbidden", "That device belongs to somebody else."
                )

            issued_at = _now()
            token, grant = issue_tracking_grant(
                self._grant_secret,
                organization_id=scope.organization_id,
                trip_id=row["id"],
                device_id=device["id"],
                driver_profile_id=scope.profile_id,
                issued_at=issued_at,
            )
            with connection.transaction():
                self._audit(
                    connection,
                    organization_id=scope.organization_id,
                    actor_id=scope.profile_id,
                    action="trip.tracking_grant_issued",
                    entity_type="trip",
                    entity_id=row["id"],
                    before=None,
                    after={"device_id": device["id"]},
                    metadata={
                        "device_id": device["id"],
                        "expires_at": grant.expires_at.isoformat(),
                    },
                )

        return TrackingGrantResponse(
            token=token,
            trip_id=grant.trip_id,
            device_id=grant.device_id,
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
            policy=TrackingPolicy(),
            readiness=self._readiness(row),
        )

    # -- ingestion -----------------------------------------------------------

    _LOCATION_SELECT = """
        select
          l.trip_id::text as trip_id,
          extensions.st_y(l.location)::float8 as latitude,
          extensions.st_x(l.location)::float8 as longitude,
          l.as_of,
          l.stale_after,
          p.accuracy_m,
          p.speed_kph,
          p.heading
        from public.trip_current_location as l
        join public.telemetry_points as p
          on p.id = l.telemetry_point_id and p.organization_id = l.organization_id
    """

    def _current_location(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        trip_id: str,
        now: datetime,
    ) -> TripLocation | None:
        row = connection.execute(
            self._LOCATION_SELECT
            + """
            where l.organization_id = %(org)s::uuid and l.trip_id = %(trip)s::uuid
            """,
            {"org": organization_id, "trip": trip_id},
        ).fetchone()
        if row is None:
            return None
        return TripLocation(**row, is_stale=now > row["stale_after"])

    def _anchor_fix(
        self,
        connection: psycopg.Connection,
        *,
        organization_id: str,
        trip_id: str,
        before: datetime,
    ) -> Fix | None:
        """The last accepted point at or before ``before``."""

        row = connection.execute(
            """
            select
              captured_at,
              extensions.st_y(location)::float8 as latitude,
              extensions.st_x(location)::float8 as longitude
            from public.telemetry_points
            where organization_id = %(org)s::uuid
              and trip_id = %(trip)s::uuid
              and captured_at <= %(before)s
            order by captured_at desc
            limit 1
            """,
            {"org": organization_id, "trip": trip_id, "before": before},
        ).fetchone()
        if row is None:
            return None
        return Fix(
            captured_at=row["captured_at"],
            latitude=row["latitude"],
            longitude=row["longitude"],
        )

    async def ingest_batch(
        self,
        *,
        token: str,
        request: TelemetryBatchRequest,
        idempotency_key: str,
    ) -> TelemetryBatchResponse:
        now = _now()
        grant = verify_tracking_grant(self._grant_secret, token, now=now)

        # The body states which trip and device it claims to be. The credential
        # decides. A mismatch is the wrong-trip case the acceptance requires.
        if request.trip_id != grant.trip_id or request.device_id != grant.device_id:
            raise ApiError(
                403,
                "tracking_grant_mismatch",
                "This credential does not cover that trip and device.",
            )

        request_hash = canonical_hash(request.model_dump(mode="json"))
        with self._connect() as connection:
            hit = lookup_ledger(
                connection,
                organization_id=grant.organization_id,
                actor_id=grant.driver_profile_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if hit is not None:
                return TelemetryBatchResponse(
                    **{**hit.result_payload, "replayed": True}
                )

            trip = self._read_trip(
                connection,
                organization_id=grant.organization_id,
                trip_id=grant.trip_id,
            )
            if trip is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            device = connection.execute(
                self._DEVICE_SELECT + " where id = %(id)s::uuid",
                {"id": grant.device_id},
            ).fetchone()
            if device is None or device["revoked_at"] is not None:
                raise ApiError(
                    403, "device_revoked", "This device may no longer report."
                )

            window = TripWindow(
                status=trip["status"],
                started_at=trip["started_at"],
                ended_at=trip["ended_at"],
            )
            ordered = sorted(request.points, key=lambda p: p.captured_at)
            previous = self._anchor_fix(
                connection,
                organization_id=grant.organization_id,
                trip_id=grant.trip_id,
                before=ordered[0].captured_at,
            )

            results: list[PointResult] = []
            accepted_rows: list[tuple[str, TelemetryPointInput]] = []
            with connection.transaction():
                for point in ordered:
                    reason = judge_point(
                        point, window=window, previous=previous, now=now
                    )
                    if reason is not None:
                        results.append(
                            PointResult(
                                client_point_id=point.client_point_id,
                                outcome="rejected",
                                reason=reason,
                                detail=REJECTION_DETAIL[reason],
                            )
                        )
                        continue

                    inserted = connection.execute(
                        """
                        insert into public.telemetry_points (
                          organization_id, trip_id, device_id, captured_at, location,
                          accuracy_m, speed_kph, heading, battery_percent, idempotency_key
                        )
                        values (
                          %(org)s::uuid, %(trip)s::uuid, %(device)s::uuid, %(captured)s,
                          extensions.st_setsrid(
                            extensions.st_makepoint(%(lon)s, %(lat)s), 4326
                          ),
                          %(accuracy)s, %(speed)s, %(heading)s, %(battery)s, %(key)s::uuid
                        )
                        on conflict (organization_id, idempotency_key) do nothing
                        returning id::text as id
                        """,
                        {
                            "org": grant.organization_id,
                            "trip": grant.trip_id,
                            "device": grant.device_id,
                            "captured": point.captured_at,
                            "lon": point.longitude,
                            "lat": point.latitude,
                            "accuracy": point.accuracy_m,
                            "speed": point.speed_kph,
                            "heading": point.heading,
                            "battery": point.battery_percent,
                            "key": str(point.idempotency_key),
                        },
                    ).fetchone()

                    if inserted is None:
                        # The key is already stored.
                        results.append(
                            PointResult(
                                client_point_id=point.client_point_id,
                                outcome="duplicate",
                                reason="already_recorded",
                                detail="This point was already stored; the first one was kept.",
                            )
                        )
                        continue

                    results.append(
                        PointResult(
                            client_point_id=point.client_point_id,
                            outcome="accepted",
                            telemetry_point_id=inserted["id"],
                        )
                    )
                    accepted_rows.append((inserted["id"], point))
                    previous = Fix(
                        captured_at=point.captured_at,
                        latitude=point.latitude,
                        longitude=point.longitude,
                    )

                if accepted_rows:
                    newest_id, newest = max(
                        accepted_rows, key=lambda entry: entry[1].captured_at
                    )
                    # Forward only. A late upload lands in the timeline but
                    # never drags the current position back in time.
                    connection.execute(
                        """
                        insert into public.trip_current_location (
                          organization_id, trip_id, telemetry_point_id, location,
                          as_of, stale_after
                        )
                        values (
                          %(org)s::uuid, %(trip)s::uuid, %(point)s::uuid,
                          extensions.st_setsrid(
                            extensions.st_makepoint(%(lon)s, %(lat)s), 4326
                          ),
                          %(as_of)s, %(stale_after)s
                        )
                        on conflict (organization_id, trip_id) do update
                        set telemetry_point_id = excluded.telemetry_point_id,
                            location = excluded.location,
                            as_of = excluded.as_of,
                            stale_after = excluded.stale_after,
                            updated_at = now(),
                            version = public.trip_current_location.version + 1
                        where public.trip_current_location.as_of < excluded.as_of
                        """,
                        {
                            "org": grant.organization_id,
                            "trip": grant.trip_id,
                            "point": newest_id,
                            "lon": newest.longitude,
                            "lat": newest.latitude,
                            "as_of": newest.captured_at,
                            "stale_after": newest.captured_at + STALE_AFTER,
                        },
                    )

                counts = {
                    "accepted": sum(1 for r in results if r.outcome == "accepted"),
                    "duplicates": sum(1 for r in results if r.outcome == "duplicate"),
                    "rejected": sum(1 for r in results if r.outcome == "rejected"),
                }
                rejection_reasons: dict[str, int] = {}
                for result in results:
                    if result.outcome == "rejected" and result.reason:
                        rejection_reasons[result.reason] = (
                            rejection_reasons.get(result.reason, 0) + 1
                        )
                self._audit(
                    connection,
                    organization_id=grant.organization_id,
                    actor_id=grant.driver_profile_id,
                    action="telemetry.batch_ingested",
                    entity_type="trip",
                    entity_id=grant.trip_id,
                    before=None,
                    after=counts,
                    # Counts and reason codes only. No coordinate is written to
                    # the audit trail; the points themselves are the record.
                    metadata={
                        **counts,
                        "device_id": grant.device_id,
                        "rejection_reasons": rejection_reasons,
                    },
                )

                response = TelemetryBatchResponse(
                    trip_id=grant.trip_id,
                    **counts,
                    results=results,
                    current_location=self._current_location(
                        connection,
                        organization_id=grant.organization_id,
                        trip_id=grant.trip_id,
                        now=now,
                    ),
                    server_time=now,
                    stale_after_seconds=int(STALE_AFTER.total_seconds()),
                )
                record_ledger(
                    connection,
                    organization_id=grant.organization_id,
                    actor_id=grant.driver_profile_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    result_code="accepted",
                    result_payload=response.model_dump(mode="json"),
                )

        return response

    # -- reads ---------------------------------------------------------------

    def _require_trip_read(self, scope: WorkspaceScope, trip: dict[str, Any]) -> None:
        """A driver reads their own trip; a dispatcher reads their district's."""

        if trip["driver_id"] == scope.profile_id and scope.has("trip:read"):
            return
        if any(
            scope.has(capability) for capability in TRIP_READ_CAPABILITIES
        ) and scope.allows_district(trip["district_id"]):
            return
        raise ApiError(403, "forbidden", "You cannot read this trip.")

    async def trip_history(
        self,
        *,
        scope: WorkspaceScope,
        trip_id: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> TripTelemetryResponse:
        now = _now()
        with self._connect() as connection:
            trip = self._read_trip(
                connection, organization_id=scope.organization_id, trip_id=trip_id
            )
            if trip is None:
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            self._require_trip_read(scope, trip)

            window = {
                "org": scope.organization_id,
                "trip": trip["id"],
                "start": start,
                "end": end,
            }
            total = connection.execute(
                """
                select count(*)::int as total
                from public.telemetry_points
                where organization_id = %(org)s::uuid
                  and trip_id = %(trip)s::uuid
                  and (%(start)s::timestamptz is null or captured_at >= %(start)s)
                  and (%(end)s::timestamptz is null or captured_at <= %(end)s)
                """,
                window,
            ).fetchone()["total"]

            stride = downsample(total, limit)
            rows = connection.execute(
                """
                with ordered as (
                  select
                    id::text as id,
                    captured_at,
                    received_at,
                    extensions.st_y(location)::float8 as latitude,
                    extensions.st_x(location)::float8 as longitude,
                    accuracy_m,
                    speed_kph,
                    heading,
                    row_number() over (order by captured_at, id) as position,
                    count(*) over () as total
                  from public.telemetry_points
                  where organization_id = %(org)s::uuid
                    and trip_id = %(trip)s::uuid
                    and (%(start)s::timestamptz is null or captured_at >= %(start)s)
                    and (%(end)s::timestamptz is null or captured_at <= %(end)s)
                )
                select
                  id, captured_at, received_at, latitude, longitude,
                  accuracy_m, speed_kph, heading
                from ordered
                -- Keep an evenly spaced sample, and always keep the last fix:
                -- a thinned track that stops short of where the vehicle
                -- actually reached would be worse than no track.
                where (position - 1) %% %(stride)s = 0 or position = total
                order by captured_at, id
                """,
                {**window, "stride": stride},
            ).fetchall()

        points = [TelemetryHistoryPoint(**row) for row in rows]
        with self._connect() as connection:
            current = self._current_location(
                connection,
                organization_id=scope.organization_id,
                trip_id=trip["id"],
                now=now,
            )
        return TripTelemetryResponse(
            trip_id=trip["id"],
            points=points,
            total_points=total,
            returned_points=len(points),
            downsampled=stride > 1,
            sample_interval=stride,
            current_location=current,
            server_time=now,
        )

    async def fleet_locations(
        self, *, scope: WorkspaceScope, district_id: str | None
    ) -> FleetLocationsResponse:
        """Where every trip the caller may see has most recently reported."""

        if not any(scope.has(capability) for capability in TRIP_READ_CAPABILITIES):
            raise ApiError(
                403, "scope_denied", "You do not have access to this action."
            )
        if district_id is not None:
            scope.require_district(district_id)

        now = _now()
        districts = None if scope.district_ids is None else list(scope.district_ids)
        with self._connect() as connection:
            rows = connection.execute(
                """
                select
                  t.id::text as trip_id,
                  t.consignment_id::text as consignment_id,
                  c.reference as consignment_reference,
                  t.district_id::text as district_id,
                  t.vehicle_id::text as vehicle_id,
                  v.registration_ref as vehicle_registration,
                  t.driver_id::text as driver_id,
                  d.display_name as driver_name,
                  t.status::text as status,
                  t.started_at,
                  l.as_of,
                  l.stale_after,
                  extensions.st_y(l.location)::float8 as latitude,
                  extensions.st_x(l.location)::float8 as longitude,
                  p.accuracy_m,
                  p.speed_kph,
                  p.heading
                from public.trips as t
                join public.consignments as c
                  on c.id = t.consignment_id and c.organization_id = t.organization_id
                join public.vehicles as v
                  on v.id = t.vehicle_id and v.organization_id = t.organization_id
                join public.profiles as d
                  on d.id = t.driver_id and d.organization_id = t.organization_id
                left join public.trip_current_location as l
                  on l.trip_id = t.id and l.organization_id = t.organization_id
                left join public.telemetry_points as p
                  on p.id = l.telemetry_point_id and p.organization_id = l.organization_id
                where t.organization_id = %(org)s::uuid
                  and t.status in ('active', 'paused')
                  and (%(districts)s::uuid[] is null
                       or t.district_id = any(%(districts)s::uuid[]))
                  and (%(district)s::uuid is null or t.district_id = %(district)s::uuid)
                order by l.as_of desc nulls last, t.started_at desc nulls last
                """,
                {
                    "org": scope.organization_id,
                    "districts": districts,
                    "district": district_id,
                },
            ).fetchall()

        trips: list[FleetTripLocation] = []
        counts = {"live": 0, "stale": 0, "never_reported": 0}
        for row in rows:
            state = classify_reporting_state(
                as_of=row["as_of"], now=now, stale_after=row["stale_after"]
            )
            counts[state] += 1
            location = (
                None
                if state == "never_reported"
                else TripLocation(
                    trip_id=row["trip_id"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    accuracy_m=row["accuracy_m"],
                    as_of=row["as_of"],
                    stale_after=row["stale_after"],
                    is_stale=state == "stale",
                    speed_kph=row["speed_kph"],
                    heading=row["heading"],
                )
            )
            trips.append(
                FleetTripLocation(
                    trip_id=row["trip_id"],
                    consignment_id=row["consignment_id"],
                    consignment_reference=row["consignment_reference"],
                    district_id=row["district_id"],
                    vehicle_id=row["vehicle_id"],
                    vehicle_registration=row["vehicle_registration"],
                    driver_id=row["driver_id"],
                    driver_name=row["driver_name"],
                    status=row["status"],
                    started_at=row["started_at"],
                    location=location,
                    reporting_state=state,
                )
            )

        return FleetLocationsResponse(
            trips=trips,
            **counts,
            stale_after_seconds=int(STALE_AFTER.total_seconds()),
            server_time=now,
        )


def build_telemetry_repository(
    database_url: str | None, *, grant_secret: str
) -> TelemetryRepository | None:
    if not database_url:
        return None
    return PostgresTelemetryRepository(database_url, grant_secret=grant_secret)


__all__ = [
    "DeviceListResponse",
    "DeviceRegistration",
    "DeviceRegistrationRequest",
    "FleetLocationsResponse",
    "PostgresTelemetryRepository",
    "TelemetryBatchRequest",
    "TelemetryBatchResponse",
    "TelemetryRepository",
    "TrackingGrantRequest",
    "TrackingGrantResponse",
    "TripTelemetryResponse",
    "build_telemetry_repository",
    "issue_tracking_grant",
    "judge_point",
    "verify_tracking_grant",
]
