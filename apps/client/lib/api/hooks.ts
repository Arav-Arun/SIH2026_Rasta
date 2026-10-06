'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useAuth } from '@/components/auth/auth-provider';

import { getIncidentDetail, reviewIncident } from './incidents';
import {
  cancelInspection,
  createInspection,
  getAssignableOfficers,
  getInspections,
} from './inspections';
import {
  bindTripRoutePlan,
  createConsignment,
  createTrip,
  getConsignments,
  getDrivers,
  getSupplyGaps,
  getSupplyRequests,
  getTripReceipt,
  getTrips,
  getVehicles,
  planConsignment,
  transitionTrip,
} from './logistics';
import type {
  InspectionCreateRequest,
  InviteRequest,
  RoleGrantRequest,
} from './contracts';
import type { IncidentReviewRequest } from './contracts';
import { getFleetLocations } from './telemetry';
import { getPeople, grantRole, invitePerson, revokeGrant } from './admin';
import {
  acknowledgeAlert,
  getAlerts,
  getDataHealth,
  getPushStatus,
  getRiskOutcomes,
  recomputeRisk,
} from './operations';
import { approveRoutePlan, createRoutePlan, getRoutePlan } from './routing';
import type {
  RoutePlanApproveRequest,
  RoutePlanCreateRequest,
} from './contracts';
import type {
  ConsignmentCreateRequest,
  TripCreateRequest,
  TripTransition,
} from './contracts';
import {
  bboxParam,
  getConnectivitySummary,
  getIncidents,
  getNetworkSegments,
  getSegmentDetail,
  type SegmentsQuery,
} from './network';

const COMMAND_POLL_MS = 15_000;

/** Access token for API calls; null while no verified workspace exists. */
export function useAccessToken(): string | null {
  const { session, workspace } = useAuth();
  return workspace && session ? session.access_token : null;
}

export function useNetworkSegments(query: SegmentsQuery | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: [
      'network-segments',
      query ? bboxParam(query.bbox) : null,
      query?.districtId ?? null,
      query?.passability ?? null,
      query?.simplifyM ?? null,
      query?.limit ?? null,
    ],
    queryFn: ({ signal }) =>
      getNetworkSegments(
        { accessToken: accessToken as string, signal },
        query as SegmentsQuery,
      ),
    enabled: Boolean(accessToken && query),
    refetchInterval: COMMAND_POLL_MS,
    placeholderData: (previous) => previous,
  });
}

export function useSegmentDetail(segmentId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['segment-detail', segmentId],
    queryFn: ({ signal }) =>
      getSegmentDetail(
        { accessToken: accessToken as string, signal },
        segmentId as string,
      ),
    enabled: Boolean(accessToken && segmentId),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useConnectivitySummary(districtId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['connectivity-summary', districtId],
    queryFn: ({ signal }) =>
      getConnectivitySummary(
        { accessToken: accessToken as string, signal },
        districtId as string,
      ),
    enabled: Boolean(accessToken && districtId),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useIncidents(status: string | null, limit = 20) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['incidents', status, limit],
    queryFn: ({ signal }) =>
      getIncidents(
        { accessToken: accessToken as string, signal },
        { status, limit },
      ),
    enabled: Boolean(accessToken),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useIncidentDetail(incidentId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['incident-detail', incidentId],
    queryFn: ({ signal }) =>
      getIncidentDetail(
        { accessToken: accessToken as string, signal },
        incidentId as string,
      ),
    enabled: Boolean(accessToken && incidentId),
    refetchInterval: COMMAND_POLL_MS,
  });
}

/** Submits a dispatcher decision. */
export function useReviewIncident(incidentId: string | null) {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (variables: {
      body: IncidentReviewRequest;
      idempotencyKey: string;
    }) =>
      reviewIncident(
        { accessToken: accessToken as string },
        incidentId as string,
        variables.body,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['incidents'] });
      void queryClient.invalidateQueries({
        queryKey: ['incident-detail', incidentId],
      });
      void queryClient.invalidateQueries({ queryKey: ['network-segments'] });
      void queryClient.invalidateQueries({ queryKey: ['segment-detail'] });
      void queryClient.invalidateQueries({
        queryKey: ['connectivity-summary'],
      });
    },
  });
}

/** Road segments within roughly `radiusDeg` of a reported position. */
export function useSegmentsNearPoint(
  point: { latitude: number; longitude: number } | null,
  districtId: string | null,
  // About 300 m: the box only has to hold the road the report is on. The
  // caller sorts by distance, so everything in it is fetched, not a first page.
  radiusDeg = 0.003,
) {
  const query: SegmentsQuery | null = point
    ? {
        bbox: {
          minLon: point.longitude - radiusDeg,
          minLat: point.latitude - radiusDeg,
          maxLon: point.longitude + radiusDeg,
          maxLat: point.latitude + radiusDeg,
        },
        districtId,
        limit: 1000,
      }
    : null;

  return useNetworkSegments(query);
}

