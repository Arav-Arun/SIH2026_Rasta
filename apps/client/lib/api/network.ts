import { apiRequest, type ApiRequestOptions } from './client';
import type {
  ConnectivitySummaryResponse,
  IncidentListResponse,
  NetworkSegmentsResponse,
  SegmentDetailResponse,
} from './contracts';

type Auth = Pick<
  ApiRequestOptions,
  'accessToken' | 'baseUrl' | 'fetchImpl' | 'signal'
>;

export type BBox = {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
};

export function bboxParam(bbox: BBox): string {
  return [bbox.minLon, bbox.minLat, bbox.maxLon, bbox.maxLat]
    .map((value) => value.toFixed(6))
    .join(',');
}

/** Pad a viewport by a fraction so a small pan does not refetch immediately. */
export function padBBox(bbox: BBox, fraction = 0.15): BBox {
  const dx = (bbox.maxLon - bbox.minLon) * fraction;
  const dy = (bbox.maxLat - bbox.minLat) * fraction;
  return {
    minLon: Math.max(-180, bbox.minLon - dx),
    minLat: Math.max(-90, bbox.minLat - dy),
    maxLon: Math.min(180, bbox.maxLon + dx),
    maxLat: Math.min(90, bbox.maxLat + dy),
  };
}

export type SegmentsQuery = {
  bbox: BBox;
  districtId?: string | null;
  passability?: 'open' | 'restricted' | 'closed' | 'unknown' | null;
  simplifyM?: number | null;
  limit?: number | null;
};

export async function getNetworkSegments(
  auth: Auth,
  query: SegmentsQuery,
): Promise<NetworkSegmentsResponse> {
  const { payload } = await apiRequest('/v1/network/segments', {
    ...auth,
    query: {
      bbox: bboxParam(query.bbox),
      district_id: query.districtId ?? undefined,
      passability: query.passability ?? undefined,
      simplify_m: query.simplifyM ?? undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as NetworkSegmentsResponse;
}

export async function getSegmentDetail(
  auth: Auth,
  segmentId: string,
): Promise<SegmentDetailResponse> {
  const { payload } = await apiRequest(
    `/v1/network/segments/${encodeURIComponent(segmentId)}`,
    auth,
  );
  return payload as SegmentDetailResponse;
}

export async function getConnectivitySummary(
  auth: Auth,
  districtId: string,
): Promise<ConnectivitySummaryResponse> {
  const { payload } = await apiRequest('/v1/connectivity/summary', {
    ...auth,
    query: { district_id: districtId },
  });
  return payload as ConnectivitySummaryResponse;
}

export async function getIncidents(
  auth: Auth,
  query: { status?: string | null; limit?: number } = {},
): Promise<IncidentListResponse> {
  const { payload } = await apiRequest('/v1/incidents', {
    ...auth,
    query: {
      status: query.status ?? undefined,
      limit: query.limit ?? undefined,
    },
  });
  return payload as IncidentListResponse;
}
