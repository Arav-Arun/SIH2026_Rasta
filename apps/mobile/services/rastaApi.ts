import { supabase } from './supabase';

/**
 * Client for the RASTA API, the service that owns road state, and the only
 * place a report from this phone goes.
 */

export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number | null; code: string | null; reason: string };

async function accessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

async function request<T>(
  path: string,
  init: {
    method?: 'GET' | 'POST';
    body?: unknown;
    idempotencyKey?: string;
    timeoutMs?: number;
    /**
     * A trip-scoped tracking credential, used instead of the session for
     * telemetry.
     */
    trackingGrant?: string;
  } = {},
): Promise<ApiResult<T>> {
  const token = init.trackingGrant ? null : await accessToken();
  if (!token && !init.trackingGrant) {
    return {
      ok: false,
      status: null,
      code: 'no_session',
      reason: 'Not signed in, so this cannot be sent to the control room.',
    };
  }

  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    init.timeoutMs ?? 12_000,
  );

  try {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (init.trackingGrant) headers['X-Tracking-Grant'] = init.trackingGrant;
    else headers.Authorization = `Bearer ${token}`;
    if (init.body !== undefined) headers['Content-Type'] = 'application/json';
    // Every write carries one; a retry of the same attempt replays server-side
    // rather than filing the report twice.
    if (init.idempotencyKey) headers['Idempotency-Key'] = init.idempotencyKey;

    const response = await fetch(`${API_BASE_URL}${path}`, {
      method: init.method ?? 'GET',
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: controller.signal,
    });

    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;

    if (!response.ok) {
      const envelope = payload?.error;
      return {
        ok: false,
        status: response.status,
        code: envelope?.code ?? null,
        reason:
          envelope?.message ??
          `The control room refused this (${response.status}).`,
      };
    }

    return { ok: true, data: payload as T };
  } catch (err) {
    const aborted = err instanceof Error && err.name === 'AbortError';
    return {
      ok: false,
      status: null,
      code: aborted ? 'timeout' : 'network_error',
      reason: aborted
        ? 'The control room did not answer in time.'
        : 'The control room could not be reached.',
    };
  } finally {
    clearTimeout(timeout);
  }
}

// --- Contract subset the field app uses -------------------------------------

export interface AttachmentDraft {
  local_id: string;
  mime_type: string;
  bytes: number;
  sha256: string;
  captured_at?: string;
}

export interface UploadInstruction {
  attachment_id: string;
  local_id: string;
  bucket: string;
  path: string;
  max_bytes: number;
  allowed_mime_types: string[];
  expires_at: string;
}

export interface IncidentCreateResponse {
  incident: {
    id: string;
    status: string;
    version: number;
    district_id: string;
  };
  upload_instructions: UploadInstruction[];
  suggested_segments: {
    segment_id: string;
    name: string | null;
    distance_m: number;
  }[];
  replayed: boolean;
}

export interface AttachmentAddResponse {
  attachment: { id: string; upload_status: string };
  upload_instruction: UploadInstruction;
  replayed: boolean;
}

export interface AttachmentCompleteResponse {
  attachment: { id: string; upload_status: string };
  verification: { status: string; reason: string | null };
  replayed: boolean;
}

/** Incident types the API accepts. Keep in step with the API's enum. */
export type ApiIncidentType =
  | 'landslide_debris'
  | 'flooding'
  | 'bridge_damage'
  | 'road_damage'
  | 'congestion'
  | 'road_reopened'
  | 'other';

export function createIncident(
  body: {
    type: ApiIncidentType;
    captured_at: string;
    /** Required: the API refuses a report it cannot place. */
    location: { latitude: number; longitude: number };
    location_source?: 'device_gps' | 'manual_pin';
    accuracy_m: number | null;
    note?: string;
    attachments?: AttachmentDraft[];
  },
  idempotencyKey: string,
): Promise<ApiResult<IncidentCreateResponse>> {
  return request<IncidentCreateResponse>('/v1/incidents', {
    method: 'POST',
    body,
    idempotencyKey,
  });
}