export function useInspections(status: string | null, limit = 50) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['inspections', status, limit],
    queryFn: ({ signal }) =>
      getInspections(
        { accessToken: accessToken as string, signal },
        { status, limit },
      ),
    enabled: Boolean(accessToken),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useAssignableOfficers(districtId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['assignable-officers', districtId],
    queryFn: ({ signal }) =>
      getAssignableOfficers(
        { accessToken: accessToken as string, signal },
        districtId as string,
      ),
    enabled: Boolean(accessToken && districtId),
    // The roster changes rarely; refetching it every poll would be noise.
    staleTime: 300_000,
  });
}

export function useAssignInspection() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (variables: {
      body: InspectionCreateRequest;
      idempotencyKey: string;
    }) =>
      createInspection(
        { accessToken: accessToken as string },
        variables.body,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['inspections'] });
    },
  });
}

export function useCancelInspection() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (variables: { inspectionId: string; idempotencyKey: string }) =>
      cancelInspection(
        { accessToken: accessToken as string },
        variables.inspectionId,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['inspections'] });
    },
  });
}

/** Every logistics view is invalidated together. */
function useInvalidateLogistics() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of [
      'supply-requests',
      'supply-gaps',
      'consignments',
      'trips',
      'trip-receipt',
      'vehicles',
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

export function useSupplyRequests(status: string | null = null, limit = 50) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['supply-requests', status, limit],
    queryFn: ({ signal }) =>
      getSupplyRequests(
        { accessToken: accessToken as string, signal },
        { status, limit },
      ),
    enabled: Boolean(accessToken),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useSupplyGaps(districtId: string | null = null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['supply-gaps', districtId],
    queryFn: ({ signal }) =>
      getSupplyGaps({ accessToken: accessToken as string, signal }, districtId),
    enabled: Boolean(accessToken),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useConsignments(status: string | null = null, limit = 50) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['consignments', status, limit],
    queryFn: ({ signal }) =>
      getConsignments(
        { accessToken: accessToken as string, signal },
        { status, limit },
      ),
    enabled: Boolean(accessToken),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useTrips(status: string | null = null, limit = 50) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['trips', status, limit],
    queryFn: ({ signal }) =>
      getTrips(
        { accessToken: accessToken as string, signal },
        { status, limit },
      ),
    enabled: Boolean(accessToken),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export function useVehicles() {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['vehicles'],
    queryFn: ({ signal }) =>
      getVehicles({ accessToken: accessToken as string, signal }),
    enabled: Boolean(accessToken),
    // A fleet roster changes rarely; polling it every 15 s would be noise.
    staleTime: 300_000,
  });
}

export function useDrivers(districtId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['drivers', districtId],
    queryFn: ({ signal }) =>
      getDrivers(
        { accessToken: accessToken as string, signal },
        districtId as string,
      ),
    enabled: Boolean(accessToken && districtId),
    staleTime: 300_000,
  });
}

export function useTripReceipt(tripId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['trip-receipt', tripId],
    queryFn: ({ signal }) =>
      getTripReceipt(
        { accessToken: accessToken as string, signal },
        tripId as string,
      ),
    enabled: Boolean(accessToken && tripId),
    refetchInterval: COMMAND_POLL_MS,
  });
}
export function useCreateConsignment() {
  const accessToken = useAccessToken();
  const invalidate = useInvalidateLogistics();
  return useMutation({
    mutationFn: (variables: {
      body: ConsignmentCreateRequest;
      idempotencyKey: string;
    }) =>
      createConsignment(
        { accessToken: accessToken as string },
        variables.body,
        variables.idempotencyKey,
      ),
    onSuccess: invalidate,
  });
}

export function usePlanConsignment() {
  const accessToken = useAccessToken();
  const invalidate = useInvalidateLogistics();
  return useMutation({
    mutationFn: (variables: {
      consignmentId: string;
      idempotencyKey: string;
    }) =>
      planConsignment(
        { accessToken: accessToken as string },
        variables.consignmentId,
        variables.idempotencyKey,
      ),
    onSuccess: invalidate,
  });
}

export function useCreateTrip() {
  const accessToken = useAccessToken();
  const invalidate = useInvalidateLogistics();
  return useMutation({
    mutationFn: (variables: {
      body: TripCreateRequest;
      idempotencyKey: string;
    }) =>
      createTrip(
        { accessToken: accessToken as string },
        variables.body,
        variables.idempotencyKey,
      ),
    onSuccess: invalidate,
  });
}

export function useTransitionTrip() {
  const accessToken = useAccessToken();
  const invalidate = useInvalidateLogistics();
  return useMutation({
    mutationFn: (variables: {
      tripId: string;
      action: TripTransition;
      idempotencyKey: string;
    }) =>
      transitionTrip(
        { accessToken: accessToken as string },
        variables.tripId,
        variables.action,
        variables.idempotencyKey,
      ),
    onSuccess: invalidate,
  });
}
export function useRoutePlan(planId: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['route-plan', planId],
    queryFn: ({ signal }) =>
      getRoutePlan(
        { accessToken: accessToken as string, signal },
        planId as string,
      ),
    enabled: Boolean(accessToken && planId),
  });
}

