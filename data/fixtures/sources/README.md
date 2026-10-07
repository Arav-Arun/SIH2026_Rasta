# Recorded source fixtures

**Nothing in this folder is live or official data.** These are recorded
documents that the adapter pipeline in `api/app/sources.py` reads until a live
endpoint is configured, with the same conditional reuse, checksums and run
provenance as a live feed.

| File | Shape | What it proves |
|---|---|---|
| `imd_rainfall.json` | Documented IMD district rainfall forecast JSON | A success run parses district records and normalises millimetres |
| `sachet_cap.xml` | OASIS CAP 1.2 alert (`status: Exercise`) | A success run parses a warning and normalises its severity |

Any run reading these is recorded with `source_mode = 'recorded'`, so no screen
can present them as live. A deployment with `SACHET_CAP_BASE_URL` set reads
SACHET's public feed live instead of `sachet_cap.xml`. IMD's API admits only
whitelisted addresses, so `imd_rainfall.json` stays in use until access is granted.
