"""SOS requests: what is accepted, and what raising one does in the database.

The database tests are skipped without DATABASE_URL and roll back everything.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from app.alerts import ALERT_TYPES
from app.capabilities import ROLE_CAPABILITIES
from app.config import Settings
from app.errors import ApiError
from app.scope import WorkspaceScope
from app.sos import SosRequest, raise_sos
from psycopg.rows import dict_row
from pydantic import ValidationError

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def request(**values) -> SosRequest:
    return SosRequest(**{"captured_at": NOW, **values})


def test_a_position_needs_both_coordinates_and_an_accuracy_needs_a_position() -> None:
    assert request().latitude is None
    assert request(latitude=25.57, longitude=91.88, accuracy_m=12).accuracy_m == 12
    for bad in (
        {"latitude": 25.57},
        {"longitude": 91.88},
        {"accuracy_m": 12},
        {"latitude": 95, "longitude": 91.88},
        {"note": "x" * 501},
        {"captured_at": datetime(2026, 10, 6, 12)},
    ):
        with pytest.raises(ValidationError):
            request(**bad)


def test_every_alert_type_has_an_english_title_for_the_inbox() -> None:
    catalogue = next(
        (
            parent / "apps" / "client" / "i18n" / "en.json"
            for parent in Path(__file__).resolve().parents
            if (parent / "apps" / "client" / "i18n" / "en.json").is_file()
        ),
        None,
    )
    if catalogue is None:
        pytest.skip("the web client is not next to the API")
    titles = json.loads(catalogue.read_text(encoding="utf-8")).get("alert", {})
    assert sorted(set(ALERT_TYPES) - set(titles)) == []


DATABASE_URL = Settings().database_url
needs_db = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")


@pytest.fixture
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def driver(db) -> WorkspaceScope:
    row = db.execute(
        """
        select p.id::text as profile, p.organization_id::text as org,
               ra.district_id::text as district
        from public.profiles as p
        join public.role_assignments as ra
          on ra.profile_id = p.id and ra.organization_id = p.organization_id
        where ra.role = 'driver' and ra.revoked_at is null and p.active
          and ra.district_id is not null
        order by p.id
        limit 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("no demo driver")
    return WorkspaceScope(
        organization_id=row["org"],
        profile_id=row["profile"],
        roles=("driver",),
        capabilities=frozenset(ROLE_CAPABILITIES["driver"]),
        district_ids=frozenset({row["district"]}),
    )


@needs_db
def test_an_sos_reaches_the_districts_dispatchers_once_per_request(db, driver) -> None:
    now = datetime.now(UTC)
    sos = request(
        captured_at=now - timedelta(minutes=2),
        latitude=25.57,
        longitude=91.88,
        accuracy_m=9,
        note="Vehicle stuck, slope moving",
    )
    key = str(uuid.uuid4())
    first = raise_sos(db, scope=driver, request=sos, idempotency_key=key, now=now)
    assert first.recipients >= 1
    assert first.calls_emergency_services is False

    alert = db.execute(
        "select type, severity, title_key, district_id::text as district, payload "
        "from public.alerts where id = %s::uuid",
        (first.alert_id,),
    ).fetchone()
    assert (alert["type"], alert["severity"], alert["title_key"]) == (
        "sos",
        "critical",
        "alert.sos",
    )
    assert alert["district"] == next(iter(driver.district_ids))
    assert alert["payload"]["position_known"] is True
    assert alert["payload"]["accuracy_m"] == 9
    assert alert["payload"]["note"] == "Vehicle stuck, slope moving"

    # The phone's outbox sends the same request again: same answer, nothing new.
    again = raise_sos(db, scope=driver, request=sos, idempotency_key=key, now=now)
    assert again == first

    # A second press a minute later updates the same alert with the newer fix.
    later = request(captured_at=now - timedelta(minutes=1))
    second = raise_sos(
        db, scope=driver, request=later, idempotency_key=str(uuid.uuid4()), now=now
    )
    assert second.alert_id == first.alert_id
    payload = db.execute(
        "select payload from public.alerts where id = %s::uuid", (first.alert_id,)
    ).fetchone()["payload"]
    assert payload["position_known"] is False


@needs_db
def test_an_sos_about_an_unknown_trip_or_from_the_future_is_refused(db, driver) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ApiError) as missing:
        raise_sos(
            db,
            scope=driver,
            request=request(captured_at=now, trip_id=uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            now=now,
        )
    assert missing.value.status_code == 404
    with pytest.raises(ApiError) as future:
        raise_sos(
            db,
            scope=driver,
            request=request(captured_at=now + timedelta(hours=1)),
            idempotency_key=str(uuid.uuid4()),
            now=now,
        )
    assert future.value.status_code == 422
