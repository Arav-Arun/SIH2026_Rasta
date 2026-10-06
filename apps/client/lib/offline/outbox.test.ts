// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  RastaOfflineDatabase,
  countUnsentWork,
  purgeProfileData,
  setOfflineDatabase,
} from './db';
import {
  InvalidTransitionError,
  blockedMutations,
  canTransition,
  discardAccepted,
  enqueueMutation,
  getMutation,
  listMutations,
  outboxCounts,
  readyMutations,
  requeueInterruptedUploads,
  retryMutation,
  transitionMutation,
} from './outbox';

const PROFILE = 'profile-1';
let db: RastaOfflineDatabase;

beforeEach(async () => {
  db = new RastaOfflineDatabase(`rasta-test-${Math.random()}`);
  setOfflineDatabase(db);
  await db.open();
});

afterEach(async () => {
  await db.delete();
  setOfflineDatabase(null);
});

async function queueIncident() {
  return enqueueMutation({
    type: 'incident.create',
    body: { note: 'Debris across both lanes.' },
    profileId: PROFILE,
  });
}

describe('mutation state machine', () => {
  it('permits only the transitions the sync design defines', () => {
    expect(canTransition('queued', 'uploading')).toBe(true);
    expect(canTransition('uploading', 'accepted')).toBe(true);
    expect(canTransition('uploading', 'queued')).toBe(true);
    expect(canTransition('uploading', 'conflict')).toBe(true);
    expect(canTransition('uploading', 'needs_action')).toBe(true);
    expect(canTransition('conflict', 'queued')).toBe(true);
    expect(canTransition('needs_action', 'queued')).toBe(true);

    // Accepted is terminal, and nothing skips the uploading step.
    expect(canTransition('accepted', 'queued')).toBe(false);
    expect(canTransition('queued', 'accepted')).toBe(false);
    expect(canTransition('queued', 'conflict')).toBe(false);
  });

  it('refuses an illegal transition rather than corrupting the queue', async () => {
    const mutation = await queueIncident();
    await expect(
      transitionMutation(mutation.mutationId, 'accepted'),
    ).rejects.toBeInstanceOf(InvalidTransitionError);

    const stored = await getMutation(mutation.mutationId);
    expect(stored?.state).toBe('queued');
  });
});

describe('durability', () => {
  it('survives a reopen of the database, which is what a force-close looks like', async () => {
    const mutation = await queueIncident();
    const name = db.name;

    db.close();
    const reopened = new RastaOfflineDatabase(name);
    setOfflineDatabase(reopened);
    await reopened.open();

    const rows = await listMutations(PROFILE);
    expect(rows).toHaveLength(1);
    expect(rows[0].mutationId).toBe(mutation.mutationId);
    expect(rows[0].body).toEqual({ note: 'Debris across both lanes.' });

    reopened.close();
    setOfflineDatabase(db);
    await db.open();
  });

  it('keeps the same idempotency key across a failed attempt and its retry', async () => {
    const mutation = await queueIncident();
    const original = mutation.idempotencyKey;

    await transitionMutation(mutation.mutationId, 'uploading', {
      countAttempt: true,
    });
    await transitionMutation(mutation.mutationId, 'queued', {
      error: 'The network dropped.',
    });

    const afterRetry = await getMutation(mutation.mutationId);
    // A new key would let the server file the same report twice.
    expect(afterRetry?.idempotencyKey).toBe(original);
    expect(afterRetry?.attemptCount).toBe(1);
    expect(afterRetry?.lastError).toBe('The network dropped.');
  });

  it('takes back an upload interrupted by a closed tab or a killed app', async () => {
    const mutation = await queueIncident();
    // The request started, then the page went away: nothing moves it on.
    await transitionMutation(mutation.mutationId, 'uploading', {
      countAttempt: true,
    });
    expect(await readyMutations(PROFILE)).toHaveLength(0);

    // Held too briefly to be sure without the cross-tab lock: left alone.
    expect(await requeueInterruptedUploads(PROFILE)).toBe(0);
    // With the lock held (no other tab can be sending) it is taken back at once.
    expect(await requeueInterruptedUploads(PROFILE, 0)).toBe(1);

    const recovered = await getMutation(mutation.mutationId);
    expect(recovered?.state).toBe('queued');
    // Same key, so a request that did land is answered with its first result.
    expect(recovered?.idempotencyKey).toBe(mutation.idempotencyKey);
    expect(await readyMutations(PROFILE)).toHaveLength(1);
  });

  it('takes back an upload untouched for a minute even without the lock', async () => {
    const mutation = await queueIncident();
    await transitionMutation(mutation.mutationId, 'uploading');
    const later = Date.now() + 61_000;
    expect(await requeueInterruptedUploads(PROFILE, undefined, later)).toBe(1);
  });

  it('will not discard anything the server may not have', async () => {
    const mutation = await queueIncident();
    await expect(discardAccepted(mutation.mutationId)).rejects.toThrow(
      /not accepted/,
    );

    await transitionMutation(mutation.mutationId, 'uploading');
    await transitionMutation(mutation.mutationId, 'accepted');
    await discardAccepted(mutation.mutationId);
    expect(await getMutation(mutation.mutationId)).toBeNull();
  });
});

