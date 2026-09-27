'use client';

import { useState } from 'react';
import { CircleAlert, Route, Smartphone } from 'lucide-react';

import { useLocale } from '@/components/i18n/locale-provider';
import { RouteMap } from '@/components/planner/route-map';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import type { RoutePlan, Trip } from '@/lib/api/contracts';
import { useRoutePlan, useTrips } from '@/lib/api/hooks';
import {
  formatAbsoluteTime,
  formatDistance,
  formatDuration,
} from '@/lib/i18n/format';
import { cn } from '@/lib/utils';

/** Trips still in the driver's hands, before the finished ones. */
const LIVE = new Set<Trip['status']>([
  'awaiting_driver',
  'planned',
  'active',
  'paused',
]);

/**
 * The web version of this driver's trips and the route the control room
 * approved for each.
 */
export function DriverTripsScreen() {
  const { t } = useLocale();
  const trips = useTrips(null, 50);
  const rows = trips.data?.trips ?? [];
  const ordered = [
    ...rows.filter((row) => LIVE.has(row.status)),
    ...rows.filter((row) => !LIVE.has(row.status)),
  ];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected =
    ordered.find((row) => row.id === selectedId) ?? ordered[0] ?? null;

  return (
    <CommandShell
      ownHeading
      activeHref="/driver/trip"
      title={t('driverTrips.title')}
      headerExtra={
        trips.data ? <FreshnessLabel asOf={trips.data.as_of} /> : undefined
      }
    >
      <div className="flex flex-col gap-5">
        <div className="max-w-3xl">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('driverTrips.heading')}
          </h1>
          <p className="mt-2 flex items-start gap-2 rounded-lg border border-[#FDE68A] bg-[#FFFBEB] p-3 text-sm text-[#78350F]">
            <Smartphone className="mt-0.5 size-4 shrink-0" aria-hidden />
            {t('driverTrips.phoneOnly')}
          </p>
        </div>

        {trips.isError ? (
          <ErrorPanel
            title={t('driverTrips.error')}
            message={
              trips.error instanceof Error ? trips.error.message : undefined
            }
            onRetry={() => void trips.refetch()}
          />
        ) : null}

        {trips.isPending ? (
          <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
        ) : ordered.length === 0 ? (
          <EmptyState title={t('driverTrips.empty.title')} />
        ) : (
          <div className="grid min-w-0 gap-4 xl:grid-cols-[22rem_minmax(0,1fr)]">
            <ul
              className="flex min-w-0 flex-col gap-2"
              aria-label={t('driverTrips.listLabel')}
            >
              {ordered.map((trip) => (
                <li key={trip.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(trip.id)}
                    aria-pressed={selected?.id === trip.id}
                    className={cn(
                      'flex w-full flex-col gap-1 rounded-lg border bg-card p-3 text-start',
                      selected?.id === trip.id
                        ? 'border-primary ring-1 ring-primary'
                        : 'border-border',
                    )}
                    data-trip-id={trip.id}
                  >
                    <span className="flex items-start justify-between gap-2">
                      <span className="min-w-0 truncate text-sm font-semibold">
                        {trip.consignment_reference ??
                          t('driverTrips.noReference')}
                      </span>
                      <StatusBadge kind="trip" value={trip.status} />
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {trip.vehicle_registration ?? t('driverTrips.noVehicle')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {selected ? <TripRoute trip={selected} /> : null}
          </div>
        )}
      </div>
    </CommandShell>
  );
}

function TripRoute({ trip }: { trip: Trip }) {
  const { locale, t } = useLocale();
  const plan = useRoutePlan(trip.route_plan_id ?? null);

  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-4"
      aria-labelledby="trip-route-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2
          id="trip-route-heading"
          className="flex items-center gap-2 text-base font-semibold"
        >
          <Route className="size-4" aria-hidden />
          {t('driverTrips.routeHeading')}
        </h2>
        {trip.started_at ? (
          <span className="text-xs text-muted-foreground">
            {t('driverTrips.startedAt', {
              when: formatAbsoluteTime(locale, new Date(trip.started_at)),
            })}
          </span>
        ) : null}
      </div>

      {!trip.route_plan_id ? (
        <p className="text-sm text-muted-foreground" data-route-state="none">
          {t('driverTrips.noRoute')}
        </p>
      ) : plan.isPending ? (
        <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
      ) : plan.isError || !plan.data ? (
        <ErrorPanel
          title={t('driverTrips.routeError')}
          message={plan.error instanceof Error ? plan.error.message : undefined}
          onRetry={() => void plan.refetch()}
        />
      ) : (
        <PlanView plan={plan.data} />
      )}
    </section>
  );
}

function PlanView({ plan }: { plan: RoutePlan }) {
  const { locale, t } = useLocale();

  // A withdrawn or replaced route is not drawn: a line on a map reads as "go
  // this way", whatever the caption beside it says.
  if (plan.status !== 'approved') {
    return (
      <p
        className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm"
        role="alert"
        data-route-state={plan.status}
      >
        <CircleAlert
          className="mt-0.5 size-4 shrink-0 text-destructive"
          aria-hidden
        />
        {t(`driverTrips.planState.${plan.status}`)}
      </p>
    );
  }

  const chosen =
    plan.alternatives.find(
      (alternative) => alternative.id === plan.chosen_alternative_id,
    ) ?? null;
  if (!chosen || !chosen.geometry) {
    return (
      <p
        className="text-sm text-muted-foreground"
        data-route-state="no_geometry"
      >
        {t('driverTrips.noGeometry')}
      </p>
    );
  }

  const [low, high] = chosen.eta_range_seconds;
  return (
    <div className="flex flex-col gap-3" data-route-state="approved">
      <p className="text-sm">
        {t('driverTrips.approvedSummary', {
          distance: formatDistance(locale, chosen.distance_m),
          low: formatDuration(locale, low * 1000),
          high: formatDuration(locale, high * 1000),
        })}
      </p>
      <div className="h-[22rem] min-w-0 overflow-hidden rounded-md border">
        <RouteMap
          alternatives={[chosen]}
          selectedId={chosen.id}
          className="h-full"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {t('driverTrips.approvedAgainst', {
          version: plan.network_version,
          when: plan.approved_at
            ? formatAbsoluteTime(locale, new Date(plan.approved_at))
            : '-',
        })}
      </p>
    </div>
  );
}
