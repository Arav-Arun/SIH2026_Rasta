// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiClientError } from '@/lib/api/client';

import { RastaOfflineDatabase, setOfflineDatabase } from './db';
import { enqueueMutation, getMutation, listMutations } from './outbox';
import { classifyFailure, pushOutbox } from './sync';

const PROFILE = 'profile-1';
const TOKEN = 'test-token';
let db: RastaOfflineDatabase;

beforeEach(async () => {
  db = new RastaOfflineDatabase(`rasta-sync-${Math.random()}`);
  setOfflineDatabase(db);
  await db.open();
});

afterEach(async () => {
  await db.delete();
  setOfflineDatabase(null);
  vi.restoreAllMocks();
});

function apiError(
  status: number | null,
  kind: ApiClientError['kind'],
  message: string,
) {
  return new ApiClientError({
    code: 'test_error',
    kind,
    message,
    status,
  });
}

function accepted(payload: Record<string, unknown> = {}) {
  return { payload, requestId: 'req_1', status: 200 };
}

describe('failure classification', () => {
  it('treats an unanswered request as retryable, because the server may hold it', () => {
    expect(
      classifyFailure({
        status: null,
        code: null,
        kind: 'network',
        message: '',
      }),
    ).toBe('queued');
    expect(
      classifyFailure({
        status: 503,
        code: null,
        kind: 'unavailable',
        message: '',
      }),
    ).toBe('queued');
    expect(
      classifyFailure({ status: 500, code: null, kind: 'http', message: '' }),
    ).toBe('queued');
    expect(
      classifyFailure({ status: 429, code: null, kind: 'http', message: '' }),
    ).toBe('queued');
  });

  it('stops retrying what the server rejected on its merits', () => {
    expect(
      classifyFailure({ status: 422, code: null, kind: 'http', message: '' }),
    ).toBe('needs_action');
    expect(
      classifyFailure({
        status: 403,
        code: null,
        kind: 'forbidden',
        message: '',
      }),
    ).toBe('needs_action');
  });

  it('keeps a write whose session was refused, because the write was never judged', () => {
    expect(
      classifyFailure({
        status: 401,
        code: null,
        kind: 'session',
        message: '',
      }),
    ).toBe('queued');
  });

  it('routes a version clash to conflict, which a person resolves', () => {
    expect(
      classifyFailure({ status: 409, code: null, kind: 'http', message: '' }),
    ).toBe('conflict');
  });
});

