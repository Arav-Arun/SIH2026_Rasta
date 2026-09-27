-- RASTA core operational schema.

begin;

create table public.organizations (
  id uuid primary key default public.generate_app_uuid(),
  name text not null check (btrim(name) <> ''),
  mode public.app_mode not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, mode)
);

create table public.districts (
  id uuid primary key default public.generate_app_uuid(),
  state_code text not null check (state_code ~ '^[A-Z]{2,8}$'),
  code text not null check (code ~ '^[A-Z0-9_-]{2,32}$'),
  name text not null check (btrim(name) <> ''),
  boundary extensions.geometry(MultiPolygon, 4326),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (state_code, code),
  check (
    boundary is null
    or (
      extensions.st_srid(boundary) = 4326
      and extensions.st_isvalid(boundary)
      and not extensions.st_isempty(boundary)
    )
  )
);

create table public.organization_districts (
  organization_id uuid not null references public.organizations (id) on delete restrict,
  district_id uuid not null references public.districts (id) on delete restrict,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (organization_id, district_id)
);

create table public.profiles (
  id uuid primary key default public.generate_app_uuid(),
  user_id uuid not null unique references auth.users (id) on delete cascade,
  organization_id uuid not null references public.organizations (id) on delete restrict,
  display_name text not null check (btrim(display_name) <> ''),
  locale text not null default 'en' check (locale ~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id)
);

