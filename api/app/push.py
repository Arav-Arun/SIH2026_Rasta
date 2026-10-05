"""Optional push delivery. The inbox is the record; push is a courtesy."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db
from app.errors import ApiError
from app.scope import WorkspaceScope

AttemptStatus = Literal["sent", "failed", "skipped"]

#: Push messages are small by protocol.
MAX_PAYLOAD_BYTES = 3800

#: The push services browsers hand out endpoints on.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",  # Chrome, Edge and Samsung Internet on Android
    "push.services.mozilla.com",  # Firefox
    "notify.windows.com",  # Edge on Windows
    "push.apple.com",  # Safari
)

#: An Android (FCM or Expo) device token: an opaque string, never a URL.
_DEVICE_TOKEN = re.compile(r"[A-Za-z0-9_:\-\[\]]{16,4096}")


def endpoint_allowed(
    platform: str, endpoint: str, extra_hosts: tuple[str, ...] = ()
) -> bool:
    """Whether this server may ever send to ``endpoint``."""

    if platform == "android":
        return bool(_DEVICE_TOKEN.fullmatch(endpoint))
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.username or parts.password or not host:
        return False
    if host in extra_hosts:
        return parts.scheme in ("https", "http")
    if parts.scheme != "https" or port not in (None, 443):
        return False
    return any(
        host == known or host.endswith(f".{known}") for known in PUSH_SERVICE_HOSTS
    )


class PushSubscriptionRequest(BaseModel):
    platform: Literal["android", "web"]
    endpoint: str = Field(min_length=8, max_length=2000)
    keys: dict[str, str] = Field(default_factory=dict)
    user_agent: str | None = Field(default=None, max_length=400)


class PushRevokeRequest(BaseModel):
    #: In the body, never the query string: an endpoint is bearer-equivalent and
    #: query strings end up in proxy and access logs.
    endpoint: str = Field(min_length=8, max_length=2000)


class PushTestResponse(BaseModel):
    configured: bool
    attempted: int
    sent: int
    failed: int
    skipped: int
    #: Reason codes of what did not go, e.g. ``not_configured`` or
    #: ``subscription_gone``. Never a provider's response body.
    errors: list[str]
    notice: str


class PushSubscriptionResponse(BaseModel):
    id: str
    platform: str
    created_at: str
    #: Deliberately not the endpoint. A read that echoed it back would turn any
    #: session leak into a push-spoofing capability.
    endpoint_fingerprint: str
    revoked: bool


class PushStatusResponse(BaseModel):
    configured: bool
    sender: str | None
    public_key: str | None
    subscriptions: int
    last_attempt_status: str | None
    last_attempt_error: str | None
    notice: str


def fingerprint(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PushMessage:
    alert_id: str
    title_key: str
    severity: str
    #: Where tapping the notification should land. A route inside the app, never
    #: an external URL, so a notification cannot navigate anybody off-app.
    deep_link: str

    def as_payload(self) -> bytes:
        body = json.dumps(
            {
                "alert_id": self.alert_id,
                "title_key": self.title_key,
                "severity": self.severity,
                "deep_link": self.deep_link,
            },
            separators=(",", ":"),
        ).encode()
        if len(body) > MAX_PAYLOAD_BYTES:  # pragma: no cover - ids are short
            raise ValueError("Push payload exceeds the protocol's practical limit.")
        return body


class PushSender(Protocol):
    name: str
    #: Hosts accepted as endpoints besides the real push services; see
    #: endpoint_allowed. Empty in every deployment.
    extra_hosts: tuple[str, ...]

    def configured(self) -> bool: ...

    def send(
        self, *, endpoint: str, keys: dict[str, str], message: PushMessage
    ) -> tuple[AttemptStatus, str | None, int | None]: ...


class UnconfiguredSender:
    """The safe default: nothing is sent and the attempt says so."""

    name = "none"

    def __init__(self, extra_hosts: tuple[str, ...] = ()) -> None:
        self.extra_hosts = extra_hosts

    def configured(self) -> bool:
        return False

    def send(
        self, *, endpoint: str, keys: dict[str, str], message: PushMessage
    ) -> tuple[AttemptStatus, str | None, int | None]:
        return "skipped", "not_configured", None


class WebPushSender:
    """Web Push with VAPID authentication (RFC 8292)."""

    name = "web_push"

    def __init__(
        self,
        private_key_pem: str,
        public_key: str,
        subject: str,
        extra_hosts: tuple[str, ...] = (),
    ) -> None:
        self.extra_hosts = extra_hosts
        self._private_key_pem = (private_key_pem or "").strip()
        self._public_key = (public_key or "").strip()
        self._subject = (subject or "").strip()

    def configured(self) -> bool:
        return bool(self._private_key_pem and self._public_key and self._subject)

    def _vapid_header(self, endpoint: str) -> str:
        from urllib.parse import urlsplit

        import jwt  # PyJWT, already a dependency of the auth path

        origin = urlsplit(endpoint)
        audience = f"{origin.scheme}://{origin.netloc}"
        token = jwt.encode(
            {
                "aud": audience,
                "exp": int(time.time()) + 12 * 3600,
                "sub": self._subject,
            },
            self._private_key_pem,
            algorithm="ES256",
        )
        return f"vapid t={token}, k={self._public_key}"

    def send(
        self, *, endpoint: str, keys: dict[str, str], message: PushMessage
    ) -> tuple[AttemptStatus, str | None, int | None]:
        if not self.configured():
            return "skipped", "not_configured", None
        try:
            request = urllib.request.Request(
                endpoint,
                data=b"",
                method="POST",
                headers={
                    "Authorization": self._vapid_header(endpoint),
                    "TTL": "900",
                    "Urgency": "high" if message.severity == "critical" else "normal",
                    "Content-Length": "0",
                },
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                return "sent", None, response.status
        except urllib.error.HTTPError as error:
            # 404 and 410 mean the browser threw the subscription away.
            code = (
                "subscription_gone"
                if error.code in (404, 410)
                else f"http_{error.code}"
            )
            return "failed", code, error.code
        except Exception as error:
            return "failed", f"send_failed_{type(error).__name__}", None


def build_sender(
    *,
    vapid_private_key: str,
    vapid_public_key: str,
    vapid_subject: str,
    extra_hosts: tuple[str, ...] = (),
) -> PushSender:
    sender = WebPushSender(
        vapid_private_key, vapid_public_key, vapid_subject, extra_hosts
    )
    return sender if sender.configured() else UnconfiguredSender(extra_hosts)


class PushRepository(Protocol):
    async def subscribe(
        self, *, scope: WorkspaceScope, request: PushSubscriptionRequest
    ) -> PushSubscriptionResponse: ...

    async def revoke(self, *, scope: WorkspaceScope, endpoint: str) -> None: ...

    async def status(self, *, scope: WorkspaceScope) -> PushStatusResponse: ...

    async def send_test(self, *, scope: WorkspaceScope) -> PushTestResponse: ...


class PostgresPushRepository:
    def __init__(self, database_url: str, sender: PushSender, public_key: str) -> None:
        self._database_url = database_url
        self._sender = sender
        self._public_key = public_key.strip()

    def _connect(self) -> AbstractContextManager[psycopg.Connection[Any]]:
        return db.connect(self._database_url)

    async def subscribe(
        self, *, scope: WorkspaceScope, request: PushSubscriptionRequest
    ) -> PushSubscriptionResponse:
        if not endpoint_allowed(
            request.platform, request.endpoint, self._sender.extra_hosts
        ):
            raise ApiError(
                422,
                "push_endpoint_not_allowed",
                "That is not an endpoint issued by a known push service.",
            )
        digest = fingerprint(request.endpoint)
        with self._connect() as connection, connection.transaction():
            row = connection.execute(
                """
                insert into public.push_subscriptions (
                  organization_id, profile_id, platform, endpoint, endpoint_hash,
                  keys, user_agent
                )
                values (
                  %(org)s::uuid, %(profile)s::uuid, %(platform)s::public.device_platform,
                  %(endpoint)s, %(hash)s, %(keys)s, %(agent)s
                )
                on conflict (organization_id, endpoint_hash) do update
                  set profile_id = excluded.profile_id,
                      keys = excluded.keys,
                      user_agent = excluded.user_agent,
                      revoked_at = null,
                      updated_at = now()
                returning id::text as id, platform::text as platform,
                          created_at, revoked_at
                """,
                {
                    "org": scope.organization_id,
                    "profile": scope.profile_id,
                    "platform": request.platform,
                    "endpoint": request.endpoint,
                    "hash": digest,
                    "keys": Jsonb(request.keys),
                    "agent": request.user_agent,
                },
            ).fetchone()
        return PushSubscriptionResponse(
            id=row["id"],
            platform=row["platform"],
            created_at=row["created_at"].isoformat(),
            endpoint_fingerprint=digest[:16],
            revoked=row["revoked_at"] is not None,
        )

    async def revoke(self, *, scope: WorkspaceScope, endpoint: str) -> None:
        with self._connect() as connection, connection.transaction():
            updated = connection.execute(
                """
                update public.push_subscriptions
                set revoked_at = now(), updated_at = now()
                where organization_id = %(org)s::uuid
                  and endpoint_hash = %(hash)s
                  and profile_id = %(profile)s::uuid
                  and revoked_at is null
                returning id
                """,
                {
                    "org": scope.organization_id,
                    "hash": fingerprint(endpoint),
                    "profile": scope.profile_id,
                },
            ).fetchone()
        if updated is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")

    async def status(self, *, scope: WorkspaceScope) -> PushStatusResponse:
        with self._connect() as connection:
            count = connection.execute(
                """
                select count(*)::int as total
                from public.push_subscriptions
                where organization_id = %(org)s::uuid and profile_id = %(profile)s::uuid
                  and revoked_at is null
                """,
                {"org": scope.organization_id, "profile": scope.profile_id},
            ).fetchone()["total"]
            last = connection.execute(
                """
                select status, error_code
                from public.push_attempts
                where organization_id = %(org)s::uuid
                order by attempted_at desc
                limit 1
                """,
                {"org": scope.organization_id},
            ).fetchone()

        configured = self._sender.configured()
        return PushStatusResponse(
            configured=configured,
            sender=self._sender.name if configured else None,
            # The public key is meant to be public; it is what the browser needs
            # to create a subscription. The private key never leaves the server.
            public_key=self._public_key or None,
            subscriptions=count,
            last_attempt_status=last["status"] if last else None,
            last_attempt_error=last["error_code"] if last else None,
            notice=NOTICE,
        )

    async def send_test(self, *, scope: WorkspaceScope) -> PushTestResponse:
        """Send a test notification to the caller's own registrations only."""

        with self._connect() as connection:
            subscriptions = _live_subscriptions(
                connection,
                organization_id=scope.organization_id,
                profile_ids=[scope.profile_id],
            )
        message = PushMessage(
            alert_id="test", title_key="push.test", severity="info", deep_link="/alerts"
        )
        # The network calls happen outside any transaction; the attempts are
        # written afterwards in one short one.
        outcomes = [
            (subscription, *send_one(self._sender, subscription, message))
            for subscription in subscriptions
        ]
        counts = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}
        errors: list[str] = []
        with self._connect() as connection, connection.transaction():
            if not outcomes:
                _record_attempt(
                    connection,
                    organization_id=scope.organization_id,
                    subscription_id=None,
                    alert_id=None,
                    status="skipped",
                    error_code="no_subscriptions",
                    http_status=None,
                )
                counts["skipped"] = 1
                errors.append("no_subscriptions")
            for subscription, status, error_code, http_status in outcomes:
                counts["attempted"] += 1
                counts[status] += 1
                if error_code:
                    errors.append(error_code)
                _record_attempt(
                    connection,
                    organization_id=scope.organization_id,
                    subscription_id=subscription["id"],
                    alert_id=None,
                    status=status,
                    error_code=error_code,
                    http_status=http_status,
                )
                if error_code == "subscription_gone":
                    _revoke_gone(connection, scope.organization_id, subscription["id"])
        return PushTestResponse(
            configured=self._sender.configured(),
            errors=sorted(set(errors)),
            notice=NOTICE,
            **counts,
        )


