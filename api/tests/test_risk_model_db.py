"""A trained model in shadow, against the local database, with a SYNTHETIC model.

Skipped without DATABASE_URL. Runs inside a transaction that is rolled back. The
model file and its inputs are made up to exercise the plumbing; they say nothing
about any real road.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app import risk_model
from app.config import Settings
from app.recorded_samples import redate_recorded_sources
from app.risk_engine import MODEL_VERSION
from app.risk_model import ModelInput, load_model
from app.risk_pipeline import run_pipeline
from psycopg.rows import dict_row

DATABASE_URL = Settings().database_url
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs DATABASE_URL")

DATASET = "5e" * 32


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
               count(*) as segments, min(s.id::text) as road
        from public.road_segments as s
        group by 1, 2
        order by count(*) desc
        limit 1
        """
    ).fetchone()
    if row is None or row["segments"] < 10:
        pytest.skip("no imported road network")
    return row


def model_file(tmp_path, *, coefficient: float = 0.8):
    model = {
        "schema_version": "rasta-risk-model-v1",
        "name": "synthetic-test-logistic",
        "version": f"lr-{DATASET[:12]}",
        "trained": True,
        "features": [
            {
                "name": "rain_1d",
                "meaning": "rainfall on the day, mm",
                "transform": "log1p",
                "mean": 1.0,
                "scale": 1.2,
                "coefficient": coefficient,
            }
        ],
        "intercept": -5.0,
        "operating_threshold": 0.05,
        "manifest_sha256": "7a" * 32,
        "dataset_sha256": DATASET,
        "test_metrics": {"pr_auc": 0.2},
        "decision": {"passed": True, "reasons": []},
        "created_at": "2026-10-06T00:00:00+00:00",
    }
    path = tmp_path / f"model-{coefficient}.json"
    content = (json.dumps(model, sort_keys=True) + "\n").encode()
    path.write_bytes(content)
    return load_model(path, hashlib.sha256(content).hexdigest())


def run(db, district, tmp_path, shadow, now):
    redate_recorded_sources(tmp_path, now=now)
    return run_pipeline(
        db,
        organization_id=district["org"],
        district_id=district["district"],
        imd_base_url=None,
        cap_base_url=None,
        fixture_root=tmp_path,
        now=now,
        shadow=shadow,
    )


def test_a_shadow_model_is_logged_beside_the_baseline_and_changes_nothing(
    db, district, tmp_path, monkeypatch
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    road = district["road"]

    def one_road(connection, *, organization_id, district_id, segment_ids, now):
        return {road: {"rain_1d": ModelInput(120.0, now - timedelta(hours=2), "test")}}

    monkeypatch.setattr(risk_model, "supplied_inputs", one_road)
    model = model_file(tmp_path)
    db.execute(
        "delete from public.model_versions where organization_id = %s::uuid "
        "and name = %s",
        (district["org"], model.name),
    )
    result = run(db, district, tmp_path, model, now)

    assert result.shadow_model == {
        "version": model.version,
        "mode": "shadow",
        "roads_with_an_opinion": 1,
        "roads_without": district["segments"] - 1,
        "inputs_missing": ["rain_1d"],
    }
    registered = db.execute(
        "select active, training_manifest from public.model_versions "
        "where organization_id = %s::uuid and name = %s and version = %s",
        (district["org"], model.name, model.version),
    ).fetchone()
    assert registered["active"] is False
    assert registered["training_manifest"]["trained"] is True
    assert registered["training_manifest"]["mode"] == "shadow"

    state = db.execute(
        "select risk_model_version, risk_explanation from public.segment_current_state "
        "where segment_id = %s::uuid",
        (road,),
    ).fetchone()
    # The road's score, and so its route cost and alerts, stay the baseline's.
    assert state["risk_model_version"] == MODEL_VERSION
    shadow = state["risk_explanation"]["shadow"]
    assert shadow["model_version"] == model.version
    assert shadow["mode"] == "shadow"

    logged = db.execute(
        "select model_version from public.risk_daily_predictions "
        "where organization_id = %s::uuid and segment_id = %s::uuid and day = %s",
        (district["org"], road, now.date()),
    ).fetchall()
    assert {row["model_version"] for row in logged} == {MODEL_VERSION, model.version}


def test_a_different_model_under_a_registered_version_is_refused(
    db, district, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(risk_model, "supplied_inputs", lambda *a, **k: {})
    first = model_file(tmp_path)
    db.execute(
        "delete from public.model_versions where organization_id = %s::uuid "
        "and name = %s",
        (district["org"], first.name),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    assert run(db, district, tmp_path, first, now).shadow_model is not None

    # Same name and version, different coefficients: refused, and the run goes on
    # with the baseline alone.
    impostor = model_file(tmp_path, coefficient=3.0)
    assert impostor.version == first.version
    result = run(db, district, tmp_path, impostor, now + timedelta(minutes=1))
    assert result.shadow_model is None
    assert result.segments_scored + result.segments_unscored == district["segments"]
