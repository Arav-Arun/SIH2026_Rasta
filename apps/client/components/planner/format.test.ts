import { describe, expect, it } from 'vitest';

import type { RouteAlternative } from '@/lib/api/contracts';

import {
  avoidedClosureCount,
  deadlineMarginSeconds,
  formatDistanceMetres,
  formatDurationSeconds,
  judgeMargin,
  reviewWarnings,
  unknownConstraintCount,
} from './format';

const alternative = (over: Partial<RouteAlternative> = {}): RouteAlternative =>
  ({
    id: 'alt-1',
    rank: 1,
    category: 'fastest_feasible',
    segment_ids: ['s1'],
    node_ids: ['n1', 'n2'],
    geometry: null,
    distance_m: 1000,
    travel_time_seconds: 600,
    cost_seconds: 700,
    eta_range_seconds: [450, 750],
    risk_summary: {},
    constraint_warnings: [],
    reasons: [],
    requires_review: false,
    ...over,
  }) as RouteAlternative;

describe('formatDurationSeconds', () => {
  it('splits into hours and minutes', () => {
    expect(formatDurationSeconds(4320)).toEqual({ hours: 1, minutes: 12 });
    expect(formatDurationSeconds(600)).toEqual({ hours: 0, minutes: 10 });
  });

  it('never returns a negative duration', () => {
    expect(formatDurationSeconds(-50)).toEqual({ hours: 0, minutes: 0 });
  });
});

describe('formatDistanceMetres', () => {
  it('switches to kilometres above a kilometre', () => {
    expect(formatDistanceMetres(1624.85)).toEqual({ value: '1.6', unit: 'km' });
    expect(formatDistanceMetres(938)).toEqual({ value: '938', unit: 'm' });
  });
});

describe('deadlineMarginSeconds', () => {
  const departure = '2026-09-25T10:00:00Z';

  it('uses the slow end of the ETA band, not the optimistic one', () => {
    // Deadline is 800 s after departure; the slow ETA is 750 s, so 50 s spare.
    // Using the 450 s fast end would have claimed 350 s and overstated safety.
    const margin = deadlineMarginSeconds(
      alternative(),
      departure,
      '2026-09-25T10:13:20Z',
    );
    expect(margin).toBe(50);
  });

  it('reports a negative margin when the route cannot make it', () => {
    const margin = deadlineMarginSeconds(
      alternative(),
      departure,
      '2026-09-25T10:05:00Z',
    );
    expect(margin).toBeLessThan(0);
  });

  it('is null when there is no deadline at all', () => {
    // No deadline is not the same as plenty of time.
    expect(deadlineMarginSeconds(alternative(), departure, null)).toBeNull();
  });

  it('is null rather than wrong when a date cannot be parsed', () => {
    expect(
      deadlineMarginSeconds(alternative(), departure, 'not-a-date'),
    ).toBeNull();
  });
});

describe('judgeMargin', () => {
  it('separates late, tight and comfortable, and admits unknown', () => {
    expect(judgeMargin(-1)).toBe('late');
    expect(judgeMargin(600)).toBe('tight');
    expect(judgeMargin(7200)).toBe('comfortable');
    expect(judgeMargin(null)).toBe('unknown');
  });

  it('treats exactly on time as late rather than comfortable', () => {
    expect(judgeMargin(0)).toBe('tight');
  });
});

describe('reviewWarnings', () => {
  it('keeps only the warnings a person must resolve', () => {
    const row = alternative({
      constraint_warnings: [
        {
          code: 'unknown_constraint',
          segment_ids: ['a'],
          requires_review: true,
        },
        {
          code: 'unobserved_segment_state',
          segment_ids: ['b'],
          requires_review: false,
        },
      ],
    });
    expect(
      reviewWarnings(row).map((w) => (w as { code: string }).code),
    ).toEqual(['unknown_constraint']);
  });
});

describe('unknownConstraintCount', () => {
  it('counts segments, not warnings', () => {
    const row = alternative({
      constraint_warnings: [
        {
          code: 'unknown_constraint',
          segment_ids: ['a', 'b', 'c'],
          requires_review: true,
        },
      ],
    });
    expect(unknownConstraintCount(row)).toBe(3);
  });

  it('is zero when nothing is unknown', () => {
    expect(unknownConstraintCount(alternative())).toBe(0);
  });
});

describe('avoidedClosureCount', () => {
  it('reads what the planner excluded rather than inspecting the route', () => {
    expect(avoidedClosureCount({ segment_closed: [{ segment_id: 'x' }] })).toBe(
      1,
    );
    expect(avoidedClosureCount({})).toBe(0);
  });
});
