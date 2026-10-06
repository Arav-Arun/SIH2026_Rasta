// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClientError } from '@/lib/api/client';
import { deriveKey } from '@/lib/ids';

import { RastaOfflineDatabase, setOfflineDatabase } from './db';
import {
  EMPTY_DRAFT,
  MAX_EVIDENCE_BYTES,
  type FieldReportDraft,
  discardReportDraft,
  loadReportDraft,
  missingForSubmit,
  queueFieldReport,
  saveReportDraft,
  sha256Hex,
  storeEvidence,
} from './field-report';
import { getMutation, listMutations } from './outbox';
import { pushOutbox, type UploadObject } from './sync';

const PROFILE = 'profile-officer';
const TOKEN = 'test-token';
let db: RastaOfflineDatabase;

beforeEach(async () => {
  db = new RastaOfflineDatabase(`rasta-report-${Math.random()}`);
  setOfflineDatabase(db);
  await db.open();
});

afterEach(async () => {
  await db.delete();
  setOfflineDatabase(null);
  vi.restoreAllMocks();
});

const PHOTO_BYTES = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5]);

function photo(): Blob {
  return new Blob([PHOTO_BYTES], { type: 'image/jpeg' });
}

function completeDraft(
  overrides: Partial<FieldReportDraft> = {},
): FieldReportDraft {
  return {
    ...EMPTY_DRAFT,
    type: 'landslide_debris',
    note: '  Debris across both lanes  ',
    latitude: 25.5788,
    longitude: 91.8933,
    locationSource: 'device_gps',
    accuracyMetres: 12,
    fixCapturedAt: '2026-09-26T05:00:00.000Z',
    ...overrides,
  };
}

async function draftWithPhoto(): Promise<FieldReportDraft> {
  const stored = await storeEvidence({
    blob: photo(),
    mimeType: 'image/jpeg',
    capturedAt: '2026-09-26T05:01:00.000Z',
    profileId: PROFILE,
  });
  if (!stored.ok) throw new Error(stored.reason);
  return completeDraft({ mediaId: stored.media.mediaId });
}

function createAccepted(expiresAt: string) {
  return {
    payload: {
      incident: { id: 'inc-1' },
      upload_instructions: [
        {
          attachment_id: 'att-1',
          local_id: 'LOCAL',
          path: 'org/inc-1/att-1/photo.jpg',
          bucket: 'evidence',
          expires_at: expiresAt,
        },
      ],
      suggested_segments: [],
    },
    requestId: 'req_create',
    status: 201,
  };
}

/** A server double: echoes the local id the client declared back in its instruction. */
function server(options: { expiresAt: string }) {
  let declaredLocalId = '';
  return vi.fn(
    async (path: string, init: { body?: unknown; idempotencyKey?: string }) => {
      const body = (init.body ?? {}) as Record<string, unknown>;
      if (path === '/v1/incidents') {
        const attachments = body.attachments as { local_id: string }[];
        declaredLocalId = attachments[0]?.local_id ?? '';
        const response = createAccepted(options.expiresAt);
        response.payload.upload_instructions[0].local_id = declaredLocalId;
        return response;
      }
      if (path === '/v1/incidents/inc-1/attachments') {
        return {
          payload: {
            attachment: { id: 'att-2' },
            upload_instruction: {
              attachment_id: 'att-2',
              local_id: declaredLocalId,
              path: 'org/inc-1/att-2/photo.jpg',
              bucket: 'evidence',
              expires_at: '2999-01-01T00:00:00.000Z',
            },
          },
          requestId: 'req_renew',
          status: 201,
        };
      }
      if (path.endsWith('/complete')) {
        return {
          payload: { verification: { status: 'verified', reason: null } },
          requestId: 'req_complete',
          status: 200,
        };
      }
      throw new Error(`unexpected ${path}`);
    },
  );
}

