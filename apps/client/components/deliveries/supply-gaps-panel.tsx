'use client';

import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { ErrorPanel } from '@/components/common/error-panel';
import type { GapRequest, SupplyGapsResponse } from '@/lib/api/contracts';
import { useSupplyGaps } from '@/lib/api/hooks';
import { cn } from '@/lib/utils';

const STATE_STYLES: Record<GapRequest['state'], string> = {
  overdue: 'border-[#FECACA] bg-[#FEF2F2] text-[#991B1B]',
  at_risk: 'border-[#FDE68A] bg-[#FFFBEB] text-[#92400E]',
  unknown: 'border-border bg-muted text-muted-foreground',
  waiting: 'border-border bg-card text-muted-foreground',
  on_track: 'border-[#A7F3D0] bg-[#ECFDF5] text-[#065F46]',
  no_deadline: 'border-border bg-card text-muted-foreground',
};

const STATES: GapRequest['state'][] = [
  'overdue',
  'at_risk',
  'unknown',
  'waiting',
  'on_track',
  'no_deadline',
];

function quantity(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** Unmet needs, most urgent first: the gap between what was asked and what came. */
export function SupplyGapsPanel() {
  const t = useT();
  const gaps = useSupplyGaps();
  if (gaps.isPending) return null;
  if (gaps.isError) {
    return (
      <ErrorPanel
        title={t('supplyGaps.error')}
        message={gaps.error instanceof Error ? gaps.error.message : undefined}
        onRetry={() => void gaps.refetch()}
      />
    );
  }
  return <SupplyGaps report={gaps.data} />;
}

export function SupplyGaps({ report }: { report: SupplyGapsResponse }) {
  const t = useT();
  const counts = report.counts as Record<string, number>;

  return (
    <section className="flex flex-col gap-2" data-supply-gaps>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('supplyGaps.heading')}
        </h2>
        <ul
          className="flex flex-wrap gap-1"
          aria-label={t('supplyGaps.heading')}
        >
          {STATES.filter((state) => (counts[state] ?? 0) > 0).map((state) => (
            <li
              key={state}
              className={cn(
                'rounded-md border px-2 py-0.5 text-xs font-medium',
                STATE_STYLES[state],
              )}
            >
              {t(`supplyGaps.state.${state}`)}: {counts[state]}
            </li>
          ))}
        </ul>
      </div>
      {report.requests.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground">
          {t('supplyGaps.empty')}
        </p>
      ) : (
        <ul className="grid gap-2 lg:grid-cols-2">
          {report.requests.map((gap) => (
            <GapRow key={gap.request_id} gap={gap} />
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{t('supplyGaps.note')}</p>
    </section>
  );
}

function GapRow({ gap }: { gap: GapRequest }) {
  const t = useT();
  const formatTime = useFormatTime();
  const values = {
    deadline: gap.needed_by ? formatTime.time(gap.needed_by) : '',
    arrival: gap.expected_arrival ? formatTime.time(gap.expected_arrival) : '',
  };

  return (
    <li
      data-gap-state={gap.state}
      className={cn('min-w-0 rounded-lg border p-3', STATE_STYLES[gap.state])}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold text-foreground">
          {gap.facility_name ?? t('deliveries.unnamedFacility')}
        </p>
        <span className="shrink-0 text-xs font-semibold">
          {t(`supplyGaps.state.${gap.state}`)}
        </span>
      </div>
      <p className="mt-1 text-xs">
        {t(`priority.${gap.priority}`)}.{' '}
        {t(`supplyGaps.reason.${gap.reason}`, values)}
      </p>
      {gap.on_the_way.length > 0 || gap.shortfalls.length > 0 ? (
        <ul className="mt-1.5 flex flex-col gap-0.5 text-xs text-foreground">
          {gap.on_the_way.map((line) => (
            <li key={`way:${line.commodity}:${line.unit}`}>
              {t('supplyGaps.onTheWay', {
                quantity: quantity(line.quantity),
                unit: line.unit,
                commodity: line.commodity,
              })}
            </li>
          ))}
          {gap.shortfalls.map((line) => (
            <li
              key={`short:${line.commodity}:${line.unit}`}
              className="font-medium"
            >
              {t('supplyGaps.short', {
                received: quantity(line.received),
                dispatched: quantity(line.dispatched),
                unit: line.unit,
                commodity: line.commodity,
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
