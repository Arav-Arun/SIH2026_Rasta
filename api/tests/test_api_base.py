from __future__ import annotations

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = create_app(
        Settings(
            app_version="test-version",
            allowed_origins=("https://console.example.test",),
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_public_and_traceable(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "test-version"
    assert response.json()["mode"] == "local_demo"
    assert response.headers["x-request-id"].startswith("req_")


def test_valid_client_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-Id": "trace-42"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trace-42"


def test_unsafe_client_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-Id": "bad\r\nvalue"})

    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("req_")


def test_openapi_publishes_metadata_and_auth_error_contract(client: TestClient) -> None:
    response = client.get("/openapi.json")
    document = response.json()

    assert response.status_code == 200
    assert document["info"]["title"] == "RASTA API"
    assert document["info"]["version"] == "test-version"
    assert "/v1/me" in document["paths"]
    assert "401" in document["paths"]["/v1/me"]["get"]["responses"]
    assert "ErrorEnvelope" in document["components"]["schemas"]
    assert document["components"]["securitySchemes"]["SupabaseBearer"] == {
        "type": "http",
        "description": (
            "A Supabase-issued JWT verified against the project JWKS endpoint when "
            "SUPABASE_URL is configured."
        ),
        "scheme": "bearer",
    }
    assert document["paths"]["/v1/me"]["get"]["security"] == [{"SupabaseBearer": []}]


def test_missing_bearer_token_returns_defined_401_envelope(client: TestClient) -> None:
    response = client.get("/v1/me")
    payload = response.json()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["x-request-id"] == payload["error"]["request_id"]
    assert payload["error"] == {
        "code": "authentication_required",
        "message": "A valid session is required.",
        "details": {},
        "request_id": response.headers["x-request-id"],
    }


@pytest.mark.parametrize(
    "authorization",
    ["Basic abc", "Bearer", "Bearer ", "Bearer unverified-token"],
)
def test_unverified_or_malformed_bearer_never_bypasses_authentication(
    client: TestClient, authorization: str
) -> None:
    response = client.get("/v1/me", headers={"Authorization": authorization})
    payload = response.json()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert payload["error"]["code"] == "invalid_session"
    assert authorization not in response.text


def test_unknown_route_uses_the_same_safe_error_envelope(client: TestClient) -> None:
    response = client.get("/not-a-route")
    payload = response.json()

    assert response.status_code == 404
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["request_id"] == response.headers["x-request-id"]


def test_explicit_origin_is_allowed_and_other_origin_is_not(client: TestClient) -> None:
    allowed = client.get("/health", headers={"Origin": "https://console.example.test"})
    denied = client.get("/health", headers={"Origin": "https://outside.example.test"})

    assert (
        allowed.headers["access-control-allow-origin"] == "https://console.example.test"
    )
    assert "access-control-allow-origin" not in denied.headers


def test_preflight_allows_only_documented_origin_and_headers(
    client: TestClient,
) -> None:
    response = client.options(
        "/v1/me",
        headers={
            "Origin": "https://console.example.test",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,X-Request-Id",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://console.example.test"
    )
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert response.headers["x-request-id"].startswith("req_")
