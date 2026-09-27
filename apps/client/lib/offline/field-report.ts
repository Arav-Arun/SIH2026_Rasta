import { newUuid } from '@/lib/ids';

import { getOfflineDatabase } from './db';
import { enqueueMutation } from './outbox';
import type { MediaRecord } from './types';

/**
 * A field report filed from the browser, including when there is no network.
 */

/** Matches `MAX_ATTACHMENT_BYTES` in services/api/app/incidents.py. */
export const MAX_EVIDENCE_BYTES = 5 * 1024 * 1024;

/** Matches `ALLOWED_MIME_TYPES` in services/api/app/evidence.py. */
const EVIDENCE_MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const;

export const INCIDENT_TYPES = [
  'landslide_debris',
  'flooding',
  'bridge_damage',
  'road_damage',
  'congestion',
  'road_reopened',
  'other',
] as const;

export type IncidentType = (typeof INCIDENT_TYPES)[number];

export type LocationSource = 'device_gps' | 'manual_pin';

export interface FieldReportDraft {
  type: IncidentType | '';
  note: string;
  latitude: number | null;
  longitude: number | null;
  /**
   * `device_gps` only for a fix the device measured; a coordinate typed in or
   * taken from the map is a `manual_pin`.
   */
  locationSource: LocationSource | null;
  accuracyMetres: number | null;
  fixCapturedAt: string | null;
  segmentId: string | null;
  segmentLabel: string | null;
  mediaId: string | null;
}

export const EMPTY_DRAFT: FieldReportDraft = {
  type: '',
  note: '',
  latitude: null,
  longitude: null,
  locationSource: null,
  accuracyMetres: null,
  fixCapturedAt: null,
  segmentId: null,
  segmentLabel: null,
  mediaId: null,
};

/** One draft per person: a shared phone never shows one officer another's form. */
function draftIdFor(profileId: string | null): string {
  return `field-report:${profileId ?? 'unattributed'}`;
}

export async function loadReportDraft(
  profileId: string | null,
): Promise<FieldReportDraft | null> {
  const db = getOfflineDatabase();
  if (!db) return null;
  const row = await db.drafts.get(draftIdFor(profileId));
  if (!row) return null;
  return { ...EMPTY_DRAFT, ...(row.body as Partial<FieldReportDraft>) };
}

/** Returns false when there is nowhere durable to put it, so the form can say so. */
export async function saveReportDraft(
  profileId: string | null,
  draft: FieldReportDraft,
): Promise<boolean> {
  const db = getOfflineDatabase();
  if (!db) return false;
  await db.drafts.put({
    draftId: draftIdFor(profileId),
    kind: 'incident',
    profileId,
    updatedAt: new Date().toISOString(),
    body: { ...draft },
  });
  return true;
}

/** Whether a queued upload still needs these bytes. */
async function mediaIsQueued(mediaId: string): Promise<boolean> {
  const db = getOfflineDatabase();
  if (!db) return false;
  const references = await db.outbox
    .where('type')
    .equals('incident.attachment.upload')
    .filter((row) => row.body.media_id === mediaId)
    .count();
  return references > 0;
}

/** Throws the draft away, and any photo that was only ever attached to it. */
export async function discardReportDraft(
  profileId: string | null,
): Promise<void> {
  const db = getOfflineDatabase();
  if (!db) return;
  await db.transaction('rw', db.drafts, db.media, db.outbox, async () => {
    const row = await db.drafts.get(draftIdFor(profileId));
    const mediaId = (row?.body as Partial<FieldReportDraft> | undefined)
      ?.mediaId;
    // A photo already handed to the outbox belongs to a queued report now.
    if (mediaId && !(await mediaIsQueued(mediaId)))
      await db.media.delete(mediaId);
    await db.drafts.delete(draftIdFor(profileId));
  });
}

async function sha256OfBytes(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * A Blob's bytes. `Blob.arrayBuffer()` where it exists; FileReader otherwise,
 * which older Android WebViews and the jsdom test environment still need.
 */
function readBlobBytes(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === 'function') return blob.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () =>
      reject(reader.error ?? new Error('The photo could not be read.'));
    reader.readAsArrayBuffer(blob);
  });
}

export async function sha256Hex(blob: Blob): Promise<string> {
  return sha256OfBytes(await readBlobBytes(blob));
}

/** The stored photo as something a browser can show or send. */
export function mediaBlob(media: MediaRecord): Blob {
  return new Blob([new Uint8Array(media.bytes)], { type: media.mimeType });
}

type StoreEvidenceResult =
  | { ok: true; media: MediaRecord }
  | {
      ok: false;
      reason:
        | 'too_large'
        | 'unsupported_type'
        | 'empty'
        | 'storage_unavailable';
    };

