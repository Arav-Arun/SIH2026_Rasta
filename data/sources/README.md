# Pilot source data

This directory contains the bounded source extract used to build the first
routing graph. It is not a live road-status feed.

## OpenStreetMap extract

- Query: `osm_shillong_pilot.overpassql`
- Raw response: `osm_shillong_pilot.raw.json`
- Coverage query: south 25.560, west 91.875, north 25.585, east 91.900
- Purpose: reproducible road topology and named facility candidates for a
  small Shillong pilot
- Attribution: © OpenStreetMap contributors
- License: Open Data Commons Open Database License 1.0 (ODbL)
- License information: https://www.openstreetmap.org/copyright

The query requests vehicle-relevant road classes plus health, pharmacy,
marketplace, and warehouse features. It does not request map tiles. Ways may
extend beyond the query bounds because Overpass returns the complete members of
matching ways.

Do not combine this directory with data whose terms are unknown. GeoSadak is a
candidate production source, but it is not imported here until the team confirms
the exact redistribution terms for the selected extract.

## SRTM elevation

- Script: `build_terrain.py` downloads one 1 arc-second tile (`N25E091`), checks it against
  the pinned SHA-256 and writes `data/pilot/shillong_terrain.json` and
  `data/manifests/terrain_shillong_srtm.json`. Standard library only:
  `python data/sources/build_terrain.py`.
- Source: NASA/USGS Shuttle Radar Topography Mission, packaged as Tilezen terrain tiles on
  AWS Open Data.
- Attribution: United States 3DEP (formerly NED) and global GMTED2010 and SRTM terrain data
  courtesy of the U.S. Geological Survey.
- Terms: https://github.com/tilezen/joerd/blob/master/docs/attribution.md
- The 26 MB tile itself is not stored in the repository.