create table public.role_assignments (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  profile_id uuid not null,
  role public.app_role not null,
  district_id uuid,
  valid_from timestamptz not null default now(),
  valid_to timestamptz,
  revoked_at timestamptz,
  granted_by_profile_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  foreign key (granted_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (valid_to is null or valid_to > valid_from),
  check (revoked_at is null or revoked_at >= valid_from)
);

create unique index role_assignments_exact_grant_key
  on public.role_assignments (
    organization_id,
    profile_id,
    role,
    coalesce(district_id, '00000000-0000-0000-0000-000000000000'::uuid),
    valid_from
  );

create table public.facilities (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  type text not null check (btrim(type) <> ''),
  name text not null check (btrim(name) <> ''),
  location extensions.geometry(Point, 4326),
  active boolean not null default true,
  source_mode public.data_mode not null default 'recorded',
  source_ref text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  check (
    location is null
    or (
      extensions.st_srid(location) = 4326
      and extensions.st_isvalid(location)
      and not extensions.st_isempty(location)
    )
  )
);

create table public.vehicles (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  registration_ref text not null check (btrim(registration_ref) <> ''),
  class text not null check (btrim(class) <> ''),
  capacity_kg numeric(12, 3) check (capacity_kg is null or capacity_kg > 0),
  max_height_m numeric(7, 3) check (max_height_m is null or max_height_m > 0),
  active boolean not null default true,
  source_mode public.data_mode not null default 'recorded',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (organization_id, registration_ref),
  unique (id, organization_id)
);

create table public.device_registrations (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  vehicle_id uuid,
  profile_id uuid,
  platform public.device_platform not null,
  device_public_id text not null check (device_public_id ~ '^[A-Za-z0-9._:-]{8,160}$'),
  display_label text check (display_label is null or btrim(display_label) <> ''),
  approved_at timestamptz not null default now(),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (organization_id, device_public_id),
  unique (id, organization_id),
  foreign key (vehicle_id, organization_id)
    references public.vehicles (id, organization_id) on delete restrict,
  foreign key (profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (revoked_at is null or revoked_at >= approved_at),
  check (vehicle_id is not null or profile_id is not null)
);

create table public.road_segments (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  from_node_id text not null check (btrim(from_node_id) <> ''),
  to_node_id text not null check (btrim(to_node_id) <> ''),
  geometry extensions.geometry(LineString, 4326) not null,
  length_m numeric(12, 3) not null check (length_m > 0),
  road_class text not null check (btrim(road_class) <> ''),
  base_speed_kph numeric(7, 2) check (base_speed_kph is null or base_speed_kph > 0),
  max_weight_t numeric(8, 3) check (max_weight_t is null or max_weight_t > 0),
  network_version text not null check (btrim(network_version) <> ''),
  source_mode public.data_mode not null default 'recorded',
  source_ref text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  check (from_node_id <> to_node_id),
  check (
    extensions.st_srid(geometry) = 4326
    and extensions.st_isvalid(geometry)
    and not extensions.st_isempty(geometry)
    and extensions.st_npoints(geometry) > 1
  )
);

create table public.bridges (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  segment_id uuid not null,
  max_weight_t numeric(8, 3) check (max_weight_t is null or max_weight_t > 0),
  status public.road_passability not null default 'unknown',
  source_id text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (segment_id, organization_id)
    references public.road_segments (id, organization_id) on delete restrict
);

create table public.network_observations (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  segment_id uuid not null,
  kind text not null check (btrim(kind) <> ''),
  passability public.road_passability,
  risk_level public.risk_level,
  risk_score numeric(5, 4) check (risk_score is null or (risk_score >= 0 and risk_score <= 1)),
  observed_at timestamptz not null,
  valid_until timestamptz,
  source_id text not null check (btrim(source_id) <> ''),
  source_mode public.data_mode not null,
  confidence numeric(5, 4) check (confidence is null or (confidence >= 0 and confidence <= 1)),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  unique (id, organization_id),
  foreign key (segment_id, organization_id)
    references public.road_segments (id, organization_id) on delete restrict,
  check (valid_until is null or valid_until >= observed_at)
);

create table public.segment_current_state (
  organization_id uuid not null,
  segment_id uuid not null,
  passability public.road_passability not null default 'unknown',
  risk_level public.risk_level not null default 'unknown',
  risk_score numeric(5, 4) check (risk_score is null or (risk_score >= 0 and risk_score <= 1)),
  as_of timestamptz not null,
  source_summary jsonb not null default '{}'::jsonb check (jsonb_typeof(source_summary) = 'object'),
  network_version text not null check (btrim(network_version) <> ''),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  primary key (organization_id, segment_id),
  foreign key (segment_id, organization_id)
    references public.road_segments (id, organization_id) on delete restrict
);

create table public.incidents (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  type text not null check (btrim(type) <> ''),
  status public.incident_status not null default 'draft',
  location extensions.geometry(Point, 4326),
  accuracy_m numeric(10, 2) check (accuracy_m is null or accuracy_m > 0),
  captured_at timestamptz,
  reported_at timestamptz not null default now(),
  reporter_id uuid,
  source_id text,
  source_mode public.data_mode not null default 'recorded',
  note text check (note is null or char_length(note) <= 4000),
  idempotency_key uuid not null default public.generate_app_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  unique (organization_id, idempotency_key),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  foreign key (reporter_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (
    location is null
    or (
      extensions.st_srid(location) = 4326
      and extensions.st_isvalid(location)
      and not extensions.st_isempty(location)
    )
  ),
  check (
    status not in ('under_review', 'confirmed')
    or (
      location is not null
      and captured_at is not null
      and (reporter_id is not null or source_id is not null)
    )
  )
);

create table public.incident_segments (
  organization_id uuid not null,
  incident_id uuid not null,
  segment_id uuid not null,
  impact public.incident_impact not null,
  reviewed_by_profile_id uuid,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  primary key (organization_id, incident_id, segment_id),
  foreign key (incident_id, organization_id)
    references public.incidents (id, organization_id) on delete cascade,
  foreign key (segment_id, organization_id)
    references public.road_segments (id, organization_id) on delete restrict,
  foreign key (reviewed_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check ((reviewed_by_profile_id is null) = (reviewed_at is null))
);

create table public.attachments (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  incident_id uuid not null,
  uploader_id uuid,
  storage_key text not null check (
    storage_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[A-Za-z0-9._-]{1,120}$'
  ),
  sha256 text not null check (sha256 ~ '^[a-f0-9]{64}$'),
  mime_type text not null check (mime_type ~ '^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$'),
  size_bytes bigint check (size_bytes is null or size_bytes > 0),
  captured_at timestamptz,
  uploaded_at timestamptz,
  upload_status public.attachment_upload_status not null default 'pending',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (storage_key),
  unique (id, organization_id),
  foreign key (incident_id, organization_id)
    references public.incidents (id, organization_id) on delete cascade,
  foreign key (uploader_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (
    (upload_status in ('uploaded', 'verified') and uploaded_at is not null)
    or (upload_status not in ('uploaded', 'verified'))
  )
);

create table public.inspections (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  target_type text not null check (target_type in ('incident', 'segment', 'bridge', 'facility')),
  target_id uuid not null,
  assignee_id uuid not null,
  status public.inspection_status not null default 'assigned',
  due_at timestamptz,
  instructions text check (instructions is null or char_length(instructions) <= 4000),
  result_incident_id uuid,
  created_by_profile_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  foreign key (assignee_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  foreign key (result_incident_id, organization_id)
    references public.incidents (id, organization_id) on delete restrict,
  foreign key (created_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict
);

create table public.supply_requests (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  facility_id uuid not null,
  priority text not null check (priority in ('low', 'normal', 'high', 'critical')),
  needed_by timestamptz,
  status public.supply_request_status not null default 'open',
  note text check (note is null or char_length(note) <= 4000),
  created_by_profile_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (facility_id, organization_id)
    references public.facilities (id, organization_id) on delete restrict,
  foreign key (created_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict
);

create table public.consignments (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  supply_request_id uuid,
  reference text not null check (btrim(reference) <> ''),
  origin_facility_id uuid not null,
  destination_facility_id uuid not null,
  priority text not null check (priority in ('low', 'normal', 'high', 'critical')),
  deadline_at timestamptz,
  status public.consignment_status not null default 'draft',
  created_by_profile_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (organization_id, reference),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  foreign key (supply_request_id, organization_id)
    references public.supply_requests (id, organization_id) on delete restrict,
  foreign key (origin_facility_id, organization_id)
    references public.facilities (id, organization_id) on delete restrict,
  foreign key (destination_facility_id, organization_id)
    references public.facilities (id, organization_id) on delete restrict,
  foreign key (created_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (origin_facility_id <> destination_facility_id)
);

create table public.consignment_items (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  consignment_id uuid not null,
  commodity text not null check (btrim(commodity) <> ''),
  quantity numeric(14, 3) not null check (quantity > 0),
  unit text not null check (btrim(unit) <> ''),
  weight_kg numeric(12, 3) check (weight_kg is null or weight_kg >= 0),
  expiry_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (consignment_id, organization_id)
    references public.consignments (id, organization_id) on delete cascade
);

create table public.trips (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  consignment_id uuid not null,
  vehicle_id uuid not null,
  driver_id uuid not null,
  status public.trip_status not null default 'planned',
  route_plan_id uuid,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  foreign key (consignment_id, organization_id)
    references public.consignments (id, organization_id) on delete restrict,
  foreign key (vehicle_id, organization_id)
    references public.vehicles (id, organization_id) on delete restrict,
  foreign key (driver_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (ended_at is null or started_at is not null),
  check (ended_at is null or ended_at >= started_at)
);

create table public.route_plans (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  district_id uuid not null,
  trip_id uuid,
  request jsonb not null check (jsonb_typeof(request) = 'object'),
  network_version text not null check (btrim(network_version) <> ''),
  risk_snapshot_version text not null check (btrim(risk_snapshot_version) <> ''),
  status public.route_plan_status not null default 'proposed',
  chosen_alternative_id uuid,
  approved_by_profile_id uuid,
  approved_at timestamptz,
  created_by_profile_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  foreign key (trip_id, organization_id)
    references public.trips (id, organization_id) on delete restrict,
  foreign key (approved_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  foreign key (created_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check ((approved_by_profile_id is null) = (approved_at is null)),
  check (
    status <> 'approved'
    or (chosen_alternative_id is not null and approved_by_profile_id is not null and approved_at is not null)
  )
);

create table public.route_alternatives (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  route_plan_id uuid not null,
  rank integer not null check (rank > 0),
  category text not null check (btrim(category) <> ''),
  geometry extensions.geometry(LineString, 4326),
  segment_ids jsonb not null check (jsonb_typeof(segment_ids) = 'array'),
  distance_m numeric(14, 3) not null check (distance_m >= 0),
  eta_seconds integer check (eta_seconds is null or eta_seconds >= 0),
  risk_summary jsonb not null default '{}'::jsonb check (jsonb_typeof(risk_summary) = 'object'),
  constraint_warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(constraint_warnings) = 'array'),
  created_at timestamptz not null default now(),
  unique (id, route_plan_id, organization_id),
  unique (route_plan_id, organization_id, rank),
  foreign key (route_plan_id, organization_id)
    references public.route_plans (id, organization_id) on delete cascade,
  check (
    geometry is null
    or (
      extensions.st_srid(geometry) = 4326
      and extensions.st_isvalid(geometry)
      and not extensions.st_isempty(geometry)
      and extensions.st_npoints(geometry) > 1
    )
  )
);

alter table public.route_plans
  add constraint route_plans_chosen_alternative_same_plan_fkey
  foreign key (chosen_alternative_id, id, organization_id)
  references public.route_alternatives (id, route_plan_id, organization_id)
  on delete restrict;

alter table public.trips
  add constraint trips_route_plan_same_organization_fkey
  foreign key (route_plan_id, organization_id)
  references public.route_plans (id, organization_id)
  on delete restrict;

create table public.telemetry_points (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  trip_id uuid not null,
  device_id uuid not null,
  captured_at timestamptz not null,
  received_at timestamptz not null default now(),
  location extensions.geometry(Point, 4326) not null,
  accuracy_m numeric(10, 2) not null check (accuracy_m > 0),
  speed_kph numeric(7, 2) check (speed_kph is null or speed_kph >= 0),
  heading numeric(6, 2) check (heading is null or (heading >= 0 and heading < 360)),
  idempotency_key uuid not null,
  created_at timestamptz not null default now(),
  unique (id, organization_id),
  unique (organization_id, idempotency_key),
  foreign key (trip_id, organization_id)
    references public.trips (id, organization_id) on delete restrict,
  foreign key (device_id, organization_id)
    references public.device_registrations (id, organization_id) on delete restrict,
  check (
    extensions.st_srid(location) = 4326
    and extensions.st_isvalid(location)
    and not extensions.st_isempty(location)
  )
);

create table public.trip_current_location (
  organization_id uuid not null,
  trip_id uuid not null,
  telemetry_point_id uuid not null,
  location extensions.geometry(Point, 4326) not null,
  as_of timestamptz not null,
  stale_after timestamptz not null,
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  primary key (organization_id, trip_id),
  foreign key (trip_id, organization_id)
    references public.trips (id, organization_id) on delete cascade,
  foreign key (telemetry_point_id, organization_id)
    references public.telemetry_points (id, organization_id) on delete restrict,
  check (stale_after >= as_of),
  check (
    extensions.st_srid(location) = 4326
    and extensions.st_isvalid(location)
    and not extensions.st_isempty(location)
  )
);

create table public.delivery_receipts (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  trip_id uuid not null,
  status public.delivery_receipt_status not null,
  received_at timestamptz not null default now(),
  received_by_ref text check (received_by_ref is null or char_length(received_by_ref) <= 256),
  notes text check (notes is null or char_length(notes) <= 4000),
  correction_reason text check (correction_reason is null or char_length(correction_reason) <= 1000),
  created_by_profile_id uuid,
  idempotency_key uuid not null default public.generate_app_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  unique (organization_id, idempotency_key),
  foreign key (trip_id, organization_id)
    references public.trips (id, organization_id) on delete restrict,
  foreign key (created_by_profile_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict
);

create table public.alerts (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  district_id uuid,
  type text not null check (btrim(type) <> ''),
  severity text not null check (severity in ('info', 'warning', 'critical')),
  title_key text not null check (btrim(title_key) <> ''),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  valid_from timestamptz not null default now(),
  valid_until timestamptz,
  dedupe_key text not null check (btrim(dedupe_key) <> ''),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  unique (organization_id, dedupe_key),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  check (valid_until is null or valid_until >= valid_from)
);

create table public.alert_recipients (
  organization_id uuid not null,
  alert_id uuid not null,
  profile_id uuid not null,
  status public.alert_recipient_status not null default 'pending',
  delivered_at timestamptz,
  acknowledged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  primary key (organization_id, alert_id, profile_id),
  foreign key (alert_id, organization_id)
    references public.alerts (id, organization_id) on delete cascade,
  foreign key (profile_id, organization_id)
    references public.profiles (id, organization_id) on delete cascade,
  check (acknowledged_at is null or status = 'acknowledged')
);

create table public.source_runs (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  district_id uuid,
  source text not null check (btrim(source) <> ''),
  source_mode public.data_mode not null,
  started_at timestamptz not null,
  finished_at timestamptz,
  status public.source_run_status not null,
  etag text,
  raw_checksum text check (raw_checksum is null or raw_checksum ~ '^[a-f0-9]{64}$'),
  record_count integer not null default 0 check (record_count >= 0),
  error_code text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (id, organization_id),
  foreign key (organization_id, district_id)
    references public.organization_districts (organization_id, district_id) on delete restrict,
  check (finished_at is null or finished_at >= started_at)
);

create table public.model_versions (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  name text not null check (btrim(name) <> ''),
  version text not null check (btrim(version) <> ''),
  feature_schema_hash text not null check (feature_schema_hash ~ '^[a-f0-9]{64}$'),
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  training_manifest jsonb not null default '{}'::jsonb check (jsonb_typeof(training_manifest) = 'object'),
  active boolean not null default false,
  created_at timestamptz not null default now(),
  unique (organization_id, name, version),
  unique (id, organization_id)
);

create unique index model_versions_one_active_per_name
  on public.model_versions (organization_id, name)
  where active;

create table public.sync_mutations (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  actor_id uuid not null,
  idempotency_key uuid not null,
  request_hash text not null check (request_hash ~ '^[a-f0-9]{64}$'),
  result_code text not null check (btrim(result_code) <> ''),
  result_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(result_payload) = 'object'),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (organization_id, actor_id, idempotency_key),
  foreign key (actor_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict,
  check (expires_at > created_at)
);

create table public.audit_events (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  actor_id uuid,
  action text not null check (btrim(action) <> ''),
  entity_type text not null check (btrim(entity_type) <> ''),
  entity_id uuid,
  occurred_at timestamptz not null default now(),
  before_hash text check (before_hash is null or before_hash ~ '^[a-f0-9]{64}$'),
  after_hash text check (after_hash is null or after_hash ~ '^[a-f0-9]{64}$'),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  foreign key (actor_id, organization_id)
    references public.profiles (id, organization_id) on delete restrict
);

create table public.event_outbox (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  event_type text not null check (btrim(event_type) <> ''),
  aggregate_id uuid,
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  available_at timestamptz not null default now(),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  processed_at timestamptz,
  created_at timestamptz not null default now()
);

-- Domain invariant: a normal delivered receipt needs a started trip.
create function public.enforce_delivery_receipt_trip_started()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
declare
  trip_started_at timestamptz;
begin
  select started_at
  into trip_started_at
  from public.trips
  where id = new.trip_id
    and organization_id = new.organization_id;

  if new.status = 'delivered'
    and trip_started_at is null
    and nullif(btrim(coalesce(new.correction_reason, '')), '') is null then
    raise exception 'a delivered receipt requires a started trip or correction_reason'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

-- Timestamp/version triggers are intentionally attached only to mutable
-- entities. Observation, telemetry, audit and outbox records stay append-only.
create trigger organizations_touch before update on public.organizations
  for each row execute function public.touch_updated_at_and_version();
create trigger districts_touch before update on public.districts
  for each row execute function public.touch_updated_at_and_version();
create trigger organization_districts_touch before update on public.organization_districts
  for each row execute function public.set_updated_at();
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at_and_version();
create trigger role_assignments_touch before update on public.role_assignments
  for each row execute function public.touch_updated_at_and_version();
create trigger facilities_touch before update on public.facilities
  for each row execute function public.touch_updated_at_and_version();
create trigger vehicles_touch before update on public.vehicles
  for each row execute function public.touch_updated_at_and_version();
create trigger device_registrations_touch before update on public.device_registrations
  for each row execute function public.touch_updated_at_and_version();
create trigger road_segments_touch before update on public.road_segments
  for each row execute function public.touch_updated_at_and_version();
create trigger bridges_touch before update on public.bridges
  for each row execute function public.touch_updated_at_and_version();
create trigger segment_current_state_touch before update on public.segment_current_state
  for each row execute function public.touch_updated_at_and_version();
create trigger incidents_touch before update on public.incidents
  for each row execute function public.touch_updated_at_and_version();
create trigger attachments_touch before update on public.attachments
  for each row execute function public.touch_updated_at_and_version();
create trigger inspections_touch before update on public.inspections
  for each row execute function public.touch_updated_at_and_version();
create trigger supply_requests_touch before update on public.supply_requests
  for each row execute function public.touch_updated_at_and_version();
create trigger consignments_touch before update on public.consignments
  for each row execute function public.touch_updated_at_and_version();
create trigger consignment_items_touch before update on public.consignment_items
  for each row execute function public.touch_updated_at_and_version();
create trigger trips_touch before update on public.trips
  for each row execute function public.touch_updated_at_and_version();
create trigger route_plans_touch before update on public.route_plans
  for each row execute function public.touch_updated_at_and_version();
create trigger trip_current_location_touch before update on public.trip_current_location
  for each row execute function public.touch_updated_at_and_version();
create trigger delivery_receipts_touch before update on public.delivery_receipts
  for each row execute function public.touch_updated_at_and_version();
create trigger alerts_touch before update on public.alerts
  for each row execute function public.touch_updated_at_and_version();
create trigger alert_recipients_touch before update on public.alert_recipients
  for each row execute function public.touch_updated_at_and_version();

create trigger delivery_receipts_require_started_trip
  before insert or update on public.delivery_receipts
  for each row execute function public.enforce_delivery_receipt_trip_started();

create trigger audit_events_append_only
  before update or delete on public.audit_events
  for each row execute function public.prevent_append_only_mutation();
create trigger telemetry_points_append_only
  before update or delete on public.telemetry_points
  for each row execute function public.prevent_append_only_mutation();
create trigger network_observations_append_only
  before update or delete on public.network_observations
  for each row execute function public.prevent_append_only_mutation();

-- Query-path indexes. JSONB intentionally has no catch-all GIN index: future
-- query needs must be demonstrated and modelled as typed columns first.
create index districts_boundary_gix on public.districts using gist (boundary);
create index facilities_location_gix on public.facilities using gist (location);
create index road_segments_geometry_gix on public.road_segments using gist (geometry);
create index incidents_location_gix on public.incidents using gist (location);
create index telemetry_points_location_gix on public.telemetry_points using gist (location);
create index trip_current_location_gix on public.trip_current_location using gist (location);

create index facilities_org_district_active_idx
  on public.facilities (organization_id, district_id, active);
create index road_segments_org_district_network_idx
  on public.road_segments (organization_id, district_id, network_version);
create index bridges_segment_idx on public.bridges (organization_id, segment_id);
create index network_observations_segment_observed_idx
  on public.network_observations (organization_id, segment_id, observed_at desc);
create index segment_current_state_passability_as_of_idx
  on public.segment_current_state (organization_id, passability, as_of desc);
create index incidents_org_district_status_updated_idx
  on public.incidents (organization_id, district_id, status, updated_at desc);
create index inspections_org_district_status_due_idx
  on public.inspections (organization_id, district_id, status, due_at);
create index supply_requests_open_priority_idx
  on public.supply_requests (organization_id, facility_id, priority, needed_by)
  where status = 'open';
create index consignments_org_district_status_updated_idx
  on public.consignments (organization_id, district_id, status, updated_at desc);
create index trips_active_org_district_idx
  on public.trips (organization_id, district_id, updated_at desc)
  where status in ('awaiting_driver', 'active', 'paused');
create index trips_driver_status_idx
  on public.trips (organization_id, driver_id, status, updated_at desc);
create index route_plans_org_district_status_updated_idx
  on public.route_plans (organization_id, district_id, status, updated_at desc);
create index telemetry_points_trip_captured_idx
  on public.telemetry_points (organization_id, trip_id, captured_at desc);
create index alerts_validity_idx
  on public.alerts (organization_id, valid_until, valid_from desc);
create index alert_recipients_profile_status_idx
  on public.alert_recipients (organization_id, profile_id, status, updated_at desc);
create index source_runs_org_source_started_idx
  on public.source_runs (organization_id, source, started_at desc);
create index sync_mutations_expiry_idx on public.sync_mutations (expires_at);
create index audit_events_org_occurred_idx
  on public.audit_events (organization_id, occurred_at desc);
create index event_outbox_unprocessed_idx
  on public.event_outbox (available_at)
  where processed_at is null;

revoke all on function public.enforce_delivery_receipt_trip_started() from public;

commit;