/**
 * Live vehicle positions refresh on their own; a dispatcher should not have to
 * reload a page to find out a truck has moved.
 */
export function useFleetLocations(districtId?: string | null) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['fleet-locations', districtId ?? null],
    enabled: Boolean(accessToken),
    refetchInterval: 30_000,
    queryFn: ({ signal }) =>
      getFleetLocations(
        { accessToken: accessToken as string, signal },
        {
          districtId,
        },
      ),
  });
}

/**
 * The alert inbox. Polled rather than pushed, because push is optional and the
 * inbox is the record: a dispatcher must not depend on a notification arriving.
 */
export function useAlerts(options: { unacknowledged?: boolean } = {}) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['alerts', options.unacknowledged ?? false],
    enabled: Boolean(accessToken),
    refetchInterval: 60_000,
    queryFn: ({ signal }) =>
      getAlerts({ accessToken: accessToken as string, signal }, options),
  });
}

export function useAcknowledgeAlert() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: { alertId: string; idempotencyKey: string }) =>
      acknowledgeAlert(
        { accessToken: accessToken as string },
        variables.alertId,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}

export function useDataHealth() {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['data-health'],
    enabled: Boolean(accessToken),
    refetchInterval: 120_000,
    queryFn: ({ signal }) =>
      getDataHealth({ accessToken: accessToken as string, signal }),
  });
}

export function useRiskOutcomes(districtId: string | null, days = 30) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['risk-outcomes', districtId, days],
    enabled: Boolean(accessToken && districtId),
    // A day's record only changes with the next scoring round.
    staleTime: 10 * 60_000,
    queryFn: ({ signal }) =>
      getRiskOutcomes(
        { accessToken: accessToken as string, signal },
        districtId as string,
        days,
      ),
  });
}

export function usePeople(enabled = true) {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['admin-people'],
    queryFn: ({ signal }) =>
      getPeople({ accessToken: accessToken as string, signal }),
    enabled: Boolean(accessToken) && enabled,
  });
}

/** Grant, revoke and invite: each refreshes the people list when it lands. */
export function useAdministerRoles() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();
  const auth = () => ({ accessToken: accessToken as string });
  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: ['admin-people'] });
  return {
    grant: useMutation({
      mutationFn: (variables: {
        profileId: string;
        body: RoleGrantRequest;
        idempotencyKey: string;
      }) =>
        grantRole(
          auth(),
          variables.profileId,
          variables.body,
          variables.idempotencyKey,
        ),
      onSuccess: refresh,
    }),
    revoke: useMutation({
      mutationFn: (variables: { grantId: string; idempotencyKey: string }) =>
        revokeGrant(auth(), variables.grantId, variables.idempotencyKey),
      onSuccess: refresh,
    }),
    invite: useMutation({
      mutationFn: (variables: {
        body: InviteRequest;
        idempotencyKey: string;
      }) => invitePerson(auth(), variables.body, variables.idempotencyKey),
      onSuccess: refresh,
    }),
  };
}

export function usePushStatus() {
  const accessToken = useAccessToken();
  return useQuery({
    queryKey: ['push-status'],
    enabled: Boolean(accessToken),
    queryFn: ({ signal }) =>
      getPushStatus({ accessToken: accessToken as string, signal }),
  });
}

export function useRecomputeRisk() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: { districtId: string; idempotencyKey: string }) =>
      recomputeRisk(
        { accessToken: accessToken as string },
        variables.districtId,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      // New scores change the map, the planner and the health report.
      void queryClient.invalidateQueries({ queryKey: ['data-health'] });
      void queryClient.invalidateQueries({ queryKey: ['segments'] });
      void queryClient.invalidateQueries({ queryKey: ['connectivity'] });
    },
  });
}

export function useCreateRoutePlan() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: {
      body: RoutePlanCreateRequest;
      idempotencyKey: string;
    }) =>
      createRoutePlan(
        { accessToken: accessToken as string },
        variables.body,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['route-plans'] });
    },
  });
}

export function useApproveRoutePlan() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: {
      planId: string;
      body: RoutePlanApproveRequest;
      idempotencyKey: string;
    }) =>
      approveRoutePlan(
        { accessToken: accessToken as string },
        variables.planId,
        variables.body,
        variables.idempotencyKey,
      ),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['route-plans'] });
      void queryClient.invalidateQueries({
        queryKey: ['route-plan', variables.planId],
      });
      // An approved route binds a trip, so the delivery views change too.
      void queryClient.invalidateQueries({ queryKey: ['trips'] });
    },
  });
}

/** Gives a trip its approved route; the driver's view and deliveries follow. */
export function useBindTripRoutePlan() {
  const accessToken = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: {
      tripId: string;
      routePlanId: string;
      idempotencyKey: string;
    }) =>
      bindTripRoutePlan(
        { accessToken: accessToken as string },
        variables.tripId,
        variables.routePlanId,
        variables.idempotencyKey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['trips'] });
      void queryClient.invalidateQueries({ queryKey: ['route-plans'] });
    },
  });
}