describe('push loop', () => {
  it('a report filed during a long spell offline survives an expired token', async () => {
    const first = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'filed at 09:00 with no signal' },
      profileId: PROFILE,
    });
    const second = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'filed at 10:30 with no signal' },
      profileId: PROFILE,
    });

    // Back in coverage at 11:00, with the token issued before 09:00.
    const expired = vi.fn(async () => {
      throw apiError(
        401,
        'session',
        'The session is invalid or could not be verified.',
      );
    });
    const refused = await pushOutbox({
      accessToken: 'expired',
      profileId: PROFILE,
      send: expired as never,
    });
    expect(expired).toHaveBeenCalledTimes(1); // the pass stops at the first refusal
    expect(refused.needsAction).toBe(0);
    expect((await getMutation(first.mutationId))?.state).toBe('queued');
    expect((await getMutation(second.mutationId))?.state).toBe('queued');

    // The session refreshes; the next pass sends both, nothing needed a person.
    const fresh = vi.fn(async () => accepted({ incident: { id: 'inc-1' } }));
    const sent = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: fresh as never,
    });
    expect(sent.accepted).toBe(2);
    expect((await getMutation(first.mutationId))?.state).toBe('accepted');
    expect((await getMutation(second.mutationId))?.state).toBe('accepted');
  });

  it('does not let one invalid mutation block the others', async () => {
    const bad = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'missing required fields' },
      profileId: PROFILE,
    });
    const good = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'fine' },
      profileId: PROFILE,
    });

    const send = vi.fn(async (_path: string, init: { body?: unknown }) => {
      const body = init.body as { note?: string };
      if (body?.note === 'missing required fields') {
        throw apiError(422, 'http', 'The report is missing a district.');
      }
      return accepted({ incident: { id: 'inc-1' } });
    });

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(summary.attempted).toBe(2);
    expect(summary.accepted).toBe(1);
    expect(summary.needsAction).toBe(1);

    expect((await getMutation(bad.mutationId))?.state).toBe('needs_action');
    expect((await getMutation(good.mutationId))?.state).toBe('accepted');
  });

  it('reuses the original idempotency key on a retry, so a duplicate cannot be filed', async () => {
    const mutation = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'flaky network' },
      profileId: PROFILE,
    });

    const seenKeys: string[] = [];
    const send = vi.fn(
      async (_path: string, init: { idempotencyKey?: string }) => {
        seenKeys.push(init.idempotencyKey as string);
        if (seenKeys.length === 1)
          throw apiError(null, 'network', 'Connection lost.');
        // The server recognises the key from the first attempt and replays it.
        return accepted({ incident: { id: 'inc-1' }, replayed: true });
      },
    );

    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });
    expect((await getMutation(mutation.mutationId))?.state).toBe('queued');

    const second = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[0]).toBe(seenKeys[1]);
    expect(seenKeys[0]).toBe(mutation.idempotencyKey);
    expect(second.outcomes[0]).toMatchObject({
      state: 'accepted',
      replayed: true,
    });
    expect((await getMutation(mutation.mutationId))?.attemptCount).toBe(2);
  });

  it('sends a dependent mutation in the same pass once its shell is accepted', async () => {
    const incident = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'landslide' },
      profileId: PROFILE,
    });
    await enqueueMutation({
      type: 'incident.attachment.complete',
      body: {
        incident_id: 'inc-1',
        attachment_id: 'att-1',
        sha256: 'abc',
        size_bytes: 12,
      },
      profileId: PROFILE,
      dependsOn: [incident.mutationId],
    });

    const paths: string[] = [];
    const send = vi.fn(async (path: string) => {
      paths.push(path);
      return accepted({});
    });

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(summary.accepted).toBe(2);
    // Order matters: the shell must exist before its evidence is completed.
    expect(paths).toEqual([
      '/v1/incidents',
      '/v1/incidents/inc-1/attachments/att-1/complete',
    ]);
  });

  it('holds evidence back when its incident shell failed', async () => {
    const incident = await enqueueMutation({
      type: 'incident.create',
      body: { note: 'landslide' },
      profileId: PROFILE,
    });
    const attachment = await enqueueMutation({
      type: 'incident.attachment.complete',
      body: { incident_id: 'inc-1', attachment_id: 'att-1' },
      profileId: PROFILE,
      dependsOn: [incident.mutationId],
    });

    const send = vi.fn(async () => {
      throw apiError(null, 'network', 'Connection lost.');
    });

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    // Only the shell was tried; completing evidence against an id the server
    // does not have would fail for a second, confusing reason.
    expect(summary.attempted).toBe(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect((await getMutation(attachment.mutationId))?.state).toBe('queued');
    expect((await getMutation(attachment.mutationId))?.attemptCount).toBe(0);
  });

  it('marks a stale review as a conflict and keeps the draft', async () => {
    const review = await enqueueMutation({
      type: 'incident.review',
      body: {
        incident_id: 'inc-1',
        decision: 'confirm_closure',
        reason: 'Photo shows both lanes buried.',
        expected_version: 3,
        affected_segment_ids: ['seg-1'],
      },
      profileId: PROFILE,
    });

    const send = vi.fn(async () => {
      throw apiError(409, 'http', 'This report was decided by someone else.');
    });

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(summary.conflicts).toBe(1);
    const stored = await getMutation(review.mutationId);
    expect(stored?.state).toBe('conflict');
    // The reviewer's work is still here to rebase from, not discarded.
    expect(stored?.body.reason).toBe('Photo shows both lanes buried.');
    expect(stored?.lastError).toMatch(/decided by someone else/);
  });

  it('does not retry a conflicted mutation on the next pass without a person', async () => {
    await enqueueMutation({
      type: 'incident.review',
      body: {
        incident_id: 'inc-1',
        decision: 'reject',
        reason: 'x',
        expected_version: 1,
      },
      profileId: PROFILE,
    });

    const send = vi.fn(async () => {
      throw apiError(409, 'http', 'Already decided.');
    });

    await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });
    const second = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(second.attempted).toBe(0);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('leaves other profiles alone', async () => {
    await enqueueMutation({
      type: 'incident.create',
      body: {},
      profileId: 'other',
    });
    const send = vi.fn(async () => accepted({}));

    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(summary.attempted).toBe(0);
    expect((await listMutations('other'))[0].state).toBe('queued');
  });
});

describe('malformed bodies', () => {
  it('refuses a queued change whose target id is missing, rather than calling a bad URL', async () => {
    const broken = await enqueueMutation({
      type: 'incident.review',
      // incident_id absent: a schema change or a partial write could do this.
      body: { decision: 'reject', reason: 'x', expected_version: 1 },
      profileId: PROFILE,
    });

    const send = vi.fn(async () => accepted({}));
    const summary = await pushOutbox({
      accessToken: TOKEN,
      profileId: PROFILE,
      send: send as never,
    });

    expect(send).not.toHaveBeenCalled();
    expect(summary.needsAction).toBe(1);
    const stored = await getMutation(broken.mutationId);
    expect(stored?.state).toBe('needs_action');
    expect(stored?.lastError).toMatch(/incident_id/);
    // It was never sent, so it must not be counted as an attempt.
    expect(stored?.attemptCount).toBe(0);
  });
});
