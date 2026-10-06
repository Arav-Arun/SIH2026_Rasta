"""The outcome log, its report and the scheduled round, against the local database.

Skipped without DATABASE_URL (as in CI). Everything runs in a transaction that is
rolled back, except the scheduled round, which is only ever let as far as its
lock and its list of districts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import psycopg
import pytest
from app import risk_schedule
from app.config import Settings
from app.recorded_samples import redate_recorded_sources
from app.risk_outcomes import outcome_report
from app.risk_pipeline import run_pipeline
from app.risk_schedule import ROUND_LOCK_KEY, run_round
from psycopg.rows import dict_row

DATABASE_URL = Settings().database_url
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")


@pytest.fixture
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def district(db) -> dict:
    present = db.execute(
        "select to_regclass('public.risk_daily_predictions') is not null as ok"
    ).fetchone()["ok"]
    if not present:
        pytest.skip("the outcome-log migration is not applied")
    row = db.execute(
        """
        select s.organization_id::text as org, s.district_id::text as district,
               count(*) as segments, array_agg(s.id::text order by s.id) as ids
        from public.road_segments as s
        group by 1, 2
        order by count(*) desc
        limit 1
        """
    ).fetchone()
    if row is None or row["segments"] < 10:
        pytest.skip("no imported road network")
    return row


def logged(db, district: dict, day: date) -> dict:
    return db.execute(
        """
        select count(*)::int as roads, min(runs) as fewest, max(runs) as most
        from public.risk_daily_predictions
        where organization_id = %s::uuid and district_id = %s::uuid and day = %s
        """,
        (district["org"], district["district"], day),
    ).fetchone()


def test_each_run_folds_into_one_row_per_road_and_day(db, district, tmp_path) -> None:
    noon = datetime.combine(datetime.now(UTC).date(), time(12), tzinfo=UTC)
    # Runs this database already logged today would add to the count.
    db.execute(
        "delete from public.risk_daily_predictions where organization_id = %s::uuid "
        "and district_id = %s::uuid and day = %s",
        (district["org"], district["district"], noon.date()),
    )
    for moment in (noon, noon + timedelta(minutes=30)):
        redate_recorded_sources(tmp_path, now=moment)
        run_pipeline(
            db,
            organization_id=district["org"],
            district_id=district["district"],
            imd_base_url=None,
            cap_base_url=None,
            fixture_root=tmp_path,
            now=moment,
        )
    scored = db.execute(
        """
        select count(*)::int as n
        from public.segment_current_state as scs
        join public.road_segments as s
          on s.id = scs.segment_id and s.organization_id = scs.organization_id
        where s.organization_id = %s::uuid and s.district_id = %s::uuid
        """,
        (district["org"], district["district"]),
    ).fetchone()["n"]
    day = logged(db, district, noon.date())
    assert day["roads"] == scored > 0
    assert day["fewest"] == day["most"] == 2


def predict(db, district: dict, segment: str, day: date, level: str) -> None:
    db.execute(
        """
        insert into public.risk_daily_predictions (
          organization_id, district_id, segment_id, day, model_version,
          max_score, max_level, runs, first_computed_at, last_computed_at
        )
        values (%s::uuid, %s::uuid, %s::uuid, %s, 'baseline-v1', null,
                %s::public.risk_level, 1, %s, %s)
        """,
        (
            district["org"],
            district["district"],
            segment,
            day,
            level,
            datetime.combine(day, time(9), tzinfo=UTC),
            datetime.combine(day, time(9), tzinfo=UTC),
        ),
    )


def confirmed_incident(db, district: dict, segment: str, reported: datetime) -> None:
    incident = db.execute(
        """
        insert into public.incidents (
          organization_id, district_id, type, status, reported_at, captured_at,
          location, source_id, idempotency_key
        )
        values (
          %s::uuid, %s::uuid, 'landslide', 'confirmed', %s, %s,
          extensions.st_setsrid(extensions.st_makepoint(91.88, 25.57), 4326),
          'outcome-log test', %s::uuid
        )
        returning id::text as id
        """,
        (district["org"], district["district"], reported, reported, str(uuid.uuid4())),
    ).fetchone()["id"]
    db.execute(
        """
        insert into public.incident_segments (organization_id, incident_id, segment_id, impact)
        values (%s::uuid, %s::uuid, %s::uuid, 'closure')
        """,
        (district["org"], incident, segment),
    )


def test_a_days_scores_are_set_against_the_next_days_confirmed_incidents(
    db, district
) -> None:
    flagged, quiet, unscored = district["ids"][:3]
    day = date(2026, 9, 1)
    predict(db, district, flagged, day, "high")
    predict(db, district, quiet, day, "low")
    # Next day: an incident on the flagged road, and one on a road nobody scored.
    next_morning = datetime.combine(day + timedelta(days=1), time(10), tzinfo=UTC)
    confirmed_incident(db, district, flagged, next_morning)
    confirmed_incident(db, district, unscored, next_morning)
    # Same day as the scores: it belongs to the day before's scores, not these.
    confirmed_incident(db, district, quiet, datetime.combine(day, time(15), tzinfo=UTC))

    report = outcome_report(
        db,
        organization_id=district["org"],
        district_id=district["district"],
        days=2,
        today=day + timedelta(days=1),
    )
    assert (report.first_score_day, report.last_score_day) == (
        day - timedelta(days=1),
        day,
    )
    table = {
        (row.model_version, row.level): (
            row.road_days,
            row.road_days_with_confirmed_incident,
        )
        for row in report.rows
    }
    assert table == {
        ("baseline-v1", "high"): (1, 1),
        ("baseline-v1", "low"): (1, 0),
        (None, "not_scored"): (0, 2),
    }
    assert report.confirmed_incident_road_days == 3
    assert [row.level for row in report.rows] == ["high", "low", "not_scored"]


def test_a_round_another_process_holds_is_skipped(db) -> None:
    # This transaction holds the round; the scheduled round must step aside.
    db.execute("select pg_advisory_xact_lock(%s)", (ROUND_LOCK_KEY,))
    assert run_round(Settings()) is None


def test_a_round_scores_each_district_unless_it_was_scored_just_now(
    monkeypatch, tmp_path, district
) -> None:
    scored: list[str] = []

    def fake_pipeline(connection, **kwargs):
        scored.append(kwargs["district_id"])
        return SimpleNamespace(as_dict=lambda: {"district_id": kwargs["district_id"]})

    monkeypatch.setattr(risk_schedule, "run_pipeline", fake_pipeline)
    config = Settings(app_mode="local_demo", source_fixture_root=str(tmp_path))

    results = run_round(config)
    assert district["district"] in scored
    assert {"district_id": district["district"]} in results
    assert (tmp_path / "sachet_cap.xml").is_file(), "the demo's own copy is re-dated"

    scored.clear()
    assert run_round(config, min_gap=timedelta(days=36500)) == []
    assert scored == []


def test_the_log_keeps_two_years_and_refuses_a_window_shorter_than_a_month(
    db, district
) -> None:
    segment = district["ids"][0]
    today = datetime.now(UTC).date()
    old, recent = today - timedelta(days=800), today - timedelta(days=10)
    db.execute(
        "delete from public.risk_daily_predictions where organization_id = %s::uuid "
        "and segment_id = %s::uuid and day in (%s, %s)",
        (district["org"], segment, old, recent),
    )
    predict(db, district, segment, old, "low")
    predict(db, district, segment, recent, "low")

    assert db.execute("select public.trim_outcome_log(730) as n").fetchone()["n"] >= 1
    kept = {
        row["day"]
        for row in db.execute(
            "select day from public.risk_daily_predictions where organization_id = "
            "%s::uuid and segment_id = %s::uuid and day in (%s, %s)",
            (district["org"], segment, old, recent),
        )
    }
    assert kept == {recent}
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("select public.trim_outcome_log(7)")
