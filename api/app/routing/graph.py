"""Loads a district's directed road graph out of PostGIS for route planning."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from app.routing.core import Edge

#: Road classes with no `base_speed_kph` on the segment fall back to these.
PILOT_ROAD_CLASS_SPEED_KPH: dict[str, float] = {
    "motorway": 70.0,
    "trunk": 60.0,
    "primary": 50.0,
    "secondary": 45.0,
    "tertiary": 40.0,
    "unclassified": 30.0,
    "residential": 25.0,
    "living_street": 15.0,
    "service": 15.0,
    "track": 15.0,
    "path": 10.0,
}


@dataclass(frozen=True)
class DistrictGraph:
    edges: list[Edge]
    node_count: int
    network_version: str
    risk_snapshot_version: str
    loaded_at: str
    #: Segments whose current state has never been observed.
    unobserved_segment_count: int


_GRAPH_SELECT = """
    select
      rs.id::text as segment_id,
      rs.from_node_id,
      rs.to_node_id,
      rs.length_m::float8 as length_m,
      rs.road_class,
      rs.base_speed_kph::float8 as base_speed_kph,
      rs.max_weight_t::float8 as segment_max_weight_t,
      rs.network_version,
      coalesce(scs.passability::text, 'unknown') as passability,
      coalesce(scs.risk_level::text, 'unknown') as risk_level,
      scs.risk_score::float8 as risk_score,
      scs.as_of as state_as_of,
      scs.network_version as state_network_version,
      b.bridge_id,
      b.bridge_max_weight_t
    from public.road_segments as rs
    left join public.segment_current_state as scs
      on scs.segment_id = rs.id and scs.organization_id = rs.organization_id
    left join lateral (
      select br.id::text as bridge_id, br.max_weight_t::float8 as bridge_max_weight_t
      from public.bridges as br
      where br.segment_id = rs.id and br.organization_id = rs.organization_id
      order by br.created_at
      limit 1
    ) as b on true
    where rs.organization_id = %(org)s::uuid
      and rs.district_id = %(district)s::uuid
    order by rs.id
"""


def _version_of(values: list[str]) -> str:
    """One version string for a set of per-row versions."""

    distinct = sorted(set(values))
    if len(distinct) == 1:
        return distinct[0]
    digest = hashlib.sha256("|".join(distinct).encode("utf-8")).hexdigest()
    return f"mixed-{digest[:16]}"


def load_district_graph(
    connection: psycopg.Connection, *, organization_id: str, district_id: str
) -> DistrictGraph:
    rows: list[dict[str, Any]] = connection.execute(
        _GRAPH_SELECT, {"org": organization_id, "district": district_id}
    ).fetchall()

    edges: list[Edge] = []
    nodes: set[str] = set()
    network_versions: list[str] = []
    state_versions: list[str] = []
    state_stamps: list[datetime] = []
    risk_tokens: list[str] = []
    unobserved = 0

    for row in rows:
        segment_id = row["segment_id"]
        score = row["risk_score"]
        score_text = "none" if score is None else f"{float(score):.4f}"
        risk_tokens.append(f"{segment_id}:{row['risk_level']}:{score_text}")
        # The bridge's own limit wins where both exist: it is the narrower
        # statement about the same piece of road.
        max_weight = row["bridge_max_weight_t"]
        if max_weight is None:
            max_weight = row["segment_max_weight_t"]

        state_as_of = row["state_as_of"]
        if row["state_as_of"] is None:
            unobserved += 1
        else:
            state_stamps.append(row["state_as_of"])
        if row["state_network_version"]:
            state_versions.append(row["state_network_version"])

        network_versions.append(row["network_version"])
        nodes.add(row["from_node_id"])
        nodes.add(row["to_node_id"])

        edges.append(
            Edge(
                # The segment row is already one direction, so the segment id is
                # a stable edge id and the two never drift apart.
                edge_id=segment_id,
                segment_id=segment_id,
                from_node=row["from_node_id"],
                to_node=row["to_node_id"],
                length_m=float(row["length_m"]),
                road_class=row["road_class"],
                base_speed_kph=row["base_speed_kph"],
                max_weight_t=max_weight,
                max_height_m=None,
                is_bridge=row["bridge_id"] is not None,
                passability=row["passability"],
                risk_score=row["risk_score"],
                state_as_of=state_as_of.isoformat() if state_as_of else None,
            )
        )

    network_version = _version_of(network_versions) if network_versions else "empty"
    # No snapshot table: the token is whatever this load actually used. The risk
    # digest is the scores themselves, so a recompute that rewrites the same
    # numbers does not invalidate a plan, and a score that changes does.
    risk_digest = hashlib.sha256("\n".join(risk_tokens).encode("utf-8")).hexdigest()[
        :16
    ]
    if not rows:
        risk_version = "state-unobserved"
    elif state_versions or state_stamps:
        latest = max(state_stamps).isoformat() if state_stamps else "none"
        risk_version = (
            f"state-{_version_of(state_versions or ['none'])}@{latest}+{risk_digest}"
        )
    else:
        risk_version = f"state-unobserved+{risk_digest}"

    return DistrictGraph(
        edges=edges,
        node_count=len(nodes),
        network_version=network_version,
        risk_snapshot_version=risk_version,
        loaded_at=datetime.now(UTC).isoformat(),
        unobserved_segment_count=unobserved,
    )


__all__ = ["DistrictGraph", "PILOT_ROAD_CLASS_SPEED_KPH", "load_district_graph"]
