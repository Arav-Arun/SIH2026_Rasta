# Tests

| Folder | What it covers | Needs |
|---|---|---|
| `scripts/` | Unit tests for the data pipeline and tooling | Python only |
| `integration/` | API behaviour against a freshly reset database: security, logistics, telemetry, routing, alerts | Docker, Supabase CLI |
| `e2e/` | Browser tests of the web client (Playwright), each with a runner that starts the stack | Docker, Supabase CLI, Chromium |

Unit tests for the API, web client and mobile app live next to their code
(`services/api/tests`, `*.test.ts`).

```bash
services/api/.venv/bin/python -m unittest discover -s tests/scripts
services/api/.venv/bin/python tests/integration/check_security.py
npm run test:e2e:install && npm run test:e2e:planner
```

Integration checks and e2e runners reset the local database, then exit non-zero
on any failure and write a JSON report to `artifacts/reports/`.