describe('evidence kept on the device', () => {
  it('measures the stored bytes, so the declared checksum is of what will be sent', async () => {
    const stored = await storeEvidence({
      blob: photo(),
      mimeType: 'image/jpeg',
      capturedAt: '2026-09-26T05:01:00.000Z',
      profileId: PROFILE,
    });
    expect(stored.ok).toBe(true);
    if (!stored.ok) return;
    expect(stored.media.sizeBytes).toBe(PHOTO_BYTES.length);
    expect(stored.media.sha256).toBe(await sha256Hex(new Blob([PHOTO_BYTES])));
    expect(stored.media.mutationId).toBeNull();
  });

  it('refuses what the server would refuse, before anything is queued', async () => {
    const base = { capturedAt: '2026-09-26T05:01:00.000Z', profileId: PROFILE };
    expect(
      await storeEvidence({ ...base, blob: photo(), mimeType: 'image/gif' }),
    ).toEqual({
      ok: false,
      reason: 'unsupported_type',
    });
    expect(
      await storeEvidence({
        ...base,
        blob: new Blob([]),
        mimeType: 'image/png',
      }),
    ).toEqual({
      ok: false,
      reason: 'empty',
    });
    const huge = new Blob([new Uint8Array(MAX_EVIDENCE_BYTES + 1)], {
      type: 'image/jpeg',
    });
    expect(
      await storeEvidence({ ...base, blob: huge, mimeType: 'image/jpeg' }),
    ).toEqual({
      ok: false,
      reason: 'too_large',
    });
  });
});

describe('the report draft', () => {
  it('survives a reload, and discarding it drops a photo that was never queued', async () => {
    const draft = await draftWithPhoto();
    await saveReportDraft(PROFILE, draft);

    const restored = await loadReportDraft(PROFILE);
    expect(restored).toEqual(draft);
    // Another person on the same browser does not see it.
    expect(await loadReportDraft('someone-else')).toBeNull();

    await discardReportDraft(PROFILE);
    expect(await loadReportDraft(PROFILE)).toBeNull();
    expect(await db.media.count()).toBe(0);
  });

  it('names what is missing rather than letting an incomplete report through', () => {
    expect(missingForSubmit(EMPTY_DRAFT)).toEqual(['type', 'location']);
    expect(missingForSubmit(completeDraft({ latitude: 95 }))).toEqual([
      'location',
    ]);
    expect(missingForSubmit(completeDraft({ locationSource: null }))).toEqual([
      'location',
    ]);
    expect(missingForSubmit(completeDraft())).toEqual([]);
  });
});

describe('queueing a report', () => {
  it('queues report, upload and verification as one chain and clears the draft', async () => {
    const draft = await draftWithPhoto();
    await saveReportDraft(PROFILE, draft);

    const queued = await queueFieldReport({ profileId: PROFILE, draft });

    const rows = await listMutations(PROFILE);
    expect(rows.map((row) => row.type)).toEqual([
      'incident.create',
      'incident.attachment.upload',
      'incident.attachment.complete',
    ]);
    const [create, upload, complete] = rows;
    expect(upload.dependsOn).toEqual([create.mutationId]);
    expect(complete.dependsOn).toEqual([upload.mutationId]);
    expect(queued.createMutationId).toBe(create.mutationId);

    const attachments = create.body.attachments as Record<string, unknown>[];
    const media = await db.media.get(draft.mediaId as string);
    expect(attachments[0]).toMatchObject({
      mime_type: 'image/jpeg',
      bytes: PHOTO_BYTES.length,
      sha256: media?.sha256,
    });
    expect(create.body.note).toBe('Debris across both lanes');
    expect(upload.body.media_id).toBe(draft.mediaId);
    expect(await loadReportDraft(PROFILE)).toBeNull();
  });

  it('records a map pin as a pin, with no accuracy it never measured', async () => {
    await queueFieldReport({
      profileId: PROFILE,
      draft: completeDraft({
        locationSource: 'manual_pin',
        accuracyMetres: 30,
      }),
    });
    const [create] = await listMutations(PROFILE);
    expect(create.body.location_source).toBe('manual_pin');
    expect(create.body.accuracy_m).toBeNull();
    expect(create.body.attachments).toEqual([]);
  });

  it('refuses an incomplete draft instead of queueing a report the server will reject', async () => {
    await expect(
      queueFieldReport({
        profileId: PROFILE,
        draft: completeDraft({ type: '' }),
      }),
    ).rejects.toThrow();
    expect(await listMutations(PROFILE)).toEqual([]);
  });
});