describe('dependencies', () => {
  it('holds a dependent mutation back until its dependency is accepted', async () => {
    const incident = await queueIncident();
    const attachment = await enqueueMutation({
      type: 'incident.attachment.complete',
      body: { sha256: 'abc', size_bytes: 10 },
      profileId: PROFILE,
      dependsOn: [incident.mutationId],
    });

    let ready = await readyMutations(PROFILE);
    expect(ready.map((row) => row.mutationId)).toEqual([incident.mutationId]);
    expect((await blockedMutations(PROFILE)).map((r) => r.mutationId)).toEqual([
      attachment.mutationId,
    ]);

    await transitionMutation(incident.mutationId, 'uploading');
    await transitionMutation(incident.mutationId, 'accepted');

    ready = await readyMutations(PROFILE);
    expect(ready.map((row) => row.mutationId)).toEqual([attachment.mutationId]);
    expect(await blockedMutations(PROFILE)).toHaveLength(0);
  });

  it('does not let one stuck mutation block an unrelated one', async () => {
    const stuck = await queueIncident();
    const other = await enqueueMutation({
      type: 'inspection.accept',
      body: {},
      profileId: PROFILE,
    });

    await transitionMutation(stuck.mutationId, 'uploading');
    await transitionMutation(stuck.mutationId, 'needs_action', {
      error: 'District is outside your grant.',
    });

    const ready = await readyMutations(PROFILE);
    expect(ready.map((row) => row.mutationId)).toEqual([other.mutationId]);
  });
});

describe('queue summary and purge', () => {
  it('counts blocked separately from queued so the UI can explain the wait', async () => {
    const incident = await queueIncident();
    await enqueueMutation({
      type: 'incident.attachment.complete',
      body: {},
      profileId: PROFILE,
      dependsOn: [incident.mutationId],
    });

    const counts = await outboxCounts(PROFILE);
    expect(counts.queued).toBe(1);
    expect(counts.blocked).toBe(1);
    expect(counts.conflict).toBe(0);
  });

  it('a conflict returns to the queue only when someone resolves it', async () => {
    const mutation = await queueIncident();
    await transitionMutation(mutation.mutationId, 'uploading');
    await transitionMutation(mutation.mutationId, 'conflict', {
      error: 'This report was already decided.',
    });

    expect(await readyMutations(PROFILE)).toHaveLength(0);
    await retryMutation(mutation.mutationId);
    expect(await readyMutations(PROFILE)).toHaveLength(1);
  });

  it('purging a profile leaves another profile untouched', async () => {
    await queueIncident();
    await enqueueMutation({
      type: 'incident.create',
      body: {},
      profileId: 'profile-2',
    });

    await purgeProfileData(PROFILE);

    expect(await listMutations(PROFILE)).toHaveLength(0);
    expect(await listMutations('profile-2')).toHaveLength(1);
  });

  it('counts what a sign-out would destroy before the server has it', async () => {
    // Sign-out asks first whenever this is not zero (docs/10).
    expect(await countUnsentWork(PROFILE)).toBe(0);

    const sent = await queueIncident();
    await transitionMutation(sent.mutationId, 'uploading');
    await transitionMutation(sent.mutationId, 'accepted');
    expect(await countUnsentWork(PROFILE)).toBe(0);

    const refused = await queueIncident();
    await transitionMutation(refused.mutationId, 'uploading');
    await transitionMutation(refused.mutationId, 'needs_action', {
      error: 'Refused.',
    });
    await queueIncident();
    await db.drafts.put({
      draftId: 'draft-1',
      kind: 'incident',
      profileId: PROFILE,
      updatedAt: new Date().toISOString(),
      body: { note: 'half typed' },
    });
    // Someone else's work on the same device is theirs to decide about.
    await enqueueMutation({
      type: 'incident.create',
      body: {},
      profileId: 'profile-2',
    });

    expect(await countUnsentWork(PROFILE)).toBe(3);
  });
});
