'use client';

import { useMemo, useState, useSyncExternalStore } from 'react';
import { TriangleAlert } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { useT } from '@/components/i18n/locale-provider';
import {
  useApproveRoutePlan,
  useBindTripRoutePlan,
  useConnectivitySummary,
  useConsignments,
  useCreateRoutePlan,
  useTrips,
  useVehicles,
} from '@/lib/api/hooks';
import type {
  ConnectivityFacility,
  RouteAlternative,
  RoutePlan,
} from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import { AlternativeCard } from './alternative-card';
import { newUuid } from '@/lib/ids';

import { ApproveRouteDialog } from './approve-route-dialog';
import { RouteMap } from './route-map';
import { avoidedClosureCount } from './format';

const PRIORITIES = ['low', 'normal', 'high', 'critical'] as const;

/** Trips that can still be given a route. */
const OPEN_TRIP_STATUSES = new Set([
  'assigned',
  'awaiting_driver',
  'active',
  'paused',
]);

/** `?trip=<id>`: where an alert about a withdrawn route sends the dispatcher. */
function tripFromUrl(): string {
  return new URLSearchParams(window.location.search).get('trip') ?? '';
}

function errorMessage(error: unknown): string | null {
  return error instanceof Error ? error.message : null;
}

/** The server's `route_plan_stale` refusal, which needs its own explanation. */
function isStale(error: unknown): boolean {
  return (
    error instanceof Error &&
    error.message.toLowerCase().includes('road conditions changed')
  );
}

/**
 * Route planner: plan a constrained route, compare the options, approve one.
 */
