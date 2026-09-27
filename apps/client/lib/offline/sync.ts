import { type ApiErrorKind, apiRequest } from '@/lib/api/client';

import { deriveKey } from '@/lib/ids';

import { getOfflineDatabase } from './db';
import {
  ABANDONED_UPLOAD_MS,
  getMutation,
  readyMutations,
  requeueInterruptedUploads,
  transitionMutation,
} from './outbox';
import type { MutationEnvelope, MutationType } from './types';

/** The push half of synchronisation. */

/** Reads a path segment out of a mutation body. */
function pathParam(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(
      `This queued change is missing "${key}" and cannot be sent.`,
    );
  }
  return encodeURIComponent(value);
}

/** Where each API mutation type is sent, and how its body becomes a request. */
const ROUTES: Record<
  Exclude<MutationType, 'incident.attachment.upload'>,
  (body: Record<string, unknown>) => {
    path: string;
    payload: Record<string, unknown>;
  }
> = {
  'incident.create': (body) => ({ path: '/v1/incidents', payload: body }),
  'incident.attachment.complete': (body) => ({
    path: `/v1/incidents/${pathParam(body, 'incident_id')}/attachments/${pathParam(body, 'attachment_id')}/complete`,
    payload: { sha256: body.sha256, size_bytes: body.size_bytes },
  }),
  'incident.review': (body) => ({
    path: `/v1/incidents/${pathParam(body, 'incident_id')}/review`,
    payload: {
      decision: body.decision,
      reason: body.reason,
      expected_version: body.expected_version,
      affected_segment_ids: body.affected_segment_ids ?? [],
    },
  }),
  'inspection.accept': (body) => ({
    path: `/v1/inspections/${pathParam(body, 'inspection_id')}/accept`,
    payload: {},
  }),
  'inspection.start': (body) => ({
    path: `/v1/inspections/${pathParam(body, 'inspection_id')}/start`,
    payload: {},
  }),
  'inspection.complete': (body) => ({
    path: `/v1/inspections/${pathParam(body, 'inspection_id')}/complete`,
    payload: {
      note: body.note ?? null,
      result_incident_id: body.result_incident_id ?? null,
    },
  }),
};

export type PushOutcome =
  | { mutationId: string; state: 'accepted'; replayed: boolean }
  | {
      mutationId: string;
      state: 'queued';
      reason: string;
      sessionRefused?: boolean;
    }
  | { mutationId: string; state: 'conflict'; reason: string }
  | { mutationId: string; state: 'needs_action'; reason: string };

export interface PushSummary {
  attempted: number;
  accepted: number;
  retryable: number;
  conflicts: number;
  needsAction: number;
  outcomes: PushOutcome[];
}

interface ApiFailure {
  status: number | null;
  code: string | null;
  kind: ApiErrorKind | null;
  message: string;
}

function describeFailure(error: unknown): ApiFailure {
  if (error && typeof error === 'object') {
    const candidate = error as {
      status?: number | null;
      code?: string;
      kind?: ApiErrorKind;
      message?: string;
    };
    return {
      status: typeof candidate.status === 'number' ? candidate.status : null,
      code: typeof candidate.code === 'string' ? candidate.code : null,
      kind: candidate.kind ?? null,
      message: candidate.message ?? 'The request failed.',
    };
  }
  return {
    status: null,
    code: null,
    kind: null,
    message: 'The request failed.',
  };
}

/** Decides what a failure means for the queue. */
export function classifyFailure(failure: ApiFailure): PushOutcome['state'] {
  const { status, kind } = failure;

  // The transport never reached a verdict, so the server may or may not hold
  // this write. Asking again with the same key is the only way to find out.
  if (kind === 'network' || kind === 'unavailable') return 'queued';
  // A refused session says nothing about the write.
  if (kind === 'session') return 'queued';
  if (kind === 'configuration') return 'needs_action';

  if (status === null) return 'queued';
  if (status === 409) return 'conflict';
  // 408 and 429 explicitly mean "ask again"; every other 4xx needs a person.
  if (status === 408 || status === 429) return 'queued';
  if (status >= 400 && status < 500) return 'needs_action';
  return 'queued';
}

/** Where evidence bytes go, as the API issued it. */
interface UploadTarget {
  incidentId: string;
  attachmentId: string;
  bucket: string;
  path: string;
  expiresAt: string;
}

