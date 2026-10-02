"""Segment state reducer for reviewed field decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

Passability = Literal["open", "restricted", "closed", "unknown"]
Impact = Literal["monitor", "restriction", "closure"]

IMPACT_TO_PASSABILITY: dict[Impact, Passability] = {
    "closure": "closed",
    "restriction": "restricted",
    "monitor": "open",
}


@dataclass(frozen=True, slots=True)
class CurrentState:
    passability: Passability
    network_version: str
    source_summary: dict[str, Any]
    as_of: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewedDecision:
    incident_id: str
    incident_type: str
    impact: Impact
    captured_at: datetime
    reviewed_at: datetime
    reviewer_profile_id: str
    reason: str
    base_graph_version: str


@dataclass(frozen=True, slots=True)
class ReducedState:
    passability: Passability
    network_version: str
    source_summary: dict[str, Any]
    as_of: datetime
    changed: bool
    outcome: Literal["applied", "unchanged", "superseded_by_later_closure"]


def _stamp(reviewed_at: datetime) -> str:
    return reviewed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _last_decision_capture(summary: dict[str, Any]) -> datetime | None:
    """When the road was last observed, whatever the decision did to it."""

    last = summary.get("last_decision") or {}
    captured = last.get("captured_at")
    if not captured:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(captured)))
    except ValueError:
        return None


def reduce_segment_state(
    current: CurrentState, decision: ReviewedDecision
) -> ReducedState:
    """Apply one approved decision to one segment."""

    target = IMPACT_TO_PASSABILITY[decision.impact]

    # An older observation cannot overwrite a newer one. A late review of a
    # Monday closure must not undo a Wednesday reopening, and a late "reopened"
    # report must not clear a closure that was seen afterwards.
    latest_capture = _last_decision_capture(current.source_summary)
    if latest_capture is not None and _as_utc(decision.captured_at) <= latest_capture:
        return ReducedState(
            passability=current.passability,
            network_version=current.network_version,
            source_summary={
                **current.source_summary,
                "ignored_decision": {
                    "incident_id": decision.incident_id,
                    "impact": decision.impact,
                    "captured_at": decision.captured_at.isoformat(),
                    "reason": "captured at or before the latest approved observation",
                },
            },
            as_of=current.as_of or decision.reviewed_at,
            changed=False,
            outcome="superseded_by_later_closure",
        )

    summary = {
        key: value
        for key, value in current.source_summary.items()
        if key not in ("passability_basis", "ignored_decision")
    }
    summary["passability_basis"] = (
        f"{decision.impact} confirmed by dispatcher; "
        f"field evidence captured {_as_utc(decision.captured_at).isoformat()}"
    )
    summary["last_decision"] = {
        "incident_id": decision.incident_id,
        "incident_type": decision.incident_type,
        "impact": decision.impact,
        "captured_at": decision.captured_at.isoformat(),
        "reviewed_at": decision.reviewed_at.isoformat(),
        "reviewer_profile_id": decision.reviewer_profile_id,
        "reason": decision.reason,
    }

    changed = target != current.passability
    # Rule 6: a new network version only when routable constraints change.
    network_version = (
        f"{decision.base_graph_version}.{_stamp(decision.reviewed_at)}"
        if changed
        else current.network_version
    )
    return ReducedState(
        passability=target,
        network_version=network_version,
        source_summary=summary,
        as_of=decision.reviewed_at,
        changed=changed,
        outcome="applied" if changed else "unchanged",
    )
