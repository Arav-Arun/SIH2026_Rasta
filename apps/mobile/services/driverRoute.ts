import {
  loadAcknowledgement,
  loadRoutePack,
  saveAcknowledgement,
  saveRoutePack,
  type RouteAcknowledgement,
} from './driverStorage';
import {
  getRoutePlan,
  listTrips,
  type RoutePlanDetail,
  type Trip,
} from './rastaApi';
import {
  needsAcknowledgement,
  routePackFreshness,
  type RoutePack,
  type RoutePackFreshness,
} from './routePack';

/**
 * The driver's route: what the control room approved, cached for the valley.
 */

export interface DriverRouteView {
  trip: Trip | null;
  pack: RoutePack | null;
  freshness: RoutePackFreshness;
  /** A route the control room now approves that differs from the pack in hand. */
  revision: { pack: RoutePack; from_plan_id: string } | null;
  acknowledgement: RouteAcknowledgement | null;
  /** Why there is no route to show, when there is none. */
  absence: 'no_trip' | 'no_route_plan' | 'plan_unreadable' | null;
  /** Set when the last refresh could not reach the server. */
  offlineReason: string | null;
}

/** The trip a driver is actually working: running first, then next offered. */
export function activeTrip(trips: Trip[]): Trip | null {
  const order = ['active', 'paused', 'awaiting_driver', 'planned'];
  for (const status of order) {
    const found = trips.find((trip) => trip.status === status);
    if (found) return found;
  }
  return null;
}

function chosenAlternative(plan: RoutePlanDetail) {
  if (plan.chosen_alternative_id) {
    const exact = plan.alternatives.find(
      (alt) => alt.id === plan.chosen_alternative_id,
    );
    if (exact) return exact;
  }
  return null;
}

function packFromPlan(
  trip: Trip,
  plan: RoutePlanDetail,
  now: Date,
): RoutePack | null {
  const alternative = chosenAlternative(plan);
  if (!alternative) return null;
  const unknown = alternative.constraint_warnings.filter(
    (warning) => warning.code === 'unknown_constraint',
  ).length;
  return {
    trip_id: trip.id,
    plan_id: plan.id,
    plan_status: plan.status,
    alternative_id: alternative.id,
    category: alternative.category,
    network_version: plan.network_version,
    risk_snapshot_version: plan.risk_snapshot_version,
    distance_m: Number(alternative.distance_m),
    eta_range_seconds: alternative.eta_range_seconds,
    segment_ids: alternative.segment_ids,
    geometry: alternative.geometry,
    unknown_constraints: unknown,
    avoided_closures: alternative.avoided_closure_count ?? 0,
    segments_without_a_risk_score:
      alternative.risk_summary?.segments_without_a_risk_score ?? 0,
    recommendation: alternative.recommendation,
    computed_at: plan.computed_at,
    cached_at: now.toISOString(),
    verified_at: now.toISOString(),
  };
}

/** Read the cached route without touching the network. */
export async function readCachedRoute(now: Date): Promise<DriverRouteView> {
  const [pack, acknowledgement] = await Promise.all([
    loadRoutePack(),
    loadAcknowledgement(),
  ]);
  return {
    trip: null,
    pack,
    freshness: routePackFreshness(pack, { online: false, now }),
    revision: null,
    acknowledgement,
    absence: pack ? null : 'no_trip',
    offlineReason: null,
  };
}

export async function refreshRoute(now: Date): Promise<DriverRouteView> {
  const [cachedPack, acknowledgement] = await Promise.all([
    loadRoutePack(),
    loadAcknowledgement(),
  ]);
  const base: DriverRouteView = {
    trip: null,
    pack: cachedPack,
    freshness: routePackFreshness(cachedPack, { online: false, now }),
    revision: null,
    acknowledgement,
    absence: null,
    offlineReason: null,
  };

  const trips = await listTrips();
  if (!trips.ok) {
    // The cached pack is still the best answer; it is just not confirmed.
    return { ...base, offlineReason: trips.reason };
  }

  const trip = activeTrip(trips.data.trips);
  if (!trip) return { ...base, absence: 'no_trip' };
  if (!trip.route_plan_id) {
    return { ...base, trip, absence: 'no_route_plan' };
  }

  const plan = await getRoutePlan(trip.route_plan_id);
  if (!plan.ok) {
    return { ...base, trip, offlineReason: plan.reason };
  }

  const incoming = packFromPlan(trip, plan.data, now);
  if (!incoming) {
    return { ...base, trip, absence: 'plan_unreadable' };
  }

  const revision = needsAcknowledgement(
    cachedPack,
    { plan_id: incoming.plan_id, alternative_id: incoming.alternative_id },
    acknowledgement,
  );

  if (revision && cachedPack) {
    // Hold the new route to one side. The pack in hand stays the route the
    // driver is following until they say they have seen the change.
    return {
      trip,
      pack: cachedPack,
      freshness: routePackFreshness(cachedPack, {
        online: true,
        serverStatus: plan.data.status,
        now,
      }),
      revision: { pack: incoming, from_plan_id: cachedPack.plan_id },
      acknowledgement,
      absence: null,
      offlineReason: null,
    };
  }

  await saveRoutePack(incoming);
  return {
    trip,
    pack: incoming,
    freshness: routePackFreshness(incoming, {
      online: true,
      serverStatus: plan.data.status,
      now,
    }),
    revision: null,
    acknowledgement,
    absence: null,
    offlineReason: null,
  };
}

/** Accept a revised route, which is the only way it becomes the one in hand. */
export async function acknowledgeRevision(
  pack: RoutePack,
  now: Date,
): Promise<RouteAcknowledgement> {
  const acknowledgement: RouteAcknowledgement = {
    plan_id: pack.plan_id,
    alternative_id: pack.alternative_id,
    acknowledged_at: now.toISOString(),
  };
  await saveRoutePack({
    ...pack,
    cached_at: now.toISOString(),
    verified_at: now.toISOString(),
  });
  await saveAcknowledgement(acknowledgement);
  return acknowledgement;
}
