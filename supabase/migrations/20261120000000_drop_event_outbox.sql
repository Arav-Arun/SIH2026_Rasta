-- The event outbox was written on every report, review and inspection
-- assignment, but nothing ever read it: alerts and push notifications are sent
-- in the same transaction as the change that causes them. Its unprocessed count
-- could only grow, so the table and its index go.

begin;

drop table if exists public.event_outbox;

commit;
