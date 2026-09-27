import { randomUUID } from 'node:crypto';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { StoredGrant } from './driverStorage';
import { emptyStats, type QueueStats, type QueuedFix } from './telemetryQueue';

/**
 * The tracker's storage, API and GPS are replaced so its own logic runs in
 * Node: what it keeps, what it sends, and what survives a batch in flight.
 */
const store: {
  queue: QueuedFix[];
  stats: QueueStats;
  grant: StoredGrant | null;
} = {
  queue: [],
  stats: emptyStats(),
  grant: null,
};

vi.mock('./ids', () => ({ newUuid: () => randomUUID() }));

vi.mock('./driverStorage', () => ({
  devicePublicId: async () => 'device-public-id',
  loadQueue: async () => structuredClone(store.queue),
  saveQueue: async (queue: QueuedFix[]) => {
    store.queue = structuredClone(queue);
  },
  loadStats: async () => structuredClone(store.stats),
  saveStats: async (stats: QueueStats) => {
    store.stats = structuredClone(stats);
  },
  loadGrant: async () => store.grant,
  saveGrant: async (grant: StoredGrant) => {
    store.grant = grant;
  },
}));

let answerBatch: (points: QueuedFix[]) => void = () => {};
const submitTelemetryBatch = vi.fn(
  (_token: string, body: { points: QueuedFix[] }) =>
    new Promise((resolve) => {
      answerBatch = (points) =>
        resolve({
          ok: true,
          data: {
            results: points.map((point) => ({
              client_point_id: point.client_point_id,
              outcome: 'accepted',
            })),
          },
        });
      void body;
    }),
);

vi.mock('./rastaApi', () => ({
  registerDevice: vi.fn(),
  issueTrackingGrant: vi.fn(),
  submitTelemetryBatch: (...args: [string, { points: QueuedFix[] }]) =>
    submitTelemetryBatch(...args),
}));

let onFix: ((fix: unknown) => void) | null = null;
vi.mock('expo-location', () => ({
  Accuracy: { High: 4 },
  requestForegroundPermissionsAsync: async () => ({ granted: true }),
  watchPositionAsync: async (
    _options: unknown,
    callback: (fix: unknown) => void,
  ) => {
    onFix = callback;
    return { remove: () => {} };
  },
}));

const { drainQueue, startTracking, stopTracking } = await import('./tracker');

function fix(at: string, latitude: number) {
  return {
    timestamp: Date.parse(at),
    coords: { latitude, longitude: 91.88, accuracy: 8, speed: 5, heading: 90 },
  };
}

function queued(id: string, at: string): QueuedFix {
  return {
    client_point_id: id,
    idempotency_key: id,
    captured_at: at,
    latitude: 25.57,
    longitude: 91.88,
    accuracy_m: 8,
  };
}

async function settle() {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  store.queue = [];
  store.stats = emptyStats();
  store.grant = {
    token: 'grant',
    trip_id: 'trip-1',
    device_id: 'device-1',
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  };
  submitTelemetryBatch.mockClear();
});

afterEach(async () => {
  await stopTracking();
});

describe('a batch in flight', () => {
  it('keeps a position recorded while the batch was being sent', async () => {
    store.queue = [queued('p1', '2026-09-26T10:00:00Z')];
    await startTracking('trip-1');

    const draining = drainQueue('trip-1');
    await settle();
    expect(submitTelemetryBatch).toHaveBeenCalledTimes(1);

    // The truck moves on while the upload waits on a weak signal.
    onFix?.(fix('2026-09-26T10:00:30Z', 25.58));
    await settle();
    expect(store.queue.map((point) => point.client_point_id)).toContain('p1');
    expect(store.queue).toHaveLength(2);

    answerBatch([queued('p1', '2026-09-26T10:00:00Z')]);
    await draining;

    // p1 was answered; the fix taken during the upload is still waiting.
    expect(store.queue).toHaveLength(1);
    expect(store.queue[0].captured_at).toBe('2026-09-26T10:00:30.000Z');
    expect(store.stats.queued).toBe(1);
    expect(store.stats.uploaded).toBe(1);
  });

  it('a second request to send while one is in flight joins it instead of racing it', async () => {
    store.queue = [queued('p1', '2026-09-26T10:00:00Z')];
    const first = drainQueue('trip-1');
    const second = drainQueue('trip-1');
    await settle();
    expect(submitTelemetryBatch).toHaveBeenCalledTimes(1);
    answerBatch([queued('p1', '2026-09-26T10:00:00Z')]);
    expect(await first).toEqual(await second);
    expect(store.queue).toHaveLength(0);
  });
});
