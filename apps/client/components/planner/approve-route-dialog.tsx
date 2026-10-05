'use client';

import { useEffect, useRef } from 'react';
import { ShieldAlert, X } from 'lucide-react';

import { ErrorPanel } from '@/components/common/error-panel';
import { useT } from '@/components/i18n/locale-provider';
import type { RouteAlternative, RoutePlan } from '@/lib/api/contracts';

import { reviewWarnings } from './format';

/**
 * The approval summary the spec calls for: vehicle, route, the snapshot it was
 * costed against, and anything still unresolved.
 */
export function ApproveRouteDialog({
  plan,
  alternative,
  vehicleLabel,
  pending,
  error,
  onConfirm,
  onClose,
}: {
  plan: RoutePlan;
  alternative: RouteAlternative;
  vehicleLabel: string;
  pending: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const unresolved = reviewWarnings(alternative);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={t('planner.approveHeading')}
      className="m-auto w-[min(34rem,92vw)] rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-black/50"
    >
      <div className="flex flex-col gap-4 p-4">
        <header className="flex items-start justify-between gap-2">
          <h2 className="text-base font-semibold">
            {t('planner.approveHeading')}
          </h2>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            aria-label={t('planner.approveCancel')}
            className="rounded-md p-1"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>

        <dl className="flex flex-col gap-2 text-sm">
          <div className="flex flex-wrap justify-between gap-2">
            <dt className="text-muted-foreground">
              {t('planner.approveVehicle')}
            </dt>
            <dd className="font-medium">{vehicleLabel}</dd>
          </div>
          <div className="flex flex-wrap justify-between gap-2">
            <dt className="text-muted-foreground">
              {t('planner.approveRoute')}
            </dt>
            <dd className="font-medium">
              {t(`planner.category.${alternative.category}`)}
            </dd>
          </div>
          <div className="flex flex-wrap justify-between gap-2">
            <dt className="text-muted-foreground">{t('planner.distance')}</dt>
            <dd className="font-medium tabular-nums">
              {t('units.km', {
                value: (alternative.distance_m / 1000).toFixed(1),
              })}
            </dd>
          </div>
        </dl>

        {/* The snapshot is shown because it is what the approval is against.
            If it has moved, the server refuses and says so. */}
        <p className="rounded-md bg-muted px-3 py-2 font-mono text-[11px] text-muted-foreground">
          {t('planner.approveSnapshot', { version: plan.network_version })}
          <br />
          {t('planner.policyVersion', { version: plan.cost_policy_version })}
        </p>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('planner.approveUnresolved')}
          </h3>
          {unresolved.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('planner.approveNoUnresolved')}
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {unresolved.map((warning) => {
                const code = (warning as { code: string }).code;
                const segments =
                  (warning as { segment_ids?: string[] }).segment_ids ?? [];
                return (
                  <li
                    key={code}
                    data-unresolved={code}
                    className="flex items-start gap-2 rounded-md bg-[#FEF3C7] px-3 py-2 text-sm text-[#92400E]"
                  >
                    <ShieldAlert
                      className="mt-0.5 size-4 shrink-0"
                      aria-hidden
                    />
                    <span>
                      {t(`planner.warning.${code}`)}
                      {segments.length > 0 ? ` (${segments.length})` : ''}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <p className="text-xs text-muted-foreground">
          {t('planner.notSafeClaim')}
        </p>

        {error && (
          <ErrorPanel title={t('planner.error.approve')} message={error} />
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => ref.current?.close()}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium"
          >
            {t('planner.approveCancel')}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {pending ? t('planner.approving') : t('planner.approveConfirm')}
          </button>
        </div>
      </div>
    </dialog>
  );
}
