'use client';

import { useState } from 'react';
import { Plus } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { useConsignments, useSupplyRequests } from '@/lib/api/hooks';
import type { Consignment, SupplyRequest } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import { ConsignmentDetailPanel } from './consignment-detail-panel';
import { NewConsignmentDialog } from './new-consignment-dialog';
import { formatDecimal, phaseOf } from './format';

const PHASES = ['all', 'draft', 'planned', 'moving', 'arrived'] as const;
type Filter = (typeof PHASES)[number];

/** Deliveries: what each facility asked for, and what actually reached it. */
export function DeliveryQueueScreen() {
  const t = useT();
  const [filter, setFilter] = useState<Filter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { workspace } = useAuth();
  const canCreate =
    (workspace?.capabilities.includes('consignment:manage') ?? false) &&
    (workspace?.districts.length ?? 0) > 0;

  const requests = useSupplyRequests();
  const consignments = useConsignments();

  const rows = consignments.data?.consignments ?? [];
  const visible =
    filter === 'all'
      ? rows
      : rows.filter((row) => phaseOf(row.status) === filter);

  // Held by id and resolved against the current list, so the panel always
  // reflects the latest poll rather than a snapshot taken when it was opened.
  const selected = rows.find((row) => row.id === selectedId) ?? null;

  return (
    <CommandShell
      activeHref="/deliveries"
      title={t('deliveries.title')}
      headerExtra={
        consignments.data ? (
          <FreshnessLabel asOf={consignments.data.as_of} />
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        {requests.isError && (
          <ErrorPanel
            title={t('deliveries.error.requests')}
            message={
              requests.error instanceof Error
                ? requests.error.message
                : undefined
            }
            onRetry={() => void requests.refetch()}
          />
        )}
        {consignments.isError && (
          <ErrorPanel
            title={t('deliveries.error.list')}
            message={
              consignments.error instanceof Error
                ? consignments.error.message
                : undefined
            }
            onRetry={() => void consignments.refetch()}
          />
        )}

        <RequestStrip
          requests={requests.data?.requests ?? []}
          loading={requests.isPending}
        />

        <div className="grid gap-4 xl:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
          <section className="flex min-w-0 flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t('deliveries.consignmentsHeading')}
                </h2>
                {canCreate && (
                  <button
                    type="button"
                    onClick={() => setCreating(true)}
                    data-new-consignment-open
                    className="flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-xs font-semibold text-primary-foreground"
                  >
                    <Plus className="size-3.5" aria-hidden />
                    {t('newConsignment.open')}
                  </button>
                )}
              </div>
              <fieldset className="flex flex-wrap gap-1 border-0 p-0">
                <legend className="sr-only">
                  {t('deliveries.consignmentsHeading')}
                </legend>
                {PHASES.map((phase) => (
                  <button
                    key={phase}
                    type="button"
                    onClick={() => setFilter(phase)}
                    aria-pressed={filter === phase}
                    className={cn(
                      'rounded-md border px-2 py-1 text-xs font-medium',
                      filter === phase
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-card text-muted-foreground',
                    )}
                  >
                    {t(`deliveries.filter.${phase}`)}
                  </button>
                ))}
              </fieldset>
            </div>

            {consignments.isPending ? (
              <p className="text-sm text-muted-foreground">
                {t('list.loading')}
              </p>
            ) : rows.length === 0 ? (
              <EmptyState title={t('deliveries.empty.title')} />
            ) : visible.length === 0 ? (
              <EmptyState title={t('deliveries.filterEmpty')} />
            ) : (
              <ul className="flex flex-col gap-2">
                {visible.map((row) => (
                  <ConsignmentRow
                    key={row.id}
                    consignment={row}
                    selected={row.id === selectedId}
                    onSelect={() => setSelectedId(row.id)}
                  />
                ))}
              </ul>
            )}
          </section>

          <section className="min-w-0">
            {selected ? (
              <ConsignmentDetailPanel consignment={selected} />
            ) : visible.length > 0 ? (
              <EmptyState title={t('deliveries.selectTitle')} />
            ) : null}
          </section>
        </div>
      </div>
      {creating && workspace && (
        <NewConsignmentDialog
          districts={workspace.districts}
          onClose={() => setCreating(false)}
          onCreated={(consignmentId) => {
            setFilter('all');
            setSelectedId(consignmentId);
          }}
        />
      )}
    </CommandShell>
  );
}

function ConsignmentRow({
  consignment,
  selected,
  onSelect,
}: {
  consignment: Consignment;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useT();
  const weight = formatDecimal(consignment.total_weight_kg);

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        data-consignment-status={consignment.status}
        className={cn(
          'w-full rounded-lg border p-3 text-start',
          selected ? 'border-primary bg-primary/5' : 'border-border bg-card',
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 truncate text-sm font-semibold">
            {consignment.reference}
          </p>
          <StatusBadge kind="consignment" value={consignment.status} />
        </div>
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {consignment.destination_facility_name ??
            t('deliveries.unnamedFacility')}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {t('list.itemCount', { count: consignment.items.length })}
          {weight ? `, ${t('units.kg', { value: weight })}` : ''}
        </p>
      </button>
    </li>
  );
}

/** Requests sit above the queue: the need, before the attempts to meet it. */
function RequestStrip({
  requests,
  loading,
}: {
  requests: SupplyRequest[];
  loading: boolean;
}) {
  const t = useT();
  const formatTime = useFormatTime();

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('deliveries.requestsHeading')}
      </h2>
      {loading ? (
        <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
      ) : requests.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground">
          {t('deliveries.requestsEmpty')}
        </p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {requests.map((request) => (
            <li
              key={request.id}
              data-request-status={request.status}
              className="min-w-0 rounded-lg border border-border bg-card p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 truncate text-sm font-semibold">
                  {request.facility_name ?? t('deliveries.unnamedFacility')}
                </p>
                <StatusBadge kind="request" value={request.status} />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {request.consignment_count === 0
                  ? t('deliveries.noConsignments')
                  : t('deliveries.fulfilment', {
                      delivered: request.delivered_consignment_count,
                      total: request.consignment_count,
                    })}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t(`priority.${request.priority}`)},{' '}
                {request.needed_by
                  ? t('deliveries.neededBy', {
                      when: formatTime.date(request.needed_by),
                    })
                  : t('deliveries.noDeadline')}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
