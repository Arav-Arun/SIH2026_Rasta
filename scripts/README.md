# Scripts

Run everything from the repository root. Scripts that touch the database need
the local Supabase stack and the API virtualenv (`api/.venv`). Output
goes to the gitignored `artifacts/` folder.

| Path | Purpose |
|---|---|
| `local_demo.py` | Start, reset, check, back up, restore and stop the local demo |
| `pipeline/import_osm_pilot.py` | Build the Shillong road graph from the saved OpenStreetMap extract |
| `pipeline/generate_scenarios.py` | Build the six labelled synthetic scenarios |
| `pipeline/import_pilot_network.py` | Load the pilot graph into PostGIS |
| `pipeline/build_pilot_data_pack.py` | Build the offline data pack served to the web client |
| `pipeline/recorded_sources.py` | Re-date the recorded IMD and SACHET samples for a run |
| `tools/generate-api-contracts.mjs` | Export the OpenAPI schema and TypeScript types (`npm run contracts:generate`) |
| `tools/scan_secrets.py` | Fail if a credential is committed |
| `tools/translate_catalogues.py` | Draft missing language catalogue strings |
| `tools/apply_retention.py` | Apply the location-history retention policy |
| `ml/evaluate_risk_models.py` | Label audit for a supervised risk model |
| `lib/` | Helpers for starting the local stack and creating demo accounts |

The pipeline scripts need no network access or keys, and running them twice
produces byte-identical output.
