-- Per-item quantities on a delivery receipt.

begin;

create table public.delivery_receipt_items (
  id uuid primary key default public.generate_app_uuid(),
  organization_id uuid not null,
  delivery_receipt_id uuid not null,
  consignment_item_id uuid not null,
  -- Zero is meaningful and must be recordable: it is how a receipt says a line
  -- was dispatched but nothing arrived.
  delivered_quantity numeric(14, 3) not null check (delivered_quantity >= 0),
  -- Copied from the consignment item at receipt time and checked by the API
  -- against the item's unit, so a mismatch is refused rather than stored.
  unit text not null check (btrim(unit) <> ''),
  note text check (note is null or char_length(note) <= 1000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1 check (version > 0),
  unique (id, organization_id),
  -- One line per consignment item per receipt; a correction supersedes the
  -- receipt rather than adding a second row for the same item.
  unique (delivery_receipt_id, consignment_item_id),
  foreign key (delivery_receipt_id, organization_id)
    references public.delivery_receipts (id, organization_id) on delete cascade,
  foreign key (consignment_item_id, organization_id)
    references public.consignment_items (id, organization_id) on delete restrict
);

create index delivery_receipt_items_receipt_idx
  on public.delivery_receipt_items (organization_id, delivery_receipt_id);

-- Matches every other mutable operational table: bump both the timestamp and
-- the optimistic-concurrency version on update.
create trigger delivery_receipt_items_touch
  before update on public.delivery_receipt_items
  for each row execute function public.touch_updated_at_and_version();

alter table public.delivery_receipt_items enable row level security;

revoke all on table public.delivery_receipt_items from public, anon;
grant select on table public.delivery_receipt_items to authenticated;

-- Readable by exactly whoever may read the receipt's trip. Writes stay with the
-- service key, as they do for every other operational table.
create policy delivery_receipt_items_select_trip_scope on public.delivery_receipt_items
  for select to authenticated
  using (
    exists (
      select 1
      from public.delivery_receipts as r
      where r.id = delivery_receipt_id
        and r.organization_id = organization_id
        and public.can_read_trip(r.organization_id, r.trip_id)
    )
  );

commit;
