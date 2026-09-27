/** The device-side position queue. */

export const MAX_BATCH_POINTS = 20;
export const MAX_QUEUE_POINTS = 2000;
const MIN_MOVEMENT_METRES = 25;
export const HEARTBEAT_SECONDS = 60;
const MAX_ACCURACY_M = 100;

export interface QueuedFix {
  client_point_id: string;
  idempotency_key: string;
  captured_at: string;
  latitude: number;
  longitude: number;
  accuracy_m: number;
  speed_kph?: number | null;
  heading?: number | null;
  battery_percent?: number | null;
}

export interface QueueStats {
  queued: number;
  uploaded: number;
  duplicates: number;
  rejected: number;
  /** Rejection reason code → how many fixes it accounted for. */
  rejected_by_reason: Record<string, number>;
  /** Fixes the device discarded before queueing, by reason. */
  discarded_by_reason: Record<string, number>;
  dropped_for_space: number;
  last_upload_at: string | null;
  last_error: string | null;
}

export function emptyStats(): QueueStats {
  return {
    queued: 0,
    uploaded: 0,
    duplicates: 0,
    rejected: 0,
    rejected_by_reason: {},
    discarded_by_reason: {},
    dropped_for_space: 0,
    last_upload_at: null,
    last_error: null,
  };
}

export function metresBetween(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const radius = 6371008.8;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dPhi = toRad(b.latitude - a.latitude);
  const dLambda = toRad(b.longitude - a.longitude);
  const phiA = toRad(a.latitude);
  const phiB = toRad(b.latitude);
  const h =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(phiA) * Math.cos(phiB) * Math.sin(dLambda / 2) ** 2;
  return 2 * radius * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Whether a fix is worth keeping. */
export function judgeCandidate(
  candidate: {
    captured_at: string;
    latitude: number;
    longitude: number;
    accuracy_m: number;
  },
  lastKept: { captured_at: string; latitude: number; longitude: number } | null,
): { keep: boolean; reason: string | null } {
  if (
    !Number.isFinite(candidate.latitude) ||
    !Number.isFinite(candidate.longitude)
  ) {
    return { keep: false, reason: 'missing_coordinates' };
  }
  if (!(candidate.accuracy_m > 0)) {
    return { keep: false, reason: 'missing_accuracy' };
  }
  if (candidate.accuracy_m > MAX_ACCURACY_M) {
    return { keep: false, reason: 'accuracy_too_poor' };
  }
  const captured = Date.parse(candidate.captured_at);
  if (Number.isNaN(captured)) {
    return { keep: false, reason: 'invalid_timestamp' };
  }
  if (!lastKept) return { keep: true, reason: null };

  const elapsed = (captured - Date.parse(lastKept.captured_at)) / 1000;
  if (elapsed >= HEARTBEAT_SECONDS) return { keep: true, reason: null };
  if (metresBetween(lastKept, candidate) >= MIN_MOVEMENT_METRES) {
    return { keep: true, reason: null };
  }
  return { keep: false, reason: 'not_moved_enough' };
}

/** Add a fix to the queue, dropping the oldest only once the cap is reached. */
export function admit(
  queue: QueuedFix[],
  fix: QueuedFix,
  cap: number = MAX_QUEUE_POINTS,
): { queue: QueuedFix[]; dropped: number } {
  const next = [...queue, fix];
  if (next.length <= cap) return { queue: next, dropped: 0 };
  const dropped = next.length - cap;
  return { queue: next.slice(dropped), dropped };
}

export function nextBatch(queue: QueuedFix[]): QueuedFix[] {
  return queue.slice(0, MAX_BATCH_POINTS);
}

interface PointResult {
  client_point_id: string;
  outcome: 'accepted' | 'duplicate' | 'rejected';
  reason?: string | null;
}

/** Fold a server answer back into the queue and the counters. */
export function applyResults(
  queue: QueuedFix[],
  results: PointResult[],
  stats: QueueStats,
  now: Date,
): { queue: QueuedFix[]; stats: QueueStats } {
  const answered = new Set(results.map((result) => result.client_point_id));
  const rejectedByReason = { ...stats.rejected_by_reason };
  let accepted = 0;
  let duplicates = 0;
  let rejected = 0;

  for (const result of results) {
    if (result.outcome === 'accepted') accepted += 1;
    else if (result.outcome === 'duplicate') duplicates += 1;
    else {
      rejected += 1;
      const reason = result.reason ?? 'unspecified';
      rejectedByReason[reason] = (rejectedByReason[reason] ?? 0) + 1;
    }
  }

  const remaining = queue.filter((fix) => !answered.has(fix.client_point_id));
  return {
    queue: remaining,
    stats: {
      ...stats,
      queued: remaining.length,
      uploaded: stats.uploaded + accepted,
      duplicates: stats.duplicates + duplicates,
      rejected: stats.rejected + rejected,
      rejected_by_reason: rejectedByReason,
      last_upload_at: now.toISOString(),
      last_error: null,
    },
  };
}

export function countDiscard(stats: QueueStats, reason: string): QueueStats {
  const discarded = { ...stats.discarded_by_reason };
  discarded[reason] = (discarded[reason] ?? 0) + 1;
  return { ...stats, discarded_by_reason: discarded };
}

/** The one line a driver reads: what is waiting, and what was refused. */
export function describeQueue(stats: QueueStats): string {
  const parts: string[] = [];
  parts.push(
    stats.queued === 0
      ? 'Nothing waiting to send'
      : `${stats.queued} waiting to send`,
  );
  if (stats.uploaded > 0) parts.push(`${stats.uploaded} sent`);
  if (stats.rejected > 0) parts.push(`${stats.rejected} refused`);
  if (stats.dropped_for_space > 0) {
    parts.push(`${stats.dropped_for_space} dropped for space`);
  }
  return parts.join(', ');
}