export function RoutePlannerScreen() {
  const t = useT();
  const { workspace } = useAuth();

  const districts = workspace?.districts ?? [];
  const [districtId, setDistrictId] = useState(districts[0]?.id ?? '');
  const effectiveDistrict = districtId || districts[0]?.id || '';

  // The trip this route is for, if any. An approved plan is then given to it,
  // which is what puts it in front of the driver.
  const linkedTrip = useSyncExternalStore(
    () => () => undefined,
    tripFromUrl,
    () => '',
  );
  const [tripChoice, setTripChoice] = useState<string | null>(null);
  const tripId = tripChoice ?? linkedTrip;

  const [originChoice, setOriginId] = useState('');
  const [destinationChoice, setDestinationId] = useState('');
  const [vehicleChoice, setVehicleId] = useState('');
  const [priority, setPriority] =
    useState<(typeof PRIORITIES)[number]>('normal');
  const [departure, setDeparture] = useState('');
  const [deadline, setDeadline] = useState('');

  const [plan, setPlan] = useState<RoutePlan | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [approving, setApproving] = useState<RouteAlternative | null>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);

  const connectivity = useConnectivitySummary(effectiveDistrict || null);
  const vehicles = useVehicles();
  const trips = useTrips(null, 200);
  const consignments = useConsignments(null, 200);
  const createPlan = useCreateRoutePlan();
  const approvePlan = useApproveRoutePlan();
  const bindTrip = useBindTripRoutePlan();

  const openTrips = (trips.data?.trips ?? []).filter(
    (row) =>
      OPEN_TRIP_STATUSES.has(row.status) &&
      row.district_id === effectiveDistrict,
  );
  const trip = openTrips.find((row) => row.id === tripId) ?? null;
  const tripConsignment =
    (consignments.data?.consignments ?? []).find(
      (row) => row.id === trip?.consignment_id,
    ) ?? null;
  // A trip fills in its own ends and vehicle; anything picked by hand wins.
  const originId = originChoice || (tripConsignment?.origin_facility_id ?? '');
  const destinationId =
    destinationChoice || (tripConsignment?.destination_facility_id ?? '');
  const vehicleId = vehicleChoice || (trip?.vehicle_id ?? '');

  // Only facilities the graph can actually start or end at.
  const routableFacilities = useMemo<ConnectivityFacility[]>(
    () =>
      (connectivity.data?.facility_status ?? []).filter(
        (facility) => facility.routing_node_id != null,
      ),
    [connectivity.data],
  );

  const byId = useMemo(
    () =>
      new Map(
        routableFacilities.map((facility) => [facility.facility_id, facility]),
      ),
    [routableFacilities],
  );

  const origin = byId.get(originId) ?? null;
  const destination = byId.get(destinationId) ?? null;
  const sameFacility = originId !== '' && originId === destinationId;
  const canPlan =
    Boolean(origin?.routing_node_id) &&
    Boolean(destination?.routing_node_id) &&
    !sameFacility &&
    !createPlan.isPending;

  const alternatives = plan?.alternatives ?? [];
  const selected =
    alternatives.find((item) => item.id === selectedId) ??
    alternatives[0] ??
    null;
  const vehicleLabel =
    vehicles.data?.vehicles.find((row) => row.id === vehicleId)
      ?.registration_ref ?? t('planner.anyVehicle');

  function submitPlan() {
    if (!canPlan || !origin?.routing_node_id || !destination?.routing_node_id)
      return;
    createPlan.mutate(
      {
        body: {
          district_id: effectiveDistrict,
          origin_node_id: origin.routing_node_id,
          destination_node_id: destination.routing_node_id,
          vehicle_id: vehicleId || null,
          trip_id: trip?.id ?? null,
          priority,
          requested_alternatives: 3,
        },
        idempotencyKey: newUuid(),
      },
      {
        onSuccess: (response) => {
          setPlan(response.plan);
          setSelectedId(response.plan.alternatives[0]?.id ?? null);
          setOptionsOpen(true);
        },
      },
    );
  }

  function confirmApproval() {
    if (!plan || !approving) return;
    approvePlan.mutate(
      {
        planId: plan.id,
        body: {
          alternative_id: approving.id,
          expected_network_version: plan.network_version,
        },
        idempotencyKey: newUuid(),
      },
      {
        onSuccess: (response) => {
          setPlan(response.plan);
          setApproving(null);
          if (trip) {
            bindTrip.mutate({
              tripId: trip.id,
              routePlanId: response.plan.id,
              idempotencyKey: newUuid(),
            });
          }
        },
        onError: (error) => {
          // Nothing inside the approval dialog can rescue a stale plan, and
          // leaving it open would show the same refusal twice.
          if (isStale(error)) setApproving(null);
        },
      },
    );
  }

  const planError = createPlan.isError ? errorMessage(createPlan.error) : null;
  const approveStale = approvePlan.isError && isStale(approvePlan.error);
  const noRoute = plan?.result_status === 'no_route';

  return (
    <CommandShell
      activeHref="/planner"
      title={t('planner.title')}
      // The facility list is the recorded network before any plan exists, so
      // the badge says so rather than claiming nothing is connected.
      headerExtra={
        plan ? <FreshnessLabel asOf={plan.computed_at} /> : undefined
      }
    >
      <div className="grid gap-4 lg:grid-cols-[17.5rem_minmax(0,1fr)] xl:grid-cols-[17.5rem_minmax(0,1fr)_21.25rem]">
        <section className="flex min-w-0 flex-col gap-3">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('planner.inputsHeading')}
          </h2>

          {connectivity.isError && (
            <ErrorPanel
              title={t('planner.error.facilities')}
              message={errorMessage(connectivity.error) ?? undefined}
              onRetry={() => void connectivity.refetch()}
            />
          )}

          <Field label={t('planner.forTrip')}>
            <select
              value={trip?.id ?? ''}
              onChange={(event) => {
                setTripChoice(event.target.value);
                // The new trip's own ends and vehicle take over.
                setOriginId('');
                setDestinationId('');
                setVehicleId('');
                setPlan(null);
                bindTrip.reset();
              }}
              data-planner-trip
              className="rounded-md border border-border bg-background p-2 text-sm"
            >
              <option value="">{t('planner.noTrip')}</option>
              {openTrips.map((row) => (
                <option key={row.id} value={row.id}>
                  {t('planner.tripOption', {
                    reference: row.consignment_reference ?? '-',
                    vehicle: row.vehicle_registration ?? '-',
                    route:
                      row.route_plan_status === 'invalidated'
                        ? t('planner.routeWithdrawn')
                        : (row.route_plan_status ?? t('planner.routeNone')),
                  })}
                </option>
              ))}
            </select>
          </Field>

          {districts.length > 1 && (
            <Field label={t('planner.district')}>
              <select
                value={effectiveDistrict}
                onChange={(event) => {
                  setDistrictId(event.target.value);
                  setOriginId('');
                  setDestinationId('');
                  setPlan(null);
                }}
                className="rounded-md border border-border bg-background p-2 text-sm"
              >
                {districts.map((district) => (
                  <option key={district.id} value={district.id}>
                    {district.name}
                  </option>
                ))}
              </select>
            </Field>
          )}

          <Field label={t('planner.origin')}>
            <FacilitySelect
              end="origin"
              value={originId}
              onChange={setOriginId}
              facilities={routableFacilities}
              disabled={connectivity.isPending}
            />
          </Field>

          <Field label={t('planner.destination')}>
            <FacilitySelect
              end="destination"
              value={destinationId}
              onChange={setDestinationId}
              facilities={routableFacilities}
              disabled={connectivity.isPending}
            />
          </Field>

          {sameFacility && (
            <p className="text-xs text-[#92400E]">
              {t('planner.sameFacility')}
            </p>
          )}
          {!connectivity.isPending && routableFacilities.length < 2 && (
            <p className="text-xs text-[#92400E]">
              {t('planner.noFacilities')}
            </p>
          )}

          <Field label={t('planner.vehicle')}>
            <select
              value={vehicleId}
              onChange={(event) => setVehicleId(event.target.value)}
              className="rounded-md border border-border bg-background p-2 text-sm"
            >
              <option value="">{t('planner.anyVehicle')}</option>
              {(vehicles.data?.vehicles ?? [])
                .filter((row) => row.active)
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.registration_ref}
                  </option>
                ))}
            </select>
          </Field>

          <Field label={t('planner.priority')}>
            <select
              value={priority}
              onChange={(event) =>
                setPriority(event.target.value as (typeof PRIORITIES)[number])
              }
              className="rounded-md border border-border bg-background p-2 text-sm"
            >
              {PRIORITIES.map((value) => (
                <option key={value} value={value}>
                  {t(`priority.${value}`)}
                </option>
              ))}
            </select>
          </Field>

          <div className="grid grid-cols-2 gap-2">
            <Field label={t('planner.departure')}>
              <input
                type="datetime-local"
                value={departure}
                onChange={(event) => setDeparture(event.target.value)}
                className="rounded-md border border-border bg-background p-2 text-sm"
              />
            </Field>
            <Field label={t('planner.deadline')}>
              <input
                type="datetime-local"
                value={deadline}
                onChange={(event) => setDeadline(event.target.value)}
                className="rounded-md border border-border bg-background p-2 text-sm"
              />
            </Field>
          </div>

          <button
            type="button"
            onClick={submitPlan}
            disabled={!canPlan}
            data-testid="plan-route"
            className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {createPlan.isPending
              ? t('planner.planning')
              : plan
                ? t('planner.replan')
                : t('planner.plan')}
          </button>

          {planError && (
            <ErrorPanel title={t('planner.error.plan')} message={planError} />
          )}
        </section>

        <section className="flex min-h-[24rem] min-w-0 flex-col gap-3">
          {alternatives.length > 0 ? (
            <RouteMap
              alternatives={alternatives}
              selectedId={selected?.id ?? null}
              onSelect={setSelectedId}
              className="flex-1"
            />
          ) : (
            <EmptyState title={t('planner.mapEmptyTitle')} className="flex-1" />
          )}

          {plan && <PlanNotices plan={plan} />}
        </section>

        <section className="flex min-w-0 flex-col gap-3 lg:col-span-2 xl:col-span-1">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('planner.alternativesHeading')}
            </h2>
            {/* Below the three-column breakpoint the options become a drawer,
                so the map keeps the width it needs to be read. */}
            {alternatives.length > 0 && (
              <button
                type="button"
                onClick={() => setOptionsOpen((open) => !open)}
                className="rounded-md border border-border px-2 py-1 text-xs font-medium xl:hidden"
              >
                {optionsOpen
                  ? t('planner.hideOptions')
                  : t('planner.showOptions', { count: alternatives.length })}
              </button>
            )}
          </div>

          <div
            className={cn(
              'flex flex-col gap-3',
              !optionsOpen && 'hidden xl:flex',
            )}
          >
            {approveStale && (
              <ErrorPanel
                title={t('planner.staleTitle')}
                message={t('planner.staleBody')}
                onRetry={submitPlan}
              />
            )}
            {approvePlan.isError && !approveStale && (
              <ErrorPanel
                title={t('planner.error.approve')}
                message={errorMessage(approvePlan.error) ?? undefined}
              />
            )}

            {plan?.status === 'approved' && (
              <p
                data-testid="plan-approved"
                className="rounded-md bg-[#DCFCE7] px-3 py-2 text-sm font-medium text-[#166534]"
              >
                {t('planner.approved')}
              </p>
            )}
            {bindTrip.isSuccess && (
              <p
                data-testid="plan-bound"
                className="rounded-md bg-[#DCFCE7] px-3 py-2 text-sm font-medium text-[#166534]"
              >
                {t('planner.bound', {
                  reference: bindTrip.data.trip.consignment_reference ?? '-',
                })}
              </p>
            )}
            {bindTrip.isError && (
              <ErrorPanel
                title={t('planner.error.bind')}
                message={errorMessage(bindTrip.error) ?? undefined}
              />
            )}

            {!plan ? (
              <EmptyState title={t('planner.emptyTitle')} />
            ) : noRoute ? (
              <NoRoutePanel plan={plan} />
            ) : (
              <ul
                className="grid gap-3 lg:grid-cols-2 xl:grid-cols-1"
                data-testid="alternatives"
              >
                {alternatives.map((alternative) => (
                  <AlternativeCard
                    key={alternative.id}
                    alternative={alternative}
                    avoidedClosures={avoidedClosureCount(
                      plan.exclusions as Record<
                        string,
                        { segment_id: string }[]
                      >,
                    )}
                    departureIso={
                      departure ? new Date(departure).toISOString() : null
                    }
                    deadlineIso={
                      deadline ? new Date(deadline).toISOString() : null
                    }
                    selected={alternative.id === selected?.id}
                    onSelect={() => setSelectedId(alternative.id)}
                    onApprove={() => setApproving(alternative)}
                    approveDisabled={
                      plan.status !== 'proposed' || approvePlan.isPending
                    }
                  />
                ))}
              </ul>
            )}

            {plan && (
              <p className="text-xs text-muted-foreground">
                {t('planner.notSafeClaim')}
              </p>
            )}
          </div>
        </section>
      </div>

      {plan && approving && (
        <ApproveRouteDialog
          plan={plan}
          alternative={approving}
          vehicleLabel={vehicleLabel}
          pending={approvePlan.isPending}
          error={approvePlan.isError ? errorMessage(approvePlan.error) : null}
          onConfirm={confirmApproval}
          onClose={() => setApproving(null)}
        />
      )}
    </CommandShell>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

