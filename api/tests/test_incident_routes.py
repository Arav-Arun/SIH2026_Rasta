from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.config import Settings
from app.errors import ApiError
from app.idempotency import canonical_hash, require_idempotency_key
from app.incidents import (
    AttachmentDraft,
    GeoPoint,
    IncidentCreateRequest,
    IncidentCreateResponse,
    IncidentSummary,
    storage_key_for,
)
from app.inspections import TRANSITIONS, InspectionCreateRequest
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.test_identity import FakeBearerTokenVerifier, FakeIdentityRepository

AUTH = {"Authorization": "Bearer valid-token"}
KEY = {"Idempotency-Key": str(uuid.uuid4())}
NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
ORG = "a2600002-0000-4000-8000-000000000001"
DISTRICT = "a2600002-0000-4000-8000-000000000002"


def _summary(incident_id: str = "inc-1") -> IncidentSummary:
    return IncidentSummary(
        id=incident_id,
        district_id=DISTRICT,
        type="landslide_debris",
        status="submitted",
        location=GeoPoint(longitude=91.88, latitude=25.57),
        accuracy_m=12.0,
        captured_at=NOW,
        reported_at=NOW,
        reporter_profile_id="22222222-2222-4222-8222-222222222222",
        note=None,
        source_mode="recorded",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeIncidentRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def create_incident(self, *, scope, request, idempotency_key):
        self.calls.append(
            (
                "create",
                {
                    "key": idempotency_key,
                    "type": request.type,
                    "scope": scope.profile_id,
                },
            )
        )
        return IncidentCreateResponse(
            incident=_summary(), upload_instructions=[], suggested_segments=[]
        )

    async def add_attachment(self, **kwargs):
        raise AssertionError("not used")

    async def complete_attachment(self, **kwargs):
        raise AssertionError("not used")

    async def review_incident(self, **kwargs):
        raise AssertionError("not used")

    async def list_incidents(self, *, scope, status, limit):
        from app.incidents import IncidentListResponse

        return IncidentListResponse(
            incidents=[_summary()], total=1, as_of=NOW, scope="district"
        )

    async def get_incident(self, *, scope, incident_id):
        return _summary() if incident_id == "inc-1" else None


@pytest.fixture
def repository() -> FakeIncidentRepository:
    return FakeIncidentRepository()


@pytest.fixture
def client(repository: FakeIncidentRepository) -> TestClient:
    app = create_app(
        Settings(database_url=""),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=FakeIdentityRepository(),
        incident_repository=repository,
    )
    with TestClient(app) as test_client:
        yield test_client


VALID_BODY = {
    "type": "landslide_debris",
    "captured_at": "2026-09-18T08:55:00+05:30",
    "location": {"longitude": 91.8801, "latitude": 25.5701},
    "accuracy_m": 12,
    "note": "Debris covers one lane.",
    "attachments": [
        {
            "local_id": "att-local-1",
            "mime_type": "image/jpeg",
            "bytes": 2841120,
            "sha256": "a" * 64,
        }
    ],
}


def test_write_requires_idempotency_key(client: TestClient) -> None:
    missing = client.post("/v1/incidents", json=VALID_BODY, headers=AUTH)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "idempotency_key_required"

    bad = client.post(
        "/v1/incidents", json=VALID_BODY, headers={**AUTH, "Idempotency-Key": "nope"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "idempotency_key_invalid"


def test_create_incident_routes_to_repository_with_scope_and_key(
    client: TestClient, repository: FakeIncidentRepository
) -> None:
    response = client.post("/v1/incidents", json=VALID_BODY, headers={**AUTH, **KEY})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["incident"]["status"] == "submitted"
    assert body["replayed"] is False
    assert repository.calls == [
        (
            "create",
            {
                "key": KEY["Idempotency-Key"],
                "type": "landslide_debris",
                "scope": "22222222-2222-4222-8222-222222222222",
            },
        )
    ]


def test_create_incident_validates_contract(client: TestClient) -> None:
    bad_mime = {
        **VALID_BODY,
        "attachments": [
            {**VALID_BODY["attachments"][0], "mime_type": "application/pdf"}
        ],
    }
    response = client.post("/v1/incidents", json=bad_mime, headers={**AUTH, **KEY})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

    bad_type = {**VALID_BODY, "type": "alien_invasion"}
    assert (
        client.post("/v1/incidents", json=bad_type, headers={**AUTH, **KEY}).status_code
        == 422
    )

    too_big = {
        **VALID_BODY,
        "attachments": [{**VALID_BODY["attachments"][0], "bytes": 6 * 1024 * 1024}],
    }
    assert (
        client.post("/v1/incidents", json=too_big, headers={**AUTH, **KEY}).status_code
        == 422
    )


def test_reads_are_scoped_and_404_out_of_scope(client: TestClient) -> None:
    assert client.get("/v1/incidents", headers=AUTH).status_code == 200
    assert client.get("/v1/incidents/inc-1", headers=AUTH).status_code == 200
    assert client.get("/v1/incidents/inc-2", headers=AUTH).status_code == 404
    assert client.get("/v1/incidents").status_code == 401


def test_openapi_lists_incident_and_inspection_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/v1/incidents",
        "/v1/incidents/{incident_id}",
        "/v1/incidents/{incident_id}/attachments",
        "/v1/incidents/{incident_id}/attachments/{attachment_id}/complete",
        "/v1/incidents/{incident_id}/review",
        "/v1/inspections",
        "/v1/inspections/{inspection_id}/accept",
        "/v1/inspections/{inspection_id}/complete",
        "/v1/inspections/{inspection_id}/cancel",
    ):
        assert path in paths, path


def test_idempotency_helpers() -> None:
    assert canonical_hash({"b": 1, "a": [1, 2]}) == canonical_hash(
        {"a": [1, 2], "b": 1}
    )
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})
    key = str(uuid.uuid4())
    assert require_idempotency_key(key.upper()) == key
    with pytest.raises(ApiError) as error:
        require_idempotency_key("not-a-uuid")
    assert error.value.code == "idempotency_key_invalid"