NOTICE = (
    "Push is a courtesy. Every alert is in the in-app inbox whether or not a "
    "notification arrives, and a push failure never changes an alert's state."
)


def send_one(
    sender: PushSender, subscription: dict[str, Any], message: PushMessage
) -> tuple[AttemptStatus, str | None, int | None]:
    """One attempt to one registration, refused before any network call when
    the endpoint is not one this server may send to."""

    platform = subscription.get("platform") or "web"
    if not endpoint_allowed(platform, subscription["endpoint"], sender.extra_hosts):
        return "failed", "endpoint_not_allowed", None
    if platform == "android":
        # Registrations are kept so an Android sender can be added without
        # asking everyone to register again; until then nothing is sent.
        return "skipped", "no_android_sender", None
    return sender.send(
        endpoint=subscription["endpoint"],
        keys=subscription["keys"] or {},
        message=message,
    )


def _live_subscriptions(
    connection: psycopg.Connection, *, organization_id: str, profile_ids: list[str]
) -> list[dict[str, Any]]:
    return connection.execute(
        """
        select id::text as id, platform::text as platform, endpoint, keys
        from public.push_subscriptions
        where organization_id = %(org)s::uuid
          and profile_id = any(%(profiles)s::uuid[])
          and revoked_at is null
        """,
        {"org": organization_id, "profiles": profile_ids},
    ).fetchall()


