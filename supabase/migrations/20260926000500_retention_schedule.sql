-- Runs the retention policy every night.

begin;

create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;

-- An operator's tool. Nothing holding an API key may call it, the server's
-- own service key included.
revoke all on function public.apply_retention(integer, integer, boolean) from service_role;

select cron.schedule(
  'rasta-apply-retention',
  '47 21 * * *',  -- 21:47 UTC is 03:17 IST
  $$select * from public.apply_retention(30, 90, false)$$
);

commit;
