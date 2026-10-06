'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { FilePlus2, ListChecks, Play, Send, ThumbsUp } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { useLocale } from '@/components/i18n/locale-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { Button } from '@/components/ui/button';
import type { InspectionSummary } from '@/lib/api/contracts';
import { useInspections } from '@/lib/api/hooks';
import { formatAbsoluteTime } from '@/lib/i18n/format';
import { enqueueMutation, listMutations } from '@/lib/offline/outbox';
import { requestSync } from '@/lib/offline/sync-request';
import type { MutationEnvelope, MutationType } from '@/lib/offline/types';
import { cn } from '@/lib/utils';

import { SosCard } from './sos-card';

type InspectionAction =
  | 'inspection.accept'
  | 'inspection.start'
  | 'inspection.complete';

/** The next move the API allows from each state, and nothing else. */
const NEXT_ACTION: Partial<
  Record<InspectionSummary['status'], InspectionAction>
> = {
  assigned: 'inspection.accept',
  overdue: 'inspection.accept',
  accepted: 'inspection.start',
  in_progress: 'inspection.complete',
};

const OPEN_STATES = new Set<InspectionSummary['status']>([
  'assigned',
  'overdue',
  'accepted',
  'in_progress',
  'submitted',
]);

function isInspectionAction(type: MutationType): type is InspectionAction {
  return (
    type === 'inspection.accept' ||
    type === 'inspection.start' ||
    type === 'inspection.complete'
  );
}

/** Inspection moves this person has queued and the server has not yet taken. */
function usePendingInspectionMoves(profileId: string | null) {
  return useQuery({
    queryKey: ['outbox', 'inspection-moves', profileId],
    queryFn: async () => {
      const rows = await listMutations(profileId);
      const pending = new Map<string, MutationEnvelope>();
      for (const row of rows) {
        if (!isInspectionAction(row.type) || row.state === 'accepted') continue;
        const id = row.body.inspection_id;
        if (typeof id === 'string') pending.set(id, row);
      }
      return pending;
    },
    refetchInterval: (query) =>
      (query.state.data?.size ?? 0) > 0 ? 2_000 : false,
  });
}

