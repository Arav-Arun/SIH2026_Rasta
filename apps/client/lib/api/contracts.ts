import type { components, paths } from '@rasta/contracts';
export type ApiErrorEnvelope = components['schemas']['ErrorEnvelope'];
export type MeGetResponse =
  paths['/v1/me']['get']['responses'][200]['content']['application/json'];

export type NetworkSegmentsResponse =
  paths['/v1/network/segments']['get']['responses'][200]['content']['application/json'];
export type SegmentFeature = components['schemas']['SegmentFeature'];
export type SegmentProperties = components['schemas']['SegmentProperties'];
export type SegmentDetailResponse =
  paths['/v1/network/segments/{segment_id}']['get']['responses'][200]['content']['application/json'];
export type ConnectivitySummaryResponse =
  paths['/v1/connectivity/summary']['get']['responses'][200]['content']['application/json'];
export type ConnectivityFacility =
  components['schemas']['ConnectivityFacility'];
export type IncidentListResponse =
  paths['/v1/incidents']['get']['responses'][200]['content']['application/json'];
export type IncidentSummary = components['schemas']['IncidentSummary'];
export type DistrictIdentity = components['schemas']['DistrictIdentity'];
export type IncidentReviewRequest =
  components['schemas']['IncidentReviewRequest'];
export type IncidentReviewResponse =
  components['schemas']['IncidentReviewResponse'];
export type IncidentReviewDecision = IncidentReviewRequest['decision'];
export type AttachmentInfo = components['schemas']['AttachmentInfo'];
export type InspectionSummary = components['schemas']['InspectionSummary'];
export type InspectionListResponse =
  components['schemas']['InspectionListResponse'];
export type InspectionCreateRequest =
  components['schemas']['InspectionCreateRequest'];
export type InspectionMutationResponse =
  components['schemas']['InspectionMutationResponse'];
export type AssignableOfficerListResponse =
  components['schemas']['AssignableOfficerListResponse'];
export type SupplyRequest = components['schemas']['SupplyRequest'];
export type SupplyRequestListResponse =
  components['schemas']['SupplyRequestListResponse'];
export type Consignment = components['schemas']['Consignment'];
export type ConsignmentCreateRequest =
  components['schemas']['ConsignmentCreateRequest'];
export type ConsignmentItem = components['schemas']['ConsignmentItem'];
export type ConsignmentListResponse =
  components['schemas']['ConsignmentListResponse'];
export type Vehicle = components['schemas']['Vehicle'];
export type VehicleListResponse = components['schemas']['VehicleListResponse'];
export type DriverListResponse = components['schemas']['DriverListResponse'];
export type Trip = components['schemas']['Trip'];
export type TripListResponse = components['schemas']['TripListResponse'];
export type TripCreateRequest = components['schemas']['TripCreateRequest'];
export type TripMutationResponse =
  components['schemas']['TripMutationResponse']; /** The trip moves the API exposes as named endpoints. */
export type TripTransition =
  | 'awaiting_driver'
  | 'active'
  | 'paused'
  | 'cancelled'
  | 'failed';
export type DeliveryReceipt = components['schemas']['DeliveryReceipt'];
export type ReceiptItem = components['schemas']['ReceiptItem'];
export type TripReceiptResponse = components['schemas']['TripReceiptResponse'];
export type RoutePlan = components['schemas']['RoutePlan'];
export type RouteAlternative = components['schemas']['RouteAlternativeModel'];
export type RoutePlanCreateRequest =
  components['schemas']['RoutePlanCreateRequest'];
export type RoutePlanCreateResponse =
  components['schemas']['RoutePlanCreateResponse'];
export type RoutePlanApproveRequest =
  components['schemas']['RoutePlanApproveRequest'];
export type RoutePlanApproveResponse =
  components['schemas']['RoutePlanApproveResponse'];
export type FleetLocationsResponse =
  components['schemas']['FleetLocationsResponse'];
export type FleetTripLocation = components['schemas']['FleetTripLocation'];
export type ReportingState = FleetTripLocation['reporting_state'];
export type AlertRecord = components['schemas']['AlertRecord'];
export type AlertListResponse = components['schemas']['AlertListResponse'];
export type AlertAcknowledgeResponse =
  components['schemas']['AlertAcknowledgeResponse'];
export type AlertSeverity = AlertRecord['severity'];
export type DataHealthResponse = components['schemas']['DataHealthResponse'];
export type SourceHealth = components['schemas']['SourceHealth'];
export type CoverageReport = components['schemas']['CoverageReport'];
export type PushStatusResponse = components['schemas']['PushStatusResponse'];
export type PushSubscriptionRequest =
  components['schemas']['PushSubscriptionRequest'];
export type PushSubscriptionResponse =
  components['schemas']['PushSubscriptionResponse'];
export type PushTestResponse = components['schemas']['PushTestResponse'];
export type RiskOutcomesResponse =
  components['schemas']['RiskOutcomesResponse'];
export type RiskRecomputeResponse =
  components['schemas']['RiskRecomputeResponse'];
export type Passability = SegmentProperties['passability'];