function FacilitySelect({
  end,
  value,
  onChange,
  facilities,
  disabled,
}: {
  end: 'origin' | 'destination';
  value: string;
  onChange: (value: string) => void;
  facilities: ConnectivityFacility[];
  disabled: boolean;
}) {
  const t = useT();
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      data-planner-end={end}
      className="rounded-md border border-border bg-background p-2 text-sm"
    >
      <option value="">{t('planner.choose')}</option>
      {facilities.map((facility) => (
        <option key={facility.facility_id} value={facility.facility_id}>
          {facility.name}
        </option>
      ))}
    </select>
  );
}

/** Coverage caveats that apply to the whole plan, not to one option. */
function PlanNotices({ plan }: { plan: RoutePlan }) {
  const t = useT();
  const coverage = plan.coverage_warnings as {
    code: string;
    segment_count?: number;
  }[];
  if (coverage.length === 0) return null;

  return (
    <ul className="flex flex-col gap-1.5">
      {coverage.map((warning) => (
        <li
          key={warning.code}
          data-coverage={warning.code}
          className="flex items-start gap-2 rounded-md bg-[#FEF3C7] px-3 py-2 text-xs text-[#92400E]"
        >
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {t(`planner.coverage.${warning.code}`, {
            count: warning.segment_count ?? 0,
          })}
        </li>
      ))}
    </ul>
  );
}

