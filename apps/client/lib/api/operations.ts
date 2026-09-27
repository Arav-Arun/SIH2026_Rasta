import { apiRequest } from './client';
import type {
  AlertAcknowledgeResponse,
  AlertListResponse,
  DataHealthResponse,
  PushStatusResponse,
  PushSubscriptionRequest,
  PushSubscriptionResponse,
  PushTestResponse,
  RiskRecomputeResponse,
} from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };

export async function getAlerts(
  auth: Auth,
  query: { unacknowledged?: boolean; limit?: number } = {},
): Promise<AlertListResponse> {
  const { payload } = await apiRequest('/v1/alerts', {
    ...auth,
    query: {
      unacknowledged: query.unacknowledged ? 'true' : undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as AlertListResponse;
}

/** Acknowledging is per recipient: it never clears anybody else's copy. */
export async function acknowledgeAlert(
  auth: Auth,
  alertId: string,
  idempotencyKey: string,
): Promise<AlertAcknowledgeResponse> {
  const { payload } = await apiRequest(
    `/v1/alerts/${encodeURIComponent(alertId)}/acknowledge`,
    { ...auth, method: 'POST', body: {}, idempotencyKey },
  );
  return payload as AlertAcknowledgeResponse;
}

export async function getDataHealth(auth: Auth): Promise<DataHealthResponse> {
  const { payload } = await apiRequest('/v1/data-health', auth);
  return payload as DataHealthResponse;
}

export async function recomputeRisk(
  auth: Auth,
  districtId: string,
  idempotencyKey: string,
): Promise<RiskRecomputeResponse> {
  const { payload } = await apiRequest('/v1/risk/recompute', {
    ...auth,
    method: 'POST',
    body: {},
    query: { district_id: districtId },
    idempotencyKey,
  });
  return payload as RiskRecomputeResponse;
}

export async function getPushStatus(auth: Auth): Promise<PushStatusResponse> {
  const { payload } = await apiRequest('/v1/push-subscriptions', auth);
  return payload as PushStatusResponse;
}

export async function registerPush(
  auth: Auth,
  body: PushSubscriptionRequest,
  idempotencyKey: string,
): Promise<PushSubscriptionResponse> {
  const { payload } = await apiRequest('/v1/push-subscriptions', {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as PushSubscriptionResponse;
}

/** The endpoint goes in the body: it is bearer-equivalent, and URLs get logged. */
export async function revokePush(auth: Auth, endpoint: string): Promise<void> {
  await apiRequest('/v1/push-subscriptions/revoke', {
    ...auth,
    method: 'POST',
    body: { endpoint },
  });
}

export async function sendTestPush(auth: Auth): Promise<PushTestResponse> {
  const { payload } = await apiRequest('/v1/push-subscriptions/test', {
    ...auth,
    method: 'POST',
    body: {},
  });
  return payload as PushTestResponse;
}
