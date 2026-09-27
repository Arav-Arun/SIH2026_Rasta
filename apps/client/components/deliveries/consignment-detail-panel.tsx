'use client';

import { useState } from 'react';
import { ArrowRight, TriangleAlert } from 'lucide-react';

import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { usePlanConsignment, useTripReceipt, useTrips } from '@/lib/api/hooks';
import type { Consignment, DeliveryReceipt, Trip } from '@/lib/api/contracts';
import { newUuid } from '@/lib/ids';

import { AssignTripDialog } from './assign-trip-dialog';
import { formatDecimal, receiptByItem, shortfallOf } from './format';

/**
 * One consignment in full: what was loaded, who is carrying it, and what the
 * receiving end says arrived.
 */
export function ConsignmentDetailPanel({
  consignment,
}: {
  consignment: Consignment;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  const [assigning, setAssigning] = useState(false);

  const plan = usePlanConsignment();
  const trips = useTrips();
  const trip =
    trips.data?.trips.find((row) => row.consignment_id === consignment.id) ??
    null;

  const receipt = useTripReceipt(trip?.id ?? null);
  const weight = formatDecimal(consignment.total_weight_kg);

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">
            {consignment.reference}
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
            <span className="truncate">
              {consignment.origin_facility_name ??
                t('deliveries.unnamedFacility')}
            </span>
            <ArrowRight className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">
              {consignment.destination_facility_name ??
                t('deliveries.unnamedFacility')}
            </span>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t(`priority.${consignment.priority}`)},{' '}
            {consignment.deadline_at
              ? t('deliveries.neededBy', {
                  when: formatTime.time(consignment.deadline_at),
                })
              : t('deliveries.noDeadline')}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <StatusBadge kind="consignment" value={consignment.status} />
          <FreshnessLabel asOf={consignment.updated_at} showAbsolute={false} />
        </div>
      </header>

      {plan.isError && (
        <ErrorPanel
          title={t('deliveries.error.plan')}
          message={plan.error instanceof Error ? plan.error.message : undefined}
        />
      )}

      <Manifest
        consignment={consignment}
        receipt={receipt.data?.receipt ?? null}
      />

      <p className="text-xs text-muted-foreground">
        {weight
          ? t('deliveries.totalWeight', { weight })
          : t('deliveries.weightUnknown')}
      </p>

      <TripSection trip={trip} loading={trips.isPending} />

      <ReceiptSection
        receipt={receipt.data?.receipt ?? null}
        hasTrip={Boolean(trip)}
      />

      {/* Only the moves the server would accept are offered. A draft is
          released for planning; a released consignment gets a vehicle. */}
      {consignment.status === 'draft' && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-3">
          <button
            type="button"
            disabled={plan.isPending || consignment.items.length === 0}
            onClick={() =>
              plan.mutate({
                consignmentId: consignment.id,
                idempotencyKey: newUuid(),
              })
            }
            className="w-fit rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {t('deliveries.action.plan')}
          </button>
          <p className="text-xs text-muted-foreground">
            {t('deliveries.action.planHint')}
          </p>
        </div>
      )}

      {consignment.status === 'planned' && (
        <div className="border-t border-border pt-3">
          <button
            type="button"
            onClick={() => setAssigning(true)}
            className="w-fit rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground"
          >
            {t('deliveries.action.assign')}
          </button>
        </div>
      )}

      {assigning && (
        <AssignTripDialog
          consignment={consignment}
          onClose={() => setAssigning(false)}
        />
      )}
    </div>
  );
}

