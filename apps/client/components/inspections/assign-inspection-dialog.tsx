'use client';

import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

import { ErrorPanel } from '@/components/common/error-panel';
import { useAuth } from '@/components/auth/auth-provider';
import { useT } from '@/components/i18n/locale-provider';
import { newUuid } from '@/lib/ids';
import {
  useAssignInspection,
  useAssignableOfficers,
  useIncidents,
} from '@/lib/api/hooks';

/** Sends a field officer to verify something. */
export function AssignInspectionDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const { workspace } = useAuth();
  const ref = useRef<HTMLDialogElement>(null);

  const districts = workspace?.districts ?? [];
  const [districtId, setDistrictId] = useState(districts[0]?.id ?? '');
  const [incidentId, setIncidentId] = useState('');
  const [assignee, setAssignee] = useState('');
  const [instructions, setInstructions] = useState('');

  const officers = useAssignableOfficers(districtId || null);
  const incidents = useIncidents('submitted', 50);
  const assign = useAssignInspection();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const canSubmit =
    districtId !== '' &&
    incidentId !== '' &&
    assignee !== '' &&
    !assign.isPending;

  function submit() {
    if (!canSubmit) return;
    assign.mutate(
      {
        body: {
          target_type: 'incident',
          target_id: incidentId,
          assignee_profile_id: assignee,
          instructions: instructions.trim() || null,
          due_at: null,
        },
        idempotencyKey: newUuid(),
      },
      { onSuccess: () => ref.current?.close() },
    );
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={t('inspections.assign')}
      className="w-[min(32rem,92vw)] rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-black/50"
    >
      <div className="flex flex-col gap-4 p-4">
        <header className="flex items-start justify-between gap-2">
          <h2 className="text-base font-semibold">{t('inspections.assign')}</h2>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            aria-label={t('confirm.cancel')}
            className="rounded-md p-1"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>

        {districts.length > 1 && (
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('inspections.district')}
            </span>
            <select
              value={districtId}
              onChange={(event) => {
                setDistrictId(event.target.value);
                setAssignee('');
              }}
              className="rounded-md border border-border bg-background p-2 text-sm"
            >
              {districts.map((district) => (
                <option key={district.id} value={district.id}>
                  {district.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('inspections.target.incident')}
          </span>
          <select
            value={incidentId}
            onChange={(event) => setIncidentId(event.target.value)}
            className="rounded-md border border-border bg-background p-2 text-sm"
          >
            <option value="">{t('inspections.choose')}</option>
            {(incidents.data?.incidents ?? []).map((incident) => (
              <option key={incident.id} value={incident.id}>
                {t(`incidentType.${incident.type}`)},{' '}
                {incident.note?.slice(0, 60) ?? t('incidents.noNote')}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('inspections.assignee')}
          </span>
          <select
            value={assignee}
            onChange={(event) => setAssignee(event.target.value)}
            disabled={officers.isPending}
            className="rounded-md border border-border bg-background p-2 text-sm"
          >
            <option value="">{t('inspections.choose')}</option>
            {(officers.data?.officers ?? []).map((officer) => (
              <option key={officer.profile_id} value={officer.profile_id}>
                {officer.display_name}
              </option>
            ))}
          </select>
          {officers.data?.officers.length === 0 && (
            <span className="text-xs text-amber-700 dark:text-amber-400">
              {t('inspections.noOfficers')}
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('inspections.instructions')}
          </span>
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            rows={3}
            placeholder={t('inspections.instructionsPlaceholder')}
            className="rounded-md border border-border bg-background p-2 text-sm"
          />
        </label>

        {assign.isError && (
          <ErrorPanel
            title={t('inspections.error.assign')}
            message={
              assign.error instanceof Error ? assign.error.message : undefined
            }
          />
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => ref.current?.close()}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium"
          >
            {t('confirm.cancel')}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {assign.isPending
              ? t('inspections.assigning')
              : t('inspections.assign')}
          </button>
        </div>
      </div>
    </dialog>
  );
}
