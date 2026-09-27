# RASTA API

FastAPI service that owns road state, incidents, route plans, logistics,
telemetry and alerts. It verifies Supabase JWTs, applies organisation and
district scope to every request, and writes an audit event for every change.

## Run locally

From the repository root:

```bash
python3.12 -m venv api/.venv
api/.venv/bin/python -m pip install -e './api[dev]'
api/.venv/bin/python -m uvicorn app.main:app --app-dir api --reload
```

`GET /health` is the health check and `GET /openapi.json` is the schema. Copy
`.env.example` to `.env` for the database and Supabase settings;
`scripts/local_demo.py up` writes it for you. `ALLOWED_ORIGINS` must be an
explicit comma-separated list; wildcards are rejected.

## Layout

| Module | Responsibility |
|---|---|
| `routes_*.py` | HTTP endpoints |
| `incidents.py`, `reducer.py`, `evidence.py` | Field reports, evidence checks, road state decisions |
| `routing/` | Graph loading and constrained route search |
| `route_plans.py`, `exposure.py` | Plan approval and rerouting after closures |
| `logistics.py`, `telemetry.py` | Consignments, trips, receipts, GPS batches |
| `risk_engine.py`, `risk_pipeline.py`, `sources.py` | Explainable risk baseline and IMD/SACHET adapters |
| `alerts.py`, `push.py` | Alert inbox and optional web push |
| `auth.py`, `supabase_jwt.py`, `scope.py`, `identity.py` | Authentication and access scope |
| `audit.py`, `idempotency.py`, `ratelimit.py`, `middleware.py` | Audit trail, safe retries, limits, headers |

## Checks

```bash
api/.venv/bin/python -m pytest -q api/tests
api/.venv/bin/python -m ruff check api/app api/tests
```

The unit tests need no database, network or credentials.
