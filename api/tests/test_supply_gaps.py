"""How an unmet request's deadline is judged, from what is on the way."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.main import create_app
from app.supply_gaps import AT_RISK_WINDOW, judge
from fastapi.testclient import TestClient
from tests.test_identity import FakeBearerTokenVerifier, FakeIdentityRepository

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
HOUR = timedelta(hours=1)


def test_a_passed_deadline_is_overdue_whatever_is_on_the_way() -> None:
    state, reason, _ = judge(needed_by=NOW - HOUR, arrivals=[NOW + HOUR], now=NOW)
    assert (state, reason) == ("overdue", "deadline_passed")


def test_something_arriving_in_time_is_on_track() -> None:
    state, reason, arrival = judge(
        needed_by=NOW + 6 * HOUR, arrivals=[NOW + 2 * HOUR, NOW + 8 * HOUR], now=NOW
    )
    assert (state, reason, arrival) == (
        "on_track",
        "arrives_before_deadline",
        NOW + 2 * HOUR,
    )


def test_arriving_after_the_deadline_is_at_risk() -> None:
    state, reason, arrival = judge(
        needed_by=NOW + 2 * HOUR, arrivals=[NOW + 3 * HOUR], now=NOW
    )
    assert (state, reason, arrival) == (
        "at_risk",
        "arrives_after_deadline",
        NOW + 3 * HOUR,
    )


def test_on_the_way_without_a_route_eta_is_unknown_not_on_track() -> None:
    state, reason, arrival = judge(needed_by=NOW + 6 * HOUR, arrivals=[None], now=NOW)
    assert (state, reason, arrival) == ("unknown", "no_route_eta", None)


def test_nothing_on_the_way_is_at_risk_only_close_to_the_deadline() -> None:
    near = judge(needed_by=NOW + AT_RISK_WINDOW, arrivals=[], now=NOW)
    far = judge(needed_by=NOW + AT_RISK_WINDOW + HOUR, arrivals=[], now=NOW)
    assert near[:2] == ("at_risk", "nothing_on_the_way")
    assert far[:2] == ("waiting", "nothing_on_the_way")


def test_a_request_with_no_deadline_says_so() -> None:
    assert judge(needed_by=None, arrivals=[], now=NOW)[:2] == (
        "no_deadline",
        "no_deadline",
    )


# --- the route ---------------------------------------------------------------

DISTRICT = "a2600002-0000-4000-8000-000000000002"
OTHER_DISTRICT = "b2600002-0000-4000-8000-0000000000b2"
AUTH = {"Authorization": "Bearer valid-token"}


@pytest.fixture
def client():
    app = create_app(
        Settings(database_url=""),
        bearer_token_verifier=FakeBearerTokenVerifier(),
        identity_repository=FakeIdentityRepository(),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_route_needs_a_session(client) -> None:
    assert client.get("/v1/supply-gaps").status_code == 401


def test_a_district_outside_the_grant_is_refused_before_anything_is_read(
    client,
) -> None:
    response = client.get(
        "/v1/supply-gaps", params={"district_id": OTHER_DISTRICT}, headers=AUTH
    )
    assert response.status_code == 403


def test_without_a_database_the_route_says_so(client) -> None:
    response = client.get(
        "/v1/supply-gaps", params={"district_id": DISTRICT}, headers=AUTH
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
