import type {
  DeclaredPhoto,
  EvidenceStatus,
  HazardCategory,
  HazardReport,
  ReportFiling,
  UploadTarget,
} from '../types';
import type { PhotoBytes, PhotoReadResult } from './evidenceUpload';
import type {
  ApiIncidentType,
  ApiResult,
  AttachmentAddResponse,
  AttachmentCompleteResponse,
  AttachmentDraft,
  IncidentCreateResponse,
  UploadInstruction,
} from './rastaApi';

/** The phone's outbox: how a saved report reaches the control room. */

/** Must match MAX_ATTACHMENT_BYTES in api/app/evidence.py. */
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
/** Must match ALLOWED_MIME_TYPES in api/app/evidence.py. */
const ACCEPTED_PHOTO_TYPES: readonly string[] = [
  'image/jpeg',
  'image/png',
  'image/webp',
];
/** Must match the `note` limit on IncidentCreateRequest. */
const MAX_NOTE_LENGTH = 4000;
/** An upload target is refused by the bucket once its window closes. */
const RENEW_BEFORE_EXPIRY_MS = 60_000;
/** Times a photo is sent again after the server found nothing to check. */
const MAX_REUPLOADS = 3;

/** Maps the field app's categories onto the incident types the API accepts. */
const INCIDENT_TYPE_BY_CATEGORY: Record<HazardCategory, ApiIncidentType> = {
  landslide: 'landslide_debris',
  boulder_fall: 'landslide_debris',
  bridge_overwash: 'bridge_damage',
  road_crack: 'road_damage',
  flash_flood: 'flooding',
  heavy_jam: 'congestion',
};

function incidentTypeFor(category: HazardCategory): ApiIncidentType {
  return INCIDENT_TYPE_BY_CATEGORY[category] ?? 'other';
}

export type UploadOutcome =
  | { ok: true }
  | { ok: false; kind: 'exists' | 'refused' | 'network'; reason: string };

/** What sending needs from the outside world. */
export interface OutboxDeps {
  createIncident(
    body: IncidentBody,
    idempotencyKey: string,
  ): Promise<ApiResult<IncidentCreateResponse>>;
  addAttachment(
    incidentId: string,
    body: AttachmentDraft,
    idempotencyKey: string,
  ): Promise<ApiResult<AttachmentAddResponse>>;
  completeAttachment(
    incidentId: string,
    attachmentId: string,
    body: { sha256: string; size_bytes: number },
    idempotencyKey: string,
  ): Promise<ApiResult<AttachmentCompleteResponse>>;
  readPhoto(uri: string): Promise<PhotoReadResult>;
  uploadPhoto(path: string, photo: PhotoBytes): Promise<UploadOutcome>;
  now(): Date;
}

export interface IncidentBody {
  type: ApiIncidentType;
  captured_at: string;
  location: { latitude: number; longitude: number };
  location_source: 'device_gps' | 'manual_pin';
  accuracy_m: number | null;
  note?: string;
  attachments?: AttachmentDraft[];
}

/** A new report's filing state: nothing sent, the key fixed. */
export function newFiling(key: string): ReportFiling {
  return {
    key,
    photoDecided: false,
    photo: null,
    target: null,
    uploaded: false,
    attempts: 0,
    reuploads: 0,
    lastAttemptAt: null,
    lastError: null,
    evidenceError: null,
  };
}

/**
 * A second key derived from a first, so a step that is repeated after a lost
 * answer carries the same key every time, without having to be stored.
 */
