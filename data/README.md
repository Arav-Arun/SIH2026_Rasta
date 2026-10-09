# Data

Nothing in this folder is live road status. It holds the pilot's baseline road
network, recorded source samples and synthetic scenarios, each labelled as such.

| Path | What it is |
|---|---|
| `pilot/shillong_graph.json` | Directed road graph of central Shillong from OpenStreetMap: 1,236 nodes and 2,860 edges, the largest connected component of the query area. Every road starts as `unknown`; speeds come only from a parseable `maxspeed`, and bridge limits stay null when OSM has none |
| `pilot/shillong_facilities.json` | 30 health, pharmacy, market and warehouse facilities from OpenStreetMap, snapped to the graph |
| `pilot/shillong_terrain.json` | Slope around each road edge from SRTM elevation: the 90th-percentile slope within 45 m, the mean, and the samples used. It describes the terrain, not whether it will fail |
| `sources/osm_shillong_pilot.overpassql` | The Overpass query for the extract (south 25.560, west 91.875, north 25.585, east 91.900) |
| `sources/build_terrain.py` | Rebuilds the terrain file: downloads SRTM tile `N25E091` (26 MB, not stored), checks its pinned SHA-256 and writes the terrain file and its manifest. Standard library only: `python data/sources/build_terrain.py` |
| `fixtures/sources/` | Recorded samples, not live or official data: an IMD-format district rainfall forecast and a CAP 1.2 alert marked `Exercise`. Runs that read them are stored with `source_mode = 'recorded'`. A deployment with `SACHET_CAP_BASE_URL` set reads SACHET live instead; IMD's sample stays in use until IMD grants access |
| `fixtures/synthetic_scenarios.json` | The eleven routing scenarios the replay scores (`python -m app.routing.replay`), labelled `SIMULATED SCENARIO` with a clock at `2000-01-01`. Only the topology and facility references are real; every vehicle, closure, warning and limit is synthetic. S1 to S6 were generated with seed `26002`, S7 to S11 written by hand |
| `manifests/` | Provenance for the files above: sources, licences, query and output checksums |

The OSM import and the scenario generator ran outside this repository; their
versions and the checksums of their inputs, including the raw Overpass response,
are kept in `manifests/`.

## Licences and attribution

Road and facility data © OpenStreetMap contributors, available under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
(https://www.openstreetmap.org/copyright). Any redistributed derivative database
must keep the ODbL's attribution and share-alike terms; the application code is
not covered by the ODbL because it reads the data. Screens that show this
geography credit "© OpenStreetMap contributors" with a link to that page.

Terrain: United States 3DEP (formerly NED) and global GMTED2010 and SRTM terrain
data courtesy of the U.S. Geological Survey, packaged as Tilezen terrain tiles
on AWS Open Data ([terms](https://github.com/tilezen/joerd/blob/master/docs/attribution.md)).
