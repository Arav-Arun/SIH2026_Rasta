'use client';

import { useState } from 'react';
import { UserRoundPlus, XCircle } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { useCancelInspection, useInspections } from '@/lib/api/hooks';
import type { InspectionSummary } from '@/lib/api/contracts';
import { newUuid } from '@/lib/ids';
import { cn } from '@/lib/utils';

import { AssignInspectionDialog } from './assign-inspection-dialog';

/** Inspections: the dispatcher's view of who has been sent to look at what. */
export function InspectionQueueScreen() {
  const t = useT();
  const { workspace } = useAuth();
  // Offered only to someone the API would let make the move, so the board
  // never shows a control whose only outcome is a refusal.
  const canManage =
    workspace?.capabilities.includes('inspection:manage') ?? false;
  const [assigning, setAssigning] = useState(false);

  const queue = useInspections(null, 50);
  const cancel = useCancelInspection();

  const inspections = queue.data?.inspections ?? [];
  const open = inspections.filter(
    (row) => row.status !== 'reviewed' && row.status !== 'cancelled',
  );
  const closed = inspections.filter(
    (row) => row.status === 'reviewed' || row.status === 'cancelled',
  );

  return (
    <CommandShell
      activeHref="/inspections"
      title={t('inspections.title')}
      headerExtra={
        queue.data ? <FreshnessLabel asOf={queue.data.as_of} /> : undefined
      }
    >
      <div className="flex max-w-3xl flex-col gap-4">
        <div hidden={!canManage}>
          <button
            type="button"
            onClick={() => setAssigning(true)}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground"
          >
            <UserRoundPlus className="size-4" aria-hidden />
            {t('inspections.assign')}
          </button>
        </div>

        {queue.isError && (
          <ErrorPanel
            title={t('inspections.error.queue')}
            message={
              queue.error instanceof Error ? queue.error.message : undefined
            }
            onRetry={() => void queue.refetch()}
          />
        )}

        {cancel.isError && (
          <ErrorPanel
            title={t('inspections.error.cancel')}
            message={
              cancel.error instanceof Error ? cancel.error.message : undefined
            }
          />
        )}

        {queue.isPending ? (
          <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
        ) : inspections.length === 0 ? (
          <EmptyState title={t('inspections.empty.title')} />
        ) : (
          <>
            <Section
              heading={t('inspections.openHeading')}
              rows={open}
              onCancel={
                canManage
                  ? (id) =>
                      cancel.mutate({
                        inspectionId: id,
                        idempotencyKey: newUuid(),
                      })
                  : undefined
              }
              cancelling={cancel.isPending}
            />
            {closed.length > 0 && (
              <Section heading={t('inspections.closedHeading')} rows={closed} />
            )}
          </>
        )}
      </div>

      {assigning && canManage && (
        <AssignInspectionDialog onClose={() => setAssigning(false)} />
      )}
    </CommandShell>
  );
}

function Section({
  heading,
  rows,
  onCancel,
  cancelling,
}: {
  heading: string;
  rows: InspectionSummary[];
  onCancel?: (inspectionId: string) => void;
  cancelling?: boolean;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  if (rows.length === 0) return null;

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {heading}
      </h2>
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <li
            key={row.id}
            className="rounded-lg border border-border bg-card p-3"
            data-inspection-status={row.status}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">
                  {row.target_label ?? t('inspections.unnamedTarget')}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t(`inspections.target.${row.target_type}`)},{' '}
                  {row.assignee_display_name ??
                    t('inspections.unnamedAssignee')}
                </p>
              </div>
              <span
                className={cn(
                  'shrink-0 rounded-md px-2 py-0.5 text-xs font-medium',
                  row.status === 'overdue'
                    ? 'bg-destructive/10 text-destructive'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                {t(`inspections.status.${row.status}`)}
              </span>
            </div>

            {row.instructions && (
              <p className="mt-2 text-sm">{row.instructions}</p>
            )}

            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                {row.due_at
                  ? t('inspections.dueAt', {
                      when: formatTime.time(row.due_at),
                    })
                  : t('inspections.noDueDate')}
              </span>

              {/* Only an inspection nobody has started may be withdrawn; the
                  server refuses a late cancel, so the control is hidden too. */}
              {onCancel && row.status === 'assigned' && (
                <button
                  type="button"
                  onClick={() => onCancel(row.id)}
                  disabled={cancelling}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 font-medium disabled:opacity-50"
                >
                  <XCircle className="size-3" aria-hidden />
                  {t('inspections.cancel')}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
