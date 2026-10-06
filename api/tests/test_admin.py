"""People and roles: who may call the admin routes at all, without a database."""

from __future__ import annotations

import uuid

import pytest
from app.admin import InviteRequest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.test_identity import FakeBearerTokenVerifier, FakeIdentityRepository

AUTH = {"Authorization": "Bearer valid-token"}
PROFILE = "44444444-4444-4444-8444-444444444444"
GRANT = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def client():
    app = create_app(
        Settings(database_url=""),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=FakeIdentityRepository(),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_every_admin_route_needs_a_session(client) -> None:
    assert client.get("/v1/admin/people").status_code == 401
    assert client.post("/v1/admin/people", json={}).status_code == 401


def test_a_dispatcher_cannot_read_or_change_anyones_roles(client) -> None:
    # The synthetic caller is a district dispatcher: no admin:settings.
    assert client.get("/v1/admin/people", headers=AUTH).status_code == 403
    writes = {
        f"/v1/admin/people/{PROFILE}/grants": {
            "role": "field_officer",
            "district_id": str(uuid.uuid4()),
        },
        f"/v1/admin/grants/{GRANT}/revoke": {},
        "/v1/admin/people": {
            "email": "someone@example.test",
            "display_name": "Someone",
            "grant": {"role": "driver", "district_id": str(uuid.uuid4())},
        },
    }
    for path, body in writes.items():
        response = client.post(
            path,
            json=body,
            headers={**AUTH, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "scope_denied"


def test_a_change_needs_an_idempotency_key(client) -> None:
    response = client.post(f"/v1/admin/grants/{GRANT}/revoke", json={}, headers=AUTH)
    assert response.status_code == 400


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "two words@example.test"])
def test_an_invitation_needs_a_plausible_email(email) -> None:
    with pytest.raises(ValidationError):
        InviteRequest(
            email=email, display_name="X", grant={"role": "driver", "district_id": None}
        )


def test_an_invitation_email_is_normalised() -> None:
    request = InviteRequest(
        email="  Officer@Example.TEST ",
        display_name="  Field Officer ",
        grant={"role": "field_officer", "district_id": str(uuid.uuid4())},
    )
    assert (request.email, request.display_name) == (
        "officer@example.test",
        "Field Officer",
    )
