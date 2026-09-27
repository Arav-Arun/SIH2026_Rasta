# API contracts

This package contains generated, reviewable contracts for the FastAPI OpenAPI
schema. It intentionally contains no handwritten duplicate domain models.

From the repository root:

```bash
npm run contracts:generate
npm run contracts:check
```

`contracts:generate` exports a deterministic local-schema snapshot to
`openapi.json` and generates `src/openapi.d.ts` with `openapi-typescript`.
`contracts:check` regenerates in a temporary directory and fails if either
checked-in artifact drifted. The React client imports types through
`apps/client/lib/api/contracts.ts`.

The schema export does not make an HTTP request and does not need credentials,
a running server, production data, or a Supabase project.
