from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.config import Settings
from app.errors import ApiError
from app.identity import IdentityRecord
from app.main import create_app
from app.network import (
    DistrictNetwork,
    SegmentPage,
    SegmentQuery,
    bbox_area_deg2,
    compute_connectivity,
    parse_bbox,
)
from app.schemas import (
    BBox,
    NetworkCoverage,
    OrganizationIdentity,
    ProfileIdentity,
    RoleGrantIdentity,
    SegmentDetailResponse,
    SegmentFeature,
    SegmentProperties,
    SegmentRisk,
)
from app.scope import scope_from_identity
from fastapi.testclient import TestClient
from tests.test_identity import FakeBearerTokenVerifier, FakeIdentityRepository

ORG = "a2600002-0000-4000-8000-000000000001"
DISTRICT = "a2600002-0000-4000-8000-000000000002"
OTHER_DISTRICT = "b2600002-0000-4000-8000-0000000000b2"
PILOT_BBOX = "91.875,25.56,91.9,25.585"


def _feature(
    segment_id: str, district_id: str = DISTRICT, passability: str = "unknown"
) -> SegmentFeature:
    return SegmentFeature(
        id=segment_id,
        geometry={
            "type": "LineString",
            "coordinates": [[91.88, 25.57], [91.881, 25.571]],
        },
        properties=SegmentProperties(
            segment_id=segment_id,
            district_id=district_id,
            from_node_id="n1",
            to_node_id="n2",
            road_class="primary",
            length_m=120.5,
            passability=passability,
            graph_version="osm-test",
            network_version="osm-test",
            source_mode="recorded",
            state_as_of=datetime(2026, 9, 7, tzinfo=UTC),
        ),
    )


class RecordingNetworkRepository:
    """Returns canned pages and records the scoped query it received."""

    def __init__(self) -> None:
        self.queries: list[SegmentQuery] = []
        self.detail_calls: list[tuple[str, frozenset[str] | None, str]] = []

    async def list_segments(self, query: SegmentQuery) -> SegmentPage:
        self.queries.append(query)
        features = [_feature("seg-1"), _feature("seg-2", passability="closed")]
        return SegmentPage(
            features=features[: query.limit],
            total_in_bbox=len(features),
            coverage=NetworkCoverage(
                bbox=BBox(min_lon=91.875, min_lat=25.56, max_lon=91.9, max_lat=25.585),
                segment_count=2,
                network_versions=["osm-test"],
                district_ids=[DISTRICT],
                returned=min(len(features), query.limit),
                truncated=len(features) > query.limit,
            ),
        )

    async def get_segment(self, *, organization_id, district_ids, segment_id):
        self.detail_calls.append((organization_id, district_ids, segment_id))
        if segment_id != "seg-1":
            return None
        return SegmentDetailResponse(
            segment=_feature("seg-1"),
            risk=SegmentRisk(available=False, reason="risk_engine_not_available"),
            observations=[],
            affected_trips=[],
            affected_trips_available=False,
            allowed_actions=[],
            as_of=datetime.now(UTC),
            mode="recorded",
            network_version="osm-test",
        )

    async def district_network(self, *, organization_id, district_id):
        if district_id != DISTRICT:
            return None
        return DistrictNetwork(
            district_id=district_id,
            network_versions=["osm-test"],
            baseline_as_of=datetime(2026, 9, 7, tzinfo=UTC),
            edges=[
                ("s1", "hub", "a", "unknown"),
                ("s2", "a", "b", "closed"),
                ("s3", "a", "c", "open"),
            ],
            facilities=[
                {
                    "id": "f-hub",
                    "name": "Depot",
                    "type": "warehouse",
                    "metadata": {
                        "supply_hub_candidate": True,
                        "routing_node_id": "hub",
                        "routing_eligible": True,
                    },
                },
                {
                    "id": "f-b",
                    "name": "Clinic B",
                    "type": "clinic",
                    "metadata": {"routing_node_id": "b", "routing_eligible": True},
                },
                {
                    "id": "f-c",
                    "name": "Clinic C",
                    "type": "clinic",
                    "metadata": {"routing_node_id": "c", "routing_eligible": True},
                },
                {
                    "id": "f-x",
                    "name": "Far clinic",
                    "type": "clinic",
                    "metadata": {"routing_eligible": False},
                },
            ],
            passability_counts={"unknown": 1, "closed": 1, "open": 1},
        )


@pytest.fixture
def repository() -> RecordingNetworkRepository:
    return RecordingNetworkRepository()


@pytest.fixture
def client(repository: RecordingNetworkRepository) -> TestClient:
    app = create_app(
        Settings(network_max_features=100),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=FakeIdentityRepository(),
        network_repository=repository,
    )
    with TestClient(app) as test_client:
        yield test_client


AUTH = {"Authorization": "Bearer valid-token"}


def test_parse_bbox_validates_shape_and_order() -> None:
    assert parse_bbox(PILOT_BBOX) == BBox(
        min_lon=91.875, min_lat=25.56, max_lon=91.9, max_lat=25.585
    )
    for bad in ("1,2,3", "a,b,c,d", "91.9,25.56,91.875,25.585", "0,0,181,1"):
        with pytest.raises(ApiError) as error:
            parse_bbox(bad)
        assert error.value.code == "invalid_bbox"
    assert bbox_area_deg2(parse_bbox(PILOT_BBOX)) == pytest.approx(0.000625)


def test_segments_require_session_and_capability(client: TestClient) -> None:
    assert (
        client.get("/v1/network/segments", params={"bbox": PILOT_BBOX}).status_code
        == 401
    )
    denied = client.get(
        "/v1/network/segments",
        params={"bbox": PILOT_BBOX},
        headers={"Authorization": "Bearer other-token"},
    )
    assert denied.status_code == 401