describe('sending a queued report with its photo', () => {
  it('files the report, uploads to the issued path and has the server verify it, in one pass', async () => {
    const draft = await draftWithPhoto();
    const declared = (await db.media.get(draft.mediaId as string))?.sha256;
    await queueFieldReport({ profileId: PROFILE, draft });

    const send = server({ expiresAt: '2999-01-01T00:00:00.000Z' });
    const uploads: string[] = [];
    const uploadObject: UploadObject = vi.fn(async ({ path, blob }) => {
      uploads.push(`${path}:${blob.size}`);
      return { ok: true } as const;
    });

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
      uploadObject,
    });

    expect(summary.accepted).toBe(3);
    expect(uploads).toEqual([
      `org/inc-1/att-1/photo.jpg:${PHOTO_BYTES.length}`,
    ]);
    const completion = send.mock.calls.find(([path]) =>
      path.endsWith('/complete'),
    );
    expect(completion?.[0]).toBe(
      '/v1/incidents/inc-1/attachments/att-1/complete',
    );
    expect(declared).toMatch(/^[a-f0-9]{64}$/);
    expect(completion?.[1].body).toEqual({
      sha256: declared,
      size_bytes: PHOTO_BYTES.length,
    });
    // Verified on the server, so the device copy is released.
    expect(await db.media.get(draft.mediaId as string)).toBeUndefined();
  });

  it('keeps the photo on the device when the server did not verify it', async () => {
    const draft = await draftWithPhoto();
    await queueFieldReport({ profileId: PROFILE, draft });
    const verifying = server({ expiresAt: '2999-01-01T00:00:00.000Z' });
    const send = vi.fn(
      async (
        path: string,
        init: { body?: unknown; idempotencyKey?: string },
      ) => {
        if (path.endsWith('/complete')) {
          return {
            payload: {
              verification: {
                status: 'rejected',
                reason: 'Checksum does not match.',
              },
            },
            requestId: 'req_complete',
            status: 200,
          };
        }
        return verifying(path, init);
      },
    );
    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
      uploadObject: async () => ({ ok: true }),
    });
    expect(await db.media.get(draft.mediaId as string)).toBeDefined();
  });

  it('does not discard a queued photo when the (already cleared) draft is discarded', async () => {
    const draft = await draftWithPhoto();
    await queueFieldReport({ profileId: PROFILE, draft });
    await saveReportDraft(PROFILE, { ...EMPTY_DRAFT, mediaId: draft.mediaId });
    await discardReportDraft(PROFILE);
    expect(await db.media.get(draft.mediaId as string)).toBeDefined();
  });

  it('renews an upload target that expired while the phone was offline', async () => {
    const draft = await draftWithPhoto();
    await queueFieldReport({ profileId: PROFILE, draft });
    const send = server({ expiresAt: '2026-09-26T05:15:00.000Z' });
    const uploadObject: UploadObject = vi.fn(
      async () => ({ ok: true }) as const,
    );

    // The report reached the server, then the connection dropped for an hour.
    const offline: UploadObject = vi.fn(
      async () =>
        ({
          ok: false,
          kind: 'network',
          message: 'offline',
        }) as const,
    );
    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
      uploadObject: offline,
      now: () => new Date('2026-09-26T05:05:00.000Z'),
    });
    const [, upload] = await listMutations(PROFILE);
    expect((await getMutation(upload.mutationId))?.state).toBe('queued');

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
      uploadObject,
      now: () => new Date('2026-09-26T06:05:00.000Z'),
    });

    expect(summary.accepted).toBe(2);
    const renewal = send.mock.calls.find(
      ([path]) => path === '/v1/incidents/inc-1/attachments',
    );
    // The key is derived from the dead target, so a lost answer replays, not
    // repeats — and it is a UUID, the only kind of key the API accepts. (It
    // was `<key>.renew.att-1`, which the API refuses with a 400.)
    expect(renewal?.[1].idempotencyKey).toBe(
      deriveKey(upload.idempotencyKey, 'renew:att-1'),
    );
    expect(renewal?.[1].idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(uploadObject).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'org/inc-1/att-2/photo.jpg' }),
    );
    const completion = send.mock.calls.find(([path]) =>
      path.endsWith('/complete'),
    );
    expect(completion?.[0]).toBe(
      '/v1/incidents/inc-1/attachments/att-2/complete',
    );
  });

  it('treats an object already at the path as uploaded, and lets the server check it', async () => {
    const draft = await draftWithPhoto();
    await queueFieldReport({ profileId: PROFILE, draft });
    const send = server({ expiresAt: '2999-01-01T00:00:00.000Z' });
    const uploadObject: UploadObject = vi.fn(
      async () =>
        ({
          ok: false,
          kind: 'exists',
          message: 'The resource already exists',
        }) as const,
    );

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
      uploadObject,
    });
    expect(summary.accepted).toBe(3);
  });

  it('stops for a person when the bucket refuses, and holds the verification back', async () => {
    const draft = await draftWithPhoto();
    await queueFieldReport({ profileId: PROFILE, draft });
    const send = server({ expiresAt: '2999-01-01T00:00:00.000Z' });
    const uploadObject: UploadObject = vi.fn(
      async () =>
        ({
          ok: false,
          kind: 'refused',
          message: 'new row violates row-level security policy',
        }) as const,
    );

    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
      uploadObject,
    });

    const [create, upload, complete] = await listMutations(PROFILE);
    expect(create.state).toBe('accepted');
    expect(upload.state).toBe('needs_action');
    expect(upload.lastError).toContain('row-level security');
    expect(complete.state).toBe('queued');
    expect(send.mock.calls.some(([path]) => path.endsWith('/complete'))).toBe(
      false,
    );
  });

  it('waits, rather than failing, when this page cannot reach storage at all', async () => {
    const draft = await draftWithPhoto();
    await queueFieldReport({ profileId: PROFILE, draft });
    const send = server({ expiresAt: '2999-01-01T00:00:00.000Z' });

    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    const [create, upload] = await listMutations(PROFILE);
    expect(create.state).toBe('accepted');
    expect(upload.state).toBe('queued');
    expect(upload.attemptCount).toBe(0);
  });

  it('files a report without a photo as a single request', async () => {
    await queueFieldReport({ profileId: PROFILE, draft: completeDraft() });
    const send = vi.fn(async () => ({
      payload: { incident: { id: 'inc-9' }, upload_instructions: [] },
      requestId: 'r',
      status: 201,
    }));
    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });
    expect(summary.accepted).toBe(1);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('never sends a report twice when the answer to the first attempt was lost', async () => {
    await queueFieldReport({ profileId: PROFILE, draft: completeDraft() });
    const keys: string[] = [];
    const send = vi.fn(
      async (_path: string, init: { idempotencyKey?: string }) => {
        keys.push(init.idempotencyKey as string);
        if (keys.length === 1) {
          throw new ApiClientError({
            code: 'x',
            kind: 'network',
            message: 'lost',
            status: null,
          });
        }
        return {
          payload: { incident: { id: 'inc-9' }, replayed: true },
          requestId: 'r',
          status: 200,
        };
      },
    );
    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });
    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });
});