def _record_attempt(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    subscription_id: str | None,
    alert_id: str | None,
    status: AttemptStatus,
    error_code: str | None,
    http_status: int | None,
) -> None:
    connection.execute(
        """
        insert into public.push_attempts (
          organization_id, subscription_id, alert_id, status, error_code, http_status
        )
        values (
          %(org)s::uuid, %(sub)s::uuid, %(alert)s::uuid, %(status)s, %(error)s, %(http)s
        )
        """,
        {
            "org": organization_id,
            "sub": subscription_id,
            "alert": alert_id,
            "status": status,
            "error": error_code,
            "http": http_status,
        },
    )


def _revoke_gone(
    connection: psycopg.Connection, organization_id: str, subscription_id: str
) -> None:
    # The browser discarded it. Revoking stops a dead endpoint being retried
    # forever; the person keeps their inbox either way.
    connection.execute(
        """
        update public.push_subscriptions
        set revoked_at = now(), updated_at = now()
        where organization_id = %(org)s::uuid and id = %(id)s::uuid
        """,
        {"org": organization_id, "id": subscription_id},
    )


def deliver_alert(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    sender: PushSender,
    alert_id: str,
    title_key: str,
    severity: str,
    profile_ids: list[str],
    deep_link: str = "/alerts",
) -> dict[str, Any]:
    """Attempt a push per live subscription and record every attempt."""

    if not profile_ids:
        return {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}

    subscriptions = _live_subscriptions(
        connection, organization_id=organization_id, profile_ids=profile_ids
    )

    message = PushMessage(
        alert_id=alert_id, title_key=title_key, severity=severity, deep_link=deep_link
    )
    counts = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}

    if not subscriptions:
        _record_attempt(
            connection,
            organization_id=organization_id,
            subscription_id=None,
            alert_id=alert_id,
            status="skipped",
            error_code="no_subscriptions",
            http_status=None,
        )
        counts["skipped"] = 1
        return counts

    for subscription in subscriptions:
        status, error_code, http_status = send_one(sender, subscription, message)
        counts["attempted"] += 1
        counts[status] += 1
        _record_attempt(
            connection,
            organization_id=organization_id,
            subscription_id=subscription["id"],
            alert_id=alert_id,
            status=status,
            error_code=error_code,
            http_status=http_status,
        )
        if error_code == "subscription_gone":
            _revoke_gone(connection, organization_id, subscription["id"])

    return counts


