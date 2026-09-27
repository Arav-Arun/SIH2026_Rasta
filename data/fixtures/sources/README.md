# Recorded source fixtures

**Nothing in this folder is live or official data.** These are recorded
documents whose only purpose is to exercise the adapter pipeline in
`services/api/app/sources.py`: conditional reuse, checksums, parse failure and
run provenance.

| File | Shape | What it proves |
|---|---|---|
| `imd_rainfall.json` | Documented IMD district rainfall forecast JSON | A success run parses district records and normalises millimetres |
| `sachet_cap.xml` | OASIS CAP 1.2 alert (`status: Exercise`) | A success run parses a warning and normalises its severity |
| `malformed_cap.xml` | Well-formed XML that is not CAP | A malformed payload is a `failed` run that leaves stored data untouched |

Any run reading these is recorded with `source_mode = 'recorded'`, so no screen
can present them as live. RASTA has **no confirmed access** to the live IMD or
SACHET feeds yet; the same adapters read them once an endpoint is configured.
