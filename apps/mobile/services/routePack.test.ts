import { describe, expect, it } from 'vitest';

import {
  VERIFICATION_WINDOW_SECONDS,
  formatDistance,
  formatEtaRange,
  linesFromGeometry,
  needsAcknowledgement,
  routePackFreshness,
  secondsSince,
  type RoutePack,
} from './routePack';

const NOW = new Date('2026-09-26T10:00:00Z');

function pack(overrides: Partial<RoutePack> = {}): RoutePack {
  return {
    trip_id: 'trip-1',
    plan_id: 'plan-1',
    plan_status: 'approved',
    alternative_id: 'alt-1',
    category: 'fastest_feasible',
    network_version: 'net-1',
    risk_snapshot_version: 'risk-1',
    distance_m: 6200,
    eta_range_seconds: [900, 1500],
    segment_ids: ['s1', 's2'],
    geometry: null,
    unknown_constraints: 2,
    avoided_closures: 1,
    segments_without_a_risk_score: 30,
    recommendation: 'Fastest route that meets every known limit.',
    computed_at: '2026-09-26T09:30:00Z',
    cached_at: '2026-09-26T09:30:00Z',
    verified_at: '2026-09-26T09:55:00Z',
    ...overrides,
  };
}

describe('what the screen may claim about a route', () => {
  it('calls a recently verified route current', () => {
    expect(routePackFreshness(pack(), { online: true, now: NOW })).toBe(
      'confirmed_current',
    );
  });

  it('calls an unverified route cached even when the device is online', () => {
    // Online is not the same as checked. Saying "current" because a network
    // exists would be a claim nobody made.
    const stale = pack({ verified_at: '2026-09-26T09:00:00Z' });
    expect(routePackFreshness(stale, { online: true, now: NOW })).toBe(
      'cached_unverified',
    );
  });

  it('keeps calling a route current offline while its verification still holds', () => {
    // Losing signal does not un-confirm what was confirmed five minutes ago.
    expect(routePackFreshness(pack(), { online: false, now: NOW })).toBe(
      'confirmed_current',
    );
  });

  it('calls a route cached once its verification lapses and there is no signal', () => {
    const old = pack({ verified_at: '2026-09-26T09:00:00Z' });
    expect(routePackFreshness(old, { online: false, now: NOW })).toBe(
      'cached_offline',
    );
  });

  it('separates "offline" from "online but not checked"', () => {
    const old = pack({ verified_at: '2026-09-26T09:00:00Z' });
    expect(routePackFreshness(old, { online: true, now: NOW })).toBe(
      'cached_unverified',
    );
    expect(routePackFreshness(old, { online: false, now: NOW })).toBe(
      'cached_offline',
    );
  });

  it('calls a route with no verification at all cached, never current', () => {
    const never = pack({ verified_at: null });
    expect(routePackFreshness(never, { online: true, now: NOW })).toBe(
      'cached_unverified',
    );
  });

  it('treats the verification window as a boundary, not a suggestion', () => {
    const edge = new Date(
      Date.parse(pack().verified_at as string) +
        VERIFICATION_WINDOW_SECONDS * 1000,
    );
    expect(routePackFreshness(pack(), { online: true, now: edge })).toBe(
      'confirmed_current',
    );
    expect(
      routePackFreshness(pack(), {
        online: true,
        now: new Date(edge.getTime() + 1000),
      }),
    ).toBe('cached_unverified');
  });

  it('reports a replaced plan even when the cached copy still says approved', () => {
    for (const status of ['superseded', 'invalidated']) {
      expect(
        routePackFreshness(pack(), {
          online: true,
          serverStatus: status,
          now: NOW,
        }),
      ).toBe('replaced');
    }
  });

  it('reports a withdrawn plan separately from a replaced one', () => {
    expect(
      routePackFreshness(pack(), {
        online: true,
        serverStatus: 'rejected',
        now: NOW,
      }),
    ).toBe('withdrawn');
  });

  it('says there is no route rather than inventing a state', () => {
    expect(routePackFreshness(null, { online: true, now: NOW })).toBe('none');
  });
});

