import { apiRequest } from './client';
import type {
  AssignableOfficerListResponse,
  InspectionCreateRequest,
  InspectionListResponse,
  InspectionMutationResponse,
} from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };

export async function getInspections(
  auth: Auth,
  query: { status?: string | null; limit?: number } = {},
): Promise<InspectionListResponse> {
  const { payload } = await apiRequest('/v1/inspections', {
    ...auth,
    query: {
      status: query.status ?? undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as InspectionListResponse;
}

/** Officers this dispatcher may assign to, for one district. */
export async function getAssignableOfficers(
  auth: Auth,
  districtId: string,
): Promise<AssignableOfficerListResponse> {
  const { payload } = await apiRequest(
    `/v1/districts/${encodeURIComponent(districtId)}/assignable-officers`,
    auth,
  );
  return payload as AssignableOfficerListResponse;
}

export async function createInspection(
  auth: Auth,
  body: InspectionCreateRequest,
  idempotencyKey: string,
): Promise<InspectionMutationResponse> {
  const { payload } = await apiRequest('/v1/inspections', {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as InspectionMutationResponse;
}

/** Dispatcher-side cancellation; the assignee drives accept/start/complete. */
export async function cancelInspection(
  auth: Auth,
  inspectionId: string,
  idempotencyKey: string,
): Promise<InspectionMutationResponse> {
  const { payload } = await apiRequest(
    `/v1/inspections/${encodeURIComponent(inspectionId)}/cancel`,
    { ...auth, method: 'POST', body: {}, idempotencyKey },
  );
  return payload as InspectionMutationResponse;
}
