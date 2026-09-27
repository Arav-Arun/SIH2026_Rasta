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
can present them as live. RASTA has **no confirmed access** to the live IMD or
SACHET feeds yet; the same adapters read them once an endpoint is configured.
