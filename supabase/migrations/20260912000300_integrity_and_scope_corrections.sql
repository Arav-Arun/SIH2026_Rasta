-- Forward-only integrity and scope corrections.

begin;

-- District-facing roles must carry an explicit active district grant.
alter table public.role_assignments
  add constraint role_assignments_district_scope_check
  check (
    (role in ('admin', 'state_coordinator') and district_id is null)
    or (
      role in ('district_dispatcher', 'field_officer', 'driver', 'reviewer')
      and district_id is not null
    )
  );

create function public.enforce_role_assignment_scope()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  scoped_district_active boolean;
begin
  if new.district_id is not null then
    select od.active
      into scoped_district_active
      from public.organization_districts as od
      where od.organization_id = new.organization_id
        and od.district_id = new.district_id;

    if not found or not scoped_district_active then
      raise exception 'role assignment requires an active organization district'
        using errcode = '23503';
    end if;
  end if;

  return new;
end;
$$;

create trigger role_assignments_require_active_scope
  before insert or update of organization_id, district_id, role
  on public.role_assignments
  for each row execute function public.enforce_role_assignment_scope();

-- District roles are deliberately exact-scope only.
create or replace function public.has_district_role(
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
    join public.organization_districts as od
      on od.organization_id = ra.organization_id
     and od.district_id = ra.district_id
     and od.active
    where p.user_id = auth.uid()
      and p.active
      and p.organization_id = target_organization_id
      and ra.district_id = target_district_id
      and ra.role = any(allowed_roles)
      and ra.valid_from <= now()
      and (ra.valid_to is null or ra.valid_to > now())
      and ra.revoked_at is null
  );
$$;

create function public.is_active_organization_district(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.organization_districts as od
    where od.organization_id = target_organization_id
      and od.district_id = target_district_id
      and od.active
  );
$$;

create or replace function public.can_read_district(target_district_id uuid)
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
    join public.organization_districts as od
      on od.organization_id = p.organization_id
     and od.district_id = target_district_id
     and od.active
    where p.user_id = auth.uid()
      and p.active
      and ra.valid_from <= now()
      and (ra.valid_to is null or ra.valid_to > now())
      and ra.revoked_at is null
      and (
        (ra.role in ('admin', 'state_coordinator') and ra.district_id is null)
        or ra.district_id = target_district_id
      )
  );
$$;

