import { describe, expect, it } from 'vitest';

import { toServerWorkspace } from './identity';

describe('toServerWorkspace', () => {
  it('maps only the server-validated profile, organization and grants', () => {
    const workspace = toServerWorkspace({
      capabilities: ['route:plan'],
      organization: {
        id: 'organization-1',
        mode: 'pilot',
        name: 'Pilot organization',
      },
      profile: {
        active: true,
        display_name: 'A. Das',
        id: 'profile-1',
        locale: 'as',
        user_id: 'user-1',
      },
      roles: [
        {
          district_id: null,
          id: 'grant-1',
          role: 'state_coordinator',
          valid_from: '2026-09-12T00:00:00Z',
          valid_to: null,
        },
      ],
      server_time: '2026-09-12T00:00:00Z',
    });

    expect(workspace).toMatchObject({
      displayName: 'A. Das',
      identity: {
        active: true,
        grants: [{ role: 'state_coordinator' }],
        organizationId: 'organization-1',
        profileId: 'profile-1',
        userId: 'user-1',
      },
      locale: 'as',
      organizationMode: 'pilot',
    });
    expect(workspace.identity.grants[0].districtId).toBeUndefined();
  });
});
