/**
 * The last workspace the server confirmed, for opening the app with no network.
 */

import type { ServerWorkspace } from './identity';

const STORAGE_KEY = 'rasta.lastVerifiedWorkspace.v1';

/** Three days: long enough for a field trip out of coverage, short enough to age out. */
export const OFFLINE_IDENTITY_MAX_AGE_MS = 72 * 60 * 60 * 1000;

type Stored = {
  userId: string;
  verifiedAt: string;
  workspace: ServerWorkspace;
};

type RecalledIdentity = { workspace: ServerWorkspace; verifiedAt: string };

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function rememberVerifiedWorkspace(
  userId: string,
  workspace: ServerWorkspace,
  now: Date = new Date(),
): void {
  const value: Stored = { userId, verifiedAt: now.toISOString(), workspace };
  try {
    storage()?.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Storage full or blocked: the app still works online.
  }
}

export function recallVerifiedWorkspace(
  userId: string,
  now: Date = new Date(),
): RecalledIdentity | null {
  let stored: Stored | null = null;
  try {
    const raw = storage()?.getItem(STORAGE_KEY);
    stored = raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
  if (!stored || stored.userId !== userId || !stored.workspace) return null;
  const verifiedAt = Date.parse(stored.verifiedAt);
  if (!Number.isFinite(verifiedAt)) return null;
  const age = now.getTime() - verifiedAt;
  if (age < 0 || age > OFFLINE_IDENTITY_MAX_AGE_MS) return null;
  return { workspace: stored.workspace, verifiedAt: stored.verifiedAt };
}

export function forgetVerifiedWorkspace(): void {
  try {
    storage()?.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do: an unreadable store holds nothing usable either.
  }
}
