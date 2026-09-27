import { describe, expect, it } from 'vitest';

import type { HazardReport } from '../types';
import type { PhotoBytes, PhotoReadResult } from './evidenceUpload';
import type { ApiResult, UploadInstruction } from './rastaApi';
import {
  MAX_PHOTO_BYTES,
  classifyStorageFailure,
  deriveKey,
  isRetryable,
  localPhotoId,
  migrateLegacyReports,
  newFiling,
  pendingWork,
  sendReport,
  summarise,
  type IncidentBody,
  type OutboxDeps,
  type UploadOutcome,
} from './reportOutbox';

const NOW = new Date('2026-09-26T10:00:00Z');
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const INCIDENT = 'a2600002-0000-4000-8000-00000000cafe';
const SHA = 'a'.repeat(64);

function photo(overrides: Partial<PhotoBytes> = {}): PhotoBytes {
  return {
    bytes: new Uint8Array([1, 2, 3]),
    sha256: SHA,
    sizeBytes: 2_000,
    mimeType: 'image/jpeg',
    ...overrides,
  };
}

function report(overrides: Partial<HazardReport> = {}): HazardReport {
  return {
    id: 'rpt-1',
    category: 'landslide',
    categoryLabel: 'Landslide',
    corridorCode: 'NH-6',
    locationName: 'km 42 near Sonapur',
    latitude: 25.5788,
    longitude: 91.8933,
    severity: 'high',
    notes: 'Debris across both lanes',
    photoUri: 'file:///cache/photo.jpg',
    reportedAt: '2026-09-26T09:58:00.000Z',
    capturedAtIso: '2026-09-26T09:57:30.000Z',
    accuracyMeters: 8,
    positionSource: 'device_gps',
    syncStatus: 'queued',
    idempotencyKey: '11111111-1111-4111-8111-111111111111',
    offlineRecorded: false,
    filing: newFiling('11111111-1111-4111-8111-111111111111'),
    ...overrides,
  };
}

function instruction(
  attachmentId: string,
  expiresAt: string,
): UploadInstruction {
  return {
    attachment_id: attachmentId,
    local_id: localPhotoId({ id: 'rpt-1' }),
    bucket: 'evidence',
    path: `org/${INCIDENT}/${attachmentId}/photo.jpg`,
    max_bytes: MAX_PHOTO_BYTES,
    allowed_mime_types: ['image/jpeg'],
    expires_at: expiresAt,
  };
}

const OPEN = new Date(NOW.getTime() + 15 * 60_000).toISOString();
const CLOSED = new Date(NOW.getTime() - 60_000).toISOString();

function fail(status: number | null, reason = 'no'): ApiResult<never> {
  return {
    ok: false,
    status,
    code: status === null ? 'network_error' : 'refused',
    reason,
  };
}

type Call = { step: string; key?: string; body?: unknown; path?: string };

/** A scripted API: each step answers from its queue, or with a default. */
function fakeApi(
  script: {
    create?: Awaited<ReturnType<OutboxDeps['createIncident']>>[];
    add?: Awaited<ReturnType<OutboxDeps['addAttachment']>>[];
    complete?: Awaited<ReturnType<OutboxDeps['completeAttachment']>>[];
    read?: PhotoReadResult[];
    upload?: UploadOutcome[];
    now?: Date;
  } = {},
) {
  const calls: Call[] = [];
  const next = <T>(queue: T[] | undefined, fallback: T): T =>
    queue?.shift() ?? fallback;
  const deps: OutboxDeps = {
    async createIncident(body: IncidentBody, key: string) {
      calls.push({ step: 'create', key, body });
      return next(script.create, {
        ok: true,
        data: {
          incident: {
            id: INCIDENT,
            status: 'submitted',
            version: 1,
            district_id: 'd',
          },
          upload_instructions: body.attachments
            ? [instruction('att-1', OPEN)]
            : [],
          suggested_segments: [],
          replayed: false,
        },
      });
    },
    async addAttachment(incidentId, body, key) {
      calls.push({ step: 'add', key, body });
      return next(script.add, {
        ok: true,
        data: {
          attachment: { id: 'att-2', upload_status: 'pending' },
          upload_instruction: instruction('att-2', OPEN),
          replayed: false,
        },
      });
    },
    async completeAttachment(incidentId, attachmentId, body, key) {
      calls.push({ step: 'complete', key, body, path: attachmentId });
      return next(script.complete, {
        ok: true,
        data: {
          attachment: { id: attachmentId, upload_status: 'verified' },
          verification: { status: 'verified', reason: null },
          replayed: false,
        },
      });
    },
    async readPhoto() {
      calls.push({ step: 'read' });
      return next(script.read, { ok: true, photo: photo() });
    },
    async uploadPhoto(path) {
      calls.push({ step: 'upload', path });
      return next(script.upload, { ok: true });
    },
    now: () => script.now ?? NOW,
  };
  return { deps, calls, steps: () => calls.map((call) => call.step) };
}

