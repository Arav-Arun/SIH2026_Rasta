-- The outcome log is kept for two years: long enough to train and monitor a model
-- across two monsoons, short enough that it stays small (about a million rows a
-- year for one district). A separate nightly job, so the existing retention
-- function and its schedule stay as they are.

begin;

create or replace function public.trim_outcome_log(p_keep_days integer default 730)
returns bigint
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  removed bigint;
begin
  if p_keep_days is null or p_keep_days < 30 then
    raise exception 'the outcome log is kept for at least 30 days';
  end if;
  delete from risk_daily_predictions where day < current_date - p_keep_days;
  get diagnostics removed = row_count;
  return removed;
end;
$$;

comment on function public.trim_outcome_log(integer) is
  'Deletes outcome-log days older than the window (730 days by default). Run nightly by pg_cron.';

-- An operator's tool, like apply_retention: nothing holding an API key calls it.
revoke all on function public.trim_outcome_log(integer) from public;
revoke all on function public.trim_outcome_log(integer) from anon, authenticated, service_role;

select cron.schedule(
  'rasta-trim-outcome-log',
  '53 21 * * *',  -- 21:53 UTC is 03:23 IST, after the main retention run
  $$select public.trim_outcome_log(730)$$
);

commit;
