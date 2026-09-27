-- RASTA tenant isolation, role guardrails and private evidence storage.

begin;

-- SECURITY DEFINER helpers intentionally expose booleans/opaque IDs only.
create function public.current_profile_id()
returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select p.id
  from public.profiles as p
  where p.user_id = auth.uid()
    and p.active
  limit 1;
$$;

create function public.current_organization_id()
returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select p.organization_id
  from public.profiles as p
  where p.user_id = auth.uid()
    and p.active
  limit 1;
$$;

create function public.is_active_organization_member(target_organization_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.profiles as p
    where p.user_id = auth.uid()
      and p.organization_id = target_organization_id
      and p.active
  );
$$;

create function public.is_current_profile(
  target_profile_id uuid,
  target_organization_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.profiles as p
    where p.id = target_profile_id
      and p.organization_id = target_organization_id
      and p.user_id = auth.uid()
      and p.active
  );
$$;

create function public.has_organization_role(
  target_organization_id uuid,
  allowed_roles public.app_role[]
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.profiles as p
    join public.role_assignments as ra
      on ra.profile_id = p.id
     and ra.organization_id = p.organization_id
    where p.user_id = auth.uid()
      and p.active
      and p.organization_id = target_organization_id
      and ra.role = any(allowed_roles)
      and ra.valid_from <= now()
      and (ra.valid_to is null or ra.valid_to > now())
      and ra.revoked_at is null
  );
$$;

create function public.has_district_role(
  target_organization_id uuid,
  target_district_id uuid,
  allowed_roles public.app_role[]
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.profiles as p
    join public.role_assignments as ra
      on ra.profile_id = p.id
     and ra.organization_id = p.organization_id
    where p.user_id = auth.uid()
      and p.active
      and p.organization_id = target_organization_id
      and ra.valid_from <= now()
      and (ra.valid_to is null or ra.valid_to > now())
      and ra.revoked_at is null
      and (
        ra.role in ('admin', 'state_coordinator')
        or (ra.role = any(allowed_roles) and ra.district_id = target_district_id)
      )
  );
$$;

create function public.can_read_district(target_district_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.profiles as p
    join public.role_assignments as ra
      on ra.profile_id = p.id
     and ra.organization_id = p.organization_id
    left join public.organization_districts as od
      on od.organization_id = p.organization_id
     and od.district_id = target_district_id
     and od.active
    where p.user_id = auth.uid()
      and p.active
      and ra.valid_from <= now()
      and (ra.valid_to is null or ra.valid_to > now())
      and ra.revoked_at is null
      and od.district_id is not null
      and (
        ra.role in ('admin', 'state_coordinator')
        or ra.district_id = target_district_id
      )
  );
$$;

