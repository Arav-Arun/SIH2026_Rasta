import { describe, expect, it } from 'vitest';

import {
  HEARTBEAT_SECONDS,
  MAX_BATCH_POINTS,
  admit,
  applyResults,
  countDiscard,
  describeQueue,
  emptyStats,
  judgeCandidate,
  metresBetween,
  nextBatch,
  type QueuedFix,
} from './telemetryQueue';

const NOW = new Date('2026-09-26T10:00:00Z');
const SHILLONG = { latitude: 25.5788, longitude: 91.8933 };

function fix(index: number, overrides: Partial<QueuedFix> = {}): QueuedFix {
  return {
    client_point_id: `p${index}`,
    idempotency_key: `00000000-0000-4000-8000-00000000000${index % 10}`,
    captured_at: new Date(NOW.getTime() + index * 60_000).toISOString(),
    latitude: SHILLONG.latitude,
    longitude: SHILLONG.longitude,
    accuracy_m: 9,
    ...overrides,
  };
}

describe('which fixes the device keeps', () => {
  it('keeps the first fix of a trip', () => {
    expect(judgeCandidate(fix(0), null)).toEqual({ keep: true, reason: null });
  });

  it('drops a fix less accurate than the pilot threshold', () => {
    expect(judgeCandidate(fix(0, { accuracy_m: 140 }), null)).toEqual({
      keep: false,
      reason: 'accuracy_too_poor',
    });
  });

  it('drops a fix with no usable accuracy rather than assuming it is good', () => {
    expect(judgeCandidate(fix(0, { accuracy_m: 0 }), null).reason).toBe(
      'missing_accuracy',
    );
  });

  it('drops a fix whose timestamp cannot be read', () => {
    expect(judgeCandidate(fix(0, { captured_at: 'soon' }), null).reason).toBe(
      'invalid_timestamp',
    );
  });

  it('keeps a heartbeat even when the vehicle has not moved', () => {
    // Without this, a driver stopped at a landslide clearance would look like a
    // driver whose phone had died.
    const last = { ...SHILLONG, captured_at: NOW.toISOString() };
    const later = fix(0, {
      captured_at: new Date(
        NOW.getTime() + HEARTBEAT_SECONDS * 1000,
      ).toISOString(),
    });
    expect(judgeCandidate(later, last).keep).toBe(true);
  });

  it('drops a fix metres from the last one inside the heartbeat', () => {
    const last = { ...SHILLONG, captured_at: NOW.toISOString() };
    const jitter = fix(0, {
      captured_at: new Date(NOW.getTime() + 5_000).toISOString(),
      latitude: SHILLONG.latitude + 0.00005,
    });
    expect(judgeCandidate(jitter, last)).toEqual({
      keep: false,
      reason: 'not_moved_enough',
    });
  });

  it('keeps a fix that moved far enough inside the heartbeat', () => {
    const last = { ...SHILLONG, captured_at: NOW.toISOString() };
    const moved = fix(0, {
      captured_at: new Date(NOW.getTime() + 5_000).toISOString(),
      latitude: SHILLONG.latitude + 0.0005,
    });
    expect(judgeCandidate(moved, last).keep).toBe(true);
  });
});

describe('the queue under a full device', () => {
  it('keeps the newest fixes and counts what it had to drop', () => {
    const full = Array.from({ length: 5 }, (_, index) => fix(index));
    const result = admit(full, fix(99), 5);
    expect(result.dropped).toBe(1);
    expect(result.queue).toHaveLength(5);
    expect(result.queue[0].client_point_id).toBe('p1');
    expect(result.queue[4].client_point_id).toBe('p99');
  });

  it('drops nothing while there is room', () => {
    expect(admit([fix(0)], fix(1), 5)).toEqual({
      queue: [fix(0), fix(1)],
      dropped: 0,
    });
  });

  it('sends at most one documented batch at a time', () => {
    const many = Array.from({ length: 45 }, (_, index) => fix(index));
    expect(nextBatch(many)).toHaveLength(MAX_BATCH_POINTS);
  });
});

describe('folding the server answer back', () => {
  const queue = [fix(0), fix(1), fix(2)];

  it('clears every point the server answered for, whatever it said', () => {
    const { queue: remaining, stats } = applyResults(
      queue,
      [
        { client_point_id: 'p0', outcome: 'accepted' },
        { client_point_id: 'p1', outcome: 'duplicate' },
        {
          client_point_id: 'p2',
          outcome: 'rejected',
          reason: 'accuracy_too_poor',
        },
      ],
      emptyStats(),
      NOW,
    );
    expect(remaining).toHaveLength(0);
    expect(stats.uploaded).toBe(1);
    expect(stats.duplicates).toBe(1);
    expect(stats.rejected).toBe(1);
    expect(stats.rejected_by_reason).toEqual({ accuracy_too_poor: 1 });
    expect(stats.last_upload_at).toBe(NOW.toISOString());
  });

  it('leaves a point the server did not mention in the queue', () => {
    const { queue: remaining } = applyResults(
      queue,
      [{ client_point_id: 'p0', outcome: 'accepted' }],
      emptyStats(),
      NOW,
    );
    expect(remaining.map((entry) => entry.client_point_id)).toEqual([
      'p1',
      'p2',
    ]);
  });

  it('never re-sends a rejected point, so the queue cannot loop', () => {
    const { queue: remaining } = applyResults(
      queue,
      queue.map((entry) => ({
        client_point_id: entry.client_point_id,
        outcome: 'rejected' as const,
        reason: 'trip_not_tracking',
      })),
      emptyStats(),
      NOW,
    );
    expect(remaining).toHaveLength(0);
  });

  it('accumulates reasons across batches rather than replacing them', () => {
    const first = applyResults(
      queue,
      [
        {
          client_point_id: 'p0',
          outcome: 'rejected',
          reason: 'accuracy_too_poor',
        },
      ],
      emptyStats(),
      NOW,
    );
    const second = applyResults(
      first.queue,
      [
        {
          client_point_id: 'p1',
          outcome: 'rejected',
          reason: 'implausible_jump',
        },
      ],
      first.stats,
      NOW,
    );
    expect(second.stats.rejected_by_reason).toEqual({
      accuracy_too_poor: 1,
      implausible_jump: 1,
    });
  });

  it('records a rejected point with no reason rather than dropping the count', () => {
    const { stats } = applyResults(
      queue,
      [{ client_point_id: 'p0', outcome: 'rejected' }],
      emptyStats(),
      NOW,
    );
    expect(stats.rejected_by_reason).toEqual({ unspecified: 1 });
  });
});

describe('what the driver is told', () => {
  it('says nothing is waiting rather than showing a zero', () => {
    expect(describeQueue(emptyStats())).toBe('Nothing waiting to send');
  });

  it('names refusals and space losses in the same line', () => {
    const stats = {
      ...emptyStats(),
      queued: 4,
      uploaded: 120,
      rejected: 3,
      dropped_for_space: 7,
    };
    expect(describeQueue(stats)).toBe(
      '4 waiting to send, 120 sent, 3 refused, 7 dropped for space',
    );
  });

  it('counts a locally discarded fix against its reason', () => {
    const stats = countDiscard(
      countDiscard(emptyStats(), 'not_moved_enough'),
      'not_moved_enough',
    );
    expect(stats.discarded_by_reason).toEqual({ not_moved_enough: 2 });
  });
});

describe('distance', () => {
  it('measures on the globe', () => {
    expect(
      Math.round(
        metresBetween(SHILLONG, { latitude: 25.5888, longitude: 91.8933 }),
      ),
    ).toBeGreaterThan(1100);
  });
});
