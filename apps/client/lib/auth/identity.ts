import type { DistrictIdentity, MeGetResponse } from '@/lib/api/contracts';

import type { WorkspaceIdentity } from './policy';

export type ServerWorkspace = {
  capabilities: string[];
  districts: DistrictIdentity[];
  displayName: string;
  identity: WorkspaceIdentity;
  locale: string;
  organizationMode: 'local_demo' | 'hosted_demo' | 'pilot';
  serverTime: string;
};

/** Convert a server-validated `/v1/me` document into client route policy. */
export function toServerWorkspace(response: MeGetResponse): ServerWorkspace {
  return {
    capabilities: response.capabilities,
    districts: response.districts ?? [],
    displayName: response.profile.display_name,
    identity: {
      active: response.profile.active,
      grants: response.roles.map((grant) => ({
        districtId: grant.district_id ?? undefined,
        role: grant.role,
        validFrom: grant.valid_from,
        validTo: grant.valid_to ?? undefined,
      })),
      organizationId: response.organization.id,
      profileId: response.profile.id,
      userId: response.profile.user_id,
    },
    locale: response.profile.locale,
    organizationMode: response.organization.mode,
    serverTime: response.server_time,
  };
}
