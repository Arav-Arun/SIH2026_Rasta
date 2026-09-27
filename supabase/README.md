# Database

PostgreSQL with PostGIS, managed through the Supabase CLI. The migrations in
`migrations/` are the only source of schema changes; `supabase db reset`
applies them in filename order and then loads `seed.sql`.

## Layout

| Path | Contents |
|---|---|
| `migrations/` | Schema, row-level security, private evidence storage, retention jobs |
| `seed.sql` | A synthetic `local_demo` organisation and district; no users or operational records |
| `config.toml` | Local stack configuration |

## Run locally

Requires Docker and the [Supabase CLI](https://supabase.com/docs/guides/local-development).

```bash
supabase start
supabase db reset
```

`python3 scripts/local_demo.py up` does all of this and loads the pilot road
network. Never run the synthetic seed against a pilot or production project.

## Access model

- `anon` has no access to application tables.
- `authenticated` reads only rows within its organisation and district scope,
  and can change only its own profile settings and alert acknowledgements. All
  other writes go through the API, which validates roles, versions and
  idempotency and writes the audit record in the same transaction.
- Roles are read from `profiles` and `role_assignments` via `auth.uid()`, never
  from anything the client sends.
- The `evidence` bucket is private. An upload is accepted only at the exact path
  pre-authorised for a pending attachment:
  `<organisation>/<incident>/<attachment>/<file name>`. There is no client
  update or delete.
- Telemetry must come from an approved device bound to the trip's vehicle or
  driver, and trip locations older than the retention window are removed
  nightly by `pg_cron`.

RLS is a second barrier behind the API's own role, scope and version checks.
