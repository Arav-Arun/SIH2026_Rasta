import { apiRequest } from './client';
import type {
  RoutePlan,
  RoutePlanApproveRequest,
  RoutePlanApproveResponse,
  RoutePlanCreateRequest,
  RoutePlanCreateResponse,
} from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };
export async function getRoutePlan(
  auth: Auth,
  planId: string,
): Promise<RoutePlan> {
  const { payload } = await apiRequest(
    `/v1/route-plans/${encodeURIComponent(planId)}`,
    auth,
  );
  return payload as RoutePlan;
}

/** Plans a route against a snapshot the server freezes at request time. */
export async function createRoutePlan(
  auth: Auth,
  body: RoutePlanCreateRequest,
  idempotencyKey: string,
): Promise<RoutePlanCreateResponse> {
  const { payload } = await apiRequest('/v1/route-plans', {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as RoutePlanCreateResponse;
}

export async function approveRoutePlan(
  auth: Auth,
  planId: string,
  body: RoutePlanApproveRequest,
  idempotencyKey: string,
): Promise<RoutePlanApproveResponse> {
  const { payload } = await apiRequest(
    `/v1/route-plans/${encodeURIComponent(planId)}/approve`,
    { ...auth, method: 'POST', body, idempotencyKey },
  );
  return payload as RoutePlanApproveResponse;
}
