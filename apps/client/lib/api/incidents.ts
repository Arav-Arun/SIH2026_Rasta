import { apiRequest } from './client';
import type {
  IncidentReviewRequest,
  IncidentReviewResponse,
  IncidentSummary,
} from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };

export async function getIncidentDetail(
  auth: Auth,
  incidentId: string,
): Promise<IncidentSummary> {
  const { payload } = await apiRequest(`/v1/incidents/${incidentId}`, auth);
  return payload as IncidentSummary;
}

/** Records a dispatcher decision. */
export async function reviewIncident(
  auth: Auth,
  incidentId: string,
  body: IncidentReviewRequest,
  idempotencyKey: string,
): Promise<IncidentReviewResponse> {
  const { payload } = await apiRequest(`/v1/incidents/${incidentId}/review`, {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as IncidentReviewResponse;
}
