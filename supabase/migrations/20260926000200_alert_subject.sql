-- An alert has to say what it is about, not only what happened.

begin;


alter table public.alerts
  add column if not exists subject_type text,
  add column if not exists subject_id text;

-- Backfill from the dedupe key, which already encodes
-- `type:subject_type:subject_id:bucket`, so existing rows are not left blank.
update public.alerts
set subject_type = split_part(dedupe_key, ':', 2),
    subject_id = split_part(dedupe_key, ':', 3)
where subject_type is null
  and array_length(string_to_array(dedupe_key, ':'), 1) >= 3;

alter table public.alerts
  add constraint alerts_subject_stated
    check (
      (subject_type is null and subject_id is null)
      or (btrim(subject_type) <> '' and btrim(subject_id) <> '')
    );

-- "Every alert about this trip", which is the inbox's and the trip screen's
-- question, and the one the dedupe key cannot answer.
create index if not exists alerts_subject_idx
  on public.alerts (organization_id, subject_type, subject_id, valid_from desc);

commit;