create or replace function public.can_read_operational_scope(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.is_active_organization_district(
      target_organization_id,
      target_district_id
    )
    and (
      public.has_organization_role(
        target_organization_id,
        array['admin', 'state_coordinator']::public.app_role[]
      )
      or public.has_district_role(
        target_organization_id,
        target_district_id,
        array['district_dispatcher', 'reviewer']::public.app_role[]
      )
    );
$$;

create or replace function public.can_manage_operational_scope(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.is_active_organization_district(
      target_organization_id,
      target_district_id
    )
    and (
      public.has_organization_role(
        target_organization_id,
        array['state_coordinator']::public.app_role[]
      )
      or public.has_district_role(
        target_organization_id,
        target_district_id,
        array['district_dispatcher']::public.app_role[]
      )
    );
$$;

create or replace function public.can_create_field_report(
  target_organization_id uuid,
  target_district_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select public.is_active_organization_district(
      target_organization_id,
      target_district_id
    )
    and (
      public.has_organization_role(
        target_organization_id,
        array['state_coordinator']::public.app_role[]
      )
      or public.has_district_role(
        target_organization_id,
        target_district_id,
        array['field_officer', 'district_dispatcher']::public.app_role[]
      )
    );
$$;

-- Coordinate reference system alone does not reject impossible WGS84 values.
create function public.geometry_is_within_wgs84_bounds(value extensions.geometry)
returns boolean
language sql
immutable
security invoker
set search_path = pg_catalog, extensions
as $$
  select value is not null
    and extensions.st_srid(value) = 4326
    and extensions.st_coveredby(
      value,
      extensions.st_makeenvelope(-180.0, -90.0, 180.0, 90.0, 4326)
    );
$$;

alter table public.districts
  add constraint districts_boundary_wgs84_bounds_check
  check (boundary is null or public.geometry_is_within_wgs84_bounds(boundary));
alter table public.facilities
  add constraint facilities_location_wgs84_bounds_check
  check (location is null or public.geometry_is_within_wgs84_bounds(location));
alter table public.road_segments
  add constraint road_segments_geometry_wgs84_bounds_check
  check (public.geometry_is_within_wgs84_bounds(geometry));
alter table public.incidents
  add constraint incidents_location_wgs84_bounds_check
  check (location is null or public.geometry_is_within_wgs84_bounds(location));
alter table public.route_alternatives
  add constraint route_alternatives_geometry_wgs84_bounds_check
  check (geometry is null or public.geometry_is_within_wgs84_bounds(geometry));
alter table public.telemetry_points
  add constraint telemetry_points_location_wgs84_bounds_check
  check (public.geometry_is_within_wgs84_bounds(location));
alter table public.trip_current_location
  add constraint trip_current_location_wgs84_bounds_check
  check (public.geometry_is_within_wgs84_bounds(location));

create function public.enforce_inspection_target_scope()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  target_organization_id uuid;
  target_district_id uuid;
begin
  case new.target_type
    when 'incident' then
      select i.organization_id, i.district_id
        into target_organization_id, target_district_id
        from public.incidents as i
        where i.id = new.target_id;
    when 'segment' then
      select rs.organization_id, rs.district_id
        into target_organization_id, target_district_id
        from public.road_segments as rs
        where rs.id = new.target_id;
    when 'bridge' then
      select b.organization_id, rs.district_id
        into target_organization_id, target_district_id
        from public.bridges as b
        join public.road_segments as rs
          on rs.id = b.segment_id
         and rs.organization_id = b.organization_id
        where b.id = new.target_id;
    when 'facility' then
      select f.organization_id, f.district_id
        into target_organization_id, target_district_id
        from public.facilities as f
        where f.id = new.target_id;
    else
      raise exception 'unsupported inspection target type %', new.target_type
        using errcode = '23514';
  end case;

  if not found then
    raise exception 'inspection target % (%) does not exist', new.target_type, new.target_id
      using errcode = '23503';
  end if;

  if target_organization_id is distinct from new.organization_id
    or target_district_id is distinct from new.district_id then
    raise exception 'inspection target must belong to the same organization and district'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

create trigger inspections_require_existing_scoped_target
  before insert or update of organization_id, district_id, target_type, target_id
  on public.inspections
  for each row execute function public.enforce_inspection_target_scope();

create function public.enforce_trip_route_plan_pair()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  linked_trip_id uuid;
begin
  if tg_table_name = 'trips' then
    if new.route_plan_id is null then
      return new;
    end if;

    select rp.trip_id
      into linked_trip_id
      from public.route_plans as rp
      where rp.id = new.route_plan_id
        and rp.organization_id = new.organization_id;

    if not found or linked_trip_id is distinct from new.id then
      raise exception 'trip route_plan_id must reference a route plan for the same trip'
        using errcode = '23514';
    end if;

    return new;
  end if;

  -- Proposed route plans may point at a trip before that trip selects them.
  if exists (
    select 1
    from public.trips as t
    where t.organization_id = new.organization_id
      and t.route_plan_id = new.id
      and t.id is distinct from new.trip_id
  ) then
    raise exception 'a selected route plan cannot point to a different or null trip'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

create constraint trigger trips_route_plan_same_trip
  after insert or update on public.trips
  deferrable initially deferred
  for each row execute function public.enforce_trip_route_plan_pair();

create constraint trigger route_plans_selected_trip_same_trip
  after insert or update on public.route_plans
  deferrable initially deferred
  for each row execute function public.enforce_trip_route_plan_pair();

create function public.enforce_telemetry_device_trip_binding()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  trip_vehicle_id uuid;
  trip_driver_id uuid;
  device_vehicle_id uuid;
  device_profile_id uuid;
  device_approved_at timestamptz;
  device_revoked_at timestamptz;
begin
  select t.vehicle_id, t.driver_id
    into trip_vehicle_id, trip_driver_id
    from public.trips as t
    where t.id = new.trip_id
      and t.organization_id = new.organization_id;

  if not found then
    raise exception 'telemetry trip does not exist in this organization'
      using errcode = '23503';
  end if;

  select d.vehicle_id, d.profile_id, d.approved_at, d.revoked_at
    into device_vehicle_id, device_profile_id, device_approved_at, device_revoked_at
    from public.device_registrations as d
    where d.id = new.device_id
      and d.organization_id = new.organization_id;

  if not found then
    raise exception 'telemetry device does not exist in this organization'
      using errcode = '23503';
  end if;

  if device_revoked_at is not null or device_approved_at > new.received_at then
    raise exception 'telemetry device is not active for this received time'
      using errcode = '23514';
  end if;

  if (device_vehicle_id is not null and device_vehicle_id is distinct from trip_vehicle_id)
    or (device_profile_id is not null and device_profile_id is distinct from trip_driver_id) then
    raise exception 'telemetry device is not bound to this trip vehicle/driver'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

create trigger telemetry_points_require_trip_bound_active_device
  before insert or update of organization_id, trip_id, device_id, received_at
  on public.telemetry_points
  for each row execute function public.enforce_telemetry_device_trip_binding();

create function public.enforce_trip_current_location_telemetry()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  telemetry_trip_id uuid;
begin
  select tp.trip_id
    into telemetry_trip_id
    from public.telemetry_points as tp
    where tp.id = new.telemetry_point_id
      and tp.organization_id = new.organization_id;

  if not found or telemetry_trip_id is distinct from new.trip_id then
    raise exception 'trip current location must reference telemetry from the same trip'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

create trigger trip_current_location_require_same_trip_telemetry
  before insert or update of organization_id, trip_id, telemetry_point_id
  on public.trip_current_location
  for each row execute function public.enforce_trip_current_location_telemetry();

create function public.enforce_attachment_storage_key_identity()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  required_prefix text;
begin
  required_prefix := new.organization_id::text
    || '/' || new.incident_id::text
    || '/' || new.id::text
    || '/';

  if left(new.storage_key, char_length(required_prefix)) <> required_prefix then
    raise exception 'attachment storage key must begin with organization/incident/attachment IDs'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

create trigger attachments_require_identity_storage_key
  before insert or update of organization_id, incident_id, id, storage_key
  on public.attachments
  for each row execute function public.enforce_attachment_storage_key_identity();

revoke all on function public.enforce_role_assignment_scope() from public;
revoke all on function public.is_active_organization_district(uuid, uuid) from public, anon;
revoke all on function public.geometry_is_within_wgs84_bounds(extensions.geometry) from public, anon;
revoke all on function public.enforce_inspection_target_scope() from public;
revoke all on function public.enforce_trip_route_plan_pair() from public;
revoke all on function public.enforce_telemetry_device_trip_binding() from public;
revoke all on function public.enforce_trip_current_location_telemetry() from public;
revoke all on function public.enforce_attachment_storage_key_identity() from public;
grant execute on function public.geometry_is_within_wgs84_bounds(extensions.geometry)
  to authenticated, service_role;

commit;