export function deriveKey(base: string, label: string): string {
  const input = `${base}|${label}`;
  let h1 = 1779033703;
  let h2 = 3144134277;
  let h3 = 1013904242;
  let h4 = 2773480762;
  for (let index = 0; index < input.length; index += 1) {
    const code = input.charCodeAt(index);
    h1 = h2 ^ Math.imul(h1 ^ code, 597399067);
    h2 = h3 ^ Math.imul(h2 ^ code, 2869860233);
    h3 = h4 ^ Math.imul(h3 ^ code, 951274213);
    h4 = h1 ^ Math.imul(h4 ^ code, 2716044179);
  }
  h1 = Math.imul(h3 ^ (h1 >>> 18), 597399067);
  h2 = Math.imul(h4 ^ (h2 >>> 22), 2869860233);
  h3 = Math.imul(h1 ^ (h3 >>> 17), 951274213);
  h4 = Math.imul(h2 ^ (h4 >>> 19), 2716044179);
  h1 ^= h2 ^ h3 ^ h4;
  h2 ^= h1;
  h3 ^= h1;
  h4 ^= h1;
  const hex = [h1, h2, h3, h4]
    .map((part) => (part >>> 0).toString(16).padStart(8, '0'))
    .join('');
  const variant = ((parseInt(hex[16], 16) & 0x3) | 0x8).toString(16);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-8${hex.slice(13, 16)}-${variant}${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

/** The photo's name within its report; stable, so a retried filing is identical. */
export function localPhotoId(report: Pick<HazardReport, 'id'>): string {
  return `photo-${report.id}`.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 80);
}

function hasPosition(
  report: Pick<HazardReport, 'latitude' | 'longitude'>,
): boolean {
  return (
    Number.isFinite(report.latitude) &&
    Number.isFinite(report.longitude) &&
    Math.abs(report.latitude) <= 90 &&
    Math.abs(report.longitude) <= 180
  );
}

type OutboxWork = 'file' | 'evidence' | null;

/** What, if anything, is left to send for this report. */
export function pendingWork(report: HazardReport): OutboxWork {
  if (!report.filing) return null;
  if (report.syncStatus === 'queued' || report.syncStatus === 'syncing')
    return 'file';
  if (
    report.syncStatus === 'synced' &&
    report.controlRoomIncidentId &&
    report.filing.photo &&
    report.filing.target &&
    (report.evidenceStatus === 'not_sent' ||
      report.evidenceStatus === 'uploaded')
  ) {
    return 'evidence';
  }
  return null;
}

/** Whether trying the same request again later can succeed. */
export function isRetryable(failure: { status: number | null }): boolean {
  const status = failure.status;
  if (status === null) return true;
  return status === 401 || status === 408 || status === 429 || status >= 500;
}

/** Sorts a storage failure into what the outbox needs to know. */
export function classifyStorageFailure(error: unknown): UploadOutcome {
  const failure = (error ?? {}) as {
    status?: unknown;
    statusCode?: unknown;
    code?: unknown;
    message?: unknown;
  };
  const reason =
    typeof failure.message === 'string' && failure.message
      ? failure.message
      : 'mobile.evidence.uploadFailed';
  const status =
    typeof failure.status === 'number'
      ? failure.status
      : typeof failure.statusCode === 'string'
        ? Number(failure.statusCode)
        : typeof failure.statusCode === 'number'
          ? failure.statusCode
          : NaN;

  if (
    failure.code === 'ResourceAlreadyExists' ||
    status === 409 ||
    /already exists|duplicate/i.test(reason)
  ) {
    return { ok: false, kind: 'exists', reason };
  }
  if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
    return { ok: false, kind: 'refused', reason };
  }
  return { ok: false, kind: 'network', reason };
}

function targetFrom(instruction: UploadInstruction): UploadTarget {
  return {
    attachmentId: instruction.attachment_id,
    path: instruction.path,
    expiresAt: instruction.expires_at,
    maxBytes: instruction.max_bytes,
  };
}

function noteFor(report: HazardReport): string | undefined {
  const text = [report.locationName, report.notes]
    .map((part) => part?.trim())
    .filter(Boolean)
    .join(': ');
  return text ? text.slice(0, MAX_NOTE_LENGTH) : undefined;
}

function capturedAt(report: HazardReport): string {
  return report.capturedAtIso ?? report.reportedAt;
}

function withFiling(
  report: HazardReport,
  patch: Partial<ReportFiling>,
): HazardReport {
  return {
    ...report,
    filing: { ...(report.filing as ReportFiling), ...patch },
  };
}

/** Settles, once, whether the report goes with its photo. */
async function decidePhoto(
  report: HazardReport,
  deps: OutboxDeps,
): Promise<HazardReport> {
  const filing = report.filing as ReportFiling;
  if (filing.photoDecided) return report;
  if (!report.photoUri) {
    return withFiling(
      { ...report, evidenceStatus: 'none' },
      { photoDecided: true, photo: null },
    );
  }

  const read = await deps.readPhoto(report.photoUri);
  let problem: string | null = null;
  if (!read.ok) {
    problem = `The photo could not be read on this phone (${read.reason}), so the report goes without it.`;
  } else if (read.photo.sizeBytes > MAX_PHOTO_BYTES) {
    problem = `The photo is ${(read.photo.sizeBytes / 1024 / 1024).toFixed(1)} MB, over the ${
      MAX_PHOTO_BYTES / 1024 / 1024
    } MB the control room accepts, so the report goes without it.`;
  } else if (!ACCEPTED_PHOTO_TYPES.includes(read.photo.mimeType)) {
    problem = `The photo is a ${read.photo.mimeType} file, which the control room does not accept, so the report goes without it.`;
  }

  if (problem || !read.ok) {
    return withFiling(
      { ...report, evidenceStatus: 'failed' },
      { photoDecided: true, photo: null, evidenceError: problem },
    );
  }
  const photo: DeclaredPhoto = {
    sha256: read.photo.sha256,
    sizeBytes: read.photo.sizeBytes,
    mimeType: read.photo.mimeType,
  };
  return withFiling(
    { ...report, evidenceStatus: 'not_sent' },
    { photoDecided: true, photo },
  );
}

