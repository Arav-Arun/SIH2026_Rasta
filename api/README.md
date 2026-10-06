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
`.env.example` to `.env` for the database and Supabase settings.
`ALLOWED_ORIGINS` must be an
explicit comma-separated list; wildcards are rejected.

## Layout

| Module | Responsibility |
|---|---|
| `routes_*.py` | HTTP endpoints |
| `incidents.py`, `reducer.py`, `evidence.py` | Field reports, evidence checks, road state decisions |
| `routing/` | Graph loading and constrained route search |
| `routing/replay.py` | The fixed synthetic scenarios run through the production planner and scored against their stated expectations (`PYTHONPATH=api python -m app.routing.replay`) |
| `route_plans.py`, `exposure.py` | Plan approval and rerouting after closures |
| `logistics.py`, `telemetry.py` | Consignments, trips, receipts, GPS batches |
| `risk_engine.py`, `risk_pipeline.py`, `sources.py`, `terrain.py` | Explainable risk baseline, IMD/SACHET adapters and terrain slope |
| `probe_speed.py` | Slow vehicles on a road from trips' own GPS: the `telemetry_anomaly` input, uncalibrated |
| `risk_schedule.py`, `risk_outcomes.py` | Re-scoring on a timer (`RISK_RECOMPUTE_MINUTES`), and each day's scores set against the next day's confirmed incidents (`GET /v1/risk/outcomes`) |
| `alerts.py`, `push.py` | Alert inbox and optional web push |
| `auth.py`, `supabase_jwt.py`, `scope.py`, `identity.py` | Authentication and access scope |
| `audit.py`, `idempotency.py`, `ratelimit.py`, `middleware.py` | Audit trail, safe retries, limits, headers |

## Tests

```bash
api/.venv/bin/python -m pip install -e './api[dev]'
cd api && .venv/bin/python -m pytest
```

The unit tests need no database. They cover the risk score, routing, source
parsing, alert de-duplication, receipts, telemetry rules and the API's
request and error handling, and they check that `contracts/openapi.json`
matches what the API serves. GitHub runs them with lint and type checks on
every push (`.github/workflows/verify.yml`).
