-- Retention may remove expired positions; nothing else may remove any.

begin;

create or replace function public.prevent_append_only_mutation()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
begin
  if tg_op = 'DELETE'
     and tg_table_name = 'telemetry_points'
     and current_setting('rasta.retention_in_progress', true) = 'on' then
    return old;
  end if;
  raise exception 'append-only table % cannot be updated or deleted', tg_table_name
    using errcode = '55000';
end;
$$;

create or replace function public.apply_retention(
  p_telemetry_days integer,
  p_push_attempt_days integer,
  p_dry_run boolean default false
)
returns table (category text, rows_affected bigint)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  telemetry_cutoff timestamptz;
  push_cutoff timestamptz;
  counted bigint;
begin
  if p_telemetry_days is null or p_telemetry_days < 1
     or p_push_attempt_days is null or p_push_attempt_days < 1 then
    raise exception 'retention windows must be at least one day';
  end if;
  telemetry_cutoff := now() - make_interval(days => p_telemetry_days);
  push_cutoff := now() - make_interval(days => p_push_attempt_days);

  -- The current position of a trip that ended long ago. It references a
  -- telemetry point, so it goes first.
  if p_dry_run then
    select count(*) into counted
    from trip_current_location as l
    join trips as t on t.id = l.trip_id and t.organization_id = l.organization_id
    where t.status in ('completed', 'failed', 'cancelled')
      and coalesce(t.ended_at, t.updated_at) < telemetry_cutoff;
  else
    delete from trip_current_location as l
    using trips as t
    where t.id = l.trip_id and t.organization_id = l.organization_id
      and t.status in ('completed', 'failed', 'cancelled')
      and coalesce(t.ended_at, t.updated_at) < telemetry_cutoff;
    get diagnostics counted = row_count;
  end if;
  category := 'trip_current_location';
  rows_affected := counted;
  return next;

  -- Position history of trips that ended before the window. A running or
  -- paused trip keeps all of its points, however old the trip is.
  if p_dry_run then
    select count(*) into counted
    from telemetry_points as p
    join trips as t on t.id = p.trip_id and t.organization_id = p.organization_id
    where t.status in ('completed', 'failed', 'cancelled')
      and coalesce(t.ended_at, t.updated_at) < telemetry_cutoff;
  else
    perform set_config('rasta.retention_in_progress', 'on', true);
    delete from telemetry_points as p
    using trips as t
    where t.id = p.trip_id and t.organization_id = p.organization_id
      and t.status in ('completed', 'failed', 'cancelled')
      and coalesce(t.ended_at, t.updated_at) < telemetry_cutoff;
    get diagnostics counted = row_count;
    perform set_config('rasta.retention_in_progress', 'off', true);
  end if;
  category := 'telemetry_points';
  rows_affected := counted;
  return next;

  -- Idempotency ledger entries past their own expiry: after it, a retry with
  -- the same key is treated as new anyway.
  if p_dry_run then
    select count(*) into counted from sync_mutations where expires_at < now();
  else
    delete from sync_mutations where expires_at < now();
    get diagnostics counted = row_count;
  end if;
  category := 'sync_mutations';
  rows_affected := counted;
  return next;

  -- Push delivery attempts: diagnostics, not a record of the alert itself.
  if p_dry_run then
    select count(*) into counted from push_attempts where attempted_at < push_cutoff;
  else
    delete from push_attempts where attempted_at < push_cutoff;
    get diagnostics counted = row_count;
  end if;
  category := 'push_attempts';
  rows_affected := counted;
  return next;
end;
$$;

commit;
