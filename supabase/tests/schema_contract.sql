-- Run after `supabase db reset` or `supabase db push`.

begin read only;

do $$
declare
  required_table text;
  policy_index integer;
  required_trigger text;
  required_constraint text;
  required_tables text[] := array[
    'organizations', 'profiles', 'role_assignments', 'facilities',
    'road_segments', 'incidents', 'attachments', 'trips',
    'telemetry_points', 'audit_events', 'event_outbox'
  ];
  required_policy_names text[] := array[
    'organizations_select_current_member',
    'profiles_select_self_or_admin',
    'incidents_select_reporter_or_scope',
    'trips_select_driver_or_scope',
    'evidence_object_insert_exact_attachment',
    'evidence_object_select_scoped'
  ];
  required_policy_schemas text[] := array[
    'public', 'public', 'public', 'public', 'storage', 'storage'
  ];
  required_policy_tables text[] := array[
    'organizations', 'profiles', 'incidents', 'trips', 'objects', 'objects'
  ];
  required_triggers text[] := array[
    'role_assignments_require_active_scope',
    'inspections_require_existing_scoped_target',
    'trips_route_plan_same_trip',
    'route_plans_selected_trip_same_trip',
    'telemetry_points_require_trip_bound_active_device',
    'trip_current_location_require_same_trip_telemetry',
    'attachments_require_identity_storage_key'
  ];
  required_constraints text[] := array[
    'role_assignments_district_scope_check',
    'districts_boundary_wgs84_bounds_check',
    'road_segments_geometry_wgs84_bounds_check',
    'telemetry_points_location_wgs84_bounds_check'
  ];
begin
  if not exists (
    select 1
    from pg_extension
    where extname = 'postgis'
  ) then
    raise exception 'postgis extension is missing';
  end if;

  foreach required_table in array required_tables loop
    if not exists (
      select 1
      from pg_class as c
      join pg_namespace as n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relname = required_table
        and c.relrowsecurity
    ) then
      raise exception 'RLS is not enabled on public.%', required_table;
    end if;
  end loop;

  for policy_index in 1..array_length(required_policy_names, 1) loop
    if not exists (
      select 1
      from pg_policies
      where schemaname = required_policy_schemas[policy_index]
        and tablename = required_policy_tables[policy_index]
        and policyname = required_policy_names[policy_index]
    ) then
      raise exception 'required policy %.%.% is missing',
        required_policy_schemas[policy_index],
        required_policy_tables[policy_index],
        required_policy_names[policy_index];
    end if;
  end loop;

  foreach required_trigger in array required_triggers loop
    if not exists (
      select 1
      from pg_trigger
      where tgname = required_trigger
        and not tgisinternal
    ) then
      raise exception 'required trigger % is missing', required_trigger;
    end if;
  end loop;

  foreach required_constraint in array required_constraints loop
    if not exists (
      select 1
      from pg_constraint
      where conname = required_constraint
    ) then
      raise exception 'required constraint % is missing', required_constraint;
    end if;
  end loop;

  if not exists (
    select 1
    from storage.buckets
    where id = 'evidence'
      and public = false
      and file_size_limit = 5242880
  ) then
    raise exception 'evidence bucket is missing or not private/limited';
  end if;

  if not exists (
    select 1
    from pg_class as c
    join pg_namespace as n on n.oid = c.relnamespace
    where n.nspname = 'storage'
      and c.relname = 'objects'
      and c.relrowsecurity
  ) then
    raise exception 'RLS is not enabled on storage.objects';
  end if;

  -- Supabase owns storage.objects privileges; browser isolation is enforced
  -- through RLS policies rather than project-level REVOKE statements.
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and policyname = 'evidence_object_insert_exact_attachment'
  ) then
    raise exception 'evidence insert storage policy is missing';
  end if;

  if not exists (
    select 1
    from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and policyname = 'evidence_object_select_scoped'
  ) then
    raise exception 'evidence select storage policy is missing';
  end if;

  if exists (
    select 1
    from public.facilities
    where organization_id = 'a2600002-0000-4000-8000-000000000001'::uuid
      and source_mode <> 'synthetic'
  ) then
    raise exception 'synthetic seed contains a facility without synthetic mode';
  end if;

  if exists (
    select 1
    from public.telemetry_points
    where organization_id = 'a2600002-0000-4000-8000-000000000001'::uuid
  ) then
    raise exception 'synthetic seed must not contain telemetry';
  end if;
end;
$$;

rollback;
