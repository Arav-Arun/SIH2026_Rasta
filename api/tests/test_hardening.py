"""Transport hardening, headers, body cap, rate limits, log redaction."""

from __future__ import annotations

import logging
import uuid

import pytest
from app.config import Settings
from app.main import create_app
from app.middleware import RedactQueryStringFilter
from app.ratelimit import LIMITS, RateLimiter, rate_limit
from fastapi.testclient import TestClient
from tests.test_identity import FakeBearerTokenVerifier, FakeIdentityRepository
from tests.test_incident_routes import VALID_BODY, FakeIncidentRepository

AUTH = {"Authorization": "Bearer valid-token"}


def make_client(**settings: object) -> TestClient:
    app = create_app(
        Settings(database_url="", **settings),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=FakeIdentityRepository(),
        incident_repository=FakeIncidentRepository(),
    )
    return TestClient(app)


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_every_response_carries_the_security_headers() -> None:
    with make_client() as client:
        for response in (
            client.get("/health"),
            client.get("/v1/me"),
            client.get("/v1/nope"),
        ):
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["x-frame-options"] == "DENY"
            assert (
                "frame-ancestors 'none'" in response.headers["content-security-policy"]
            )


def test_scoped_answers_are_not_cacheable_and_docs_keep_their_scripts() -> None:
    with make_client() as client:
        assert client.get("/v1/me", headers=AUTH).headers["cache-control"] == "no-store"
        assert "cache-control" not in client.get("/health").headers
        assert "content-security-policy" not in client.get("/docs").headers


def test_hsts_only_where_the_service_is_served_over_https() -> None:
    with make_client() as local:
        assert "strict-transport-security" not in local.get("/health").headers
    with make_client(app_mode="hosted_demo") as hosted:
        assert "max-age=" in hosted.get("/health").headers["strict-transport-security"]


def test_production_does_not_publish_the_interactive_docs() -> None:
    with make_client(app_mode="production") as client:
        assert client.get("/docs").status_code == 404


def test_an_oversized_body_is_refused_before_it_is_read() -> None:
    with make_client(max_request_bytes=512) as client:
        padded = {**VALID_BODY, "note": "x" * 2000}
        response = client.post("/v1/incidents", json=padded, headers={**AUTH, **key()})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["x-request-id"]


def test_a_chunked_body_is_counted_and_stopped_at_the_cap() -> None:
    def chunks():
        for _ in range(8):
            yield b"x" * 256

    with make_client(max_request_bytes=512) as client:
        response = client.post(
            "/v1/incidents",
            content=chunks(),
            headers={**AUTH, **key(), "Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_report_creation_is_limited_per_person_and_says_when_to_retry() -> None:
    allowed, _ = LIMITS["incident.create"]
    with make_client() as client:
        codes = [
            client.post(
                "/v1/incidents", json=VALID_BODY, headers={**AUTH, **key()}
            ).status_code
            for _ in range(allowed)
        ]
        assert set(codes) == {201}
        refused = client.post(
            "/v1/incidents", json=VALID_BODY, headers={**AUTH, **key()}
        )
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1


def test_limits_can_be_switched_off_for_a_deliberate_load_test() -> None:
    allowed, _ = LIMITS["incident.create"]
    with make_client(rate_limits_enabled=False) as client:
        codes = {
            client.post(
                "/v1/incidents", json=VALID_BODY, headers={**AUTH, **key()}
            ).status_code
            for _ in range(allowed + 5)
        }
    assert codes == {201}


def test_the_window_slides() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert limiter.hit("b", "k", 2, 60) is None
    assert limiter.hit("b", "k", 2, 60) is None
    wait = limiter.hit("b", "k", 2, 60)
    assert wait == pytest.approx(60)
    # Someone else is unaffected.
    assert limiter.hit("b", "other", 2, 60) is None
    now[0] = 60.5
    assert limiter.hit("b", "k", 2, 60) is None


def test_an_unknown_bucket_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        rate_limit("no.such.bucket")


def test_access_log_lines_lose_their_query_strings() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:5000",
            "DELETE",
            "/v1/push-subscriptions?endpoint=https://fcm/secret",
            "1.1",
            204,
        ),
        None,
    )
    assert RedactQueryStringFilter().filter(record)
    assert (
        record.getMessage()
        == '127.0.0.1:5000 - "DELETE /v1/push-subscriptions?[redacted] HTTP/1.1" 204'
    )