export type UploadObjectResult =
  | { ok: true }
  /**
   * `exists`: the object is already at that path, an earlier attempt landed and
   * only its answer was lost.
   */
  | { ok: false; kind: 'exists' | 'refused' | 'network'; message: string };

export type UploadObject = (request: {
  bucket: string;
  path: string;
  blob: Blob;
  contentType: string;
}) => Promise<UploadObjectResult>;

interface PushOptions {
  accessToken: string;
  profileId: string | null;
  /** Injected so tests can drive the loop without a network. */
  send?: typeof apiRequest;
  /** Writes evidence to storage. Without it, uploads wait rather than fail. */
  uploadObject?: UploadObject;
  /** Injected so tests can move time past an upload window. */
  now?: () => Date;
  signal?: AbortSignal;
}

/** Pushes every ready mutation once, in dependency order. */
let running: Promise<PushSummary> | null = null;
let requestedWhileRunning = false;

/**
 * One sender across every tab of this origin, where the browser has Web Locks.
 */
async function withOutboxLock<T>(
  work: (exclusive: boolean) => Promise<T>,
): Promise<T> {
  const locks =
    typeof navigator === 'undefined'
      ? undefined
      : (navigator as Navigator & { locks?: LockManager }).locks;
  if (!locks?.request) return work(false);
  return locks.request('rasta-outbox', { mode: 'exclusive' }, () =>
    work(true),
  ) as Promise<T>;
}

/** One push at a time. */
export async function pushOutbox(options: PushOptions): Promise<PushSummary> {
  if (running) {
    requestedWhileRunning = true;
    return running;
  }
  running = withOutboxLock(async (exclusive) => {
    // With the lock held no other tab is sending, so anything still marked as
    // being sent was left by a tab or app that closed mid-request.
    await requeueInterruptedUploads(
      options.profileId,
      exclusive ? 0 : ABANDONED_UPLOAD_MS,
    );
    const summary = await pushPass(options);
    while (requestedWhileRunning && !options.signal?.aborted) {
      requestedWhileRunning = false;
      const next = await pushPass(options);
      summary.attempted += next.attempted;
      summary.accepted += next.accepted;
      summary.retryable += next.retryable;
      summary.conflicts += next.conflicts;
      summary.needsAction += next.needsAction;
      summary.outcomes.push(...next.outcomes);
    }
    return summary;
  });
  try {
    return await running;
  } finally {
    running = null;
    requestedWhileRunning = false;
  }
}

async function pushPass(options: PushOptions): Promise<PushSummary> {
  const send = options.send ?? apiRequest;
  const summary: PushSummary = {
    attempted: 0,
    accepted: 0,
    retryable: 0,
    conflicts: 0,
    needsAction: 0,
    outcomes: [],
  };

  // Guard against a dependency cycle or a mutation that keeps re-queuing:
  // each row is attempted at most once per pass.
  const attempted = new Set<string>();

  for (;;) {
    if (options.signal?.aborted) break;

    const ready = (await readyMutations(options.profileId)).filter(
      (row) => !attempted.has(row.mutationId),
    );
    if (ready.length === 0) break;

    const mutation = ready[0];
    attempted.add(mutation.mutationId);
    summary.attempted += 1;

    const outcome = await pushOne(mutation, { ...options, send });
    summary.outcomes.push(outcome);

    if (outcome.state === 'accepted') summary.accepted += 1;
    else if (outcome.state === 'queued') summary.retryable += 1;
    else if (outcome.state === 'conflict') summary.conflicts += 1;
    else summary.needsAction += 1;

    // Every other row would be refused with the same token. Stop, and let the
    // next session start the next pass.
    if (outcome.state === 'queued' && outcome.sessionRefused) break;
  }

  return summary;
}

/** Refuses a mutation before it reaches the network, with a reason a person can act on. */
async function refuse(
  mutation: MutationEnvelope,
  reason: string,
): Promise<PushOutcome> {
  await transitionMutation(mutation.mutationId, 'uploading');
  await transitionMutation(mutation.mutationId, 'needs_action', {
    error: reason,
  });
  return { mutationId: mutation.mutationId, state: 'needs_action', reason };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : null;
}

