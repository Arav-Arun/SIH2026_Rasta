-- RASTA local demonstration seed.

begin;

insert into public.organizations (id, name, mode)
values (
  'a2600002-0000-4000-8000-000000000001'::uuid,
  'RASTA synthetic local demonstration',
  'local_demo'
)
on conflict (id) do update
set name = excluded.name,
    mode = excluded.mode
where public.organizations.name is distinct from excluded.name
   or public.organizations.mode is distinct from excluded.mode;

insert into public.districts (id, state_code, code, name, boundary)
values (
  'a2600002-0000-4000-8000-000000000002'::uuid,
  'DEMO',
  'SYNTH-01',
  'Synthetic pilot district (no geographic boundary)',
  null
)
on conflict (id) do update
set state_code = excluded.state_code,
    code = excluded.code,
    name = excluded.name,
    boundary = excluded.boundary
where public.districts.state_code is distinct from excluded.state_code
   or public.districts.code is distinct from excluded.code
   or public.districts.name is distinct from excluded.name
   or public.districts.boundary is distinct from excluded.boundary;

insert into public.organization_districts (organization_id, district_id, active)
values (
  'a2600002-0000-4000-8000-000000000001'::uuid,
  'a2600002-0000-4000-8000-000000000002'::uuid,
  true
)
on conflict (organization_id, district_id) do update
set active = excluded.active
where public.organization_districts.active is distinct from excluded.active;

-- Location remains NULL on purpose.
insert into public.facilities (
  id,
  organization_id,
  district_id,
  type,
  name,
  location,
  active,
  source_mode,
  source_ref,
  metadata
)
values
  (
    'a2600002-0000-4000-8000-000000000010'::uuid,
    'a2600002-0000-4000-8000-000000000001'::uuid,
    'a2600002-0000-4000-8000-000000000002'::uuid,
    'demo_depot',
    'Synthetic depot (location intentionally not loaded)',
    null,
    true,
    'synthetic',
    'seed.sql',
    '{"mode_label":"SYNTHETIC DEMO SEED","contains_operational_claims":false,"location_state":"not_loaded"}'::jsonb
  ),
  (
    'a2600002-0000-4000-8000-000000000011'::uuid,
    'a2600002-0000-4000-8000-000000000001'::uuid,
    'a2600002-0000-4000-8000-000000000002'::uuid,
    'demo_destination',
    'Synthetic destination (location intentionally not loaded)',
    null,
    true,
    'synthetic',
    'seed.sql',
    '{"mode_label":"SYNTHETIC DEMO SEED","contains_operational_claims":false,"location_state":"not_loaded"}'::jsonb
  )
on conflict (id) do update
set type = excluded.type,
    name = excluded.name,
    location = excluded.location,
    active = excluded.active,
    source_mode = excluded.source_mode,
    source_ref = excluded.source_ref,
    metadata = excluded.metadata
where public.facilities.type is distinct from excluded.type
   or public.facilities.name is distinct from excluded.name
   or public.facilities.location is distinct from excluded.location
   or public.facilities.active is distinct from excluded.active
   or public.facilities.source_mode is distinct from excluded.source_mode
   or public.facilities.source_ref is distinct from excluded.source_ref
   or public.facilities.metadata is distinct from excluded.metadata;

insert into public.vehicles (
  id,
  organization_id,
  registration_ref,
  class,
  capacity_kg,
  max_height_m,
  active,
  source_mode
)
values (
  'a2600002-0000-4000-8000-000000000020'::uuid,
  'a2600002-0000-4000-8000-000000000001'::uuid,
  'SYNTHETIC-VEHICLE-01',
  'demo_only',
  null,
  null,
  false,
  'synthetic'
)
on conflict (organization_id, registration_ref) do update
set registration_ref = excluded.registration_ref,
    class = excluded.class,
    capacity_kg = excluded.capacity_kg,
    max_height_m = excluded.max_height_m,
    active = excluded.active,
    source_mode = excluded.source_mode
where public.vehicles.class is distinct from excluded.class
   or public.vehicles.capacity_kg is distinct from excluded.capacity_kg
   or public.vehicles.max_height_m is distinct from excluded.max_height_m
   or public.vehicles.active is distinct from excluded.active
   or public.vehicles.source_mode is distinct from excluded.source_mode;

insert into public.source_runs (
  id,
  organization_id,
  district_id,
  source,
  source_mode,
  started_at,
  finished_at,
  status,
  record_count,
  metadata
)
values (
  'a2600002-0000-4000-8000-000000000030'::uuid,
  'a2600002-0000-4000-8000-000000000001'::uuid,
  'a2600002-0000-4000-8000-000000000002'::uuid,
  'synthetic_seed',
  'synthetic',
  '2000-01-01T00:00:00Z'::timestamptz,
  '2000-01-01T00:00:00Z'::timestamptz,
  'success',
  0,
  '{"mode_label":"SYNTHETIC DEMO SEED","contains_operational_claims":false,"note":"No road, weather, incident, telemetry, or user data is seeded."}'::jsonb
)
on conflict (id) do update
set source = excluded.source,
    source_mode = excluded.source_mode,
    started_at = excluded.started_at,
    finished_at = excluded.finished_at,
    status = excluded.status,
    record_count = excluded.record_count,
    metadata = excluded.metadata
where public.source_runs.source is distinct from excluded.source
   or public.source_runs.source_mode is distinct from excluded.source_mode
   or public.source_runs.started_at is distinct from excluded.started_at
   or public.source_runs.finished_at is distinct from excluded.finished_at
   or public.source_runs.status is distinct from excluded.status
   or public.source_runs.record_count is distinct from excluded.record_count
   or public.source_runs.metadata is distinct from excluded.metadata;

commit;