async function fileReport(
  report: HazardReport,
  deps: OutboxDeps,
): Promise<HazardReport> {
  if (!hasPosition(report)) {
    return withFiling(
      { ...report, syncStatus: 'failed' },
      {
        lastError: 'mobile.outbox.noPosition',
      },
    );
  }

  const decided = await decidePhoto(report, deps);
  const filing = decided.filing as ReportFiling;
  const localId = localPhotoId(decided);
  const body: IncidentBody = {
    type: incidentTypeFor(decided.category),
    captured_at: capturedAt(decided),
    location: { latitude: decided.latitude, longitude: decided.longitude },
    location_source: decided.positionSource ?? 'device_gps',
    accuracy_m:
      decided.accuracyMeters !== undefined && decided.accuracyMeters > 0
        ? decided.accuracyMeters
        : null,
    note: noteFor(decided),
    attachments: filing.photo
      ? [
          {
            local_id: localId,
            mime_type: filing.photo.mimeType,
            bytes: filing.photo.sizeBytes,
            sha256: filing.photo.sha256,
            captured_at: capturedAt(decided),
          },
        ]
      : undefined,
  };

  const created = await deps.createIncident(body, filing.key);
  if (!created.ok) {
    return withFiling(
      { ...decided, syncStatus: isRetryable(created) ? 'queued' : 'failed' },
      { lastError: created.reason },
    );
  }

  const instruction = filing.photo
    ? created.data.upload_instructions.find((item) => item.local_id === localId)
    : undefined;
  const accepted: HazardReport = {
    ...decided,
    syncStatus: 'synced',
    controlRoomIncidentId: created.data.incident.id,
  };
  if (filing.photo && !instruction) {
    return withFiling(
      { ...accepted, evidenceStatus: 'failed' },
      {
        lastError: null,
        evidenceError: 'mobile.outbox.noUploadPath',
      },
    );
  }
  return withFiling(accepted, {
    lastError: null,
    target: instruction ? targetFrom(instruction) : null,
  });
}

async function sendEvidence(
  report: HazardReport,
  deps: OutboxDeps,
): Promise<HazardReport> {
  let filing = report.filing as ReportFiling;
  const incidentId = report.controlRoomIncidentId as string;
  const declared = filing.photo as DeclaredPhoto;
  let target = filing.target as UploadTarget;

  const giveUp = (
    reason: string,
    status: EvidenceStatus = 'failed',
  ): HazardReport => ({
    ...report,
    evidenceStatus: status,
    filing: { ...filing, target, evidenceError: reason },
  });
  // Nothing was decided; the same step is tried again on the next pass.
  const later = (reason: string): HazardReport => ({
    ...report,
    filing: { ...filing, target, evidenceError: reason },
  });
  // The path is unusable (closed, or refused by the server's clock where this
  // phone's clock disagrees).
  const freshPathNextTime = (reason: string): HazardReport =>
    filing.reuploads < MAX_REUPLOADS
      ? {
          ...report,
          evidenceStatus: 'not_sent',
          filing: {
            ...filing,
            uploaded: false,
            reuploads: filing.reuploads + 1,
            target: { ...target, expiresAt: new Date(0).toISOString() },
            evidenceError: `${reason} The photo will be sent again.`,
          },
        }
      : giveUp(reason);

  if (!filing.uploaded) {
    const read = await deps.readPhoto(report.photoUri as string);
    if (!read.ok) {
      return giveUp('mobile.outbox.photoUnreadable');
    }
    if (
      read.photo.sha256 !== declared.sha256 ||
      read.photo.sizeBytes !== declared.sizeBytes
    ) {
      return giveUp('mobile.outbox.photoChanged');
    }

    // A renewal whose answer was lost is replayed, and the replayed path may
    // itself have closed since; then that one is renewed in turn.
    for (
      let renewals = 0;
      Date.parse(target.expiresAt) - deps.now().getTime() <
      RENEW_BEFORE_EXPIRY_MS;
      renewals += 1
    ) {
      if (renewals === 3) return later('mobile.outbox.pathsClosed');
      const renewed = await deps.addAttachment(
        incidentId,
        {
          local_id: localPhotoId(report),
          mime_type: declared.mimeType,
          bytes: declared.sizeBytes,
          sha256: declared.sha256,
          captured_at: capturedAt(report),
        },
        deriveKey(filing.key, `renew:${target.attachmentId}`),
      );
      if (!renewed.ok) {
        return isRetryable(renewed)
          ? later(renewed.reason)
          : giveUp(renewed.reason);
      }
      target = targetFrom(renewed.data.upload_instruction);
      filing = { ...filing, target };
    }

    if (declared.sizeBytes > target.maxBytes) {
      return giveUp('mobile.outbox.photoTooLarge');
    }

    const written = await deps.uploadPhoto(target.path, read.photo);
    if (!written.ok && written.kind !== 'exists') {
      return written.kind === 'network'
        ? later(written.reason)
        : freshPathNextTime(written.reason);
    }
    filing = { ...filing, target, uploaded: true };
    report = { ...report, evidenceStatus: 'uploaded', filing };
  }

  const completed = await deps.completeAttachment(
    incidentId,
    target.attachmentId,
    { sha256: declared.sha256, size_bytes: declared.sizeBytes },
    deriveKey(filing.key, `complete:${target.attachmentId}`),
  );
  if (!completed.ok) {
    return isRetryable(completed)
      ? later(completed.reason)
      : giveUp(completed.reason);
  }

  const { status, reason } = completed.data.verification;
  if (status === 'verified' || status === 'already_verified') {
    return {
      ...report,
      evidenceStatus: 'verified',
      filing: { ...filing, evidenceError: null },
    };
  }
  if (status === 'expired' || status === 'missing') {
    return freshPathNextTime(
      `The control room found no photo to check (${status}).`,
    );
  }
  return giveUp(
    `The control room did not accept the photo (${status}${reason ? `: ${reason}` : ''}).`,
    status === 'rejected' ? 'rejected' : 'failed',
  );
}

