'use client';

import { useEffect, useRef, useState } from 'react';
import { TriangleAlert, X } from 'lucide-react';

import { ErrorPanel } from '@/components/common/error-panel';
import { useT } from '@/components/i18n/locale-provider';
import { useCreateTrip, useDrivers, useVehicles } from '@/lib/api/hooks';
import { newUuid } from '@/lib/ids';
import type { Consignment, Vehicle } from '@/lib/api/contracts';

import { compareDecimal, formatDecimal } from './format';

/** Assigns a vehicle and driver to a released consignment. */
export function AssignTripDialog({
  consignment,
  onClose,
}: {
  consignment: Consignment;
  onClose: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const [vehicleId, setVehicleId] = useState('');
  const [driverId, setDriverId] = useState('');

  const vehicles = useVehicles();
  const drivers = useDrivers(consignment.district_id);
  const assign = useCreateTrip();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const inService = (vehicles.data?.vehicles ?? []).filter((row) => row.active);
  const vehicle = inService.find((row) => row.id === vehicleId) ?? null;
  const verdict = judgeCapacity(consignment.total_weight_kg, vehicle);

  const canSubmit =
    vehicleId !== '' &&
    driverId !== '' &&
    verdict !== 'over' &&
    !assign.isPending;

  function submit() {
    if (!canSubmit) return;
    assign.mutate(
      {
        body: {
          consignment_id: consignment.id,
          vehicle_id: vehicleId,
          driver_profile_id: driverId,
        },
        idempotencyKey: newUuid(),
      },
      { onSuccess: () => ref.current?.close() },
    );
  }

  const load = formatDecimal(consignment.total_weight_kg);
  const capacity = formatDecimal(vehicle?.capacity_kg);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={t('assignTrip.title')}
      className="m-auto w-[min(32rem,92vw)] rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-black/50"
    >
      <div className="flex flex-col gap-4 p-4">
        <header className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">{t('assignTrip.title')}</h2>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {consignment.reference},{' '}
              {load
                ? t('assignTrip.loadWeight', { weight: load })
                : t('assignTrip.loadUnknown')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            aria-label={t('confirm.cancel')}
            className="rounded-md p-1"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('assignTrip.vehicle')}
          </span>
          <select
            value={vehicleId}
            onChange={(event) => setVehicleId(event.target.value)}
            disabled={vehicles.isPending}
            className="rounded-md border border-border bg-background p-2 text-sm"
          >
            <option value="">{t('assignTrip.choose')}</option>
            {inService.map((row) => {
              const cap = formatDecimal(row.capacity_kg);
              return (
                <option key={row.id} value={row.id}>
                  {row.registration_ref},{' '}
                  {cap
                    ? t('assignTrip.capacityOf', { capacity: cap })
                    : t('assignTrip.capacityUnknown')}
                </option>
              );
            })}
          </select>
          {!vehicles.isPending && inService.length === 0 && (
            <span className="text-xs text-[#92400E]">
              {t('assignTrip.noVehicles')}
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('assignTrip.driver')}
          </span>
          <select
            value={driverId}
            onChange={(event) => setDriverId(event.target.value)}
            disabled={drivers.isPending}
            className="rounded-md border border-border bg-background p-2 text-sm"
          >
            <option value="">{t('assignTrip.choose')}</option>
            {(drivers.data?.drivers ?? []).map((driver) => (
              <option key={driver.profile_id} value={driver.profile_id}>
                {driver.display_name}
              </option>
            ))}
          </select>
          {!drivers.isPending && drivers.data?.drivers.length === 0 && (
            <span className="text-xs text-[#92400E]">
              {t('assignTrip.noDrivers')}
            </span>
          )}
        </label>

        {vehicle && verdict === 'over' && (
          <p
            role="alert"
            data-testid="capacity-over"
            className="flex items-start gap-2 rounded-md border border-[#FECACA] bg-[#FEF2F2] p-3 text-sm text-[#991B1B]"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            {t('assignTrip.overCapacity')}
          </p>
        )}

        {vehicle && verdict === 'unknown' && (
          <p
            data-testid="capacity-unknown"
            className="flex items-start gap-2 rounded-md border border-[#FDE68A] bg-[#FEF3C7] p-3 text-sm text-[#92400E]"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            {t('assignTrip.uncheckable', {
              reason: load
                ? t('assignTrip.capacityUnknown')
                : t('assignTrip.loadUnknown'),
            })}
          </p>
        )}

        {vehicle && verdict === 'within' && load && capacity && (
          <p
            className="text-sm text-muted-foreground"
            data-testid="capacity-within"
          >
            {t('deliveries.capacity.within', { load, capacity })}
          </p>
        )}

        {assign.isError && (
          <ErrorPanel
            title={t('deliveries.error.assign')}
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
            {t('assignTrip.cancel')}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {assign.isPending ? t('confirm.working') : t('assignTrip.submit')}
          </button>
        </div>
      </div>
    </dialog>
  );
}

type CapacityVerdict = 'within' | 'over' | 'unknown';

/**
 * Mirrors the server's rule so the dispatcher sees the refusal before the
 * request is sent.
 */
function judgeCapacity(
  loadKg: string | null | undefined,
  vehicle: Pick<Vehicle, 'capacity_kg'> | null,
): CapacityVerdict {
  const load = formatDecimal(loadKg);
  const capacity = formatDecimal(vehicle?.capacity_kg);
  if (!vehicle || load === null || capacity === null) return 'unknown';
  return compareDecimal(load, capacity) > 0 ? 'over' : 'within';
}
