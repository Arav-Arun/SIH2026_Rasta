'use client';
import { FleetPositions } from '@/components/fleet/fleet-positions';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { ModeBadge } from '@/components/common/mode-badge';
import { StatusBadge } from '@/components/common/status-badge';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { useTransitionTrip, useTrips, useVehicles } from '@/lib/api/hooks';
import type { Trip, TripTransition, Vehicle } from '@/lib/api/contracts';
import { formatDecimal } from '@/components/deliveries/format';
import { cn } from '@/lib/utils';

/** Fleet: the roster and what is running on it. */
export function FleetScreen() {
  const t = useT();
  const vehicles = useVehicles();
  const trips = useTrips();
  const transition = useTransitionTrip();

  const scope = trips.data?.scope;

  return (
    <CommandShell
      activeHref="/fleet"
      title={t('fleet.title')}
      headerExtra={
        trips.data ? <FreshnessLabel asOf={trips.data.as_of} /> : undefined
      }
    >
      <div className="flex flex-col gap-6">
        <FleetPositions />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
          <section className="flex min-w-0 flex-col gap-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('fleet.vehiclesHeading')}
            </h2>

            {vehicles.isError && (
              <ErrorPanel
                title={t('fleet.error.vehicles')}
                message={
                  vehicles.error instanceof Error
                    ? vehicles.error.message
                    : undefined
                }
                onRetry={() => void vehicles.refetch()}
              />
            )}

            {vehicles.isPending ? (
              <p className="text-sm text-muted-foreground">
                {t('list.loading')}
              </p>
            ) : (vehicles.data?.vehicles.length ?? 0) === 0 ? (
              <EmptyState title={t('fleet.empty.vehiclesTitle')} />
            ) : (
              <ul className="flex flex-col gap-2">
                {(vehicles.data?.vehicles ?? []).map((vehicle) => (
                  <VehicleRow key={vehicle.id} vehicle={vehicle} />
                ))}
              </ul>
            )}
          </section>

          <section className="flex min-w-0 flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t('fleet.tripsHeading')}
              </h2>
              {scope ? (
                <p className="text-xs text-muted-foreground">
                  {t(`fleet.scope.${scope}`)}
                </p>
              ) : null}
            </div>

            {trips.isError && (
              <ErrorPanel
                title={t('fleet.error.trips')}
                message={
                  trips.error instanceof Error ? trips.error.message : undefined
                }
                onRetry={() => void trips.refetch()}
              />
            )}
            {transition.isError && (
              <ErrorPanel
                title={t('fleet.error.transition')}
                message={
                  transition.error instanceof Error
                    ? transition.error.message
                    : undefined
                }
              />
            )}

            {trips.isPending ? (
              <p className="text-sm text-muted-foreground">
                {t('list.loading')}
              </p>
            ) : (trips.data?.trips.length ?? 0) === 0 ? (
              <EmptyState title={t('fleet.empty.tripsTitle')} />
            ) : (
              <ul className="flex flex-col gap-2">
                {(trips.data?.trips ?? []).map((trip) => (
                  <TripRow
                    key={trip.id}
                    trip={trip}
                    busy={transition.isPending}
                    onAction={(action) =>
                      transition.mutate({
                        tripId: trip.id,
                        action,
                        idempotencyKey: crypto.randomUUID(),
                      })
                    }
                  />
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </CommandShell>
  );
}

/**
 * A vehicle class comes from the register, not from a fixed list this app
 * owns, so it is tidied for display rather than translated: inventing a
 * Hindi word for a category the register spells `light_truck` would be
 * guessing at what the operator meant.
 */
function humaniseClass(value: string): string {
  const spaced = value.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function VehicleRow({ vehicle }: { vehicle: Vehicle }) {
  const t = useT();
  const capacity = formatDecimal(vehicle.capacity_kg);
  const height = formatDecimal(vehicle.max_height_m);
  const mode =
    vehicle.source_mode === 'synthetic' ||
    vehicle.source_mode === 'recorded' ||
    vehicle.source_mode === 'live'
      ? vehicle.source_mode
      : null;

  return (
    <li
      data-vehicle-active={vehicle.active}
      data-vehicle-source={vehicle.source_mode}
      className={cn(
        'rounded-lg border border-border bg-card p-3',
        // De-emphasised with a muted surface, not with opacity.
        !vehicle.active && 'border-dashed bg-muted/40',
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold">
          {vehicle.registration_ref}
        </p>
        <span className="text-xs text-muted-foreground">
          {humaniseClass(vehicle.vehicle_class)}
        </span>
      </div>

      {/* A synthetic vehicle is marked on its own row. The header badge says
          what the screen is; this says which rows on it are not real. */}
      {mode === 'synthetic' ? (
        <div className="mt-1.5">
          <ModeBadge dataMode={mode} />
        </div>
      ) : null}
      <p className="mt-1 text-xs text-muted-foreground">
        {capacity
          ? t('fleet.capacity', { capacity })
          : t('fleet.capacityUnknown')}
        {height ? `, ${t('fleet.height', { height })}` : ''}
      </p>
      {!vehicle.active && (
        <p className="mt-1 text-xs font-medium text-[#92400E]">
          {t('fleet.outOfService')}
        </p>
      )}
    </li>
  );
}

/** Only the moves the server would accept are offered. */
function TripRow({
  trip,
  busy,
  onAction,
}: {
  trip: Trip;
  busy: boolean;
  onAction: (action: TripTransition) => void;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  const canOffer = trip.status === 'planned';
  const canCancel =
    trip.status === 'planned' || trip.status === 'awaiting_driver';

  return (
    <li
      data-trip-status={trip.status}
      className="rounded-lg border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">
            {trip.consignment_reference ?? trip.consignment_id}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {trip.vehicle_registration ?? '-'},{' '}
            {trip.driver_display_name ?? '-'}
          </p>
        </div>
        <StatusBadge kind="trip" value={trip.status} />
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {trip.started_at
            ? t('deliveries.startedAt', {
                when: formatTime.time(trip.started_at),
              })
            : t('deliveries.notStarted')}
        </span>
        {(canOffer || canCancel) && (
          <span className="flex flex-wrap gap-2">
            {canOffer && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onAction('awaiting_driver')}
                className="rounded-md border border-border px-2 py-1 font-medium disabled:opacity-50"
              >
                {t('fleet.action.offer')}
              </button>
            )}
            {canCancel && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onAction('cancelled')}
                className="rounded-md border border-border px-2 py-1 font-medium disabled:opacity-50"
              >
                {t('fleet.action.cancel')}
              </button>
            )}
          </span>
        )}
      </div>
    </li>
  );
}
