"""Run the sources, score the network, record what happened."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.risk_engine import (
    MODEL_NAME,
    MODEL_VERSION,
    Feature,
    RiskAssessment,
    assess,
    feature_schema_hash,
    snapshot_version,
)
from app.sources import (
    RunOutcome,
    SourceRecord,
    build_adapters,
    latest_records,
    run_source,
    store_records,
)
from app.terrain import load_terrain

#: A confirmed incident this recent counts as evidence about the road now.
INCIDENT_WINDOW = timedelta(days=7)

#: How many recent confirmed incidents on one segment reads as 1.0.
INCIDENTS_FOR_FULL_WEIGHT = 3


@dataclass
class PipelineResult:
    district_id: str
    runs: list[RunOutcome] = field(default_factory=list)
    segments_scored: int = 0
    segments_unscored: int = 0
    levels: dict[str, int] = field(default_factory=dict)
    risk_snapshot_version: str = ""
    model_version: str = MODEL_VERSION
    computed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "district_id": self.district_id,
            "model_version": self.model_version,
            "risk_snapshot_version": self.risk_snapshot_version,
            "computed_at": self.computed_at,
            "segments_scored": self.segments_scored,
            "segments_unscored": self.segments_unscored,
            "levels": self.levels,
            "runs": [run.as_dict() for run in self.runs],
        }


def register_model(connection: psycopg.Connection, *, organization_id: str) -> str:
    """Record the model so a score can be traced to a feature set and weights."""

    row = connection.execute(
        """
        insert into public.model_versions (
          organization_id, name, version, feature_schema_hash, metrics,
          training_manifest, active
        )
        values (
          %(org)s::uuid, %(name)s, %(version)s, %(hash)s, '{}'::jsonb,
          %(manifest)s, true
        )
        on conflict (organization_id, name, version) do update
          set feature_schema_hash = excluded.feature_schema_hash,
              training_manifest = excluded.training_manifest,
              active = true
        returning id::text as id
        """,
        {
            "org": organization_id,
            "name": MODEL_NAME,
            "version": MODEL_VERSION,
            "hash": feature_schema_hash(),
            "manifest": Jsonb(
                {
                    "kind": "documented_weighting",
                    "trained": False,
                    "evaluated_against_outcomes": False,
                    "reference": "baseline-v1 documented weighting",
                    "note": (
                        "Weights are a documented starting point for one district, "
                        "not a fitted result. No held-out evaluation exists because "
                        "no labelled outcome data exists."
                    ),
                }
            ),
        },
    ).fetchone()
    return row["id"]


def _district_name(
    connection: psycopg.Connection, *, organization_id: str, district_id: str
) -> str | None:
    row = connection.execute(
        """
        select d.name
        from public.districts as d
        join public.organization_districts as od on od.district_id = d.id
        where od.organization_id = %(org)s::uuid and d.id = %(district)s::uuid
        """,
        {"org": organization_id, "district": district_id},
    ).fetchone()
    return row["name"] if row else None


def _matches_area(record: SourceRecord, district_name: str | None) -> bool:
    """Whether an area-scoped record is about this district."""

    if not district_name:
        return False
    target = district_name.strip().lower()
    if record.subject_ref.strip().lower() == target:
        return True
    areas = record.value.get("areas") or []
    return any(str(area).strip().lower() == target for area in areas)


def _incident_counts(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    since: datetime,
) -> dict[str, int]:
    rows = connection.execute(
        """
        select isg.segment_id::text as segment_id, count(*)::int as hits
        from public.incident_segments as isg
        join public.incidents as i
          on i.id = isg.incident_id and i.organization_id = isg.organization_id
        where i.organization_id = %(org)s::uuid
          and i.district_id = %(district)s::uuid
          and i.status = 'confirmed'
          and i.reported_at >= %(since)s
        group by isg.segment_id
        """,
        {"org": organization_id, "district": district_id, "since": since},
    ).fetchall()
    return {row["segment_id"]: row["hits"] for row in rows}


def run_pipeline(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    imd_base_url: str | None,
    cap_base_url: str | None,
    fixture_root: Path | None,
    terrain_file: Path | None = None,
    now: datetime | None = None,
) -> PipelineResult:
    moment = now or datetime.now(tz=UTC)
    result = PipelineResult(district_id=district_id, computed_at=moment.isoformat())
    register_model(connection, organization_id=organization_id)
    district_name = _district_name(
        connection, organization_id=organization_id, district_id=district_id
    )

    # 1. Run every source, and keep whatever each one read.
    for adapter, mode in build_adapters(
        imd_base_url=imd_base_url, cap_base_url=cap_base_url, fixture_root=fixture_root
    ):
        outcome, records = run_source(
            connection,
            organization_id=organization_id,
            adapter=adapter,
            source_mode=mode,
            district_id=district_id,
        )
        if outcome.run_id is not None and records:
            store_records(
                connection,
                organization_id=organization_id,
                source=adapter.name,
                source_mode=mode,
                run_id=outcome.run_id,
                records=records,
            )
        result.runs.append(outcome)

    # 2. The features, from the latest reading of each kind, this run's or an
    #    earlier one's. Each keeps its own `observed_at`, so the engine's
    #    freshness rule still decides whether it may be used, and says so.
    district_features: dict[str, Feature] = {}
    for record, source, mode in latest_records(
        connection,
        organization_id=organization_id,
        kinds=("forecast_rainfall", "official_warning"),
    ):
        if not _matches_area(record, district_name):
            continue
        if record.kind == "forecast_rainfall":
            existing = district_features.get("forecast_rainfall")
            value = float(record.value.get("normalised", 0.0))
            if existing is None or record.observed_at > existing.observed_at:
                district_features["forecast_rainfall"] = Feature(
                    name="forecast_rainfall",
                    value=value,
                    observed_at=record.observed_at,
                    source=f"{source} ({mode})",
                    detail=record.value,
                )
        elif record.kind == "official_warning":
            existing = district_features.get("official_warning")
            value = float(record.value.get("severity_normalised", 0.0))
            # The most serious warning in force wins; a minor advisory does not
            # dilute a severe one.
            if existing is None or value > existing.value:
                district_features["official_warning"] = Feature(
                    name="official_warning",
                    value=value,
                    observed_at=record.observed_at,
                    source=f"{source} ({mode})",
                    detail=record.value,
                )

    # 3. Per-segment evidence.
    incidents = _incident_counts(
        connection,
        organization_id=organization_id,
        district_id=district_id,
        since=moment - INCIDENT_WINDOW,
    )
    segments = connection.execute(
        """
        select s.id::text as id, s.metadata ->> 'edge_id' as edge_id,
               scs.passability::text as passability
        from public.road_segments as s
        left join public.segment_current_state as scs
          on scs.segment_id = s.id and scs.organization_id = s.organization_id
        where s.organization_id = %(org)s::uuid and s.district_id = %(district)s::uuid
        order by s.id
        """,
        {"org": organization_id, "district": district_id},
    ).fetchall()

    # Terrain does not change with the weather, so it comes from a file rather than a
    # source run; a district with no terrain file simply lacks the input.
    terrain = load_terrain(terrain_file)

    assessments: list[RiskAssessment] = []
    levels: dict[str, int] = {}
    updates: list[dict[str, Any]] = []
    for segment in segments:
        features = list(district_features.values())
        if terrain is not None:
            terrain_feature = terrain.feature_for(segment["edge_id"])
            if terrain_feature is not None:
                features.append(terrain_feature)
        hits = incidents.get(segment["id"], 0)
        if hits:
            features.append(
                Feature(
                    name="recent_incidents",
                    value=min(1.0, hits / INCIDENTS_FOR_FULL_WEIGHT),
                    observed_at=moment,
                    source="confirmed incident reports",
                    detail={"confirmed_incidents_7d": hits},
                )
            )
        assessment = assess(features, now=moment, passability=segment["passability"])
        assessments.append(assessment)
        levels[assessment.level] = levels.get(assessment.level, 0) + 1

        if assessment.score is None:
            result.segments_unscored += 1
        else:
            result.segments_scored += 1

        updates.append(
            {
                "org": organization_id,
                "segment": segment["id"],
                "level": assessment.level,
                "score": assessment.score,
                # Null score keeps the model version too: "this model looked and
                # could not answer" is different from "nothing has looked".
                "model": MODEL_VERSION,
                "at": assessment.computed_at,
                "explanation": Jsonb(assessment.as_json()),
            }
        )

    # One pipelined batch rather than a round trip per segment: a district has
    # thousands of segments, and a hosted database is tens of milliseconds away.
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            update public.segment_current_state
            set risk_level = %(level)s::public.risk_level,
                risk_score = %(score)s,
                risk_model_version = %(model)s,
                risk_computed_at = %(at)s,
                risk_explanation = %(explanation)s,
                updated_at = now()
            where organization_id = %(org)s::uuid and segment_id = %(segment)s::uuid
            """,
            updates,
        )

    result.levels = levels
    result.risk_snapshot_version = snapshot_version(assessments)
    return result


__all__ = ["INCIDENT_WINDOW", "PipelineResult", "register_model", "run_pipeline"]