/**
 * Takes one report as far as it can go now, and returns it as it then stands.
 */
export async function sendReport(
  input: HazardReport,
  deps: OutboxDeps,
): Promise<HazardReport> {
  const work = pendingWork(input);
  if (!work || !input.filing) return input;

  let report = withFiling(input, {
    attempts: input.filing.attempts + 1,
    lastAttemptAt: deps.now().toISOString(),
  });
  try {
    if (work === 'file') {
      report = await fileReport(report, deps);
      if (pendingWork(report) !== 'evidence') return report;
    }
    return await sendEvidence(report, deps);
  } catch (error) {
    const reason =
      error instanceof Error ? error.message : 'mobile.outbox.unexpected';
    return report.syncStatus === 'synced'
      ? withFiling(report, { evidenceError: reason })
      : withFiling({ ...report, syncStatus: 'queued' }, { lastError: reason });
  }
}

/** Counts for the person, after a pass over the outbox. */
export interface OutboxSummary {
  /** Reports the control room accepted on this pass. */
  sent: number;
  /** Still on the phone, to be tried again. */
  waiting: number;
  /** Refused on the merits; waiting for the person. */
  refused: number;
  /** The most recent reason something is still waiting, if any. */
  reason: string | null;
}

export function summarise(
  before: HazardReport[],
  after: HazardReport[],
): OutboxSummary {
  const was = new Map(before.map((report) => [report.id, report]));
  let sent = 0;
  let waiting = 0;
  let refused = 0;
  let reason: string | null = null;
  for (const report of after) {
    const previous = was.get(report.id);
    if (
      report.syncStatus === 'synced' &&
      previous &&
      previous.syncStatus !== 'synced'
    )
      sent += 1;
    if (report.syncStatus === 'failed') refused += 1;
    if (pendingWork(report) !== null) {
      waiting += 1;
      reason =
        report.filing?.lastError ?? report.filing?.evidenceError ?? reason;
    }
  }
  return { sent, waiting, refused, reason };
}

/**
 * Brings reports saved by earlier versions of the app into the current shape.
 */
export function migrateLegacyReports(rows: unknown[]): HazardReport[] {
  const migrated: HazardReport[] = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const legacy = row as Record<string, unknown> & Partial<HazardReport>;
    const {
      trustBadge: _trustBadge,
      corroborationCount: _corroborationCount,
      riskScore: _riskScore,
      riskModelSource: _riskModelSource,
      ...rest
    } = legacy as Record<string, unknown>;
    const base = rest as unknown as HazardReport;
    const filedWithApi = typeof legacy.controlRoomIncidentId === 'string';
    const reportedAt =
      typeof legacy.capturedAtIso === 'string' &&
      !Number.isNaN(Date.parse(legacy.capturedAtIso))
        ? legacy.capturedAtIso
        : typeof legacy.reportedAt === 'string' &&
            !Number.isNaN(Date.parse(legacy.reportedAt))
          ? legacy.reportedAt
          : new Date(0).toISOString();
    migrated.push({
      ...base,
      reportedAt,
      syncStatus: filedWithApi ? 'synced' : 'failed',
      filing: {
        ...newFiling(
          typeof legacy.idempotencyKey === 'string'
            ? legacy.idempotencyKey
            : 'legacy',
        ),
        photoDecided: true,
        lastError: filedWithApi ? null : 'mobile.outbox.legacy',
      },
    });
  }
  return migrated;
}