/** What the control room has sent this field officer to look at. */
export function FieldHomeScreen() {
  const { locale, t } = useLocale();
  const { workspace } = useAuth();
  const profileId = workspace?.identity.profileId ?? null;
  const queryClient = useQueryClient();

  const queue = useInspections(null, 50);
  const pending = usePendingInspectionMoves(profileId);
  const [completing, setCompleting] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [queueError, setQueueError] = useState<string | null>(null);

  const inspections = queue.data?.inspections ?? [];
  const open = inspections.filter((row) => OPEN_STATES.has(row.status));

  async function queueMove(
    inspection: InspectionSummary,
    type: InspectionAction,
    body: Record<string, unknown> = {},
  ) {
    setQueueError(null);
    try {
      await enqueueMutation({
        type,
        profileId,
        body: { inspection_id: inspection.id, ...body },
      });
      await queryClient.invalidateQueries({ queryKey: ['outbox'] });
      requestSync();
    } catch (error) {
      setQueueError(
        error instanceof Error ? error.message : t('fieldHome.queueFailed'),
      );
    }
  }

  return (
    <CommandShell
      ownHeading
      activeHref="/field/home"
      title={t('fieldHome.title')}
      headerExtra={
        queue.data ? <FreshnessLabel asOf={queue.data.as_of} /> : undefined
      }
    >
      <div className="flex max-w-3xl flex-col gap-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('fieldHome.heading')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('fieldHome.lede')}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button nativeButton={false} render={<Link href="/field/report" />}>
            <FilePlus2 aria-hidden />
            {t('fieldHome.newReport')}
          </Button>
          <Button
            variant="outline"
            nativeButton={false}
            render={<Link href="/sync" />}
          >
            <ListChecks aria-hidden />
            {t('fieldHome.openQueue')}
          </Button>
        </div>

        <SosCard />

        {queue.isError ? (
          <ErrorPanel
            title={t('fieldHome.error')}
            message={
              queue.error instanceof Error ? queue.error.message : undefined
            }
            onRetry={() => void queue.refetch()}
          />
        ) : null}
        {queueError ? (
          <p className="text-sm text-destructive" role="alert">
            {queueError}
          </p>
        ) : null}

        <section
          className="flex flex-col gap-2"
          aria-labelledby="assigned-heading"
        >
          <h2
            id="assigned-heading"
            className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            {t('fieldHome.assignedHeading')}
          </h2>

          {queue.isPending ? (
            <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
          ) : open.length === 0 ? (
            <EmptyState title={t('fieldHome.empty.title')} />
          ) : (
            <ul className="flex flex-col gap-2">
              {open.map((inspection) => {
                const waiting = pending.data?.get(inspection.id) ?? null;
                const next = NEXT_ACTION[inspection.status];
                return (
                  <li
                    key={inspection.id}
                    className="rounded-lg border bg-card p-3"
                    data-inspection-id={inspection.id}
                    data-inspection-status={inspection.status}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold">
                          {inspection.target_label ??
                            t('inspections.unnamedTarget')}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {t(`inspections.target.${inspection.target_type}`)}
                          {', '}
                          {inspection.due_at
                            ? t('inspections.dueAt', {
                                when: formatAbsoluteTime(
                                  locale,
                                  new Date(inspection.due_at),
                                ),
                              })
                            : t('inspections.noDueDate')}
                        </p>
                      </div>
                      <span
                        className={cn(
                          'shrink-0 rounded-md px-2 py-0.5 text-xs font-medium',
                          inspection.status === 'overdue'
                            ? 'bg-destructive/10 text-destructive'
                            : 'bg-muted text-muted-foreground',
                        )}
                      >
                        {t(`inspections.status.${inspection.status}`)}
                      </span>
                    </div>

                    {inspection.instructions ? (
                      <p className="mt-2 text-sm">{inspection.instructions}</p>
                    ) : null}

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {waiting ? (
                        <span
                          className={cn(
                            'text-xs',
                            waiting.state === 'needs_action' ||
                              waiting.state === 'conflict'
                              ? 'text-destructive'
                              : 'text-muted-foreground',
                          )}
                          data-move-state={waiting.state}
                        >
                          {waiting.state === 'needs_action' ||
                          waiting.state === 'conflict'
                            ? (waiting.lastError ??
                              t('fieldHome.moveNeedsAttention'))
                            : t('fieldHome.moveQueued', {
                                move: t(`outbox.type.${waiting.type}`),
                              })}
                        </span>
                      ) : next === 'inspection.accept' ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void queueMove(inspection, next)}
                        >
                          <ThumbsUp aria-hidden />
                          {t('fieldHome.accept')}
                        </Button>
                      ) : next === 'inspection.start' ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void queueMove(inspection, next)}
                        >
                          <Play aria-hidden />
                          {t('fieldHome.start')}
                        </Button>
                      ) : next === 'inspection.complete' &&
                        completing !== inspection.id ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setCompleting(inspection.id);
                            setNote('');
                          }}
                        >
                          <Send aria-hidden />
                          {t('fieldHome.submitFindings')}
                        </Button>
                      ) : null}

                      {!waiting && inspection.target_type === 'segment' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          render={
                            <Link
                              href={`/field/report?segment=${encodeURIComponent(inspection.target_id)}`}
                            />
                          }
                        >
                          <FilePlus2 aria-hidden />
                          {t('fieldHome.reportHere')}
                        </Button>
                      ) : null}
                    </div>

                    {completing === inspection.id && !waiting ? (
                      <form
                        className="mt-3 flex flex-col gap-2 border-t pt-3"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void queueMove(inspection, 'inspection.complete', {
                            note: note.trim() || null,
                            result_incident_id: null,
                          }).then(() => setCompleting(null));
                        }}
                      >
                        <label className="flex flex-col gap-1.5 text-xs font-medium">
                          {t('fieldHome.findingsLabel')}
                          <textarea
                            rows={3}
                            maxLength={2000}
                            value={note}
                            onChange={(event) => setNote(event.target.value)}
                            className="rounded-md border border-border bg-background p-2 text-sm"
                          />
                        </label>
                        <div className="flex flex-wrap gap-2">
                          <Button type="submit" size="sm">
                            {t('fieldHome.queueFindings')}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => setCompleting(null)}
                          >
                            {t('confirm.cancel')}
                          </Button>
                        </div>
                      </form>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </CommandShell>
  );
}
