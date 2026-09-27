import AsyncStorage from '@react-native-async-storage/async-storage';

import { HazardReport } from '../types';
import { readPhotoForUpload, uploadEvidenceObject } from './evidenceUpload';
import { newUuid } from './ids';
import { addAttachment, completeAttachment, createIncident } from './rastaApi';
import {
  migrateLegacyReports,
  newFiling,
  pendingWork,
  sendReport,
  summarise,
  type OutboxDeps,
  type OutboxSummary,
} from './reportOutbox';

/** The reports this phone holds, and the outbox that sends them. */

const STORAGE_KEY_REPORTS = '@rasta_reports_v2';
/** Written by earlier versions; migrated once, then removed. */
const LEGACY_KEY_HAZARDS = '@rasta_ner_hazards_v1';
const STORAGE_KEY_NETWORK_MODE = '@rasta_ner_network_mode_v1';

/**
 * Whether this app is sending. `dead_zone` means the person has asked it to
 * hold reports on the phone; it does not change the phone's connection.
 */
export type NetworkMode = 'online' | 'dead_zone';

type StorageListener = () => void;
const listeners: Set<StorageListener> = new Set();

function notifyListeners() {
  listeners.forEach((listener) => {
    try {
      listener();
    } catch (e) {
      console.error('Storage listener error', e);
    }
  });
}

export function subscribeToOutbox(callback: StorageListener) {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}

export async function getNetworkMode(): Promise<NetworkMode> {
  try {
    const mode = await AsyncStorage.getItem(STORAGE_KEY_NETWORK_MODE);
    return mode === 'dead_zone' ? 'dead_zone' : 'online';
  } catch {
    return 'online';
  }
}

export async function setNetworkMode(mode: NetworkMode): Promise<void> {
  try {
    await AsyncStorage.setItem(STORAGE_KEY_NETWORK_MODE, mode);
    notifyListeners();
  } catch (e) {
    console.error('Failed to save network mode', e);
  }
}

// One pass at a time.
let chain: Promise<unknown> = Promise.resolve();
function exclusive<T>(work: () => Promise<T>): Promise<T> {
  const run = chain.then(work, work);
  chain = run.catch(() => undefined);
  return run;
}

async function readReports(): Promise<HazardReport[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY_REPORTS);
    if (raw) return JSON.parse(raw) as HazardReport[];

    const legacy = await AsyncStorage.getItem(LEGACY_KEY_HAZARDS);
    if (!legacy) return [];
    const migrated = migrateLegacyReports(JSON.parse(legacy) as unknown[]);
    await AsyncStorage.setItem(STORAGE_KEY_REPORTS, JSON.stringify(migrated));
    await AsyncStorage.removeItem(LEGACY_KEY_HAZARDS);
    return migrated;
  } catch {
    return [];
  }
}

async function writeReports(reports: HazardReport[]): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY_REPORTS, JSON.stringify(reports));
  notifyListeners();
}

/** Replaces one stored report, keeping any others written in the meantime. */
async function storeReport(report: HazardReport): Promise<void> {
  const all = await readReports();
  await writeReports(
    all.map((item) => (item.id === report.id ? report : item)),
  );
}

/** The reports this device holds, the ones its user filed. */
export async function getAllHazards(): Promise<HazardReport[]> {
  return readReports();
}

/** Reports with something still to send: the report itself, or its photo. */
export async function getOutboxQueue(): Promise<HazardReport[]> {
  return (await readReports()).filter((report) => pendingWork(report) !== null);
}

/** Everything that would be lost if this phone's data were cleared now. */
export async function countUnsent(): Promise<number> {
  return (await getOutboxQueue()).length;
}

type NewReport = Omit<
  HazardReport,
  | 'id'
  | 'reportedAt'
  | 'syncStatus'
  | 'idempotencyKey'
  | 'offlineRecorded'
  | 'filing'
  | 'controlRoomIncidentId'
  | 'evidenceStatus'
>;

/**
 * Saves a report on the phone, not yet sent. Its key is fixed here, before any
 * request, so every later attempt to send it is the same request.
 */
export async function enqueueHazardReport(
  report: NewReport,
): Promise<HazardReport> {
  const held = (await getNetworkMode()) === 'dead_zone';
  const key = newUuid();
  const saved: HazardReport = {
    ...report,
    id: `rpt-${key}`,
    reportedAt: new Date().toISOString(),
    syncStatus: 'queued',
    idempotencyKey: key,
    offlineRecorded: held,
    evidenceStatus: report.photoUri ? 'not_sent' : 'none',
    filing: newFiling(key),
  };
  await exclusive(async () => {
    await writeReports([saved, ...(await readReports())]);
  });
  return saved;
}

const liveDeps: OutboxDeps = {
  createIncident,
  addAttachment,
  completeAttachment,
  readPhoto: readPhotoForUpload,
  uploadPhoto: uploadEvidenceObject,
  now: () => new Date(),
};

/** Takes one report as far as it can go now. */
export async function sendStoredReport(
  id: string,
): Promise<HazardReport | null> {
  return exclusive(async () => {
    const report = (await readReports()).find((item) => item.id === id);
    if (!report || pendingWork(report) === null) return report ?? null;
    if (report.syncStatus === 'queued')
      await storeReport({ ...report, syncStatus: 'syncing' });
    const result = await sendReport(report, liveDeps);
    await storeReport(result);
    return result;
  });
}

/**
 * One pass over everything waiting, oldest first. Returns what happened, so
 * the screen can say it rather than claim success.
 */
export async function syncOutboxQueue(): Promise<OutboxSummary> {
  return exclusive(async () => {
    const before = await readReports();
    const waiting = before
      .filter((report) => pendingWork(report) !== null)
      .reverse();
    for (const report of waiting) {
      if (report.syncStatus === 'queued')
        await storeReport({ ...report, syncStatus: 'syncing' });
      await storeReport(await sendReport(report, liveDeps));
    }
    return summarise(before, await readReports());
  });
}

/** Removes one report from this phone, sent or not. */
export async function removeReport(id: string): Promise<void> {
  await exclusive(async () => {
    await writeReports((await readReports()).filter((item) => item.id !== id));
  });
}

/** Removes every report this phone holds. Used at sign-out, after the person chose to. */
export async function clearReports(): Promise<void> {
  await exclusive(async () => {
    await AsyncStorage.multiRemove([STORAGE_KEY_REPORTS, LEGACY_KEY_HAZARDS]);
    notifyListeners();
  });
}
