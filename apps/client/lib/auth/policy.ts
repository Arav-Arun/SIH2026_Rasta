const OPERATIONAL_ROLES = [
  'state_coordinator',
  'district_dispatcher',
  'field_officer',
  'driver',
  'admin',
  'reviewer',
] as const;

type OperationalRole = (typeof OPERATIONAL_ROLES)[number];

export type RoleGrant = {
  role: OperationalRole;
  districtId?: string;
  validFrom?: string;
  validTo?: string;
};

export type WorkspaceIdentity = {
  userId: string;
  profileId: string;
  organizationId: string;
  active: boolean;
  grants: RoleGrant[];
};

type RouteAccessContext = {
  path: string;
  districtId?: string;
  now?: Date;
};

type RouteAccessDecision = {
  allowed: boolean;
  reason:
    | 'public_route'
    | 'allowed_role'
    | 'authentication_required'
    | 'profile_inactive'
    | 'role_required'
    | 'district_scope_denied'
    | 'route_not_registered';
  matchedRole?: OperationalRole;
};

const PUBLIC_PATHS = new Set(['/', '/sign-in']);

const ROUTE_ROLES: ReadonlyArray<{
  prefix: string;
  roles: readonly OperationalRole[];
}> = [
  {
    prefix: '/overview',
    roles: ['state_coordinator', 'district_dispatcher', 'reviewer'],
  },
  {
    prefix: '/map',
    roles: [
      'state_coordinator',
      'district_dispatcher',
      'field_officer',
      'reviewer',
    ],
  },
  { prefix: '/planner', roles: ['state_coordinator', 'district_dispatcher'] },
  {
    prefix: '/deliveries',
    roles: ['state_coordinator', 'district_dispatcher', 'reviewer'],
  },
  { prefix: '/fleet', roles: ['state_coordinator', 'district_dispatcher'] },
  {
    prefix: '/incidents',
    roles: ['district_dispatcher', 'field_officer', 'reviewer'],
  },
  // The dispatcher's assignment board. A field officer's own tasks are on
  // /field/home, where the moves they can make are the ones offered.
  { prefix: '/inspections', roles: ['district_dispatcher'] },
  {
    prefix: '/alerts',
    roles: [
      'state_coordinator',
      'district_dispatcher',
      'field_officer',
      'driver',
      'admin',
      'reviewer',
    ],
  },
  // Kept in step with `data_health:read` in the API's ROLE_CAPABILITIES.
  {
    prefix: '/data-health',
    roles: ['state_coordinator', 'district_dispatcher', 'admin'],
  },
  // App version, update state and the offline data pack.
  {
    prefix: '/settings',
    roles: [
      'state_coordinator',
      'district_dispatcher',
      'field_officer',
      'driver',
      'admin',
      'reviewer',
    ],
  },
  // Checked before `/field`: first match wins.
  { prefix: '/field/report', roles: ['district_dispatcher', 'field_officer'] },
  { prefix: '/field', roles: ['field_officer'] },
  { prefix: '/driver', roles: ['driver'] },
  // Whoever can queue work offline needs to see whether it has left the device.
  {
    prefix: '/sync',
    roles: ['district_dispatcher', 'field_officer', 'driver'],
  },
  { prefix: '/permissions', roles: ['field_officer', 'driver'] },
  { prefix: '/demo', roles: ['admin'] },
];

function matchesPath(path: string, prefix: string) {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function isGrantCurrent(grant: RoleGrant, now: Date) {
  const nowMs = now.getTime();
  if (grant.validFrom && new Date(grant.validFrom).getTime() > nowMs)
    return false;
  if (grant.validTo && new Date(grant.validTo).getTime() <= nowMs) return false;
  return true;
}

export function decideRouteAccess(
  identity: WorkspaceIdentity | null,
  context: RouteAccessContext,
): RouteAccessDecision {
  const path = context.path.split('?')[0].replace(/\/$/, '') || '/';
  if (PUBLIC_PATHS.has(path)) return { allowed: true, reason: 'public_route' };
  if (!identity) return { allowed: false, reason: 'authentication_required' };
  if (!identity.active) return { allowed: false, reason: 'profile_inactive' };

  const route = ROUTE_ROLES.find((candidate) =>
    matchesPath(path, candidate.prefix),
  );
  if (!route) return { allowed: false, reason: 'route_not_registered' };

  const currentGrants = identity.grants.filter((grant) =>
    isGrantCurrent(grant, context.now ?? new Date()),
  );
  const roleGrants = currentGrants.filter((grant) =>
    route.roles.includes(grant.role),
  );
  if (roleGrants.length === 0)
    return { allowed: false, reason: 'role_required' };

  if (context.districtId) {
    const districtGrant = roleGrants.find(
      (grant) => !grant.districtId || grant.districtId === context.districtId,
    );
    if (!districtGrant)
      return { allowed: false, reason: 'district_scope_denied' };
    return {
      allowed: true,
      reason: 'allowed_role',
      matchedRole: districtGrant.role,
    };
  }

  return {
    allowed: true,
    reason: 'allowed_role',
    matchedRole: roleGrants[0].role,
  };
}

/** Screens in the order a person's work would start on them. */
const HOME_CANDIDATES = [
  '/overview',
  '/field/home',
  '/driver/trip',
  '/data-health',
  '/alerts',
];

/** Where this person's work starts: the first screen their role may open. */
export function homePathFor(identity: WorkspaceIdentity | null): string {
  if (!identity) return '/sign-in';
  return (
    HOME_CANDIDATES.find(
      (path) => decideRouteAccess(identity, { path }).allowed,
    ) ?? '/alerts'
  );
}
