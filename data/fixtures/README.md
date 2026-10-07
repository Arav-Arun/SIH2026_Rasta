# Deterministic fixtures

`synthetic_scenarios.json` contains the eleven versioned routing and offline
behavior scenarios that the routing replay (`python -m app.routing.replay`)
scores. Every scenario has:

- `mode: synthetic`
- the visible label `SIMULATED SCENARIO`
- a clock anchored at `2000-01-01T00:00:00Z`
- only synthetic vehicles, consignments, weather values, incidents, telemetry,
  and constraints

OpenStreetMap contributes only the versioned topology and facility references.
The fixture does not claim any real closure, warning, trip, delivery, or bridge
capacity. Scenario S6 explicitly overlays a demonstration weight limit because the
selected source bridge has no recorded limit. Scenario S8 leaves a bridge's limit
unknown on purpose, to check that the planner flags it for review rather than
inventing one.

S1 to S6 were generated with seed `26002`. S7 to S11 were written by hand on the
same graph and conventions: a restricted road, a bridge with no recorded limit,
three closures at once, and an orange and a red warning on the same road to show
that the risk penalty changes route cost, never the ETA of a given route. The
manifest lists the hand-written ones and records the file's checksum, which a test
checks.
