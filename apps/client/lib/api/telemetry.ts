import { apiRequest } from './client';
import type { FleetLocationsResponse } from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };

/** Where every running trip last reported, and how old that answer is. */
export async function getFleetLocations(
  auth: Auth,
  query: { districtId?: string | null } = {},
): Promise<FleetLocationsResponse> {
  const { payload } = await apiRequest('/v1/fleet/locations', {
    ...auth,
    query: { district_id: query.districtId ?? undefined },
  });
  return payload as FleetLocationsResponse;
}