create function public.can_read_operational_scope(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_district_role(
    target_organization_id,
    target_district_id,
    array['district_dispatcher', 'reviewer']::public.app_role[]
  );
$$;

create function public.can_manage_operational_scope(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_district_role(
    target_organization_id,
    target_district_id,
    array['district_dispatcher']::public.app_role[]
  );
$$;

create function public.can_create_field_report(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_district_role(
    target_organization_id,
    target_district_id,
    array['field_officer', 'district_dispatcher']::public.app_role[]
  );
$$;

create function public.can_read_segment(
  target_organization_id uuid,
  target_segment_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.road_segments as rs
    where rs.id = target_segment_id
      and rs.organization_id = target_organization_id
      and public.can_read_operational_scope(rs.organization_id, rs.district_id)
  );
$$;

create function public.can_read_facility(
  target_organization_id uuid,
  target_facility_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.facilities as f
    where f.id = target_facility_id
      and f.organization_id = target_organization_id
      and public.can_read_operational_scope(f.organization_id, f.district_id)
  );
$$;

create function public.can_read_vehicle(
  target_organization_id uuid,
  target_vehicle_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_organization_role(
      target_organization_id,
      array['admin', 'state_coordinator']::public.app_role[]
    )
    or exists (
      select 1
      from public.trips as t
      where t.organization_id = target_organization_id
        and t.vehicle_id = target_vehicle_id
        and (
          t.driver_id = public.current_profile_id()
          or public.can_read_operational_scope(t.organization_id, t.district_id)
        )
    );
$$;

create function public.can_read_incident(
  target_organization_id uuid,
  target_incident_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.incidents as i
    where i.id = target_incident_id
      and i.organization_id = target_organization_id
      and (
        i.reporter_id = public.current_profile_id()
        or public.can_read_operational_scope(i.organization_id, i.district_id)
      )
  );
$$;

create function public.can_read_inspection(
  target_organization_id uuid,
  target_inspection_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.inspections as i
    where i.id = target_inspection_id
      and i.organization_id = target_organization_id
      and (
        i.assignee_id = public.current_profile_id()
        or public.can_read_operational_scope(i.organization_id, i.district_id)
      )
  );
$$;

create function public.can_read_consignment(
  target_organization_id uuid,
  target_consignment_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.consignments as c
    where c.id = target_consignment_id
      and c.organization_id = target_organization_id
      and public.can_read_operational_scope(c.organization_id, c.district_id)
  );
$$;

create function public.can_read_trip(
  target_organization_id uuid,
  target_trip_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.trips as t
    where t.id = target_trip_id
      and t.organization_id = target_organization_id
      and (
        t.driver_id = public.current_profile_id()
        or public.can_read_operational_scope(t.organization_id, t.district_id)
      )
  );
$$;

create function public.can_read_route_plan(
  target_organization_id uuid,
  target_route_plan_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.route_plans as rp
    left join public.trips as t
      on t.id = rp.trip_id
     and t.organization_id = rp.organization_id
    where rp.id = target_route_plan_id
      and rp.organization_id = target_organization_id
      and (
        public.can_read_operational_scope(rp.organization_id, rp.district_id)
        or t.driver_id = public.current_profile_id()
      )
  );
$$;

create function public.can_read_attachment(
  target_organization_id uuid,
  target_attachment_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.attachments as a
    where a.id = target_attachment_id
      and a.organization_id = target_organization_id
      and public.can_read_incident(a.organization_id, a.incident_id)
  );
$$;

create function public.can_read_alert(
  target_organization_id uuid,
  target_alert_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_organization_role(
      target_organization_id,
      array['admin', 'state_coordinator']::public.app_role[]
    )
    or exists (
      select 1
      from public.alert_recipients as ar
      where ar.organization_id = target_organization_id
        and ar.alert_id = target_alert_id
        and ar.profile_id = public.current_profile_id()
    );
$$;

create function public.can_read_source_health(target_organization_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_organization_role(
    target_organization_id,
    array['admin', 'state_coordinator']::public.app_role[]
  );
$$;

create function public.can_read_device(
  target_organization_id uuid,
  target_device_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.has_organization_role(
      target_organization_id,
      array['admin']::public.app_role[]
    )
    or exists (
      select 1
      from public.device_registrations as d
      where d.id = target_device_id
        and d.organization_id = target_organization_id
        and d.profile_id = public.current_profile_id()
    );
$$;

create function public.can_write_evidence_object(target_object_name text)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.attachments as a
    join public.incidents as i
      on i.id = a.incident_id
     and i.organization_id = a.organization_id
    where a.storage_key = target_object_name
      and a.upload_status = 'pending'
      and a.uploader_id = public.current_profile_id()
      and i.reporter_id = public.current_profile_id()
      and public.can_create_field_report(i.organization_id, i.district_id)
  );
$$;

create function public.can_read_evidence_object(target_object_name text)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.attachments as a
    where a.storage_key = target_object_name
      and a.upload_status <> 'rejected'
      and public.can_read_incident(a.organization_id, a.incident_id)
  );
$$;

revoke all on function public.current_profile_id() from public, anon;
revoke all on function public.current_organization_id() from public, anon;
revoke all on function public.is_active_organization_member(uuid) from public, anon;
revoke all on function public.is_current_profile(uuid, uuid) from public, anon;
revoke all on function public.has_organization_role(uuid, public.app_role[]) from public, anon;
revoke all on function public.has_district_role(uuid, uuid, public.app_role[]) from public, anon;
revoke all on function public.can_read_district(uuid) from public, anon;
revoke all on function public.can_read_operational_scope(uuid, uuid) from public, anon;
revoke all on function public.can_manage_operational_scope(uuid, uuid) from public, anon;
revoke all on function public.can_create_field_report(uuid, uuid) from public, anon;
revoke all on function public.can_read_segment(uuid, uuid) from public, anon;
revoke all on function public.can_read_facility(uuid, uuid) from public, anon;
revoke all on function public.can_read_vehicle(uuid, uuid) from public, anon;
revoke all on function public.can_read_incident(uuid, uuid) from public, anon;
revoke all on function public.can_read_inspection(uuid, uuid) from public, anon;
revoke all on function public.can_read_consignment(uuid, uuid) from public, anon;
revoke all on function public.can_read_trip(uuid, uuid) from public, anon;
revoke all on function public.can_read_route_plan(uuid, uuid) from public, anon;
revoke all on function public.can_read_attachment(uuid, uuid) from public, anon;
revoke all on function public.can_read_alert(uuid, uuid) from public, anon;
revoke all on function public.can_read_source_health(uuid) from public, anon;
revoke all on function public.can_read_device(uuid, uuid) from public, anon;
revoke all on function public.can_write_evidence_object(text) from public, anon;
revoke all on function public.can_read_evidence_object(text) from public, anon;

grant execute on function public.generate_app_uuid() to authenticated, service_role;
grant execute on function public.current_profile_id() to authenticated;
grant execute on function public.current_organization_id() to authenticated;
grant execute on function public.is_active_organization_member(uuid) to authenticated;
grant execute on function public.is_current_profile(uuid, uuid) to authenticated;
grant execute on function public.has_organization_role(uuid, public.app_role[]) to authenticated;
grant execute on function public.has_district_role(uuid, uuid, public.app_role[]) to authenticated;
grant execute on function public.can_read_district(uuid) to authenticated;
grant execute on function public.can_read_operational_scope(uuid, uuid) to authenticated;
grant execute on function public.can_manage_operational_scope(uuid, uuid) to authenticated;
grant execute on function public.can_create_field_report(uuid, uuid) to authenticated;
grant execute on function public.can_read_segment(uuid, uuid) to authenticated;
grant execute on function public.can_read_facility(uuid, uuid) to authenticated;
grant execute on function public.can_read_vehicle(uuid, uuid) to authenticated;
grant execute on function public.can_read_incident(uuid, uuid) to authenticated;
grant execute on function public.can_read_inspection(uuid, uuid) to authenticated;
grant execute on function public.can_read_consignment(uuid, uuid) to authenticated;
grant execute on function public.can_read_trip(uuid, uuid) to authenticated;
grant execute on function public.can_read_route_plan(uuid, uuid) to authenticated;
grant execute on function public.can_read_attachment(uuid, uuid) to authenticated;
grant execute on function public.can_read_alert(uuid, uuid) to authenticated;
grant execute on function public.can_read_source_health(uuid) to authenticated;
grant execute on function public.can_read_device(uuid, uuid) to authenticated;
grant execute on function public.can_write_evidence_object(text) to authenticated;
grant execute on function public.can_read_evidence_object(text) to authenticated;

alter table public.organizations enable row level security;
alter table public.districts enable row level security;
alter table public.organization_districts enable row level security;
alter table public.profiles enable row level security;
alter table public.role_assignments enable row level security;
alter table public.facilities enable row level security;
alter table public.vehicles enable row level security;
alter table public.device_registrations enable row level security;
alter table public.road_segments enable row level security;
alter table public.bridges enable row level security;
alter table public.network_observations enable row level security;
alter table public.segment_current_state enable row level security;
alter table public.incidents enable row level security;
alter table public.incident_segments enable row level security;
alter table public.attachments enable row level security;
alter table public.inspections enable row level security;
alter table public.supply_requests enable row level security;
alter table public.consignments enable row level security;
alter table public.consignment_items enable row level security;
alter table public.trips enable row level security;
alter table public.route_plans enable row level security;
alter table public.route_alternatives enable row level security;
alter table public.telemetry_points enable row level security;
alter table public.trip_current_location enable row level security;
alter table public.delivery_receipts enable row level security;
alter table public.alerts enable row level security;
alter table public.alert_recipients enable row level security;
alter table public.source_runs enable row level security;
alter table public.model_versions enable row level security;
alter table public.sync_mutations enable row level security;
alter table public.audit_events enable row level security;
alter table public.event_outbox enable row level security;

-- Existing Supabase defaults vary by CLI/server version.
revoke all on all tables in schema public from anon;
revoke all on all tables in schema public from authenticated;
revoke create on schema public from public;
grant usage on schema public to authenticated;

grant select on public.organizations,
  public.districts,
  public.organization_districts,
  public.profiles,
  public.role_assignments,
  public.facilities,
  public.vehicles,
  public.device_registrations,
  public.road_segments,
  public.bridges,
  public.network_observations,
  public.segment_current_state,
  public.incidents,
  public.incident_segments,
  public.attachments,
  public.inspections,
  public.supply_requests,
  public.consignments,
  public.consignment_items,
  public.trips,
  public.route_plans,
  public.route_alternatives,
  public.telemetry_points,
  public.trip_current_location,
  public.delivery_receipts,
  public.alerts,
  public.alert_recipients,
  public.source_runs,
  public.model_versions,
  public.sync_mutations,
  public.audit_events
to authenticated;

grant update (display_name, locale) on public.profiles to authenticated;
grant update (status, acknowledged_at) on public.alert_recipients to authenticated;

create policy organizations_select_current_member on public.organizations
  for select to authenticated
  using (public.is_active_organization_member(id));

create policy districts_select_in_scope on public.districts
  for select to authenticated
  using (public.can_read_district(id));

create policy organization_districts_select_in_scope on public.organization_districts
  for select to authenticated
  using (
    public.is_active_organization_member(organization_id)
    and public.can_read_district(district_id)
  );

create policy profiles_select_self_or_admin on public.profiles
  for select to authenticated
  using (
    public.is_current_profile(id, organization_id)
    or public.has_organization_role(organization_id, array['admin']::public.app_role[])
  );

create policy profiles_update_own_preferences on public.profiles
  for update to authenticated
  using (public.is_current_profile(id, organization_id))
  with check (public.is_current_profile(id, organization_id));

create policy role_assignments_select_self_or_admin on public.role_assignments
  for select to authenticated
  using (
    public.is_current_profile(profile_id, organization_id)
    or public.has_organization_role(organization_id, array['admin']::public.app_role[])
  );

create policy facilities_select_operational_scope on public.facilities
  for select to authenticated
  using (public.can_read_operational_scope(organization_id, district_id));

create policy vehicles_select_operational_scope on public.vehicles
  for select to authenticated
  using (public.can_read_vehicle(organization_id, id));

create policy devices_select_owner_or_admin on public.device_registrations
  for select to authenticated
  using (public.can_read_device(organization_id, id));

create policy road_segments_select_operational_scope on public.road_segments
  for select to authenticated
  using (public.can_read_operational_scope(organization_id, district_id));

create policy bridges_select_operational_scope on public.bridges
  for select to authenticated
  using (public.can_read_segment(organization_id, segment_id));

create policy network_observations_select_operational_scope on public.network_observations
  for select to authenticated
  using (public.can_read_segment(organization_id, segment_id));

create policy segment_current_state_select_operational_scope on public.segment_current_state
  for select to authenticated
  using (public.can_read_segment(organization_id, segment_id));

create policy incidents_select_reporter_or_scope on public.incidents
  for select to authenticated
  using (public.can_read_incident(organization_id, id));

create policy incident_segments_select_incident_scope on public.incident_segments
  for select to authenticated
  using (public.can_read_incident(organization_id, incident_id));

create policy attachments_select_incident_scope on public.attachments
  for select to authenticated
  using (public.can_read_attachment(organization_id, id));

create policy inspections_select_assignee_or_scope on public.inspections
  for select to authenticated
  using (public.can_read_inspection(organization_id, id));

create policy supply_requests_select_operational_scope on public.supply_requests
  for select to authenticated
  using (public.can_read_facility(organization_id, facility_id));

create policy consignments_select_operational_scope on public.consignments
  for select to authenticated
  using (public.can_read_consignment(organization_id, id));

create policy consignment_items_select_operational_scope on public.consignment_items
  for select to authenticated
  using (public.can_read_consignment(organization_id, consignment_id));

create policy trips_select_driver_or_scope on public.trips
  for select to authenticated
  using (public.can_read_trip(organization_id, id));

create policy route_plans_select_driver_or_scope on public.route_plans
  for select to authenticated
  using (public.can_read_route_plan(organization_id, id));

create policy route_alternatives_select_plan_scope on public.route_alternatives
  for select to authenticated
  using (public.can_read_route_plan(organization_id, route_plan_id));

create policy telemetry_points_select_trip_scope on public.telemetry_points
  for select to authenticated
  using (public.can_read_trip(organization_id, trip_id));

create policy trip_current_location_select_trip_scope on public.trip_current_location
  for select to authenticated
  using (public.can_read_trip(organization_id, trip_id));

create policy delivery_receipts_select_trip_scope on public.delivery_receipts
  for select to authenticated
  using (public.can_read_trip(organization_id, trip_id));

create policy alerts_select_recipient_or_leadership on public.alerts
  for select to authenticated
  using (public.can_read_alert(organization_id, id));

create policy alert_recipients_select_own_or_leadership on public.alert_recipients
  for select to authenticated
  using (
    profile_id = public.current_profile_id()
    or public.has_organization_role(
      organization_id,
      array['admin', 'state_coordinator']::public.app_role[]
    )
  );

create policy alert_recipients_acknowledge_own on public.alert_recipients
  for update to authenticated
  using (profile_id = public.current_profile_id())
  with check (
    profile_id = public.current_profile_id()
    and status = 'acknowledged'
    and acknowledged_at is not null
  );

create policy source_runs_select_data_health_roles on public.source_runs
  for select to authenticated
  using (public.can_read_source_health(organization_id));

create policy model_versions_select_data_health_roles on public.model_versions
  for select to authenticated
  using (public.can_read_source_health(organization_id));

create policy sync_mutations_select_own on public.sync_mutations
  for select to authenticated
  using (actor_id = public.current_profile_id());

create policy audit_events_select_admin on public.audit_events
  for select to authenticated
  using (public.has_organization_role(organization_id, array['admin']::public.app_role[]));

-- Evidence bytes are private.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'evidence',
  'evidence',
  false,
  5242880,
  array['image/jpeg', 'image/png', 'image/webp']::text[]
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- RLS is enabled by default on storage.objects in current Supabase stacks.
-- Altering the table is blocked for non-owner roles (SQLSTATE 42501).

drop policy if exists evidence_object_insert_exact_attachment on storage.objects;
drop policy if exists evidence_object_select_scoped on storage.objects;

create policy evidence_object_insert_exact_attachment on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'evidence'
    and public.can_write_evidence_object(name)
  );

create policy evidence_object_select_scoped on storage.objects
  for select to authenticated
  using (
    bucket_id = 'evidence'
    and public.can_read_evidence_object(name)
  );

commit;
