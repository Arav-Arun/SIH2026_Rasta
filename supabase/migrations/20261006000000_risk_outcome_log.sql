-- What the risk model said about each road on each day, kept so it can be set
-- against what was later confirmed there. Every risk run writes it; the API's
-- outcome report reads it.

begin;

create table public.risk_daily_predictions (
  organization_id uuid not null,
  district_id uuid not null,
  segment_id uuid not null,
  -- The UTC calendar day the score was computed on.
  day date not null,
  model_version text not null check (btrim(model_version) <> ''),
  -- The highest score and level of the day. A null score means every run that
  -- day found too little evidence to score the road.
  max_score numeric(5, 4) check (max_score is null or (max_score >= 0 and max_score <= 1)),
  max_level public.risk_level not null,
  runs integer not null default 1 check (runs > 0),
  first_computed_at timestamptz not null,
  last_computed_at timestamptz not null,
  primary key (organization_id, segment_id, day, model_version),
  foreign key (segment_id, organization_id)
    references public.road_segments (id, organization_id) on delete cascade,
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  check (last_computed_at >= first_computed_at)
);

create index risk_daily_predictions_district_day_idx
  on public.risk_daily_predictions (organization_id, district_id, day);

comment on table public.risk_daily_predictions is
  'The outcome log: each road''s highest risk level and score per UTC day and model '
  'version, written by every risk run. Paired with confirmed incidents it shows '
  'predicted against observed, and later becomes training and monitoring data.';

alter table public.risk_daily_predictions enable row level security;

-- The same audience as data health. Writes are the service's alone.
create policy risk_daily_predictions_select_data_health_roles
  on public.risk_daily_predictions
  for select to authenticated
  using (public.can_read_source_health(organization_id));

commit;
