'use client';

import { MapPin, Radio } from 'lucide-react';

import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { useT } from '@/components/i18n/locale-provider';
import { useFleetLocations } from '@/lib/api/hooks';
import type { FleetTripLocation, ReportingState } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

/** Where the running fleet last reported. */
export function FleetPositions({ districtId }: { districtId?: string | null }) {
  const t = useT();
  const locations = useFleetLocations(districtId);
  const data = locations.data;
  const staleAfterMs = (data?.stale_after_seconds ?? 300) * 1000;

  return (
    <section className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('fleet.livePositionsHeading')}
        </h2>
        {data ? (
          <p className="text-xs text-muted-foreground" data-fleet-summary>
            {t('fleet.livePositionsSummary', {
              live: data.live,
              stale: data.stale,
              silent: data.never_reported,
            })}
          </p>
        ) : null}
      </div>

      {locations.isError && (
        <ErrorPanel
          title={t('fleet.error.locations')}
          message={
            locations.error instanceof Error
              ? locations.error.message
              : undefined
          }
          onRetry={() => void locations.refetch()}
        />
      )}

      {locations.isPending ? (
        <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
      ) : (data?.trips.length ?? 0) === 0 ? (
        <EmptyState title={t('fleet.livePositionsEmptyTitle')} />
      ) : (
        <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {(data?.trips ?? []).map((trip) => (
            <PositionCard
              key={trip.trip_id}
              trip={trip}
              staleAfterMs={staleAfterMs}
              staleAfterMinutes={Math.round(
                (data?.stale_after_seconds ?? 300) / 60,
              )}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

const STATE_STYLES: Record<ReportingState, string> = {
  live: 'border-[#047857] bg-[#ECFDF5] text-[#065F46]',
  stale: 'border-[#B45309] bg-[#FFFBEB] text-[#92400E]',
  never_reported: 'border-border bg-muted text-muted-foreground',
};

const STATE_LABELS: Record<ReportingState, string> = {
  live: 'fleet.reportingLive',
  stale: 'fleet.reportingStale',
  never_reported: 'fleet.reportingNever',
};

function PositionCard({
  trip,
  staleAfterMs,
  staleAfterMinutes,
}: {
  trip: FleetTripLocation;
  staleAfterMs: number;
  staleAfterMinutes: number;
}) {
  const t = useT();
  const state = trip.reporting_state;

  return (
    <li
      data-trip-reporting={state}
      data-trip-id={trip.trip_id}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">
            {trip.consignment_reference}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {trip.vehicle_registration}, {trip.driver_name ?? '-'}
          </p>
        </div>
        <span
          className={cn(
            'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
            STATE_STYLES[state],
          )}
        >
          <Radio className="size-3" aria-hidden />
          {t(STATE_LABELS[state])}
        </span>
      </div>

      {trip.location ? (
        <>
          <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
            <MapPin className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">
              {t('fleet.coordinates', {
                latitude: trip.location.latitude.toFixed(5),
                longitude: trip.location.longitude.toFixed(5),
              })}
            </span>
            <span className="shrink-0">
              {t('fleet.accuracy', {
                accuracy: Number(trip.location.accuracy_m).toFixed(0),
              })}
            </span>
          </p>
          <FreshnessLabel
            asOf={trip.location.as_of}
            staleAfterMs={staleAfterMs}
            showAbsolute
          />
          {/* A stale position is still shown, because where a vehicle was is
              useful. What it is not is where the vehicle is. */}
          {state === 'stale' && (
            <p className="text-xs font-medium text-[#92400E]">
              {t('fleet.staleExplained', { minutes: staleAfterMinutes })}
            </p>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {t('fleet.neverReported')}
        </p>
      )}
    </li>
  );
}
