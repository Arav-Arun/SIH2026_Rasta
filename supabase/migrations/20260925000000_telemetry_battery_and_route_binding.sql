-- Telemetry ingestion, current location and stale logic.

begin;


alter table public.telemetry_points
  add column if not exists battery_percent numeric(5, 2)
    check (
      battery_percent is null
      or (battery_percent >= 0 and battery_percent <= 100)
    );

comment on column public.telemetry_points.battery_percent is
  'Reporting device battery level at capture time, 0-100. Null means the device did not report one.';

alter table public.trips
  drop constraint if exists trips_route_plan_id_fkey;

alter table public.trips
  add constraint trips_route_plan_same_organization
    foreign key (route_plan_id, organization_id)
    references public.route_plans (id, organization_id) on delete set null;

-- A dispatcher asks "where is every active trip in my district right now?" on every
-- fleet screen refresh.
create index if not exists trips_active_district_idx
  on public.trips (organization_id, district_id, status)
  where status in ('active', 'paused');

commit;
