from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.reducer import CurrentState, ReviewedDecision, reduce_segment_state

BASE = "osm-shillong-4d449d18c4666431"
T0 = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 18, 7, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)


def decision(
    impact: str,
    captured_at: datetime,
    reviewed_at: datetime,
    incident_id: str = "inc-1",
):
    return ReviewedDecision(
        incident_id=incident_id,
        incident_type="landslide_debris" if impact != "monitor" else "road_reopened",
        impact=impact,  # type: ignore[arg-type]
        captured_at=captured_at,
        reviewed_at=reviewed_at,
        reviewer_profile_id="disp-1",
        reason="verified photo",
        base_graph_version=BASE,
    )


def baseline() -> CurrentState:
    return CurrentState(
        passability="unknown",
        network_version=BASE,
        source_summary={
            "baseline": "osm_import",
            "passability_basis": "no observation recorded",
        },
        as_of=T0,
    )


def test_confirmed_closure_changes_state_and_mints_a_new_version() -> None:
    reduced = reduce_segment_state(baseline(), decision("closure", T0, T1))
    assert reduced.passability == "closed"
    assert reduced.changed is True
    assert reduced.outcome == "applied"
    assert reduced.network_version.startswith(BASE + ".")
    assert reduced.network_version != BASE
    assert reduced.source_summary["last_decision"]["impact"] == "closure"
    assert "confirmed by dispatcher" in reduced.source_summary["passability_basis"]
    assert reduced.as_of == T1


def test_same_passability_keeps_the_network_version() -> None:
    closed = reduce_segment_state(baseline(), decision("closure", T0, T1))
    again = reduce_segment_state(
        CurrentState(
            closed.passability,
            closed.network_version,
            closed.source_summary,
            closed.as_of,
        ),
        decision("closure", T1, T2, incident_id="inc-2"),
    )
    assert again.changed is False
    assert again.outcome == "unchanged"
    assert again.network_version == closed.network_version
    # the newer decision is still recorded as the latest one
    assert again.source_summary["last_decision"]["incident_id"] == "inc-2"


def test_reopen_captured_after_closure_reopens_the_road() -> None:
    closed = reduce_segment_state(baseline(), decision("closure", T0, T1))
    reopened = reduce_segment_state(
        CurrentState(
            closed.passability,
            closed.network_version,
            closed.source_summary,
            closed.as_of,
        ),
        decision("monitor", T2, T2, incident_id="inc-3"),
    )
    assert reopened.passability == "open"
    assert reopened.changed is True
    assert reopened.network_version != closed.network_version


def test_stale_reopen_never_clears_a_later_closure() -> None:
    closed = reduce_segment_state(baseline(), decision("closure", T1, T2))
    stale = reduce_segment_state(
        CurrentState(
            closed.passability,
            closed.network_version,
            closed.source_summary,
            closed.as_of,
        ),
        decision("monitor", T0, T2, incident_id="inc-old"),
    )
    assert stale.passability == "closed"
    assert stale.changed is False
    assert stale.outcome == "superseded_by_later_closure"
    assert stale.network_version == closed.network_version
    assert stale.source_summary["ignored_decision"]["incident_id"] == "inc-old"
    # the accepted closure remains the last decision
    assert stale.source_summary["last_decision"]["impact"] == "closure"


@pytest.mark.parametrize(
    ("impact", "expected"),
    [("closure", "closed"), ("restriction", "restricted"), ("monitor", "open")],
)
def test_impact_maps_to_passability(impact: str, expected: str) -> None:
    assert (
        reduce_segment_state(baseline(), decision(impact, T0, T1)).passability
        == expected
    )
