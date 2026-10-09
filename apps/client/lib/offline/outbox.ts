import { newUuid } from '@/lib/ids';

import { getOfflineDatabase } from './db';
import type { MutationEnvelope, MutationState, MutationType } from './types';

/** The mutation outbox and its state machine. */

const MUTATION_SCHEMA_VERSION = 1;

/** Transitions the outbox state machine permits. */
const ALLOWED_TRANSITIONS: Record<MutationState, MutationState[]> = {
  queued: ['uploading'],
  uploading: ['accepted', 'queued', 'conflict', 'needs_action'],
  accepted: [],
  // Both resolve back to queued once a person has acted.
  conflict: ['queued'],
  needs_action: ['queued'],
};

export function canTransition(from: MutationState, to: MutationState): boolean {
  return ALLOWED_TRANSITIONS[from].includes(to);
}

function newId(prefix: string): string {
  return `${prefix}_${newUuid()}`;
}

interface EnqueueRequest {
  type: MutationType;
  body: Record<string, unknown>;
  profileId: string | null;
  dependsOn?: string[];
  /** Supply only to replay a known key; otherwise one is minted here. */
  idempotencyKey?: string;
}

/**
 * The outbox is sent in the order it was queued, and that order is `createdAt`.
 */
let lastQueuedAtMs = 0;

function nextQueuedAt(): string {
  lastQueuedAtMs = Math.max(Date.now(), lastQueuedAtMs + 1);
  return new Date(lastQueuedAtMs).toISOString();
}

/** Adds a mutation in `queued`. Returns the envelope, whether or not it persisted. */
export async function enqueueMutation(
  request: EnqueueRequest,
): Promise<MutationEnvelope> {
  const now = nextQueuedAt();
  const envelope: MutationEnvelope = {
    mutationId: newId('mut'),
    idempotencyKey: request.idempotencyKey ?? newUuid(),
    type: request.type,
    schemaVersion: MUTATION_SCHEMA_VERSION,
    createdAt: now,
    updatedAt: now,
    profileId: request.profileId,
    dependsOn: request.dependsOn ?? [],
    body: request.body,
    attemptCount: 0,
    state: 'queued',
    lastError: null,
    result: null,
  };

  const db = getOfflineDatabase();
  if (db) await db.outbox.put(envelope);
  return envelope;
}

export async function listMutations(
  profileId: string | null,
): Promise<MutationEnvelope[]> {
  const db = getOfflineDatabase();
  if (!db) return [];
  const rows = await db.outbox.toArray();
  return rows
    .filter((row) => row.profileId === profileId)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export async function getMutation(
  mutationId: string,
): Promise<MutationEnvelope | null> {
  const db = getOfflineDatabase();
  if (!db) return null;
  return (await db.outbox.get(mutationId)) ?? null;
}

/**
 * Mutations ready to send right now: queued, and with every dependency already
 * accepted.
 */
export async function readyMutations(
  profileId: string | null,
): Promise<MutationEnvelope[]> {
  const all = await listMutations(profileId);
  const acceptedIds = new Set(
    all.filter((row) => row.state === 'accepted').map((row) => row.mutationId),
  );

  return all.filter(
    (row) =>
      row.state === 'queued' &&
      row.dependsOn.every((dependency) => acceptedIds.has(dependency)),
  );
}

/** Mutations held only because something they depend on has not landed. */
export async function blockedMutations(
  profileId: string | null,
): Promise<MutationEnvelope[]> {
  const all = await listMutations(profileId);
  const acceptedIds = new Set(
    all.filter((row) => row.state === 'accepted').map((row) => row.mutationId),
  );
  return all.filter(
    (row) =>
      row.state === 'queued' &&
      row.dependsOn.some((dependency) => !acceptedIds.has(dependency)),
  );
}

export class InvalidTransitionError extends Error {
  constructor(from: MutationState, to: MutationState) {
    super(`A mutation cannot move from ${from} to ${to}.`);
    this.name = 'InvalidTransitionError';
  }
}

interface TransitionOptions {
  error?: string | null;
  result?: Record<string, unknown> | null;
  /** Set when the attempt actually reached the network. */
  countAttempt?: boolean;
}

/**
 * Moves one mutation to a new state, refusing transitions the machine does not
 * allow.
 */
export async function transitionMutation(
  mutationId: string,
  to: MutationState,
  options: TransitionOptions = {},
): Promise<MutationEnvelope> {
  const db = getOfflineDatabase();
  const current = db ? await db.outbox.get(mutationId) : null;
  if (!current) {
    throw new Error(`Unknown mutation: ${mutationId}`);
  }
  if (!canTransition(current.state, to)) {
    throw new InvalidTransitionError(current.state, to);
  }

  const next: MutationEnvelope = {
    ...current,
    state: to,
    updatedAt: new Date().toISOString(),
    attemptCount: options.countAttempt
      ? current.attemptCount + 1
      : current.attemptCount,
    lastError: options.error === undefined ? current.lastError : options.error,
    result: options.result === undefined ? current.result : options.result,
  };

  if (db) await db.outbox.put(next);
  return next;
}

/**
 * How long a row may sit in `uploading` before a browser without Web Locks
 * presumes the request abandoned (see requeueInterruptedUploads).
 */
export const ABANDONED_UPLOAD_MS = 60_000;

/** Returns uploads that were interrupted to the queue. */
export async function requeueInterruptedUploads(
  profileId: string | null,
  olderThanMs: number = ABANDONED_UPLOAD_MS,
  now: number = Date.now(),
): Promise<number> {
  const stuck = (await listMutations(profileId)).filter(
    (row) =>
      row.state === 'uploading' &&
      now - Date.parse(row.updatedAt) >= olderThanMs,
  );
  for (const row of stuck) {
    await transitionMutation(row.mutationId, 'queued');
  }
  return stuck.length;
}

/**
 * Returns a conflicted or blocked mutation to the queue after a person has
 * acted on it.
 */
export async function retryMutation(
  mutationId: string,
): Promise<MutationEnvelope> {
  return transitionMutation(mutationId, 'queued', { error: null });
}

interface OutboxCounts {
  queued: number;
  uploading: number;
  blocked: number;
  conflict: number;
  needsAction: number;
  accepted: number;
}

export async function outboxCounts(
  profileId: string | null,
): Promise<OutboxCounts> {
  const all = await listMutations(profileId);
  const blocked = await blockedMutations(profileId);
  const blockedIds = new Set(blocked.map((row) => row.mutationId));

  return {
    queued: all.filter(
      (row) => row.state === 'queued' && !blockedIds.has(row.mutationId),
    ).length,
    uploading: all.filter((row) => row.state === 'uploading').length,
    blocked: blocked.length,
    conflict: all.filter((row) => row.state === 'conflict').length,
    needsAction: all.filter((row) => row.state === 'needs_action').length,
    accepted: all.filter((row) => row.state === 'accepted').length,
  };
}
