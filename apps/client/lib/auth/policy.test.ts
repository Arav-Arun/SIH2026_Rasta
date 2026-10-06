import { describe, expect, it } from 'vitest';

import {
  decideRouteAccess,
  homePathFor,
  type WorkspaceIdentity,
} from './policy';

const NOW = new Date('2026-09-07T12:00:00Z');

function identity(
  overrides: Partial<WorkspaceIdentity> = {},
): WorkspaceIdentity {
  return {
    userId: 'user-test',
    profileId: 'profile-test',
    organizationId: 'organization-test',
    active: true,
    grants: [{ role: 'district_dispatcher', districtId: 'district-a' }],
    ...overrides,
  };
}

describe('decideRouteAccess', () => {
  it('keeps sign-in public and requires authentication for operational routes', () => {
    expect(
      decideRouteAccess(null, { path: '/sign-in', now: NOW }).allowed,
    ).toBe(true);
    expect(
      decideRouteAccess(null, { path: '/overview', now: NOW }),
    ).toMatchObject({
      allowed: false,
      reason: 'authentication_required',
    });
  });

  it('allows a dispatcher in their district and denies another district', () => {
    expect(
      decideRouteAccess(identity(), {
        path: '/planner',
        districtId: 'district-a',
        now: NOW,
      }),
    ).toMatchObject({ allowed: true, matchedRole: 'district_dispatcher' });
    expect(
      decideRouteAccess(identity(), {
        path: '/planner',
        districtId: 'district-b',
        now: NOW,
      }),
    ).toMatchObject({ allowed: false, reason: 'district_scope_denied' });
  });

  it('denies a driver access to control-room planning', () => {
    expect(
      decideRouteAccess(identity({ grants: [{ role: 'driver' }] }), {
        path: '/planner',
        now: NOW,
      }),
    ).toMatchObject({ allowed: false, reason: 'role_required' });
  });

  it('does not accept expired or future grants', () => {
    const expired = identity({
      grants: [{ role: 'state_coordinator', validTo: '2026-09-07T11:59:59Z' }],
    });
    const future = identity({
      grants: [
        { role: 'state_coordinator', validFrom: '2026-09-07T12:00:01Z' },
      ],
    });
    expect(
      decideRouteAccess(expired, { path: '/overview', now: NOW }).allowed,
    ).toBe(false);
    expect(
      decideRouteAccess(future, { path: '/overview', now: NOW }).allowed,
    ).toBe(false);
  });

  it('blocks inactive profiles and unknown routes', () => {
    expect(
      decideRouteAccess(identity({ active: false }), {
        path: '/overview',
        now: NOW,
      }),
    ).toMatchObject({ allowed: false, reason: 'profile_inactive' });
    expect(
      decideRouteAccess(identity(), { path: '/unregistered', now: NOW }),
    ).toMatchObject({
      allowed: false,
      reason: 'route_not_registered',
    });
  });
});

describe('the sidebar and the route gate agree', () => {
  // The sidebar filters its entries with decideRouteAccess, so a link is only
  // offered when the same decision that guards arrival allows it. These cases
  // pin the pairs that were actually wrong: every entry was offered to
  // everyone, so a driver was invited into the planner and into data health and
  // learned otherwise only on arrival.
  const cases: ReadonlyArray<{
    role: WorkspaceIdentity['grants'][number]['role'];
    allowed: readonly string[];
    refused: readonly string[];
  }> = [
    {
      role: 'driver',
      allowed: ['/alerts', '/driver/trip', '/settings'],
      refused: [
        '/planner',
        '/data-health',
        '/overview',
        '/fleet',
        '/incidents',
      ],
    },
    {
      role: 'field_officer',
      allowed: ['/alerts', '/map', '/incidents', '/field/home', '/settings'],
      refused: ['/planner', '/data-health', '/fleet', '/overview'],
    },
    {
      role: 'district_dispatcher',
      // Granted `data_health:read` by the API, so the page must open too.
      allowed: [
        '/planner',
        '/data-health',
        '/fleet',
        '/alerts',
        '/overview',
        '/settings',
      ],
      refused: ['/driver/trip', '/field/home'],
    },
  ];

  for (const { role, allowed, refused } of cases) {
    for (const path of allowed) {
      it(`lets a ${role} open ${path}`, () => {
        const decision = decideRouteAccess(identity({ grants: [{ role }] }), {
          path,
          now: NOW,
        });
        expect(decision.allowed).toBe(true);
      });
    }
    for (const path of refused) {
      it(`keeps a ${role} out of ${path}`, () => {
        const decision = decideRouteAccess(identity({ grants: [{ role }] }), {
          path,
          now: NOW,
        });
        expect(decision.allowed).toBe(false);
      });
    }
  }

  it('offers nothing at all without an identity', () => {
    for (const path of ['/overview', '/alerts', '/data-health']) {
      expect(decideRouteAccess(null, { path, now: NOW }).allowed).toBe(false);
    }
  });
});

describe('where each role starts', () => {
  const now = new Date('2026-09-26T00:00:00Z');
  const person = (
    role: WorkspaceIdentity['grants'][number]['role'],
  ): WorkspaceIdentity => ({
    active: true,
    grants: [{ role, validFrom: '2026-01-01T00:00:00Z' }],
    organizationId: 'org',
    profileId: 'profile',
    userId: 'user',
  });

  it('sends each role to a screen it may open', () => {
    expect(homePathFor(person('district_dispatcher'))).toBe('/overview');
    expect(homePathFor(person('state_coordinator'))).toBe('/overview');
    expect(homePathFor(person('field_officer'))).toBe('/field/home');
    expect(homePathFor(person('driver'))).toBe('/driver/trip');
    expect(homePathFor(person('admin'))).toBe('/data-health');
    for (const role of [
      'district_dispatcher',
      'field_officer',
      'driver',
      'admin',
      'reviewer',
    ] as const) {
      expect(
        decideRouteAccess(person(role), {
          path: homePathFor(person(role)),
          now,
        }).allowed,
        role,
      ).toBe(true);
    }
  });

  it('sends an unknown visitor to sign in', () => {
    expect(homePathFor(null)).toBe('/sign-in');
  });
});
