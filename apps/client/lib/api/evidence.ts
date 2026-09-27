import { getSupabaseBrowserClient } from '@/lib/auth/supabase';

/** Must match `EVIDENCE_BUCKET` in services/api/app/evidence.py. */
const EVIDENCE_BUCKET = 'evidence';

/** How long a generated link stays usable. Short: these are viewed, not shared. */
const SIGNED_URL_TTL_SECONDS = 300;

type EvidenceUrlResult =
  | { ok: true; url: string; expiresInSeconds: number }
  | { ok: false; reason: string };

/** Creates a short-lived link to one evidence object. */
export async function createEvidenceUrl(
  storageKey: string,
): Promise<EvidenceUrlResult> {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) {
    return { ok: false, reason: 'Evidence storage is not configured.' };
  }

  try {
    const { data, error } = await supabase.storage
      .from(EVIDENCE_BUCKET)
      .createSignedUrl(storageKey, SIGNED_URL_TTL_SECONDS);

    if (error) {
      // A denied read and a missing object are both reported by the server;
      // neither is something the reviewer can fix, so say what happened.
      return { ok: false, reason: error.message };
    }
    if (!data?.signedUrl) {
      return { ok: false, reason: 'Storage returned no link for this file.' };
    }

    return {
      ok: true,
      url: data.signedUrl,
      expiresInSeconds: SIGNED_URL_TTL_SECONDS,
    };
  } catch (err) {
    return {
      ok: false,
      reason:
        err instanceof Error ? err.message : 'Evidence storage is unreachable.',
    };
  }
}

type EvidenceUploadResult =
  | { ok: true }
  | { ok: false; kind: 'exists' | 'refused' | 'network'; message: string };

/** Sorts a storage failure into what the outbox needs to know. */
function classifyStorageFailure(error: unknown): EvidenceUploadResult {
  const failure = (error ?? {}) as {
    status?: unknown;
    statusCode?: unknown;
    code?: unknown;
    message?: unknown;
  };
  const message =
    typeof failure.message === 'string' && failure.message
      ? failure.message
      : 'The upload failed.';
  const status =
    typeof failure.status === 'number'
      ? failure.status
      : typeof failure.statusCode === 'string'
        ? Number(failure.statusCode)
        : NaN;

  if (
    failure.code === 'ResourceAlreadyExists' ||
    failure.statusCode === '409' ||
    status === 409 ||
    /already exists/i.test(message)
  ) {
    return { ok: false, kind: 'exists', message };
  }
  if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
    return { ok: false, kind: 'refused', message };
  }
  return { ok: false, kind: 'network', message };
}

/**
 * Writes evidence bytes to the exact path the API issued, with the reporter's
 * own session.
 */
export async function uploadEvidenceObject(request: {
  bucket: string;
  path: string;
  blob: Blob;
  contentType: string;
}): Promise<EvidenceUploadResult> {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) {
    return {
      ok: false,
      kind: 'network',
      message: 'Evidence storage is not configured.',
    };
  }
  try {
    const { error } = await supabase.storage
      .from(request.bucket)
      .upload(request.path, request.blob, {
        contentType: request.contentType,
        // Never overwrite: evidence already at this path is what the server
        // will verify, and replacing it would defeat the point.
        upsert: false,
      });
    if (error) return classifyStorageFailure(error);
    return { ok: true };
  } catch (error) {
    return classifyStorageFailure(error);
  }
}