/** A fresh upload target for a photo on a report that is already filed. */
export function addAttachment(
  incidentId: string,
  body: AttachmentDraft,
  idempotencyKey: string,
): Promise<ApiResult<AttachmentAddResponse>> {
  return request<AttachmentAddResponse>(
    `/v1/incidents/${incidentId}/attachments`,
    {
      method: 'POST',
      body,
      idempotencyKey,
    },
  );
}

export function completeAttachment(
  incidentId: string,
  attachmentId: string,
  body: { sha256: string; size_bytes: number },
  idempotencyKey: string,
): Promise<ApiResult<AttachmentCompleteResponse>> {
  return request<AttachmentCompleteResponse>(
    `/v1/incidents/${incidentId}/attachments/${attachmentId}/complete`,
    { method: 'POST', body, idempotencyKey },
  );
}

// --- Inspections: the tasks a field officer has been assigned ----------------

export type InspectionStatus =
  | 'assigned'
  | 'accepted'
  | 'in_progress'
  | 'submitted'
  | 'reviewed'
  | 'cancelled'
  | 'overdue';

export interface InspectionSummary {
  id: string;
  district_id: string;
  target_type: 'incident' | 'segment' | 'bridge' | 'facility';
  target_id: string;
  target_label: string | null;
  status: InspectionStatus;
  due_at: string | null;
  instructions: string | null;
  result_incident_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

interface InspectionListResponse {
  inspections: InspectionSummary[];
  total: number;
  as_of: string;
  scope: 'district' | 'assigned_to_me';
}

export function listInspections(): Promise<ApiResult<InspectionListResponse>> {
  return request<InspectionListResponse>('/v1/inspections');
}

interface InspectionMutationResponse {
  inspection: InspectionSummary;
  audit_event_id: string;
  replayed: boolean;
}

/**
 * Moves an inspection along its state machine. The server owns the transition
 * table, so an out-of-order call is refused there rather than guessed at here.
 */
export function advanceInspection(
  inspectionId: string,
  action: 'accept' | 'start' | 'complete',
  idempotencyKey: string,
  body?: { note?: string; result_incident_id?: string },
): Promise<ApiResult<InspectionMutationResponse>> {
  return request<InspectionMutationResponse>(
    `/v1/inspections/${inspectionId}/${action}`,
    { method: 'POST', body: body ?? {}, idempotencyKey },
  );
}

// --- Trips: the load this driver is carrying --------------------------------

export type TripStatus =
  | 'planned'
  | 'awaiting_driver'
  | 'active'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** The moves a driver may make. Starting and finishing are theirs; assigning is not. */
export type DriverTripAction = 'active' | 'paused' | 'failed';

export interface Trip {
  id: string;
  district_id: string;
  consignment_id: string;
  consignment_reference: string | null;
  vehicle_id: string;
  vehicle_registration: string | null;
  driver_profile_id: string;
  driver_display_name: string | null;
  status: TripStatus;
  /** The approved route the control room bound to this trip, if any. */
  route_plan_id: string | null;
  route_plan_status: string | null;
  started_at: string | null;
  ended_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

interface TripListResponse {
  trips: Trip[];
  total: number;
  as_of: string;
  scope: 'district' | 'assigned_to_me';
}

export interface ConsignmentItem {
  id: string;
  commodity: string;
  quantity: string;
  unit: string;
  weight_kg: string | null;
  expiry_at: string | null;
}

export interface Consignment {
  id: string;
  district_id: string;
  reference: string;
  supply_request_id: string | null;
  origin_facility_id: string;
  origin_facility_name: string | null;
  destination_facility_id: string;
  destination_facility_name: string | null;
  priority: string;
  deadline_at: string | null;
  status: string;
  items: ConsignmentItem[];
  total_weight_kg: string | null;
  weight_known_for_all_items: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

interface ConsignmentListResponse {
  consignments: Consignment[];
  total: number;
  as_of: string;
}

interface ReceiptItem {
  consignment_item_id: string;
  commodity: string;
  ordered_quantity: string;
  delivered_quantity: string;
  unit: string;
  note: string | null;
}

interface DeliveryReceipt {
  id: string;
  trip_id: string;
  status: 'delivered' | 'partially_delivered' | 'failed';
  received_at: string;
  received_by_ref: string | null;
  notes: string | null;
  created_by_profile_id: string | null;
  items: ReceiptItem[];
  version: number;
}

interface ReceiptResponse {
  receipt: DeliveryReceipt;
  trip: Trip;
  consignment_status: string;
  audit_event_ids: string[];
  replayed: boolean;
}

/** A driver's own trips. The server decides the scope, not this call. */
export function listTrips(): Promise<ApiResult<TripListResponse>> {
  return request<TripListResponse>('/v1/trips');
}

export function listConsignments(): Promise<
  ApiResult<ConsignmentListResponse>
> {
  return request<ConsignmentListResponse>('/v1/consignments');
}

/**
 * Moves the driver's own trip. The server owns the transition table, so an
 * out-of-order call is refused there rather than guessed at here.
 */
export function transitionTrip(
  tripId: string,
  action: DriverTripAction,
  idempotencyKey: string,
): Promise<ApiResult<{ trip: Trip; replayed: boolean }>> {
  return request<{ trip: Trip; replayed: boolean }>(
    `/v1/trips/${tripId}/${action}`,
    {
      method: 'POST',
      body: {},
      idempotencyKey,
    },
  );
}

/** Records what the receiving end says arrived. */
export function recordReceipt(
  tripId: string,
  body: {
    status: 'delivered' | 'partially_delivered' | 'failed';
    received_by_ref?: string | null;
    notes?: string | null;
    items: {
      consignment_item_id: string;
      delivered_quantity: string;
      note?: string | null;
    }[];
  },
  idempotencyKey: string,
): Promise<ApiResult<ReceiptResponse>> {
  return request<ReceiptResponse>(`/v1/trips/${tripId}/receipt`, {
    method: 'POST',
    body,
    idempotencyKey,
  });
}

// --- route plans -------------------------------------------------------------

export interface RouteAlternativeSummary {
  id: string;
  rank: number;
  category: string;
  distance_m: string;
  eta_seconds: number | null;
  eta_range_seconds: [number, number] | null;
  segment_ids: string[];
  geometry: unknown;
  risk_summary: {
    max_score: string | null;
    segments_without_a_risk_score: number;
  } | null;
  constraint_warnings: { code: string; detail?: string | null }[];
  recommendation: string | null;
  avoided_closure_count?: number;
}

export interface RoutePlanDetail {
  id: string;
  district_id: string;
  trip_id: string | null;
  status: string;
  result_status: string;
  chosen_alternative_id: string | null;
  network_version: string;
  risk_snapshot_version: string;
  computed_at: string;
  alternatives: RouteAlternativeSummary[];
  safe_route_claim: boolean;
  coverage_warnings: { code: string; detail?: string | null }[];
}

export function getRoutePlan(
  planId: string,
): Promise<ApiResult<RoutePlanDetail>> {
  return request<RoutePlanDetail>(
    `/v1/route-plans/${encodeURIComponent(planId)}`,
  );
}

// --- devices and telemetry ---------------------------------------------------

interface DeviceRegistration {
  id: string;
  device_public_id: string;
  platform: string;
  display_label: string | null;
  revoked_at: string | null;
}

export function registerDevice(
  body: {
    device_public_id: string;
    platform: 'android' | 'web';
    display_label?: string | null;
  },
  idempotencyKey: string,
): Promise<ApiResult<DeviceRegistration>> {
  return request<DeviceRegistration>('/v1/devices', {
    method: 'POST',
    body,
    idempotencyKey,
  });
}

interface TrackingGrant {
  token: string;
  trip_id: string;
  device_id: string;
  issued_at: string;
  expires_at: string;
  policy: {
    max_batch_points: number;
    max_accuracy_m: string;
    min_movement_m: number;
    heartbeat_seconds: number;
    stale_after_seconds: number;
  };
  readiness: {
    driver_assigned: boolean;
    vehicle_assigned: boolean;
    route_plan_id: string | null;
    route_plan_status: string | null;
    approved_route: boolean;
    notes: string[];
  };
}

export function issueTrackingGrant(
  tripId: string,
  devicePublicId: string,
  idempotencyKey: string,
): Promise<ApiResult<TrackingGrant>> {
  return request<TrackingGrant>(
    `/v1/trips/${encodeURIComponent(tripId)}/tracking-grants`,
    {
      method: 'POST',
      body: { device_public_id: devicePublicId },
      idempotencyKey,
    },
  );
}

interface TelemetryBatchResult {
  trip_id: string;
  accepted: number;
  duplicates: number;
  rejected: number;
  results: {
    client_point_id: string;
    outcome: 'accepted' | 'duplicate' | 'rejected';
    reason: string | null;
    detail: string | null;
  }[];
  current_location: {
    latitude: number;
    longitude: number;
    as_of: string;
    is_stale: boolean;
  } | null;
  stale_after_seconds: number;
}

export function submitTelemetryBatch(
  grantToken: string,
  body: { trip_id: string; device_id: string; points: unknown[] },
  idempotencyKey: string,
): Promise<ApiResult<TelemetryBatchResult>> {
  return request<TelemetryBatchResult>('/v1/telemetry/batches', {
    method: 'POST',
    body,
    idempotencyKey,
    trackingGrant: grantToken,
  });
}

// --- What the field screens show instead of invented content -----------------

export interface AlertRecord {
  id: string;
  type: string;
  severity: 'info' | 'warning' | 'critical';
  title_key: string;
  district_id: string | null;
  subject_type: string | null;
  subject_id: string | null;
  payload: Record<string, unknown>;
  valid_from: string;
  valid_until: string | null;
  status: string;
  acknowledged_at: string | null;
  valid_now: boolean;
}

interface AlertListResponse {
  alerts: AlertRecord[];
  unacknowledged: number;
  total: number;
  as_of: string;
}

export function listAlerts(
  options: { unacknowledgedOnly?: boolean; limit?: number } = {},
): Promise<ApiResult<AlertListResponse>> {
  const query = new URLSearchParams();
  if (options.unacknowledgedOnly) query.set('unacknowledged', 'true');
  query.set('limit', String(options.limit ?? 30));
  return request<AlertListResponse>(`/v1/alerts?${query.toString()}`);
}

export function acknowledgeAlert(
  alertId: string,
  idempotencyKey: string,
): Promise<ApiResult<{ alert_id: string; acknowledged_at: string | null }>> {
  return request(`/v1/alerts/${encodeURIComponent(alertId)}/acknowledge`, {
    method: 'POST',
    idempotencyKey,
  });
}

export interface ConnectivityFacility {
  facility_id: string;
  name: string;
  type: string;
  status: 'reachable' | 'isolated' | 'unknown_coverage';
  routing_node_id: string | null;
  location: { longitude: number; latitude: number } | null;
}

interface ConnectivitySummaryResponse {
  district_id: string;
  graph_version: string | null;
  computed_at: string;
  facility_status: ConnectivityFacility[];
}

export function getConnectivitySummary(
  districtId: string,
): Promise<ApiResult<ConnectivitySummaryResponse>> {
  return request<ConnectivitySummaryResponse>(
    `/v1/connectivity/summary?district_id=${encodeURIComponent(districtId)}`,
  );
}

export interface WorkspaceDistrict {
  id: string;
  code: string;
  name: string;
  state_code: string;
}

/** The server's answer about who the caller is. Never inferred on the device. */
export interface MeResponse {
  profile: {
    id: string;
    display_name: string;
    locale: string;
    active: boolean;
  };
  organization: { id: string; name: string };
  roles: { role: string; district_id: string | null }[];
  capabilities: string[];
  districts: WorkspaceDistrict[];
  server_time: string;
}

export function getMe(): Promise<ApiResult<MeResponse>> {
  return request<MeResponse>('/v1/me');
}
