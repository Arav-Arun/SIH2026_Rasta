import { apiRequest } from './client';
import type {
  Consignment,
  ConsignmentCreateRequest,
  ConsignmentListResponse,
  DriverListResponse,
  SupplyGapsResponse,
  SupplyRequestListResponse,
  TripCreateRequest,
  TripListResponse,
  TripMutationResponse,
  TripReceiptResponse,
  TripTransition,
  VehicleListResponse,
} from './contracts';

type Auth = { accessToken: string; signal?: AbortSignal };

export async function getSupplyRequests(
  auth: Auth,
  query: { status?: string | null; limit?: number } = {},
): Promise<SupplyRequestListResponse> {
  const { payload } = await apiRequest('/v1/supply-requests', {
    ...auth,
    query: {
      status: query.status ?? undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as SupplyRequestListResponse;
}
/** Unmet requests, most urgent first, with why each deadline is or is not at risk. */
export async function getSupplyGaps(
  auth: Auth,
  districtId: string | null = null,
): Promise<SupplyGapsResponse> {
  const { payload } = await apiRequest('/v1/supply-gaps', {
    ...auth,
    query: { district_id: districtId ?? undefined },
  });
  return payload as SupplyGapsResponse;
}

export async function getConsignments(
  auth: Auth,
  query: { status?: string | null; limit?: number } = {},
): Promise<ConsignmentListResponse> {
  const { payload } = await apiRequest('/v1/consignments', {
    ...auth,
    query: {
      status: query.status ?? undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as ConsignmentListResponse;
}
/**
 * Records a new consignment as a draft: what is going where, by when. It is
 * released for planning separately, once its manifest is right.
 */
export async function createConsignment(
  auth: Auth,
  body: ConsignmentCreateRequest,
  idempotencyKey: string,
): Promise<Consignment> {
  const { payload } = await apiRequest('/v1/consignments', {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as Consignment;
}

/**
 * Releases a draft consignment for planning. Until this happens the manifest
 * is still being corrected, and the server refuses to raise a trip against it.
 */
export async function planConsignment(
  auth: Auth,
  consignmentId: string,
  idempotencyKey: string,
): Promise<Consignment> {
  const { payload } = await apiRequest(
    `/v1/consignments/${encodeURIComponent(consignmentId)}/planned`,
    { ...auth, method: 'POST', body: {}, idempotencyKey },
  );
  return payload as Consignment;
}

export async function getVehicles(auth: Auth): Promise<VehicleListResponse> {
  const { payload } = await apiRequest('/v1/vehicles', auth);
  return payload as VehicleListResponse;
}

/** Drivers the assign call would accept for this district. */
export async function getDrivers(
  auth: Auth,
  districtId: string,
): Promise<DriverListResponse> {
  const { payload } = await apiRequest(
    `/v1/districts/${encodeURIComponent(districtId)}/drivers`,
    auth,
  );
  return payload as DriverListResponse;
}

export async function getTrips(
  auth: Auth,
  query: { status?: string | null; limit?: number } = {},
): Promise<TripListResponse> {
  const { payload } = await apiRequest('/v1/trips', {
    ...auth,
    query: {
      status: query.status ?? undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as TripListResponse;
}

export async function createTrip(
  auth: Auth,
  body: TripCreateRequest,
  idempotencyKey: string,
): Promise<TripMutationResponse> {
  const { payload } = await apiRequest('/v1/trips', {
    ...auth,
    method: 'POST',
    body,
    idempotencyKey,
  });
  return payload as TripMutationResponse;
}

/**
 * Each legal move is its own endpoint rather than a PATCH on status, so the
 * caller names the action and the server owns which ones are reachable.
 */
export async function transitionTrip(
  auth: Auth,
  tripId: string,
  action: TripTransition,
  idempotencyKey: string,
): Promise<TripMutationResponse> {
  const { payload } = await apiRequest(
    `/v1/trips/${encodeURIComponent(tripId)}/${action}`,
    { ...auth, method: 'POST', body: {}, idempotencyKey },
  );
  return payload as TripMutationResponse;
}

/**
 * Gives a trip the approved route its driver should follow. The server refuses
 * anything but an approved plan from the trip's own district, and audits it.
 */
export async function bindTripRoutePlan(
  auth: Auth,
  tripId: string,
  routePlanId: string,
  idempotencyKey: string,
): Promise<TripMutationResponse> {
  const { payload } = await apiRequest(
    `/v1/trips/${encodeURIComponent(tripId)}/route-plan`,
    {
      ...auth,
      method: 'POST',
      body: { route_plan_id: routePlanId },
      idempotencyKey,
    },
  );
  return payload as TripMutationResponse;
}

/** Reads back what the receiving end said arrived. */
export async function getTripReceipt(
  auth: Auth,
  tripId: string,
): Promise<TripReceiptResponse> {
  const { payload } = await apiRequest(
    `/v1/trips/${encodeURIComponent(tripId)}/receipt`,
    auth,
  );
  return payload as TripReceiptResponse;
}