describe('derived keys', () => {
  it('are UUIDs, because the API refuses any other key', () => {
    const key = deriveKey(
      '11111111-1111-4111-8111-111111111111',
      'complete:att-1',
    );
    expect(key).toMatch(UUID);
    expect(key[14]).toBe('8');
    // The same value is pinned in apps/client/lib/ids.test.ts: both clients
    // derive the same key for the same step.
    expect(key).toBe('3625921c-929c-8276-a704-326336827cd5');
  });

  it('are the same every time for the same step, and differ between steps', () => {
    const base = '11111111-1111-4111-8111-111111111111';
    expect(deriveKey(base, 'renew:att-1')).toBe(deriveKey(base, 'renew:att-1'));
    expect(deriveKey(base, 'renew:att-1')).not.toBe(
      deriveKey(base, 'renew:att-2'),
    );
    expect(deriveKey(base, 'renew:att-1')).not.toBe(
      deriveKey(base, 'complete:att-1'),
    );
    expect(
      deriveKey('22222222-2222-4222-8222-222222222222', 'renew:att-1'),
    ).not.toBe(deriveKey(base, 'renew:att-1'));
  });
});

describe('filing a report', () => {
  it('is only marked sent once the API has accepted it, and the photo only once verified', async () => {
    const api = fakeApi();
    const sent = await sendReport(report(), api.deps);

    expect(api.steps()).toEqual([
      'read',
      'create',
      'read',
      'upload',
      'complete',
    ]);
    expect(sent.syncStatus).toBe('synced');
    expect(sent.controlRoomIncidentId).toBe(INCIDENT);
    expect(sent.evidenceStatus).toBe('verified');
    expect(pendingWork(sent)).toBeNull();
    // The bytes go to the path the API issued, nowhere else.
    expect(api.calls.find((call) => call.step === 'upload')?.path).toBe(
      `org/${INCIDENT}/att-1/photo.jpg`,
    );
    expect(api.calls.find((call) => call.step === 'complete')?.key).toBe(
      deriveKey(sent.filing!.key, 'complete:att-1'),
    );
  });

  it('files with the position, its source and accuracy, and the declared photo', async () => {
    const api = fakeApi();
    await sendReport(
      report({ positionSource: 'manual_pin', accuracyMeters: 0 }),
      api.deps,
    );
    const body = api.calls[1].body as IncidentBody;
    expect(body.location).toEqual({ latitude: 25.5788, longitude: 91.8933 });
    expect(body.location_source).toBe('manual_pin');
    // Zero is not an accuracy; the API would refuse it, so none is claimed.
    expect(body.accuracy_m).toBeNull();
    expect(body.type).toBe('landslide_debris');
    expect(body.note).toBe('km 42 near Sonapur: Debris across both lanes');
    expect(body.attachments).toEqual([
      {
        local_id: 'photo-rpt-1',
        mime_type: 'image/jpeg',
        bytes: 2_000,
        sha256: SHA,
        captured_at: '2026-09-26T09:57:30.000Z',
      },
    ]);
  });

  it('keeps a report it could not deliver, and sends the identical request next time', async () => {
    const api = fakeApi({
      create: [fail(null, 'The control room could not be reached.')],
    });
    const first = await sendReport(report(), api.deps);
    expect(first.syncStatus).toBe('queued');
    expect(first.filing?.lastError).toBe(
      'The control room could not be reached.',
    );
    expect(first.filing?.attempts).toBe(1);
    expect(first.filing?.photoDecided).toBe(true);
    expect(pendingWork(first)).toBe('file');

    const second = await sendReport(first, api.deps);
    expect(second.syncStatus).toBe('synced');
    const creates = api.calls.filter((call) => call.step === 'create');
    expect(creates).toHaveLength(2);
    // Same key and same body: if the first request did arrive and only its
    // answer was lost, the server replays it instead of filing a second report.
    expect(creates[1].key).toBe(creates[0].key);
    expect(JSON.stringify(creates[1].body)).toBe(
      JSON.stringify(creates[0].body),
    );
    // The photo was measured once, for the declaration, and read again only
    // for its bytes.
    expect(api.steps().filter((step) => step === 'read')).toHaveLength(2);
  });

  it('waits for a person when the control room refuses it on the merits', async () => {
    const api = fakeApi({
      create: [fail(422, 'location is outside every district you cover')],
    });
    const refused = await sendReport(report(), api.deps);
    expect(refused.syncStatus).toBe('failed');
    expect(refused.filing?.lastError).toBe(
      'location is outside every district you cover',
    );
    expect(pendingWork(refused)).toBeNull();
  });

  it('keeps trying after an expired session, which a sign-in fixes', async () => {
    const api = fakeApi({ create: [fail(401, 'The session has expired.')] });
    expect((await sendReport(report(), api.deps)).syncStatus).toBe('queued');
    expect(isRetryable({ status: 429 })).toBe(true);
    expect(isRetryable({ status: 503 })).toBe(true);
    expect(isRetryable({ status: 403 })).toBe(false);
    expect(isRetryable({ status: 409 })).toBe(false);
  });

  it('never files a report without a position', async () => {
    const api = fakeApi();
    const refused = await sendReport(
      report({ latitude: Number.NaN }),
      api.deps,
    );
    expect(api.calls).toHaveLength(0);
    expect(refused.syncStatus).toBe('failed');
    expect(refused.filing?.lastError).toMatch(/no position/);
  });

  it('files without the photo, and says so, when the photo cannot be read', async () => {
    const api = fakeApi({ read: [{ ok: false, reason: 'file not found' }] });
    const sent = await sendReport(report(), api.deps);
    expect((api.calls[1].body as IncidentBody).attachments).toBeUndefined();
    expect(sent.syncStatus).toBe('synced');
    expect(sent.evidenceStatus).toBe('failed');
    expect(sent.filing?.evidenceError).toMatch(
      /could not be read.*file not found/,
    );
    expect(pendingWork(sent)).toBeNull();
  });

  it('files without a photo over the size the API accepts', async () => {
    const api = fakeApi({
      read: [{ ok: true, photo: photo({ sizeBytes: MAX_PHOTO_BYTES + 1 }) }],
    });
    const sent = await sendReport(report(), api.deps);
    expect((api.calls[1].body as IncidentBody).attachments).toBeUndefined();
    expect(sent.evidenceStatus).toBe('failed');
    expect(sent.filing?.evidenceError).toMatch(/over the 5 MB/);
  });

  it('files a report with no photo as having none', async () => {
    const api = fakeApi();
    const sent = await sendReport(report({ photoUri: undefined }), api.deps);
    expect(api.steps()).toEqual(['create']);
    expect(sent.evidenceStatus).toBe('none');
  });

  it('never throws, whatever a dependency does', async () => {
    const api = fakeApi();
    api.deps.createIncident = async () => {
      throw new Error('socket hang up');
    };
    const kept = await sendReport(report(), api.deps);
    expect(kept.syncStatus).toBe('queued');
    expect(kept.filing?.lastError).toBe('socket hang up');
  });
});

