import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import { newUuid } from './ids';

import {
  devicePublicId,
  loadGrant,
  loadQueue,
  loadStats,
  saveGrant,
  saveQueue,
  saveStats,
  type StoredGrant,
} from './driverStorage';
import {
  issueTrackingGrant,
  registerDevice,
  submitTelemetryBatch,
} from './rastaApi';
import {
  MAX_QUEUE_POINTS,
  admit,
  applyResults,
  countDiscard,
  emptyStats,
  judgeCandidate,
  nextBatch,
  type QueueStats,
  type QueuedFix,
} from './telemetryQueue';

/**
 * Position tracking for an active trip. With location allowed all the time it
 * runs as a background task under a foreground service, so it keeps reporting
 * with the phone locked and a notification shows while it does. Otherwise it
 * reports only while the app is open.
 */

/** The background task's name; the system restarts it under this name. */
const TRACKING_TASK = 'rasta-trip-tracking';
const SAMPLING = {
  accuracy: Location.Accuracy.High,
  timeInterval: 15_000,
  distanceInterval: 10,
};
const DRAIN_EVERY_MS = 30_000;

export type TrackerState =
  | 'off'
  | 'requesting_permission'
  | 'permission_denied'
  | 'starting'
  | 'running'
  | 'error';

export interface TrackerSnapshot {
  state: TrackerState;
  tripId: string | null;
  stats: QueueStats;
  message: string | null;
  lastFixAt: string | null;
  grantExpiresAt: string | null;
  /** True while the device is holding positions it has not managed to send. */
  backlog: boolean;
  /** True when reporting continues with the phone locked. */
  background: boolean;
}

type Listener = (snapshot: TrackerSnapshot) => void;

const listeners = new Set<Listener>();
let snapshot: TrackerSnapshot = {
  state: 'off',
  tripId: null,
  stats: emptyStats(),
  message: null,
  lastFixAt: null,
  grantExpiresAt: null,
  backlog: false,
  background: false,
};

let watcher: Location.LocationSubscription | null = null;
let drainTimer: ReturnType<typeof setInterval> | null = null;
let lastDrainAt = 0;
let lastKept: {
  captured_at: string;
  latitude: number;
  longitude: number;
} | null = null;

/**
 * The stored queue and stats are changed by reading them, changing the copy and
 * writing it back, across awaits.
 */
let storeChain: Promise<unknown> = Promise.resolve();
function exclusively<T>(work: () => Promise<T>): Promise<T> {
  const run = storeChain.then(work, work);
  storeChain = run.catch(() => undefined);
  return run;
}

/** The drain in progress: the timer and "Send now" share it rather than race. */
let drainInFlight: Promise<{ sent: number; error: string | null }> | null =
  null;

function publish(patch: Partial<TrackerSnapshot>): void {
  snapshot = { ...snapshot, ...patch };
  snapshot.backlog = snapshot.stats.queued > 0;
  listeners.forEach((listener) => listener(snapshot));
}

export function subscribeToTracker(listener: Listener): () => void {
  listeners.add(listener);
  listener(snapshot);
  return () => listeners.delete(listener);
}
export async function restoreTracker(): Promise<void> {
  const [stats, grant, background] = await Promise.all([
    loadStats(),
    loadGrant(),
    backgroundRunning(),
  ]);
  publish({
    stats,
    grantExpiresAt: grant?.expires_at ?? null,
    tripId: grant?.trip_id ?? null,
  });
  // The system kept the background task alive while the app was closed.
  if (background && grant && snapshot.state === 'off') {
    startDrainTimer(grant.trip_id);
    publish({ state: 'running', background: true, message: null });
  }
}

function backgroundSupported(): boolean {
  return Platform.OS === 'android';
}

async function backgroundRunning(): Promise<boolean> {
  if (!backgroundSupported()) return false;
  try {
    return await Location.hasStartedLocationUpdatesAsync(TRACKING_TASK);
  } catch {
    return false;
  }
}

if (backgroundSupported()) {
  // Defined when this module loads, which the app's root layout makes happen
  // first, so the system can hand over positions even after a restart.
  TaskManager.defineTask<{ locations?: Location.LocationObject[] }>(
    TRACKING_TASK,
    async ({ data, error }) => {
      if (error || !data?.locations?.length) return;
      for (const fix of data.locations) await record(fix);
      const grant = await loadGrant();
      if (grant && Date.now() - lastDrainAt >= DRAIN_EVERY_MS) {
        await drainQueue(grant.trip_id);
      }
    },
  );
}

function startDrainTimer(tripId: string): void {
  if (drainTimer) clearInterval(drainTimer);
  drainTimer = setInterval(() => {
    void drainQueue(tripId);
  }, DRAIN_EVERY_MS);
}

/** A credential the device holds and that has not run out. */
function usableGrant(
  grant: StoredGrant | null,
  tripId: string,
  now: Date,
): StoredGrant | null {
  if (!grant || grant.trip_id !== tripId) return null;
  return Date.parse(grant.expires_at) > now.getTime() + 60_000 ? grant : null;
}

async function ensureGrant(tripId: string): Promise<StoredGrant | null> {
  const existing = usableGrant(await loadGrant(), tripId, new Date());
  if (existing) return existing;

  const publicId = await devicePublicId();
  const registration = await registerDevice(
    {
      device_public_id: publicId,
      platform: 'android',
      display_label: "Driver's phone",
    },
    newUuid(),
  );
  if (!registration.ok) {
    publish({ state: 'error', message: registration.reason });
    return null;
  }

  const issued = await issueTrackingGrant(tripId, publicId, newUuid());
  if (!issued.ok) {
    publish({ state: 'error', message: issued.reason });
    return null;
  }

  const grant: StoredGrant = {
    token: issued.data.token,
    trip_id: issued.data.trip_id,
    device_id: issued.data.device_id,
    expires_at: issued.data.expires_at,
  };
  await saveGrant(grant);
  publish({ grantExpiresAt: grant.expires_at });
  return grant;
}

