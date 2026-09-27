-- What each source last told us, kept, so a quiet source is not a blank one.

begin;


create table public.source_records (
  organization_id uuid not null references public.organizations (id) on delete restrict,
  source text not null check (btrim(source) <> ''),
  subject_type text not null check (btrim(subject_type) <> ''),
  subject_ref text not null check (btrim(subject_ref) <> ''),
  kind text not null check (btrim(kind) <> ''),
  -- When the source says the observation was made, not when we read it.
  observed_at timestamptz not null,
  valid_until timestamptz,
  value jsonb not null default '{}'::jsonb check (jsonb_typeof(value) = 'object'),
  source_mode public.data_mode not null,
  run_id uuid not null references public.source_runs (id) on delete cascade,
  recorded_at timestamptz not null default now(),
  primary key (organization_id, source, subject_type, subject_ref, kind, observed_at),
  check (valid_until is null or valid_until >= observed_at)
);

comment on table public.source_records is
  'Normalised observations from external sources. Refreshed by a successful run; '
  'retained across unchanged and failed runs so a quiet source is distinguishable '
  'from an absent reading.';

-- "The most recent reading of this kind for this subject", which is the only
-- question the pipeline asks of this table.
create index source_records_latest_idx
  on public.source_records (organization_id, kind, subject_ref, observed_at desc);

alter table public.source_records enable row level security;

-- Same audience as the run health it belongs to: whoever may see how a source
-- is doing may see what it said. Writes are the service's alone.
create policy source_records_select_data_health_roles on public.source_records
  for select to authenticated
  using (public.can_read_source_health(organization_id));

commit;
