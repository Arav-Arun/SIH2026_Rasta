"""Persisted operational alerts and the recipient inbox."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.capabilities import ROLE_CAPABILITIES
from app.errors import ApiError
from app.idempotency import canonical_hash
from app.scope import WorkspaceScope

AlertSeverity = Literal["info", "warning", "critical"]

# The types this system raises. Keeping them closed means an inbox can explain
# every row it shows rather than rendering whatever string arrived.
ALERT_TYPES = (
    "road_closed",
    "road_restricted",
    "facility_isolated",
    "trip_route_invalidated",
    "trip_position_stale",
    "delivery_shortfall",
    "source_unavailable",
)
AlertType = Literal[
    "road_closed",
    "road_restricted",
    "facility_isolated",
    "trip_route_invalidated",
    "trip_position_stale",
    "delivery_shortfall",
    "source_unavailable",
]

DEFAULT_VALIDITY = timedelta(hours=12)

#: An alert about a subject that keeps being re-confirmed should update one row, not
#: create a new one every time.
DEDUPE_BUCKET = timedelta(hours=1)


def dedupe_key(
    *,
    alert_type: str,
    subject_type: str,
    subject_id: str,
    valid_from: datetime,
    bucket: timedelta = DEDUPE_BUCKET,
) -> str:
    """A stable key for "the same emergency, still going on"."""

    seconds = int(bucket.total_seconds())
    stamp = int(valid_from.timestamp()) // seconds * seconds
    return f"{alert_type}:{subject_type}:{subject_id}:{stamp}"


# --- contracts --------------------------------------------------------------


class AlertRecord(BaseModel):
    id: str
    type: str
    severity: AlertSeverity
    title_key: str
    district_id: str | None
    #: What the alert is about, stated the same way for every type, so a reader
    #: can link to it without knowing each payload's shape.
    subject_type: str | None
    subject_id: str | None
    payload: dict[str, Any]
    valid_from: datetime
    valid_until: datetime | None
    dedupe_key: str
    created_at: str | datetime
    #: The calling recipient's own copy. Two people see different values here.
    status: str
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    #: False once ``valid_until`` has passed; an expired alert is shown as
    #: expired rather than hidden, so a stale inbox is visibly stale.
    valid_now: bool


class AlertListResponse(BaseModel):
    alerts: list[AlertRecord]
    unacknowledged: int
    total: int
    as_of: datetime


class AlertAcknowledgeResponse(BaseModel):
    alert_id: str
    status: str
    acknowledged_at: datetime | None
    already_acknowledged: bool


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    """An alert something in the domain decided to raise."""

    alert_type: AlertType
    severity: AlertSeverity
    title_key: str
    subject_type: str
    subject_id: str
    district_id: str | None
    payload: dict[str, Any]
    #: Capabilities any one of which makes a profile a recipient.
    recipient_capabilities: tuple[str, ...] = ("alert:read",)
    #: Profiles that must receive it whatever their capabilities, such as the
    #: driver of the affected trip.
    explicit_profile_ids: tuple[str, ...] = ()
    valid_for: timedelta = DEFAULT_VALIDITY


def roles_with_capability(capabilities: tuple[str, ...]) -> list[str]:
    """Which roles hold any of these capabilities."""

    wanted = set(capabilities)
    return sorted(
        role for role, granted in ROLE_CAPABILITIES.items() if wanted & set(granted)
    )


class AlertRepository(Protocol):
    async def list_alerts(
        self, *, scope: WorkspaceScope, unacknowledged_only: bool, limit: int
    ) -> AlertListResponse: ...

    async def acknowledge(
        self, *, scope: WorkspaceScope, alert_id: str
    ) -> AlertAcknowledgeResponse: ...


def _now() -> datetime:
    return datetime.now(tz=UTC)


class PostgresAlertRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url, row_factory=dict_row)

    _SELECT = """
        select
          a.id::text as id,
          a.type,
          a.severity,
          a.title_key,
          a.district_id::text as district_id,
          a.subject_type,
          a.subject_id,
          a.payload,
          a.valid_from,
          a.valid_until,
          a.dedupe_key,
          a.created_at,
          r.status::text as status,
          r.delivered_at,
          r.acknowledged_at
        from public.alerts as a
        join public.alert_recipients as r
          on r.alert_id = a.id and r.organization_id = a.organization_id
    """

    async def list_alerts(
        self, *, scope: WorkspaceScope, unacknowledged_only: bool, limit: int
    ) -> AlertListResponse:
        scope.require("alert:read")
        now = _now()
        with self._connect() as connection:
            rows = connection.execute(
                self._SELECT
                + """
                where a.organization_id = %(org)s::uuid
                  and r.profile_id = %(profile)s::uuid
                  and (%(unack)s::boolean is not true or r.status <> 'acknowledged')
                order by
                  case a.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                  a.valid_from desc
                limit %(limit)s
                """,
                {
                    "org": scope.organization_id,
                    "profile": scope.profile_id,
                    "unack": unacknowledged_only,
                    "limit": limit,
                },
            ).fetchall()
            total = connection.execute(
                """
                select count(*)::int as total
                from public.alert_recipients
                where organization_id = %(org)s::uuid and profile_id = %(profile)s::uuid
                """,
                {"org": scope.organization_id, "profile": scope.profile_id},
            ).fetchone()["total"]

        alerts = [
            AlertRecord(
                **row,
                valid_now=row["valid_until"] is None or row["valid_until"] > now,
            )
            for row in rows
        ]
        return AlertListResponse(
            alerts=alerts,
            unacknowledged=sum(1 for a in alerts if a.status != "acknowledged"),
            total=total,
            as_of=now,
        )

    async def acknowledge(
        self, *, scope: WorkspaceScope, alert_id: str
    ) -> AlertAcknowledgeResponse:
        """Idempotent: acknowledging twice is the same answer, not an error."""

        scope.require("alert:read")
        try:
            uuid.UUID(alert_id)
        except ValueError as error:
            raise ApiError(
                404, "not_found", "The requested resource was not found."
            ) from error

        with self._connect() as connection:
            existing = connection.execute(
                """
                select status::text as status, acknowledged_at
                from public.alert_recipients
                where organization_id = %(org)s::uuid
                  and alert_id = %(alert)s::uuid
                  and profile_id = %(profile)s::uuid
                """,
                {
                    "org": scope.organization_id,
                    "alert": alert_id,
                    "profile": scope.profile_id,
                },
            ).fetchone()
            if existing is None:
                # Not "forbidden": an alert nobody sent you is one you cannot
                # tell apart from one that does not exist, and should be.
                raise ApiError(
                    404, "not_found", "The requested resource was not found."
                )
            if existing["status"] == "acknowledged":
                return AlertAcknowledgeResponse(
                    alert_id=alert_id,
                    status="acknowledged",
                    acknowledged_at=existing["acknowledged_at"],
                    already_acknowledged=True,
                )

            with connection.transaction():
                row = connection.execute(
                    """
                    update public.alert_recipients
                    set status = 'acknowledged',
                        acknowledged_at = now(),
                        updated_at = now(),
                        version = version + 1
                    where organization_id = %(org)s::uuid
                      and alert_id = %(alert)s::uuid
                      and profile_id = %(profile)s::uuid
                    returning acknowledged_at
                    """,
                    {
                        "org": scope.organization_id,
                        "alert": alert_id,
                        "profile": scope.profile_id,
                    },
                ).fetchone()
                connection.execute(
                    """
                    insert into public.audit_events (
                      organization_id, actor_id, action, entity_type, entity_id,
                      after_hash, metadata
                    )
                    values (
                      %(org)s::uuid, %(actor)s::uuid, 'alert.acknowledged', 'alert',
                      %(alert)s::uuid, %(hash)s, %(meta)s
                    )
                    """,
                    {
                        "org": scope.organization_id,
                        "actor": scope.profile_id,
                        "alert": alert_id,
                        "hash": canonical_hash({"status": "acknowledged"}),
                        "meta": Jsonb({"alert_id": alert_id}),
                    },
                )

        return AlertAcknowledgeResponse(
            alert_id=alert_id,
            status="acknowledged",
            acknowledged_at=row["acknowledged_at"],
            already_acknowledged=False,
        )


def raise_alert(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    candidate: AlertCandidate,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or update one alert and fan it out to its recipients."""

    moment = now or _now()
    valid_from = moment
    valid_until = moment + candidate.valid_for
    key = dedupe_key(
        alert_type=candidate.alert_type,
        subject_type=candidate.subject_type,
        subject_id=candidate.subject_id,
        valid_from=valid_from,
    )

    row = connection.execute(
        """
        insert into public.alerts (
          organization_id, district_id, type, severity, title_key, payload,
          valid_from, valid_until, dedupe_key, subject_type, subject_id
        )
        values (
          %(org)s::uuid, %(district)s::uuid, %(type)s, %(severity)s, %(title)s,
          %(payload)s, %(from)s, %(until)s, %(key)s, %(subject_type)s, %(subject_id)s
        )
        on conflict (organization_id, dedupe_key) do update
          set payload = excluded.payload,
              severity = excluded.severity,
              -- The same emergency going on longer extends its own validity
              -- rather than raising a second alert about it.
              valid_until = greatest(public.alerts.valid_until, excluded.valid_until),
              updated_at = now(),
              version = public.alerts.version + 1
        returning id::text as id, (version = 1) as is_new
        """,
        {
            "org": organization_id,
            "district": candidate.district_id,
            "type": candidate.alert_type,
            "severity": candidate.severity,
            "title": candidate.title_key,
            "payload": Jsonb(candidate.payload),
            "from": valid_from,
            "until": valid_until,
            "key": key,
            "subject_type": candidate.subject_type,
            "subject_id": candidate.subject_id,
        },
    ).fetchone()

    roles = roles_with_capability(candidate.recipient_capabilities)
    recipients = connection.execute(
        """
        select distinct p.id::text as id
        from public.profiles as p
        join public.role_assignments as ra
          on ra.profile_id = p.id and ra.organization_id = p.organization_id
        where p.organization_id = %(org)s::uuid
          and p.active
          and ra.revoked_at is null
          -- A grant that has expired, or has not started, tells nobody anything:
          -- the same window /v1/me applies before it lets anyone in.
          and ra.valid_from <= now()
          and (ra.valid_to is null or ra.valid_to > now())
          and ra.role::text = any(%(roles)s::text[])
          -- District scope decides who is told. An organization-wide grant
          -- hears about every district; a district grant hears about its own.
          and (
            %(district)s::uuid is null
            or ra.district_id is null
            or ra.district_id = %(district)s::uuid
          )
        """,
        {
            "org": organization_id,
            "roles": roles,
            "district": candidate.district_id,
        },
    ).fetchall()

    profile_ids = {entry["id"] for entry in recipients}
    profile_ids.update(candidate.explicit_profile_ids)
    if profile_ids:
        connection.execute(
            """
            insert into public.alert_recipients (
              organization_id, alert_id, profile_id, status, delivered_at
            )
            select %(org)s::uuid, %(alert)s::uuid, unnest(%(profiles)s::uuid[]),
                   'delivered', now()
            -- Somebody already holding this alert is not told again. An
            -- acknowledgement is never reset by the alert being refreshed.
            on conflict (organization_id, alert_id, profile_id) do nothing
            """,
            {
                "org": organization_id,
                "alert": row["id"],
                "profiles": list(profile_ids),
            },
        )

    connection.execute(
        """
        insert into public.audit_events (
          organization_id, action, entity_type, entity_id, after_hash, metadata
        )
        values (
          %(org)s::uuid, 'alert.raised', 'alert', %(alert)s::uuid, %(hash)s, %(meta)s
        )
        """,
        {
            "org": organization_id,
            "alert": row["id"],
            "hash": canonical_hash({"type": candidate.alert_type, "dedupe_key": key}),
            "meta": Jsonb(
                {
                    "type": candidate.alert_type,
                    "severity": candidate.severity,
                    "subject_type": candidate.subject_type,
                    "subject_id": candidate.subject_id,
                    "recipients": len(profile_ids),
                    "is_new": bool(row["is_new"]),
                }
            ),
        },
    )

    return {
        "alert_id": row["id"],
        "is_new": bool(row["is_new"]),
        "recipients": len(profile_ids),
        "dedupe_key": key,
    }


def build_alert_repository(database_url: str | None) -> AlertRepository | None:
    if not database_url:
        return None
    return PostgresAlertRepository(database_url)


__all__ = [
    "ALERT_TYPES",
    "AlertAcknowledgeResponse",
    "AlertCandidate",
    "AlertListResponse",
    "AlertRecord",
    "AlertRepository",
    "PostgresAlertRepository",
    "build_alert_repository",
    "dedupe_key",
    "raise_alert",
    "roles_with_capability",
]
