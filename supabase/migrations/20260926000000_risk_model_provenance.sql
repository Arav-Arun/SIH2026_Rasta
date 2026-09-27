-- The baseline risk engine needs to say which model produced a score and what it was
-- looking at.

begin;


alter table public.segment_current_state
  add column if not exists risk_model_version text
    check (risk_model_version is null or btrim(risk_model_version) <> ''),
  add column if not exists risk_computed_at timestamptz,
  add column if not exists risk_explanation jsonb not null default '{}'::jsonb
    check (jsonb_typeof(risk_explanation) = 'object');

comment on column public.segment_current_state.risk_model_version is
  'Which versioned risk model produced risk_score. Null means no model has scored this segment.';
comment on column public.segment_current_state.risk_explanation is
  'The features the model used, each with its value, weight, contribution and source age, plus the inputs that were missing.';

-- A score without the model that produced it is not attributable.
alter table public.segment_current_state
  drop constraint if exists segment_current_state_risk_attributable;
alter table public.segment_current_state
  add constraint segment_current_state_risk_attributable
  check (risk_score is null or risk_model_version is not null);

-- Source runs are read on every data-health request, newest first per source.
create index if not exists source_runs_source_started_idx
  on public.source_runs (organization_id, source, started_at desc);

-- Freshness queries on the map and the planner filter by score presence.
create index if not exists segment_current_state_risk_idx
  on public.segment_current_state (organization_id, risk_level, risk_computed_at desc);

commit;