describe('a revised route is never followed silently', () => {
  it('asks for acknowledgement when the plan changes', () => {
    expect(
      needsAcknowledgement(
        pack(),
        { plan_id: 'plan-2', alternative_id: 'alt-9' },
        null,
      ),
    ).toBe(true);
  });

  it('asks for acknowledgement when only the chosen alternative changes', () => {
    expect(
      needsAcknowledgement(
        pack(),
        { plan_id: 'plan-1', alternative_id: 'alt-2' },
        null,
      ),
    ).toBe(true);
  });

  it('does not ask twice for a revision already acknowledged', () => {
    expect(
      needsAcknowledgement(
        pack(),
        { plan_id: 'plan-2', alternative_id: 'alt-9' },
        {
          plan_id: 'plan-2',
          alternative_id: 'alt-9',
          acknowledged_at: NOW.toISOString(),
        },
      ),
    ).toBe(false);
  });

  it('does not ask when nothing changed', () => {
    expect(
      needsAcknowledgement(
        pack(),
        { plan_id: 'plan-1', alternative_id: 'alt-1' },
        null,
      ),
    ).toBe(false);
  });

  it('treats a first route as a route, not as a revision to acknowledge', () => {
    expect(
      needsAcknowledgement(
        null,
        { plan_id: 'plan-1', alternative_id: 'alt-1' },
        null,
      ),
    ).toBe(false);
  });
});

describe('how numbers are shown to a driver', () => {
  it('shows metres below a kilometre and kilometres above it', () => {
    expect(formatDistance(640)).toBe('640 m');
    expect(formatDistance(6200)).toBe('6.2 km');
  });

  it('shows an ETA as a range, never as a single promise', () => {
    expect(formatEtaRange([900, 1500])).toBe('15–25 min');
  });

  it('collapses a range only when both ends agree', () => {
    expect(formatEtaRange([900, 900])).toBe('15 min');
  });

  it('has no ETA to show when the server gave none', () => {
    expect(formatEtaRange(null)).toBeNull();
  });

  it('never reports a negative age from a clock that ran backwards', () => {
    expect(secondsSince('2026-09-26T10:30:00Z', NOW)).toBe(0);
  });

  it('reports no age for a timestamp it cannot read', () => {
    expect(secondsSince('not-a-date', NOW)).toBeNull();
    expect(secondsSince(null, NOW)).toBeNull();
  });
});

describe('what can actually be drawn', () => {
  it('draws a LineString the server sent', () => {
    expect(
      linesFromGeometry({
        type: 'LineString',
        coordinates: [
          [91.89, 25.57],
          [91.9, 25.58],
        ],
      }),
    ).toHaveLength(1);
  });

  it('draws each part of a MultiLineString separately, leaving the gap a gap', () => {
    const lines = linesFromGeometry({
      type: 'MultiLineString',
      coordinates: [
        [
          [91.89, 25.57],
          [91.9, 25.58],
        ],
        [
          [91.95, 25.6],
          [91.96, 25.61],
        ],
      ],
    });
    expect(lines).toHaveLength(2);
  });

  it('draws nothing when the server sent no geometry', () => {
    expect(linesFromGeometry(null)).toEqual([]);
    expect(linesFromGeometry(undefined)).toEqual([]);
  });

  it('draws nothing for a geometry it does not understand', () => {
    expect(linesFromGeometry({ type: 'Polygon', coordinates: [] })).toEqual([]);
    expect(
      linesFromGeometry({ type: 'Point', coordinates: [91.89, 25.57] }),
    ).toEqual([]);
  });

  it('refuses a one-point line, which is not a line', () => {
    expect(
      linesFromGeometry({ type: 'LineString', coordinates: [[91.89, 25.57]] }),
    ).toEqual([]);
  });

  it('refuses coordinates that are not numbers', () => {
    expect(
      linesFromGeometry({
        type: 'LineString',
        coordinates: [
          ['a', 'b'],
          [91.9, 25.58],
        ],
      }),
    ).toEqual([]);
  });
});