/** Ordered against delivered, line by line. */
function Manifest({
  consignment,
  receipt,
}: {
  consignment: Consignment;
  receipt: DeliveryReceipt | null;
}) {
  const t = useT();
  const delivered = receiptByItem(receipt?.items);

  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('deliveries.itemsHeading')}
      </h3>
      <div className="overflow-x-auto">
        {/* Per-line weight is the one column that can be dropped on a phone
            without losing the answer: the total is stated below the table, and
            a horizontally scrolling manifest is worse than three columns. */}
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-start text-xs text-muted-foreground">
              <th scope="col" className="py-1.5 pe-3 font-medium">
                {t('deliveries.commodity')}
              </th>
              <th scope="col" className="py-1.5 pe-3 text-end font-medium">
                {t('deliveries.ordered')}
              </th>
              <th scope="col" className="py-1.5 pe-3 text-end font-medium">
                {t('deliveries.delivered')}
              </th>
              <th
                scope="col"
                className="hidden py-1.5 text-end font-medium sm:table-cell"
              >
                {t('deliveries.weight')}
              </th>
            </tr>
          </thead>
          <tbody>
            {consignment.items.map((item) => {
              const line = delivered.get(item.id);
              const short = receipt ? shortfallOf(item, line) : null;
              const itemWeight = formatDecimal(item.weight_kg);
              return (
                <tr
                  key={item.id}
                  className="border-b border-border/60 last:border-0"
                >
                  <td className="py-1.5 pe-3">{item.commodity}</td>
                  <td className="py-1.5 pe-3 text-end tabular-nums">
                    {formatDecimal(item.quantity)} {item.unit}
                  </td>
                  <td
                    className="py-1.5 pe-3 text-end tabular-nums"
                    data-shortfall={short ?? undefined}
                  >
                    {receipt ? (
                      <>
                        <span
                          className={
                            short ? 'font-semibold text-[#92400E]' : undefined
                          }
                        >
                          {formatDecimal(line?.delivered_quantity ?? '0')}{' '}
                          {item.unit}
                        </span>
                        {short ? (
                          <span className="block text-xs font-normal text-[#92400E]">
                            {t('deliveries.shortBy', {
                              amount: short,
                              unit: item.unit,
                            })}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </td>
                  <td className="hidden py-1.5 text-end tabular-nums text-muted-foreground sm:table-cell">
                    {itemWeight ? t('units.kg', { value: itemWeight }) : '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * A receipt is a statement by a person, never a conclusion drawn from a trip's
 * position.
 */
function ReceiptSection({
  receipt,
  hasTrip,
}: {
  receipt: DeliveryReceipt | null;
  hasTrip: boolean;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  if (!hasTrip) return null;

  return (
    <section className="flex flex-col gap-2 border-t border-border pt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('deliveries.receiptHeading')}
      </h3>
      {!receipt ? (
        <p
          data-testid="no-receipt"
          className="flex items-start gap-1.5 text-sm text-muted-foreground"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          {t('deliveries.noReceipt')}
        </p>
      ) : (
        <div
          className="flex flex-col gap-1.5"
          data-receipt-status={receipt.status}
        >
          <StatusBadge kind="consignment" value={receipt.status} />
          <p className="text-xs text-muted-foreground">
            {t('deliveries.receivedAt', {
              when: formatTime.time(receipt.received_at),
            })}
            {receipt.received_by_ref
              ? `, ${t('deliveries.receivedBy', { who: receipt.received_by_ref })}`
              : ''}
          </p>
          {receipt.notes ? <p className="text-sm">{receipt.notes}</p> : null}
        </div>
      )}
    </section>
  );
}

function TripSection({
  trip,
  loading,
}: {
  trip: Trip | null;
  loading: boolean;
}) {
  const t = useT();
  const formatTime = useFormatTime();

  return (
    <section className="flex flex-col gap-2 border-t border-border pt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('deliveries.tripHeading')}
      </h3>
      {loading ? (
        <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
      ) : !trip ? (
        <p className="text-sm text-muted-foreground">
          {t('deliveries.noTrip')}
        </p>
      ) : (
        <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
          <div className="flex min-w-0 justify-between gap-2 sm:block">
            <dt className="text-xs text-muted-foreground">
              {t('deliveries.vehicle')}
            </dt>
            <dd className="truncate font-medium">
              {trip.vehicle_registration ?? '-'}
            </dd>
          </div>
          <div className="flex min-w-0 justify-between gap-2 sm:block">
            <dt className="text-xs text-muted-foreground">
              {t('deliveries.driver')}
            </dt>
            <dd className="truncate font-medium">
              {trip.driver_display_name ?? '-'}
            </dd>
          </div>
          {/* The badge carries its own accessible label, so no `dt` here: a
              second one would read the category twice to a screen reader. */}
          <div className="flex min-w-0 items-center justify-between gap-2 sm:block">
            <dd className="sm:mt-4">
              <StatusBadge kind="trip" value={trip.status} />
            </dd>
          </div>
          <div className="flex min-w-0 justify-between gap-2 sm:block">
            <dt className="text-xs text-muted-foreground">
              {t('deliveries.startedLabel')}
            </dt>
            <dd className="truncate font-medium">
              {trip.started_at
                ? formatTime.time(trip.started_at)
                : t('deliveries.notStarted')}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}