def test_segments_are_scoped_to_the_callers_districts(
    client: TestClient, repository: RecordingNetworkRepository
) -> None:
    response = client.get(
        "/v1/network/segments", params={"bbox": PILOT_BBOX}, headers=AUTH
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert [feature["id"] for feature in body["features"]] == ["seg-1", "seg-2"]
    assert body["mode"] == "recorded"
    assert body["network_version"] == "osm-test"
    assert body["coverage"]["truncated"] is False
    assert body["attribution"].startswith("© OpenStreetMap")
    assert body["features"][1]["properties"]["passability"] == "closed"

    query = repository.queries[-1]
    assert query.organization_id == ORG
    assert query.district_ids == frozenset({DISTRICT})
    assert query.limit == 100


def test_segments_reject_foreign_district_and_oversized_requests(
    client: TestClient,
) -> None:
    foreign = client.get(
        "/v1/network/segments",
        params={"bbox": PILOT_BBOX, "district_id": OTHER_DISTRICT},
        headers=AUTH,
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "scope_denied"

    huge = client.get(
        "/v1/network/segments", params={"bbox": "90,24,93,27"}, headers=AUTH
    )
    assert huge.status_code == 422
    assert huge.json()["error"]["code"] == "bbox_too_large"
    assert huge.json()["error"]["details"]["max_area_deg2"] == 0.25

    malformed = client.get("/v1/network/segments", params={"bbox": "x"}, headers=AUTH)
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_bbox"

    too_many = client.get(
        "/v1/network/segments", params={"bbox": PILOT_BBOX, "limit": 101}, headers=AUTH
    )
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "limit_too_large"


def test_segments_report_truncation(client: TestClient) -> None:
    response = client.get(
        "/v1/network/segments", params={"bbox": PILOT_BBOX, "limit": 1}, headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["features"]) == 1
    assert body["total_in_bbox"] == 2
    assert body["coverage"]["truncated"] is True


def test_segment_detail_scopes_and_lists_role_actions(
    client: TestClient, repository: RecordingNetworkRepository
) -> None:
    response = client.get("/v1/network/segments/seg-1", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["risk"] == {
        "available": False,
        "level": "unknown",
        "score": None,
        "horizon_hours": None,
        "reason": "risk_engine_not_available",
        "model_version": None,
        "computed_at": None,
        "explanations": [],
        "missing_inputs": [],
        "caveats": [],
        "shadow": None,
    }
    assert body["affected_trips_available"] is False
    # dispatcher: inspection:manage, incident:create, route:plan
    assert body["allowed_actions"] == [
        "assign_inspection",
        "report_observation",
        "plan_route",
    ]
    assert repository.detail_calls[-1] == (ORG, frozenset({DISTRICT}), "seg-1")

    missing = client.get("/v1/network/segments/seg-9", headers=AUTH)
    assert missing.status_code == 404


def test_connectivity_summary_is_computed_from_current_state(
    client: TestClient,
) -> None:
    response = client.get(
        "/v1/connectivity/summary", params={"district_id": DISTRICT}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage_state"] == "partial"  # one facility has no routing node
    assert body["facilities"] == {
        "monitored": 4,
        "reachable": 2,
        "isolated": 1,
        "unknown_coverage": 1,
        "at_risk": None,
    }
    status = {row["facility_id"]: row["status"] for row in body["facility_status"]}
    assert status == {
        "f-hub": "reachable",
        "f-b": "isolated",  # only road to b is closed
        "f-c": "reachable",
        "f-x": "unknown_coverage",
    }
    assert body["segments"] == {
        "total": 3,
        "open": 1,
        "restricted": 0,
        "closed": 1,
        "unknown": 1,
    }
    assert "passability_unknown_segments" in body["warnings"]
    assert "risk_not_scored" in body["warnings"]

    foreign = client.get(
        "/v1/connectivity/summary", params={"district_id": OTHER_DISTRICT}, headers=AUTH
    )
    assert foreign.status_code == 403


def test_connectivity_without_hubs_or_network_is_explicit() -> None:
    empty = compute_connectivity(
        DistrictNetwork(
            district_id=DISTRICT,
            network_versions=[],
            baseline_as_of=None,
            edges=[],
            facilities=[],
        )
    )
    assert empty.coverage_state == "none"
    assert "no_network_imported" in empty.warnings

    no_hub = compute_connectivity(
        DistrictNetwork(
            district_id=DISTRICT,
            network_versions=["v"],
            baseline_as_of=None,
            edges=[("s", "a", "b", "open")],
            facilities=[
                {
                    "id": "f",
                    "name": "F",
                    "type": "clinic",
                    "metadata": {"routing_node_id": "b", "routing_eligible": True},
                }
            ],
            passability_counts={"open": 1},
        )
    )
    assert no_hub.coverage_state == "partial"
    assert "no_supply_hub_configured" in no_hub.warnings
    assert no_hub.facility_status[0].status == "unknown_coverage"


def test_scope_from_identity_handles_organization_wide_grants() -> None:
    record = IdentityRecord(
        profile=ProfileIdentity(
            id="p", user_id="u", display_name="X", locale="en", active=True
        ),
        organization=OrganizationIdentity(id=ORG, name="Org", mode="local_demo"),
        roles=[
            RoleGrantIdentity(
                id="g1",
                role="state_coordinator",
                district_id=None,
                valid_from=datetime(2000, 1, 1, tzinfo=UTC),
                valid_to=None,
            )
        ],
    )
    scope = scope_from_identity(record)
    assert scope.district_ids is None
    assert scope.allows_district(OTHER_DISTRICT)
    assert scope.has("network:read") and not scope.has("telemetry:submit")

    with pytest.raises(ApiError):
        scope.require("telemetry:submit")
