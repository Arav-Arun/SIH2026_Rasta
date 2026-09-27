import AsyncStorage from '@react-native-async-storage/async-storage';

import { newUuid } from './ids';

import type { RoutePack } from './routePack';
import { emptyStats, type QueuedFix, type QueueStats } from './telemetryQueue';

/** On-device storage for the driver's route pack and position queue. */

const KEY_PACK = '@rasta_route_pack_v1';
const KEY_ACK = '@rasta_route_ack_v1';
const KEY_QUEUE = '@rasta_telemetry_queue_v1';
const KEY_STATS = '@rasta_telemetry_stats_v1';
const KEY_GRANT = '@rasta_tracking_grant_v1';
const KEY_DEVICE = '@rasta_device_public_id_v1';

export interface RouteAcknowledgement {
  plan_id: string;
  alternative_id: string;
  acknowledged_at: string;
}

export interface StoredGrant {
  token: string;
  trip_id: string;
  device_id: string;
  expires_at: string;
}

async function readJson<T>(key: string, fallback: T): Promise<T> {
  try {
    const raw = await AsyncStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    // A corrupted value is not worth failing a screen over; the driver gets
    // the empty state and the next successful write repairs it.
    return fallback;
  }
}

async function writeJson(key: string, value: unknown): Promise<void> {
  await AsyncStorage.setItem(key, JSON.stringify(value));
}

export const loadRoutePack = () => readJson<RoutePack | null>(KEY_PACK, null);
export const saveRoutePack = (pack: RoutePack) => writeJson(KEY_PACK, pack);
export const loadAcknowledgement = () =>
  readJson<RouteAcknowledgement | null>(KEY_ACK, null);
export const saveAcknowledgement = (ack: RouteAcknowledgement) =>
  writeJson(KEY_ACK, ack);

export const loadQueue = () => readJson<QueuedFix[]>(KEY_QUEUE, []);
export const saveQueue = (queue: QueuedFix[]) => writeJson(KEY_QUEUE, queue);

export const loadStats = () => readJson<QueueStats>(KEY_STATS, emptyStats());
export const saveStats = (stats: QueueStats) => writeJson(KEY_STATS, stats);

export const loadGrant = () => readJson<StoredGrant | null>(KEY_GRANT, null);
export const saveGrant = (grant: StoredGrant) => writeJson(KEY_GRANT, grant);
/**
 * Forgets everything this phone holds for the signed-in person's trips: the
 * tracking credential, positions not yet sent, their counters, the route pack
 * and its acknowledgement.
 */
export const clearDriverData = () =>
  AsyncStorage.multiRemove([
    KEY_PACK,
    KEY_ACK,
    KEY_QUEUE,
    KEY_STATS,
    KEY_GRANT,
  ]);

/** A stable public identifier for this installation. */
export async function devicePublicId(): Promise<string> {
  const existing = await AsyncStorage.getItem(KEY_DEVICE);
  if (existing) return existing;
  const generated = `rasta-app-${newUuid()}`;
  await AsyncStorage.setItem(KEY_DEVICE, generated);
  return generated;
}