describe('sending the photo', () => {
  it('resumes the upload on a later pass without filing the report again', async () => {
    const api = fakeApi({
      upload: [
        { ok: false, kind: 'network', reason: 'Network request failed' },
      ],
    });
    const first = await sendReport(report(), api.deps);
    expect(first.syncStatus).toBe('synced');
    expect(first.evidenceStatus).toBe('not_sent');
    expect(first.filing?.evidenceError).toBe('Network request failed');
    expect(pendingWork(first)).toBe('evidence');

    const second = await sendReport(first, api.deps);
    expect(second.evidenceStatus).toBe('verified');
    expect(api.steps().filter((step) => step === 'create')).toHaveLength(1);
  });

  it('asks for a fresh path when the first one closed while the phone was offline', async () => {
    const api = fakeApi({ create: [], now: NOW });
    const filed = await sendReport(report(), {
      ...api.deps,
      uploadPhoto: async () => ({
        ok: false,
        kind: 'network',
        reason: 'offline',
      }),
    });
    const stale = {
      ...filed,
      filing: {
        ...filed.filing!,
        target: { ...filed.filing!.target!, expiresAt: CLOSED },
      },
    };

    const sent = await sendReport(stale, api.deps);
    const add = api.calls.find((call) => call.step === 'add');
    expect(add?.key).toBe(deriveKey(filed.filing!.key, 'renew:att-1'));
    expect(add?.key).toMatch(UUID);
    expect(
      api.calls.filter((call) => call.step === 'upload').at(-1)?.path,
    ).toBe(`org/${INCIDENT}/att-2/photo.jpg`);
    expect(sent.evidenceStatus).toBe('verified');
    expect(sent.filing?.target?.attachmentId).toBe('att-2');
  });

  it('renews again when a replayed renewal hands back a path that has closed', async () => {
    const api = fakeApi({
      add: [
        {
          ok: true,
          data: {
            attachment: { id: 'att-2', upload_status: 'pending' },
            upload_instruction: instruction('att-2', CLOSED),
            replayed: true,
          },
        },
        {
          ok: true,
          data: {
            attachment: { id: 'att-3', upload_status: 'pending' },
            upload_instruction: instruction('att-3', OPEN),
            replayed: false,
          },
        },
      ],
    });
    const stale = report({
      syncStatus: 'synced',
      controlRoomIncidentId: INCIDENT,
      evidenceStatus: 'not_sent',
      filing: {
        ...newFiling('11111111-1111-4111-8111-111111111111'),
        photoDecided: true,
        photo: { sha256: SHA, sizeBytes: 2_000, mimeType: 'image/jpeg' },
        target: {
          attachmentId: 'att-1',
          path: 'p1',
          expiresAt: CLOSED,
          maxBytes: MAX_PHOTO_BYTES,
        },
      },
    });
    const sent = await sendReport(stale, api.deps);
    const keys = api.calls
      .filter((call) => call.step === 'add')
      .map((call) => call.key);
    expect(keys).toEqual([
      deriveKey(stale.filing!.key, 'renew:att-1'),
      deriveKey(stale.filing!.key, 'renew:att-2'),
    ]);
    expect(sent.filing?.target?.attachmentId).toBe('att-3');
    expect(sent.evidenceStatus).toBe('verified');
  });

  it('carries on when an earlier attempt already wrote the bytes', async () => {
    const api = fakeApi({
      upload: [
        { ok: false, kind: 'exists', reason: 'The resource already exists' },
      ],
    });
    const sent = await sendReport(report(), api.deps);
    expect(sent.evidenceStatus).toBe('verified');
  });

  it('tries a fresh path after a refusal, a bounded number of times', async () => {
    const refused: UploadOutcome = {
      ok: false,
      kind: 'refused',
      reason: 'new row violates row-level security policy',
    };
    const api = fakeApi({ upload: [refused, refused, refused, refused] });
    let current = await sendReport(report(), api.deps);
    const states = [current.evidenceStatus];
    for (let pass = 0; pass < 4 && pendingWork(current); pass += 1) {
      current = await sendReport(current, api.deps);
      states.push(current.evidenceStatus);
    }
    expect(states).toEqual(['not_sent', 'not_sent', 'not_sent', 'failed']);
    expect(current.filing?.evidenceError).toMatch(/row-level security/);
    expect(pendingWork(current)).toBeNull();
  });

  it('does not count a photo the server rejected as evidence', async () => {
    const api = fakeApi({
      complete: [
        {
          ok: true,
          data: {
            attachment: { id: 'att-1', upload_status: 'rejected' },
            verification: { status: 'rejected', reason: 'checksum mismatch' },
            replayed: false,
          },
        },
      ],
    });
    const sent = await sendReport(report(), api.deps);
    expect(sent.syncStatus).toBe('synced');
    expect(sent.evidenceStatus).toBe('rejected');
    expect(sent.filing?.evidenceError).toMatch(/checksum mismatch/);
    expect(pendingWork(sent)).toBeNull();
  });

  it('uploads again when the server found nothing to check', async () => {
    const api = fakeApi({
      complete: [
        {
          ok: true,
          data: {
            attachment: { id: 'att-1', upload_status: 'pending' },
            verification: { status: 'missing', reason: null },
            replayed: false,
          },
        },
      ],
    });
    const first = await sendReport(report(), api.deps);
    expect(first.evidenceStatus).toBe('not_sent');
    expect(pendingWork(first)).toBe('evidence');
    const second = await sendReport(first, api.deps);
    expect(api.steps()).toContain('add');
    expect(second.evidenceStatus).toBe('verified');
  });

  it('stops if the photo changed on the phone after it was declared', async () => {
    const api = fakeApi({
      read: [
        { ok: true, photo: photo() },
        { ok: true, photo: photo({ sha256: 'b'.repeat(64) }) },
      ],
    });
    const sent = await sendReport(report(), api.deps);
    expect(sent.evidenceStatus).toBe('failed');
    expect(api.steps()).not.toContain('upload');
  });
});

