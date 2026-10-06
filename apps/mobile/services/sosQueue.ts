import AsyncStorage from '@react-native-async-storage/async-storage';

import { newUuid } from './ids';
import { sendSos, type SosRequestBody } from './rastaApi';

/**
 * An SOS for the control room, kept on the phone until the server has it.
 *
 * The message to 112 never waits on this: it opens at once. This is the second
 * channel and, like a field report, it survives a dead network and a restart.
 * Each SOS keeps one Idempotency-Key, so a retry can never raise it twice.
 */

const KEY = 'rasta.sos.pending.v1';

interface PendingSos {
  idempotencyKey: string;
  body: SosRequestBody;
  queuedAt: string;
}

export type SosDelivery =
  | { state: 'sent'; recipients: number }
  | { state: 'waiting'; reason: string }
  | { state: 'refused'; reason: string };

async function read(): Promise<PendingSos[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as PendingSos[]) : [];
  } catch {
    return [];
  }
}

async function write(items: PendingSos[]): Promise<void> {
  await AsyncStorage.setItem(KEY, JSON.stringify(items));
}

/** Try every waiting SOS once. The answer describes the newest one. */
export async function flushSos(): Promise<SosDelivery | null> {
  const items = await read();
  if (items.length === 0) return null;
  const kept: PendingSos[] = [];
  let latest: SosDelivery | null = null;
  for (const item of items) {
    const result = await sendSos(item.body, item.idempotencyKey);
    if (result.ok) {
      latest = { state: 'sent', recipients: result.data.recipients };
    } else if (
      result.status === null ||
      result.status === 429 ||
      result.status >= 500
    ) {
      // No answer, or not now: keep it and try again later.
      kept.push(item);
      latest = { state: 'waiting', reason: result.reason };
    } else {
      // A refusal will not change on a retry.
      latest = { state: 'refused', reason: result.reason };
    }
  }
  await write(kept);
  return latest;
}

/** Keep an SOS for the control room, then try to deliver everything waiting. */
export async function queueSos(body: SosRequestBody): Promise<SosDelivery> {
  const items = await read();
  items.push({
    idempotencyKey: newUuid(),
    body,
    queuedAt: new Date().toISOString(),
  });
  await write(items);
  return (
    (await flushSos()) ?? {
      state: 'waiting',
      reason: 'It is saved on this phone.',
    }
  );
}
