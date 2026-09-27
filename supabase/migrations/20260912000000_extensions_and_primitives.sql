-- RASTA schema foundation.

begin;

create schema if not exists extensions;

-- Keep extension objects outside `public`.
create extension if not exists pgcrypto with schema extensions;
create extension if not exists postgis with schema extensions;

create type public.app_mode as enum (
  'local_demo',
  'hosted_demo',
  'pilot'
);

create type public.data_mode as enum (
  'synthetic',
  'recorded',
  'live'
);

create type public.app_role as enum (
  'state_coordinator',
  'district_dispatcher',
  'field_officer',
  'driver',
  'admin',
  'reviewer'
);

create type public.road_passability as enum (
  'open',
  'restricted',
  'closed',
  'unknown'
);

create type public.risk_level as enum (
  'unknown',
  'low',
  'moderate',
  'high',
  'critical'
);

create type public.incident_status as enum (
  'draft',
  'queued',
  'submitted',
  'under_review',
  'confirmed',
  'rejected',
  'superseded'
);

create type public.inspection_status as enum (
  'assigned',
  'accepted',
  'in_progress',
  'submitted',
  'reviewed',
  'cancelled',
  'overdue'
);

create type public.supply_request_status as enum (
  'open',
  'planned',
  'partially_fulfilled',
  'fulfilled',
  'cancelled'
);

create type public.consignment_status as enum (
  'draft',
  'planned',
  'assigned',
  'in_transit',
  'delivered',
  'partially_delivered',
  'failed',
  'cancelled'
);

create type public.trip_status as enum (
  'planned',
  'awaiting_driver',
  'active',
  'paused',
  'completed',
  'failed',
  'cancelled'
);

create type public.route_plan_status as enum (
  'proposed',
  'approved',
  'superseded',
  'invalidated',
  'rejected'
);

create type public.alert_recipient_status as enum (
  'pending',
  'sent',
  'delivered',
  'acknowledged',
  'failed',
  'expired'
);

create type public.source_run_status as enum (
  'success',
  'unchanged',
  'partial',
  'failed',
  'disabled'
);

create type public.attachment_upload_status as enum (
  'pending',
  'uploaded',
  'verified',
  'rejected',
  'expired'
);

create type public.delivery_receipt_status as enum (
  'delivered',
  'partially_delivered',
  'failed'
);

create type public.incident_impact as enum (
  'monitor',
  'restriction',
  'closure'
);

create type public.device_platform as enum (
  'android',
  'web'
);

-- Opaque UUIDs are used throughout.
create function public.generate_app_uuid()
returns uuid
language sql
volatile
set search_path = public, extensions, pg_catalog
as $$
  select extensions.gen_random_uuid();
$$;

create function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create function public.touch_updated_at_and_version()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
begin
  new.updated_at = now();
  new.version = old.version + 1;
  return new;
end;
$$;

create function public.prevent_append_only_mutation()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
begin
  raise exception 'append-only table % cannot be updated or deleted', tg_table_name
    using errcode = '55000';
end;
$$;

revoke all on function public.generate_app_uuid() from public;
revoke all on function public.set_updated_at() from public;
revoke all on function public.touch_updated_at_and_version() from public;
revoke all on function public.prevent_append_only_mutation() from public;

commit;