describe('storage failures', () => {
  it('sorts them into exists, refused and retry', () => {
    expect(
      classifyStorageFailure({
        statusCode: '409',
        message: 'The resource already exists',
      }).ok,
    ).toBe(false);
    expect(
      classifyStorageFailure({ statusCode: '409', message: 'x' }),
    ).toMatchObject({ kind: 'exists' });
    expect(
      classifyStorageFailure({ status: 403, message: 'row-level security' }),
    ).toMatchObject({ kind: 'refused' });
    expect(
      classifyStorageFailure({ status: 429, message: 'slow down' }),
    ).toMatchObject({ kind: 'network' });
    expect(
      classifyStorageFailure(new TypeError('Network request failed')),
    ).toMatchObject({ kind: 'network' });
    expect(classifyStorageFailure(undefined)).toMatchObject({
      kind: 'network',
    });
  });
});

describe('what the outbox tells the person', () => {
  it('counts what was sent, what waits and what was refused', async () => {
    const waiting = report({
      id: 'rpt-2',
      filing: { ...newFiling('k2'), lastError: 'offline' },
    });
    const before = [
      report(),
      waiting,
      report({ id: 'rpt-3', syncStatus: 'failed' }),
    ];
    const after = [
      {
        ...before[0],
        syncStatus: 'synced' as const,
        evidenceStatus: 'verified' as const,
        controlRoomIncidentId: INCIDENT,
      },
      waiting,
      before[2],
    ];
    expect(summarise(before, after)).toEqual({
      sent: 1,
      waiting: 1,
      refused: 1,
      reason: 'offline',
    });
  });
});

