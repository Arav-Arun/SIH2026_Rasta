# Shillong pilot routing data

This folder contains a **baseline topology**, not current road conditions.

| File | Purpose |
|---|---|
| `shillong_graph.json` | Directed road graph clipped to the documented query bounds and pruned to its largest weakly connected component |
| `shillong_facilities.json` | OSM-sourced health, pharmacy, marketplace, and warehouse candidates snapped to the retained graph |
| `shillong_terrain.json` | Slope of the ground around each road edge, from SRTM elevation (`data/sources/build_terrain.py`): the 90th-percentile slope within 45 m, the mean, and the samples used. It describes the terrain, not whether it will fail |
| `scenario_endpoints.json` | Deterministically selected graph references for S1–S6; it contains no incident, delivery, warning, or vehicle claim |

Current validated graph version: `osm-shillong-4d449d18c4666431`.

The graph has 1,236 nodes and 2,860 directed edges. All baseline passability is
`unknown`. Speeds are populated only when the source contains a parseable
`maxspeed`; bridge weight limits remain null when the source does not provide
them. Constraints and events are added only as clearly labelled simulated
scenario overlays.

Output files are deterministic for the same raw source, query, and importer
version.

## Licence and attribution

The graph is derived from OpenStreetMap and is covered by the data notice in
`data/OSM-NOTICE.md`. Any screen that later renders this geography must show
"© OpenStreetMap contributors" with a link to
https://www.openstreetmap.org/copyright.
