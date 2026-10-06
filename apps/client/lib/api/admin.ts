import { apiRequest } from './client';
import type {
  GrantResponse,
  InviteRequest,
  InviteResponse,
  PeopleResponse,
  RoleGrantRequest,
} from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };

/** Everyone in the organisation with every grant they hold or held. */
export async function getPeople(auth: Auth): Promise<PeopleResponse> {
  const { payload } = await apiRequest('/v1/admin/people', auth);
  return payload as PeopleResponse;
}

export async function grantRole(
  auth: Auth,
  profileId: string,
  body: RoleGrantRequest,
  idempotencyKey: string,
): Promise<GrantResponse> {
  const { payload } = await apiRequest(
    `/v1/admin/people/${encodeURIComponent(profileId)}/grants`,
    { ...auth, method: 'POST', body, idempotencyKey },
  );
  return payload as GrantResponse;
}

export async function revokeGrant(
  auth: Auth,
  grantId: string,
  idempotencyKey: string,
): Promise<GrantResponse> {
  const { payload } = await apiRequest(
    `/v1/admin/grants/${encodeURIComponent(grantId)}/revoke`,
    { ...auth, method: 'POST', body: {}, idempotencyKey },
  );
  return payload as GrantResponse;
}

export async function invitePerson(
  auth: Auth,
  body: InviteRequest,
  idempotencyKey: string,
): Promise<InviteResponse> {
  const { payload } = await apiRequest('/v1/admin/people', {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as InviteResponse;
}
