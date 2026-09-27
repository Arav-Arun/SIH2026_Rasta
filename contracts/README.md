# API contracts

This package contains generated, reviewable contracts for the FastAPI OpenAPI
schema. It intentionally contains no handwritten duplicate domain models.

`openapi.json` is a snapshot of the API schema and `src/openapi.d.ts` holds the
TypeScript types generated from it with `openapi-typescript`. The React client
imports these types through `apps/client/lib/api/contracts.ts`.