/** "No route" is an answer, not a failure. */
function NoRoutePanel({ plan }: { plan: RoutePlan }) {
  const t = useT();
  const exclusions = plan.exclusions as Record<
    string,
    { segment_id: string }[]
  >;

  return (
    <div
      data-testid="no-route"
      className="flex flex-col gap-3 rounded-lg border border-[#FDE68A] bg-[#FFFBEB] p-3"
    >
      <div>
        <h3 className="text-sm font-semibold text-[#92400E]">
          {t('planner.noRouteTitle')}
        </h3>
        <p className="mt-1 text-sm text-[#92400E]">
          {t('planner.noRouteBody')}
        </p>
      </div>

      {plan.reason_codes.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[#92400E]">
            {t('planner.reasonHeading')}
          </h4>
          <ul className="mt-1 flex flex-col gap-1 text-sm text-[#92400E]">
            {plan.reason_codes.map((code) => (
              <li key={code} data-reason={code}>
                {t(`planner.reason.${code}`)}
              </li>
            ))}
          </ul>
        </section>
      )}

      {Object.keys(exclusions).length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[#92400E]">
            {t('planner.exclusionsHeading')}
          </h4>
          <ul className="mt-1 flex flex-col gap-1 text-sm text-[#92400E]">
            {Object.entries(exclusions).map(([reason, items]) => (
              <li key={reason} data-exclusion={reason}>
                {t(`planner.exclusion.${reason}`, { count: items.length })}
              </li>
            ))}
          </ul>
        </section>
      )}

      {plan.actions.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[#92400E]">
            {t('planner.actionsHeading')}
          </h4>
          <ul className="mt-1 list-disc ps-4 text-sm text-[#92400E]">
            {plan.actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
