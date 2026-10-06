"""Predicted against observed: what the risk score said, and what was confirmed next.

Each road's highest level on one day is set against confirmed incidents reported on
that road the following day. Not the same day: a confirmed incident raises its own
road's score through the recent-incidents input, so a same-day comparison would
credit the score with what it had already been told.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg
from pydantic import BaseModel

#: Days between the scores and the incidents they are judged against.
LEAD_DAYS = 1

LEVEL_ORDER = ("critical", "high", "moderate", "low", "unknown", "not_scored")


class OutcomeRow(BaseModel):
    model_version: str | None
    #: A risk level, or ``not_scored`` for incidents on roads with no score the day before.
    level: str
    #: Road-days at this level; each road counts once a day, at its highest level.
    road_days: int
    #: Of those, the ones with a confirmed incident reported the next day.
    road_days_with_confirmed_incident: int


class RiskOutcomesResponse(BaseModel):
    district_id: str
    lead_days: int
    first_score_day: date
    last_score_day: date
    rows: list[OutcomeRow]
    confirmed_incident_road_days: int
    notes: list[str]


def outcome_report(
    connection: psycopg.Connection,
    *,
    organization_id: str,
    district_id: str,
    days: int,
    today: date,
) -> RiskOutcomesResponse:
    """Scores from the ``days`` days ending yesterday, against the day after each."""

    last_score_day = today - timedelta(days=LEAD_DAYS)
    first_score_day = last_score_day - timedelta(days=days - 1)
    window = {
        "org": organization_id,
        "district": district_id,
        "first": first_score_day,
        "last": last_score_day,
        "lead": LEAD_DAYS,
    }
    observed = """
        select distinct isg.segment_id,
               (i.reported_at at time zone 'UTC')::date - %(lead)s::int as score_day
        from public.incident_segments as isg
        join public.incidents as i
          on i.id = isg.incident_id and i.organization_id = isg.organization_id
        where i.organization_id = %(org)s::uuid
          and i.district_id = %(district)s::uuid
          and i.status = 'confirmed'
          and (i.reported_at at time zone 'UTC')::date - %(lead)s::int
              between %(first)s and %(last)s
    """
    rows = connection.execute(
        f"""
        with predicted as (
          select segment_id, day as score_day, model_version, max_level
          from public.risk_daily_predictions
          where organization_id = %(org)s::uuid and district_id = %(district)s::uuid
            and day between %(first)s and %(last)s
        ),
        observed as ({observed})
        select p.model_version,
               coalesce(p.max_level::text, 'not_scored') as level,
               count(p.segment_id)::int as road_days,
               count(o.segment_id)::int as with_incident
        from predicted as p
        full join observed as o
          on o.segment_id = p.segment_id and o.score_day = p.score_day
        group by 1, 2
        """,
        window,
    ).fetchall()
    total = connection.execute(
        f"select count(*)::int as n from ({observed}) as observed", window
    ).fetchone()["n"]

    ordered = sorted(
        rows,
        key=lambda row: (
            row["model_version"] is None,
            row["model_version"] or "",
            LEVEL_ORDER.index(row["level"]),
        ),
    )
    return RiskOutcomesResponse(
        district_id=district_id,
        lead_days=LEAD_DAYS,
        first_score_day=first_score_day,
        last_score_day=last_score_day,
        rows=[
            OutcomeRow(
                model_version=row["model_version"],
                level=row["level"],
                road_days=row["road_days"],
                road_days_with_confirmed_incident=row["with_incident"],
            )
            for row in ordered
        ],
        confirmed_incident_road_days=total,
        notes=[
            "Days are UTC calendar days. Each road counts once a day, at the highest "
            "level it reached that day.",
            "An outcome is a confirmed incident: a field report a dispatcher reviewed "
            "and confirmed on that road, reported the day after the score. Roads "
            "nobody reported on count as having none, which is not proof that "
            "nothing happened.",
            "This shows how the score has fared so far. It is not a validated "
            "accuracy figure, and the score is not a trained model.",
        ],
    )


__all__ = ["LEAD_DAYS", "OutcomeRow", "RiskOutcomesResponse", "outcome_report"]
