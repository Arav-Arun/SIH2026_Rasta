'use client';

import { useMemo, useState } from 'react';
import { CheckCircle2, CircleDashed, ShieldAlert } from 'lucide-react';

import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { useT } from '@/components/i18n/locale-provider';
import { useIncidentDetail, useIncidents } from '@/lib/api/hooks';
import type { IncidentSummary } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import { IncidentReviewPanel } from './incident-review-panel';

/** Queue tabs. `null` is "everything this dispatcher can see". */
const FILTERS = [
  { value: 'submitted', labelKey: 'incidents.filter.submitted' },
  { value: 'under_review', labelKey: 'incidents.filter.underReview' },
  { value: null, labelKey: 'incidents.filter.all' },
] as const;

type FilterValue = (typeof FILTERS)[number]['value'];

export function ReviewQueueScreen() {
  const t = useT();
  const [filter, setFilter] = useState<FilterValue>('submitted');
  // The selected report is held as a value, not an id: once a decision is
  // recorded the report leaves the "awaiting" filter, and looking it up in the
  // list would unmount the panel before the reviewer has seen what their
  // decision did to the network.
  const [selected, setSelected] = useState<IncidentSummary | null>(null);

  const queue = useIncidents(filter, 50);
  const detail = useIncidentDetail(selected?.id ?? null);

  const incidents = useMemo(
    () => queue.data?.incidents ?? [],
    [queue.data?.incidents],
  );

  return (
    <CommandShell
      activeHref="/incidents"
      title={t('incidents.title')}
      headerExtra={
        queue.data ? <FreshnessLabel asOf={queue.data.as_of} /> : undefined
      }
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <section
          aria-label={t('incidents.queueLabel')}
          className="flex flex-col gap-3"
        >
          <div
            role="tablist"
            aria-label={t('incidents.filterLabel')}
            className="flex gap-1"
          >
            {FILTERS.map((option) => {
              const active = filter === option.value;
              return (
                <button
                  key={option.labelKey}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => {
                    setFilter(option.value);
                    setSelected(null);
                  }}
                  className={cn(
                    'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                    active
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-muted/80',
                  )}
                >
                  {t(option.labelKey)}
                </button>
              );
            })}
          </div>

          {queue.isError ? (
            <ErrorPanel
              title={t('incidents.error.queue')}
              message={
                queue.error instanceof Error ? queue.error.message : undefined
              }
              onRetry={() => void queue.refetch()}
            />
          ) : queue.isPending ? (
            <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
          ) : incidents.length === 0 ? (
            <EmptyState title={t('incidents.empty.title')} />
          ) : (
            <ul className="flex flex-col gap-2">
              {incidents.map((incident) => (
                <li key={incident.id}>
                  <QueueRow
                    incident={incident}
                    selected={incident.id === selected?.id}
                    onSelect={() => setSelected(incident)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label={t('incidents.detailLabel')}>
          {selected ? (
            <IncidentReviewPanel
              // Remount per report so a half-written decision never carries over.
              key={selected.id}
              incident={detail.data ?? selected}
              detailPending={detail.isPending}
              onDecided={() => setSelected(null)}
            />
          ) : incidents.length > 0 ? (
            <EmptyState title={t('incidents.noSelection.title')} />
          ) : null}
        </section>
      </div>
    </CommandShell>
  );
}

function QueueRow({
  incident,
  selected,
  onSelect,
}: {
  incident: IncidentSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useT();
  const undecided =
    incident.status === 'submitted' || incident.status === 'under_review';

  // Evidence the dispatcher can actually open, as opposed to a draft row that
  // was never uploaded. Anything else is not proof.
  const verifiedCount = (incident.attachments ?? []).filter(
    (attachment) => attachment.upload_status === 'verified',
  ).length;

  return (
    <button
      type="button"
      onClick={onSelect}
      data-incident-id={incident.id}
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'w-full rounded-lg border p-3 text-start transition-colors',
        selected
          ? 'border-primary bg-accent'
          : 'border-border bg-card hover:bg-accent/50',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold">
          {t(`incidentType.${incident.type}`)}
        </span>
        {undecided ? (
          <CircleDashed
            className="size-4 shrink-0 text-amber-600"
            aria-label={t('incidents.badge.awaiting')}
          />
        ) : (
          <CheckCircle2
            className="size-4 shrink-0 text-emerald-600"
            aria-label={t('incidents.badge.decided')}
          />
        )}
      </div>

      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
        {incident.note ?? t('incidents.noNote')}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <FreshnessLabel asOf={incident.reported_at} />
        <span>
          {verifiedCount > 0
            ? t('incidents.evidenceCount', { count: String(verifiedCount) })
            : t('incidents.noEvidence')}
        </span>
        {/* A typed or pinned position has no GPS accuracy, and is still a
            position: only a report with none at all says so. */}
        {!incident.location ? (
          <span className="inline-flex items-center gap-1">
            <ShieldAlert className="size-3" aria-hidden />
            {t('incidents.noPosition')}
          </span>
        ) : incident.accuracy_m !== null &&
          incident.accuracy_m !== undefined ? (
          <span>±{Math.round(incident.accuracy_m)} m</span>
        ) : (
          <span>{t('incidents.pinnedPosition')}</span>
        )}
      </div>
    </button>
  );
}
