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