describe('reports saved by earlier versions', () => {
  it('are not shown as sent unless the API gave them an incident', () => {
    const [legacyOnlyBroadcast, legacyFiled] = migrateLegacyReports([
      {
        id: 'hz-local-1',
        category: 'landslide',
        syncStatus: 'synced',
        trustBadge: 'Corroborated & synced (10:02)',
        corroborationCount: 2,
        riskScore: 88,
        reportedAt: 'Just now',
        idempotencyKey: 'idemp-1',
      },
      {
        id: 'hz-local-2',
        category: 'flash_flood',
        syncStatus: 'synced',
        controlRoomIncidentId: INCIDENT,
        capturedAtIso: '2026-09-20T08:00:00.000Z',
        reportedAt: 'Just now',
      },
      null,
    ]);

    expect(legacyOnlyBroadcast.syncStatus).toBe('failed');
    expect(legacyOnlyBroadcast.filing?.lastError).toMatch(/earlier version/);
    expect(legacyOnlyBroadcast).not.toHaveProperty('trustBadge');
    expect(legacyOnlyBroadcast).not.toHaveProperty('corroborationCount');
    expect(legacyOnlyBroadcast).not.toHaveProperty('riskScore');
    expect(Number.isNaN(Date.parse(legacyOnlyBroadcast.reportedAt))).toBe(
      false,
    );
    // Never re-sent under a guessed key.
    expect(pendingWork(legacyOnlyBroadcast)).toBeNull();

    expect(legacyFiled.syncStatus).toBe('synced');
    expect(legacyFiled.reportedAt).toBe('2026-09-20T08:00:00.000Z');
    expect(pendingWork(legacyFiled)).toBeNull();
  });
});
