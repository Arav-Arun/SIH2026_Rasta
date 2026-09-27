/** Offline mutation envelope. */

export type MutationType =
  | 'incident.create'
  /** Writes evidence bytes to the storage path the API issued. */
  | 'incident.attachment.upload'
  | 'incident.attachment.complete'
  | 'incident.review'
  | 'inspection.accept'
  | 'inspection.start'
  | 'inspection.complete';

/** States from the sync state machine. */
export type MutationState =
  | 'queued'
  | 'uploading'
  | 'accepted'
  | 'conflict'
  | 'needs_action';

export interface MutationEnvelope {
  /** Local primary key. Stable across retries and app restarts. */
  mutationId: string;
  /** What the server deduplicates on. Never regenerated on retry. */
  idempotencyKey: string;
  type: MutationType;
  schemaVersion: number;
  createdAt: string;
  updatedAt: string;
  /** Who queued it, so a purge on sign-out can find their rows. */
  profileId: string | null;
  /**
   * Mutations that must be accepted first. Attachment completion waits for the
   * incident shell that owns it.
   */
  dependsOn: string[];
  body: Record<string, unknown>;
  attemptCount: number;
  state: MutationState;
  /** Why it is not queued any more, in words a person can act on. */
  lastError: string | null;
  /** Server response kept for replay, so the UI can show what was recorded. */
  result: Record<string, unknown> | null;
}

/** A form the user has started but not submitted. */
export interface DraftRecord {
  draftId: string;
  kind: 'incident' | 'review' | 'receipt';
  profileId: string | null;
  updatedAt: string;
  body: Record<string, unknown>;
}

/** Evidence bytes held until their upload is confirmed complete. */
export interface MediaRecord {
  mediaId: string;
  mutationId: string | null;
  profileId: string | null;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  /**
   * The bytes themselves, as an ArrayBuffer rather than a Blob: every IndexedDB
   * implementation can clone one, where Blob storage has been unreliable in
   * some browsers and is not cloneable at all under test.
   */
  bytes: ArrayBuffer;
  createdAt: string;
}

/** Scoped server data cached for offline reads. */
export interface EntityRecord {
  key: string;
  collection: string;
  profileId: string | null;
  fetchedAt: string;
  payload: unknown;
}