def test_each_request_is_logged_with_its_id_and_without_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="rasta.api"), make_client() as client:
        response = client.get(
            "/v1/me?endpoint=https://push.example/secret-endpoint",
            headers={**AUTH, "X-Tracking-Grant": "grant-secret-value"},
        )
    request_id = response.headers["x-request-id"]
    lines = [
        record.getMessage() for record in caplog.records if record.name == "rasta.api"
    ]
    line = next(line for line in lines if request_id in line)
    assert "method=GET path=/v1/me status=200" in line
    everything = "\n".join(lines)
    assert "secret-endpoint" not in everything
    assert "valid-token" not in everything
    assert "grant-secret-value" not in everything


# --- audit export -------------------------------------------------------------

from dataclasses import replace  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from app.audit import (  # noqa: E402
    AuditEvent,
    AuditExportResponse,
    decode_cursor,
    to_csv,
)
from app.errors import ApiError  # noqa: E402
from app.identity import IdentityRecord  # noqa: E402
from app.schemas import RoleGrantIdentity  # noqa: E402


class AdminIdentityRepository(FakeIdentityRepository):
    async def load_identity(self, user_id: str) -> IdentityRecord | None:
        record = await super().load_identity(user_id)
        if record is None:
            return None
        return replace(
            record,
            roles=[
                RoleGrantIdentity(
                    id="44444444-4444-4444-8444-444444444444",
                    role="admin",
                    district_id=None,
                    valid_from=datetime(2000, 1, 1, tzinfo=UTC),
                    valid_to=None,
                )
            ],
        )


class FakeAuditRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def export(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return AuditExportResponse(
            organization_id=kwargs["scope"].organization_id,
            events=[
                AuditEvent(
                    id="55555555-5555-4555-8555-555555555555",
                    occurred_at=datetime(2026, 9, 26, 6, 0, tzinfo=UTC),
                    actor_id="22222222-2222-4222-8222-222222222222",
                    actor_display_name='=HYPERLINK("http://evil")',
                    action="segment.state_changed",
                    entity_type="road_segment",
                    entity_id="66666666-6666-4666-8666-666666666666",
                    before_hash="a" * 64,
                    after_hash="b" * 64,
                    metadata={"reason": "@SUM(1)"},
                )
            ],
            next_cursor=None,
        )


def audit_client(identity) -> tuple[TestClient, FakeAuditRepository]:  # type: ignore[no-untyped-def]
    app = create_app(
        Settings(database_url=""),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=identity,
    )
    repository = FakeAuditRepository()
    app.state.audit_repository = repository
    return TestClient(app), repository


def test_the_audit_trail_is_an_administrators_view() -> None:
    client, repository = audit_client(FakeIdentityRepository())
    with client:
        response = client.get("/v1/audit-events", headers=AUTH)
    assert response.status_code == 403
    assert repository.calls == []


def test_an_administrator_exports_events_oldest_first() -> None:
    client, repository = audit_client(AdminIdentityRepository())
    with client:
        response = client.get(
            "/v1/audit-events?limit=50&action=segment.state_changed", headers=AUTH
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["append_only"] is True
    assert body["events"][0]["action"] == "segment.state_changed"
    assert repository.calls[0]["limit"] == 50
    assert repository.calls[0]["action"] == "segment.state_changed"


def test_the_csv_export_cannot_run_a_formula_in_a_spreadsheet() -> None:
    client, _ = audit_client(AdminIdentityRepository())
    with client:
        response = client.get("/v1/audit-events?format=csv", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.text
    assert "'=HYPERLINK" in text
    assert ",=HYPERLINK" not in text
    assert "@SUM" in text and ",@SUM" not in text


def test_a_malformed_cursor_is_refused() -> None:
    with pytest.raises(ApiError) as caught:
        decode_cursor("not-a-cursor")
    assert caught.value.status_code == 422


def test_to_csv_has_a_header_row_even_when_empty() -> None:
    assert to_csv([]).splitlines()[0].startswith("occurred_at,id,action")