/** Measures a photo and keeps its bytes on this device. */
export async function storeEvidence(options: {
  blob: Blob;
  mimeType: string;
  capturedAt: string;
  profileId: string | null;
}): Promise<StoreEvidenceResult> {
  const { blob, mimeType, capturedAt, profileId } = options;
  if (!(EVIDENCE_MIME_TYPES as readonly string[]).includes(mimeType)) {
    return { ok: false, reason: 'unsupported_type' };
  }
  if (blob.size === 0) return { ok: false, reason: 'empty' };
  // The original is the evidence, so it is never recompressed to fit.
  if (blob.size > MAX_EVIDENCE_BYTES) return { ok: false, reason: 'too_large' };

  const db = getOfflineDatabase();
  if (!db) return { ok: false, reason: 'storage_unavailable' };

  // Copied into an ArrayBuffer of this page's own realm: a buffer handed back
  // by another realm (a WebView bridge, a test DOM) is not reliably cloneable
  // into IndexedDB, and a photo that comes back empty is a lost photo.
  const bytes = new Uint8Array(await readBlobBytes(blob)).slice().buffer;
  const media: MediaRecord = {
    mediaId: `media_${newUuid()}`,
    mutationId: null,
    profileId,
    mimeType,
    sizeBytes: bytes.byteLength,
    sha256: await sha256OfBytes(bytes),
    bytes,
    createdAt: capturedAt,
  };
  await db.media.put(media);
  return { ok: true, media };
}

export async function loadMedia(mediaId: string): Promise<MediaRecord | null> {
  const db = getOfflineDatabase();
  if (!db) return null;
  return (await db.media.get(mediaId)) ?? null;
}

/** A photo removed from the draft before it was ever queued. */
export async function dropUnqueuedMedia(mediaId: string): Promise<void> {
  const db = getOfflineDatabase();
  if (!db) return;
  if (!(await mediaIsQueued(mediaId))) await db.media.delete(mediaId);
}

/** What the form must have before it can be queued. */
export function missingForSubmit(
  draft: FieldReportDraft,
): ('type' | 'location')[] {
  const missing: ('type' | 'location')[] = [];
  if (!draft.type) missing.push('type');
  if (
    draft.latitude === null ||
    draft.longitude === null ||
    !Number.isFinite(draft.latitude) ||
    !Number.isFinite(draft.longitude) ||
    Math.abs(draft.latitude) > 90 ||
    Math.abs(draft.longitude) > 180 ||
    draft.locationSource === null
  ) {
    missing.push('location');
  }
  return missing;
}

export interface QueuedReport {
  createMutationId: string;
  uploadMutationId: string | null;
  completeMutationId: string | null;
}

/**
 * Queues a finished draft as a report (and its photo, when there is one) and
 * clears the draft, atomically.
 */
export async function queueFieldReport(options: {
  profileId: string | null;
  draft: FieldReportDraft;
  /** When the observation was made. Defaults to now, i.e. when it was filed. */
  observedAt?: string;
}): Promise<QueuedReport> {
  const { profileId, draft } = options;
  if (missingForSubmit(draft).length > 0) {
    throw new Error('The report is not complete enough to queue.');
  }
  const db = getOfflineDatabase();
  if (!db) {
    throw new Error(
      'This browser is not keeping local data, so the report cannot be queued.',
    );
  }

  return db.transaction('rw', db.outbox, db.media, db.drafts, async () => {
    const media = draft.mediaId ? await db.media.get(draft.mediaId) : undefined;
    const localId = media ? `photo-${media.mediaId.slice(-12)}` : null;

    const create = await enqueueMutation({
      type: 'incident.create',
      profileId,
      body: {
        type: draft.type,
        captured_at: options.observedAt ?? new Date().toISOString(),
        location: { latitude: draft.latitude, longitude: draft.longitude },
        location_source: draft.locationSource,
        // An accuracy belongs to a measurement; a pin has none.
        accuracy_m:
          draft.locationSource === 'device_gps' && draft.accuracyMetres
            ? Math.min(draft.accuracyMetres, 100_000)
            : null,
        note: draft.note.trim() ? draft.note.trim() : null,
        proposed_segment_ids: draft.segmentId ? [draft.segmentId] : [],
        attachments:
          media && localId
            ? [
                {
                  local_id: localId,
                  mime_type: media.mimeType,
                  bytes: media.sizeBytes,
                  sha256: media.sha256,
                  captured_at: media.createdAt,
                },
              ]
            : [],
      },
    });

    let upload: string | null = null;
    let complete: string | null = null;
    if (media && localId) {
      const uploadMutation = await enqueueMutation({
        type: 'incident.attachment.upload',
        profileId,
        dependsOn: [create.mutationId],
        body: {
          create_mutation_id: create.mutationId,
          local_id: localId,
          media_id: media.mediaId,
        },
      });
      const completeMutation = await enqueueMutation({
        type: 'incident.attachment.complete',
        profileId,
        dependsOn: [uploadMutation.mutationId],
        body: {
          upload_mutation_id: uploadMutation.mutationId,
          sha256: media.sha256,
          size_bytes: media.sizeBytes,
        },
      });
      upload = uploadMutation.mutationId;
      complete = completeMutation.mutationId;
    }

    await db.drafts.delete(draftIdFor(profileId));
    return {
      createMutationId: create.mutationId,
      uploadMutationId: upload,
      completeMutationId: complete,
    };
  });
}