def test_storage_key_matches_database_constraint() -> None:
    org, inc, att = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    key = storage_key_for(org, inc, att, "photo 1 (front)", "image/jpeg")
    assert key == f"{org}/{inc}/{att}/photo-1--front-.jpg"
    import re

    pattern = r"^[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/[A-Za-z0-9._-]{1,120}$"
    assert re.match(pattern, key)


def test_contract_models_reject_bad_input() -> None:
    with pytest.raises(ValidationError):
        AttachmentDraft(local_id="x", mime_type="image/gif", bytes=10, sha256="a" * 64)
    with pytest.raises(ValidationError):
        IncidentCreateRequest(
            type="flooding",
            captured_at=NOW,
            location=GeoPoint(longitude=200, latitude=0),
        )
    with pytest.raises(ValidationError):
        InspectionCreateRequest(
            target_type="road", target_id="x", assignee_profile_id="y"
        )  # type: ignore[arg-type]


def test_inspection_transitions_are_explicit() -> None:
    assert TRANSITIONS["accept"] == {"assigned": "accepted"}
    assert TRANSITIONS["complete"] == {
        "accepted": "submitted",
        "in_progress": "submitted",
    }
    assert "submitted" not in TRANSITIONS["cancel"]
    assert "reviewed" not in TRANSITIONS["complete"]


def test_assignable_officers_requires_the_manage_capability() -> None:
    """A field officer must not be able to enumerate their colleagues.

    The endpoint exists to populate a dispatcher's assignment picker; exposing
    the district's staff list to anyone else is an unnecessary disclosure.
    """

    from app.inspections import PostgresInspectionRepository
    from app.scope import WorkspaceScope

    repository = PostgresInspectionRepository.__new__(PostgresInspectionRepository)
    scope = WorkspaceScope(
        organization_id=ORG,
        profile_id=str(uuid.uuid4()),
        roles=("field_officer",),
        capabilities=frozenset({"inspection:execute"}),
        district_ids=frozenset({DISTRICT}),
    )

    with pytest.raises(ApiError) as excinfo:
        import asyncio

        asyncio.run(repository.eligible_assignees(scope=scope, district_id=DISTRICT))

    assert excinfo.value.status_code == 403


def test_assignable_officers_refuses_a_district_outside_the_grant() -> None:
    from app.inspections import PostgresInspectionRepository
    from app.scope import WorkspaceScope

    repository = PostgresInspectionRepository.__new__(PostgresInspectionRepository)
    scope = WorkspaceScope(
        organization_id=ORG,
        profile_id=str(uuid.uuid4()),
        roles=("district_dispatcher",),
        capabilities=frozenset({"inspection:manage"}),
        district_ids=frozenset({DISTRICT}),
    )

    with pytest.raises(ApiError) as excinfo:
        import asyncio

        asyncio.run(
            repository.eligible_assignees(
                scope=scope, district_id="a2600002-0000-4000-8000-0000000000ff"
            )
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "district_not_in_scope"
