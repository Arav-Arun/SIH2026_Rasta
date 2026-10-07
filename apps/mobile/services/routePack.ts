/**
 * The route a driver is following, and how much of it can be trusted offline.
 */

import { message, type Message } from './i18n';

export type RoutePackFreshness =
  | 'confirmed_current'
  | 'cached_offline'
  | 'cached_unverified'
  | 'replaced'
  | 'withdrawn'
  | 'none';

export interface RoutePack {
  trip_id: string;
  plan_id: string;
  plan_status: string;
  alternative_id: string;
  category: string;
  network_version: string;
  risk_snapshot_version: string;
  distance_m: number;
  eta_range_seconds: [number, number] | null;
  segment_ids: string[];
  geometry: unknown;
  unknown_constraints: number;
  avoided_closures: number;
  segments_without_a_risk_score: number;
  recommendation: string | null;
  /** When the server computed the plan. */
  computed_at: string;
  /** When this device last wrote the pack to storage. */
  cached_at: string;
  /** When the device last confirmed with the server that the plan still stands. */
  verified_at: string | null;
}

/** A plan status the driver must not keep driving on without being told. */
const REPLACED_STATUSES = new Set(['superseded', 'invalidated']);
const WITHDRAWN_STATUSES = new Set(['rejected']);

/**
 * A verification older than this is not evidence that the route still stands.
 * Twenty minutes is roughly a leg between checkpoints on a district road.
 */
export const VERIFICATION_WINDOW_SECONDS = 20 * 60;

export function secondsSince(iso: string | null, now: Date): number | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.round((now.getTime() - then) / 1000));
}

/** What the screen may claim about the route in hand. */
export function routePackFreshness(
  pack: RoutePack | null,
  options: { online: boolean; serverStatus?: string | null; now: Date },
): RoutePackFreshness {
  if (!pack) return 'none';
  const status = options.serverStatus ?? pack.plan_status;
  if (WITHDRAWN_STATUSES.has(status)) return 'withdrawn';
  if (REPLACED_STATUSES.has(status)) return 'replaced';

  const verifiedAgo = secondsSince(pack.verified_at, options.now);
  if (verifiedAgo !== null && verifiedAgo <= VERIFICATION_WINDOW_SECONDS) {
    return 'confirmed_current';
  }
  return options.online ? 'cached_unverified' : 'cached_offline';
}

/** Whether the driver has to be shown a change before following the route. */
export function needsAcknowledgement(
  pack: RoutePack | null,
  incoming: { plan_id: string; alternative_id: string } | null,
  acknowledged: {
    plan_id: string;
    alternative_id: string;
    acknowledged_at?: string;
  } | null,
): boolean {
  if (!incoming) return false;
  const already =
    acknowledged !== null &&
    acknowledged.plan_id === incoming.plan_id &&
    acknowledged.alternative_id === incoming.alternative_id;
  if (already) return false;
  // Nothing to compare against yet: the first route a driver receives is
  // theirs to read, not a revision to acknowledge.
  if (!pack) return false;
  return (
    pack.plan_id !== incoming.plan_id ||
    pack.alternative_id !== incoming.alternative_id
  );
}

export function formatDistance(metres: number): string {
  if (metres < 1000) return `${Math.round(metres)} m`;
  return `${(metres / 1000).toFixed(1)} km`;
}

/**
 * An ETA is a range. A single number would read as a promise about road whose
 * state nobody has observed.
 */
export function formatEtaRange(range: [number, number] | null): Message | null {
  if (!range) return null;
  const [fast, slow] = range;
  const minutes = (seconds: number) => Math.max(1, Math.round(seconds / 60));
  const low = minutes(fast);
  const high = minutes(slow);
  if (low === high) return message('mobile.units.minutes', { count: low });
  return message('mobile.units.minutesRange', { low, high });
}

export function formatAge(seconds: number | null): Message {
  if (seconds === null) return 'mobile.age.never';
  if (seconds < 60) return 'freshness.justNow';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return message('freshness.minutesAgo', { count: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 24) return message('freshness.hoursAgo', { count: hours });
  return message('freshness.daysAgo', { count: Math.round(hours / 24) });
}

export type RouteLine = [number, number][];

/** Pull drawable lines out of the geometry the server sent. */
export function linesFromGeometry(geometry: unknown): RouteLine[] {
  if (!geometry || typeof geometry !== 'object') return [];
  const shape = geometry as { type?: string; coordinates?: unknown };
  if (shape.type === 'LineString' && Array.isArray(shape.coordinates)) {
    return isLine(shape.coordinates) ? [shape.coordinates] : [];
  }
  if (shape.type === 'MultiLineString' && Array.isArray(shape.coordinates)) {
    return shape.coordinates.filter(isLine);
  }
  return [];
}

function isLine(value: unknown): value is RouteLine {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    value.every(
      (point) =>
        Array.isArray(point) &&
        point.length >= 2 &&
        Number.isFinite(point[0]) &&
        Number.isFinite(point[1]),
    )
  );
}
