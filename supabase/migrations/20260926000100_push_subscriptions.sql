-- An optional push channel.

begin;


create table if not exists public.push_subscriptions (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  profile_id uuid not null,
  platform public.device_platform not null,
  -- Web Push: the endpoint URL the browser issued. FCM: the device token.
  -- Both are bearer-equivalent, so they are never returned by any read.
  endpoint text not null check (btrim(endpoint) <> ''),
  -- Hashed so the unique index does not need the secret value in an index we
  -- read back, and so a log of the key never identifies the endpoint.
  endpoint_hash text not null check (endpoint_hash ~ '^[a-f0-9]{64}$'),
  -- Web Push client keys (p256dh, auth). Empty for FCM.
  keys jsonb not null default '{}'::jsonb check (jsonb_typeof(keys) = 'object'),
  user_agent text check (user_agent is null or char_length(user_agent) <= 400),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  revoked_at timestamptz,
  unique (organization_id, endpoint_hash),
  unique (id, organization_id),
  foreign key (profile_id, organization_id)
    references public.profiles (id, organization_id) on delete cascade,
  check (revoked_at is null or revoked_at >= created_at)
);

create index if not exists push_subscriptions_profile_idx
  on public.push_subscriptions (organization_id, profile_id)
  where revoked_at is null;

create table if not exists public.push_attempts (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null references public.organizations (id) on delete restrict,
  subscription_id uuid,
  alert_id uuid,
  attempted_at timestamptz not null default now(),
  -- 'sent' | 'failed' | 'skipped', skipped covers "no sender configured",
  -- which is the normal state of the zero-cost local deployment.
  status text not null check (status in ('sent', 'failed', 'skipped')),
  -- A reason code, never a provider response body: those echo the endpoint.
  error_code text check (error_code is null or btrim(error_code) <> ''),
  http_status integer,
  created_at timestamptz not null default now(),
  foreign key (subscription_id, organization_id)
    references public.push_subscriptions (id, organization_id) on delete set null,
  foreign key (alert_id, organization_id)
    references public.alerts (id, organization_id) on delete cascade
);

create index if not exists push_attempts_alert_idx
  on public.push_attempts (organization_id, alert_id, attempted_at desc);

alter table public.push_subscriptions enable row level security;
alter table public.push_attempts enable row level security;

-- No browser reads either table. Registration goes through the API so the
-- endpoint is hashed and the attempt history stays server-side.
revoke all on public.push_subscriptions from authenticated, anon;
revoke all on public.push_attempts from authenticated, anon;

commit;