function record(fix: Location.LocationObject): Promise<void> {
  return exclusively(() => recordNow(fix));
}

async function recordNow(fix: Location.LocationObject): Promise<void> {
  const candidate = {
    captured_at: new Date(fix.timestamp).toISOString(),
    latitude: fix.coords.latitude,
    longitude: fix.coords.longitude,
    accuracy_m: fix.coords.accuracy ?? 0,
  };
  const verdict = judgeCandidate(candidate, lastKept);
  const stats = await loadStats();
  if (!verdict.keep) {
    // A discarded fix is counted, not silently forgotten: a driver whose GPS
    // is struggling should be able to see that is what is happening.
    const next = verdict.reason ? countDiscard(stats, verdict.reason) : stats;
    await saveStats(next);
    publish({ stats: next });
    return;
  }

  const id = newUuid();
  const queued: QueuedFix = {
    client_point_id: id,
    idempotency_key: id,
    ...candidate,
    speed_kph:
      fix.coords.speed === null ||
      fix.coords.speed === undefined ||
      fix.coords.speed < 0
        ? null
        : Number((fix.coords.speed * 3.6).toFixed(2)),
    heading:
      fix.coords.heading === null ||
      fix.coords.heading === undefined ||
      fix.coords.heading < 0
        ? null
        : Number(fix.coords.heading.toFixed(2)),
  };

  const { queue, dropped } = admit(await loadQueue(), queued, MAX_QUEUE_POINTS);
  await saveQueue(queue);
  lastKept = candidate;
  const nextStats: QueueStats = {
    ...stats,
    queued: queue.length,
    dropped_for_space: stats.dropped_for_space + dropped,
  };
  await saveStats(nextStats);
  publish({ stats: nextStats, lastFixAt: candidate.captured_at });
}

/** Send what is waiting, one batch at a time. */
export function drainQueue(
  tripId: string,
): Promise<{ sent: number; error: string | null }> {
  if (!drainInFlight) {
    lastDrainAt = Date.now();
    drainInFlight = drainOnce(tripId).finally(() => {
      drainInFlight = null;
    });
  }
  return drainInFlight;
}

async function drainOnce(
  tripId: string,
): Promise<{ sent: number; error: string | null }> {
  const queue = await loadQueue();
  if (queue.length === 0) return { sent: 0, error: null };

  const grant = await ensureGrant(tripId);
  if (!grant) return { sent: 0, error: snapshot.message };

  const batch = nextBatch(queue);
  const response = await submitTelemetryBatch(
    grant.token,
    { trip_id: grant.trip_id, device_id: grant.device_id, points: batch },
    newUuid(),
  );

  if (!response.ok) {
    // An expired or refused credential is worth clearing so the next attempt
    // asks for a fresh one rather than retrying a dead token.
    if (response.code === 'tracking_grant_invalid')
      await saveGrant({ ...grant, expires_at: new Date(0).toISOString() });
    await exclusively(async () => {
      const stats = { ...(await loadStats()), last_error: response.reason };
      await saveStats(stats);
      publish({ stats, message: response.reason });
    });
    return { sent: 0, error: response.reason };
  }

  // Fold the answer into the queue as it is now, not as it was when the batch
  // left: fixes recorded meanwhile stay queued for the next batch.
  await exclusively(async () => {
    const folded = applyResults(
      await loadQueue(),
      response.data.results,
      await loadStats(),
      new Date(),
    );
    await saveQueue(folded.queue);
    await saveStats(folded.stats);
    publish({ stats: folded.stats, message: null });
  });
  return { sent: response.data.results.length, error: null };
}

/** The notification Android shows while the trip reports in the background. */
export type TrackingNotice = { title: string; body: string };

export async function startTracking(
  tripId: string,
  notice: TrackingNotice,
): Promise<void> {
  if (watcher || (await backgroundRunning())) await stopTracking();

  publish({ state: 'requesting_permission', tripId, message: null });
  const permission = await Location.requestForegroundPermissionsAsync();
  if (!permission.granted) {
    publish({
      state: 'permission_denied',
      message: 'mobile.tracker.permissionRefused',
    });
    return;
  }

  const grant = await ensureGrant(tripId);
  if (!grant) return;

  publish({ state: 'starting' });
  lastKept = null;
  startDrainTimer(tripId);

  if (backgroundSupported()) {
    const always = await Location.requestBackgroundPermissionsAsync();
    if (always.granted) {
      try {
        await Location.startLocationUpdatesAsync(TRACKING_TASK, {
          ...SAMPLING,
          pausesUpdatesAutomatically: false,
          foregroundService: {
            notificationTitle: notice.title,
            notificationBody: notice.body,
            killServiceOnDestroy: false,
          },
        });
        publish({ state: 'running', background: true, message: null });
        return;
      } catch {
        // Some devices refuse the service; reporting while open still works.
      }
    }
  }

  watcher = await Location.watchPositionAsync(SAMPLING, (fix) => {
    void record(fix);
  });
  publish({ state: 'running', background: false, message: null });
}

export async function stopTracking(): Promise<void> {
  watcher?.remove();
  watcher = null;
  if (await backgroundRunning()) {
    await Location.stopLocationUpdatesAsync(TRACKING_TASK).catch(() => {});
  }
  if (drainTimer) clearInterval(drainTimer);
  drainTimer = null;
  lastKept = null;
  publish({ state: 'off', background: false, message: null });
}