/** A stored id, or '' when the field is missing or not a string. */
function idField(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/** The upload target the API issued for one photo of a report it accepted. */
function targetFromCreate(
  createResult: Record<string, unknown> | null,
  localId: unknown,
): UploadTarget | null {
  const incident = asRecord(createResult?.incident);
  const instructions = Array.isArray(createResult?.upload_instructions)
    ? (createResult?.upload_instructions as unknown[])
    : [];
  const instruction = instructions
    .map(asRecord)
    .find((candidate) => candidate?.local_id === localId);
  return targetFromInstruction(incident?.id, instruction ?? null);
}

function targetFromInstruction(
  incidentId: unknown,
  instruction: Record<string, unknown> | null,
): UploadTarget | null {
  if (
    typeof incidentId !== 'string' ||
    !instruction ||
    typeof instruction.attachment_id !== 'string' ||
    typeof instruction.path !== 'string'
  ) {
    return null;
  }
  return {
    incidentId,
    attachmentId: instruction.attachment_id,
    bucket:
      typeof instruction.bucket === 'string' ? instruction.bucket : 'evidence',
    path: instruction.path,
    expiresAt:
      typeof instruction.expires_at === 'string' ? instruction.expires_at : '',
  };
}

/** Fills in ids a mutation could not know when it was queued. */
async function resolveReferences(
  mutation: MutationEnvelope,
): Promise<Record<string, unknown>> {
  const body = mutation.body;
  if (
    mutation.type === 'incident.attachment.complete' &&
    body.upload_mutation_id
  ) {
    const upload = await getMutation(idField(body.upload_mutation_id));
    const target = asRecord(upload?.result);
    if (upload?.state !== 'accepted' || !target) {
      throw new Error('The photo for this report has not been uploaded yet.');
    }
    return {
      ...body,
      incident_id: target.incident_id,
      attachment_id: target.attachment_id,
    };
  }
  return body;
}

async function pushOne(
  mutation: MutationEnvelope,
  options: PushOptions & { send: typeof apiRequest },
): Promise<PushOutcome> {
  if (mutation.type === 'incident.attachment.upload') {
    return pushUpload(mutation, options);
  }

  const route = ROUTES[mutation.type];
  if (!route) {
    return refuse(mutation, `This app version cannot send "${mutation.type}".`);
  }

  let path: string;
  let payload: Record<string, unknown>;
  try {
    ({ path, payload } = route(await resolveReferences(mutation)));
  } catch (error) {
    const reason =
      error instanceof Error
        ? error.message
        : 'This queued change cannot be sent.';
    await transitionMutation(mutation.mutationId, 'uploading');
    await transitionMutation(mutation.mutationId, 'needs_action', {
      error: reason,
    });
    return { mutationId: mutation.mutationId, state: 'needs_action', reason };
  }

  await transitionMutation(mutation.mutationId, 'uploading', {
    countAttempt: true,
  });

  try {
    const { payload: result } = await options.send(path, {
      accessToken: options.accessToken,
      method: 'POST',
      body: payload,
      // The original key, always. This is what makes a retry safe.
      idempotencyKey: mutation.idempotencyKey,
      signal: options.signal,
    });

    const replayed =
      typeof result === 'object' &&
      result !== null &&
      (result as { replayed?: boolean }).replayed === true;

    await transitionMutation(mutation.mutationId, 'accepted', {
      error: null,
      result: (result ?? null) as Record<string, unknown> | null,
    });
    if (mutation.type === 'incident.attachment.complete') {
      await releaseVerifiedMedia(mutation, result);
    }
    return { mutationId: mutation.mutationId, state: 'accepted', replayed };
  } catch (error) {
    const failure = describeFailure(error);
    const state = classifyFailure(failure);
    await transitionMutation(mutation.mutationId, state, {
      error: failure.message,
    });
    return {
      mutationId: mutation.mutationId,
      state,
      reason: failure.message,
      ...(failure.kind === 'session' ? { sessionRefused: true } : {}),
    } as PushOutcome;
  }
}

/**
 * Deletes a photo's bytes from the device once the server holds a verified
 * copy.
 */
async function releaseVerifiedMedia(
  mutation: MutationEnvelope,
  result: unknown,
): Promise<void> {
  const status = asRecord(asRecord(result)?.verification)?.status;
  if (status !== 'verified' && status !== 'already_verified') return;
  const uploadId = mutation.body.upload_mutation_id;
  if (typeof uploadId !== 'string') return;
  const upload = await getMutation(uploadId);
  const mediaId = upload?.body.media_id;
  const db = getOfflineDatabase();
  if (db && typeof mediaId === 'string') await db.media.delete(mediaId);
}

/** An upload target is refused by the bucket once its window closes. */
const RENEW_BEFORE_EXPIRY_MS = 60_000;

/** Writes one photo's bytes to the path the API issued for it. */
async function pushUpload(
  mutation: MutationEnvelope,
  options: PushOptions & { send: typeof apiRequest },
): Promise<PushOutcome> {
  const body = mutation.body;
  if (!options.uploadObject) {
    // Nothing here can write to storage (a test, or storage not configured).
    // Waiting is correct: the photo is still on the device.
    return {
      mutationId: mutation.mutationId,
      state: 'queued',
      reason: 'Storage is not available.',
    };
  }

  const create = await getMutation(idField(body.create_mutation_id));
  const renewed = asRecord(asRecord(mutation.result)?.renewed_target);
  let target: UploadTarget | null = renewed
    ? targetFromInstruction(renewed.incidentId, {
        attachment_id: renewed.attachmentId,
        path: renewed.path,
        bucket: renewed.bucket,
        expires_at: renewed.expiresAt,
      })
    : targetFromCreate(asRecord(create?.result), body.local_id);
  if (create?.state !== 'accepted' || !target) {
    return refuse(
      mutation,
      'The report this photo belongs to was not recorded, so there is nowhere to upload it.',
    );
  }

  const db = getOfflineDatabase();
  const media = db ? await db.media.get(idField(body.media_id)) : undefined;
  if (!media) {
    return refuse(
      mutation,
      'The photo is no longer stored on this device, so it cannot be uploaded.',
    );
  }

  await transitionMutation(mutation.mutationId, 'uploading', {
    countAttempt: true,
  });
  const now = options.now?.() ?? new Date();

  const expiresAt = Date.parse(target.expiresAt);
  if (
    Number.isFinite(expiresAt) &&
    expiresAt - now.getTime() < RENEW_BEFORE_EXPIRY_MS
  ) {
    try {
      const { payload } = await options.send(
        `/v1/incidents/${encodeURIComponent(target.incidentId)}/attachments`,
        {
          accessToken: options.accessToken,
          method: 'POST',
          body: {
            local_id: body.local_id,
            mime_type: media.mimeType,
            bytes: media.sizeBytes,
            sha256: media.sha256,
            captured_at: media.createdAt,
          },
          idempotencyKey: deriveKey(
            mutation.idempotencyKey,
            `renew:${target.attachmentId}`,
          ),
          signal: options.signal,
        },
      );
      const fresh = targetFromInstruction(
        target.incidentId,
        asRecord(asRecord(payload)?.upload_instruction),
      );
      if (!fresh)
        throw new Error('The server did not issue a new upload target.');
      target = fresh;
    } catch (error) {
      const failure = describeFailure(error);
      const state = classifyFailure(failure);
      await transitionMutation(mutation.mutationId, state, {
        error: failure.message,
      });
      return {
        mutationId: mutation.mutationId,
        state,
        reason: failure.message,
      } as PushOutcome;
    }
  }

  const written = await options.uploadObject({
    bucket: target.bucket,
    path: target.path,
    blob: new Blob([new Uint8Array(media.bytes)], { type: media.mimeType }),
    contentType: media.mimeType,
  });

  if (written.ok || written.kind === 'exists') {
    await transitionMutation(mutation.mutationId, 'accepted', {
      error: null,
      result: {
        incident_id: target.incidentId,
        attachment_id: target.attachmentId,
        bucket: target.bucket,
        path: target.path,
        already_present: !written.ok,
      },
    });
    return {
      mutationId: mutation.mutationId,
      state: 'accepted',
      replayed: !written.ok,
    };
  }

  // Keep a renewed target, so the next attempt uses it instead of renewing again.
  const keep = { renewed_target: target } as Record<string, unknown>;
  const state = written.kind === 'network' ? 'queued' : 'needs_action';
  await transitionMutation(mutation.mutationId, state, {
    error: written.message,
    result: keep,
  });
  return { mutationId: mutation.mutationId, state, reason: written.message };
}
