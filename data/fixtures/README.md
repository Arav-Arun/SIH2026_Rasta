# Deterministic fixtures

`synthetic_scenarios.json` contains the six versioned routing and offline
behavior scenarios used by the local demo. Every scenario has:

- `mode: synthetic`
- the visible label `SIMULATED SCENARIO`
- a clock anchored at `2000-01-01T00:00:00Z`
- only synthetic vehicles, consignments, weather values, incidents, telemetry,
  and constraints

OpenStreetMap contributes only the versioned topology and facility references.
The fixture does not claim any real closure, warning, trip, delivery, or bridge
capacity. Scenario S6 explicitly overlays a demonstration weight limit because the
selected source bridge has no recorded limit.

The scenarios are generated with seed `26002`, so the same graph always yields
identical fixture and manifest file hashes.