def build_push_repository(
    database_url: str | None, *, sender: PushSender, vapid_public_key: str
) -> PushRepository | None:
    if not database_url:
        return None
    return PostgresPushRepository(database_url, sender, vapid_public_key)


def parse_extra_hosts(value: str) -> tuple[str, ...]:
    return tuple(host.strip().lower() for host in value.split(",") if host.strip())


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "PUSH_SERVICE_HOSTS",
    "PushMessage",
    "PushRepository",
    "PushRevokeRequest",
    "PushSender",
    "PushStatusResponse",
    "PushSubscriptionRequest",
    "PushSubscriptionResponse",
    "PushTestResponse",
    "UnconfiguredSender",
    "WebPushSender",
    "build_push_repository",
    "build_sender",
    "deliver_alert",
    "deliver_alerts_for",
    "endpoint_allowed",
    "fingerprint",
    "parse_extra_hosts",
    "send_one",
]


def deliver_alerts_for(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    sender: PushSender,
    alert_ids: list[str],
) -> dict[str, Any]:
    """Push every alert in the list to whoever it was delivered to."""

    totals = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}
    if not alert_ids:
        return totals

    rows = connection.execute(
        """
        select
          a.id::text as id,
          a.title_key,
          a.severity,
          array_agg(distinct r.profile_id::text) as profile_ids
        from public.alerts as a
        join public.alert_recipients as r
          on r.alert_id = a.id and r.organization_id = a.organization_id
        where a.organization_id = %(org)s::uuid and a.id = any(%(ids)s::uuid[])
        group by a.id, a.title_key, a.severity
        """,
        {"org": organization_id, "ids": alert_ids},
    ).fetchall()

    for row in rows:
        counts = deliver_alert(
            connection,
            organization_id=organization_id,
            sender=sender,
            alert_id=row["id"],
            title_key=row["title_key"],
            severity=row["severity"],
            profile_ids=row["profile_ids"],
        )
        for key, value in counts.items():
            totals[key] += value
    return totals
