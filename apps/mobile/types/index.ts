export type RiskTier = 'low' | 'moderate' | 'high' | 'critical';
export type HazardCategory =
  | 'landslide'
  | 'boulder_fall'
  | 'bridge_overwash'
  | 'road_crack'
  | 'flash_flood'
  | 'heavy_jam';

/** Where a report stands with the control room. */
export type ReportSyncStatus = 'queued' | 'syncing' | 'synced' | 'failed';

export interface HazardReport {
  id: string;
  category: HazardCategory;
  categoryLabel: string;
  corridorCode: string;
  locationName: string;
  latitude: number;
  longitude: number;
  altitudeMeters?: number;
  severity: RiskTier;
  notes?: string;
  photoUri?: string;
  /** When the report was saved on this phone (ISO 8601). */
  reportedAt: string;
  syncStatus: ReportSyncStatus;
  /** The key the report is filed under; every retry reuses it. */
  idempotencyKey: string;
  /** Saved while this app was holding reports, so nothing was sent at the time. */
  offlineRecorded: boolean;

  /** Which seat in the vehicle raised this. */
  reporterRole?: CrewRole;
  reporterName?: string;
  /** The vehicle's crew code at the time of the report. */
  crewCode?: string;
  /** GPS horizontal accuracy in metres as reported by the device. */
  accuracyMeters?: number;
  /** ISO timestamp of the position fix. */
  capturedAtIso?: string;
  /** A hand-placed pin is a usable position, not a measurement. */
  positionSource?: 'device_gps' | 'manual_pin';

  /** Id of the incident the control room filed this as, once accepted. */
  controlRoomIncidentId?: string;
  /** What became of the photo. Only `verified` counts as evidence. */
  evidenceStatus?: EvidenceStatus;
  /** Everything needed to send, or resume sending, this report. */
  filing?: ReportFiling;
}

/** Checksum, length and type of a photo, declared to the API before upload. */
export interface DeclaredPhoto {
  sha256: string;
  sizeBytes: number;
  mimeType: string;
}

/** The storage path the API issued for one photo, and until when it is open. */
export interface UploadTarget {
  attachmentId: string;
  path: string;
  expiresAt: string;
  maxBytes: number;
}

/** The resumable state of sending one report. */
export interface ReportFiling {
  /** Idempotency key for filing the report, fixed when it was saved. */
  key: string;
  /** Whether the photo question is settled: declared, or left out for a reason. */
  photoDecided: boolean;
  photo: DeclaredPhoto | null;
  target: UploadTarget | null;
  /** The bytes are at `target.path`; only the server's check is left. */
  uploaded: boolean;
  attempts: number;
  /** Times the photo had to go again because the server found nothing to check. */
  reuploads: number;
  lastAttemptAt: string | null;
  /** Why the report is not accepted yet, in words the reporter can act on. */
  lastError: string | null;
  /** Why the photo is not accepted as evidence, when it is not. */
  evidenceError: string | null;
}

/** How far a photo got through the upload handshake. */
export type EvidenceStatus =
  | 'none'
  | 'not_sent'
  | 'failed'
  | 'uploaded'
  | 'verified'
  | 'rejected';
export type VehicleType =
  | 'car_sedan'
  | 'suv_4x4'
  | 'commercial_6w'
  | 'heavy_freight_12w'; // ---------------------------------------------------------------------------
// Crew identity, a vehicle is operated by a pair: the person driving, and the
// onboard observer who documents disruptions while the vehicle is moving.
// ---------------------------------------------------------------------------

export type CrewRole = 'driver' | 'observer';

/** How the current session was established. Never hide this from the user. */
export type SessionMode = 'authenticated' | 'local_only';

export interface CrewSession {
  role: CrewRole;
  mode: SessionMode;
  /** Display name for the signed-in person, or the local label they chose. */
  displayName: string;
  /** Supabase user id when authenticated; null in local-only mode. */
  userId: string | null;
  email: string | null;
  /**
   * Shared code that pairs a driver and an observer to the same vehicle.
   * Reports filed from either seat are tagged with it on the phone.
   */
  crewCode: string;
  vehicleType: VehicleType;
  signedInAt: string;
}

/** Where a published capture was accepted. Shown verbatim, never assumed. */
export interface CaptureDeliveryReceipt {
  /** Stored in the device outbox. Always true once the capture is saved. */
  localOutbox: boolean;
  /** Accepted by the API as an incident awaiting a dispatcher's review. */
  controlRoomDb: boolean;
  /** How far the photo got. Null when no photo was taken. */
  evidence: EvidenceStatus | null;
  /** Human-readable reason when a leg did not complete. */
  notes: string[];
}

export interface ObservationCapture {
  category: HazardCategory;
  categoryLabel: string;
  corridorCode: string;
  locationName: string;
  severity: RiskTier;
  notes?: string;
  photoUri?: string;
  /** Real device fix. Null when the user declined or no fix was obtained. */
  latitude: number | null;
  longitude: number | null;
  altitudeMeters?: number;
  /** GPS horizontal accuracy in metres, straight from the device. */
  accuracyMeters?: number;
  /** ISO timestamp of the actual fix, not of form submission. */
  fixTakenAt?: string;
  /**
   * How the coordinate was obtained. A hand-entered pin is usable but is not a
   * measurement, and the control room is told which it is.
   */
  positionSource?: 'device_gps' | 'manual_pin';
}
