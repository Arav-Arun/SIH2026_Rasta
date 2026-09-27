import * as Crypto from 'expo-crypto';

import { classifyStorageFailure, type UploadOutcome } from './reportOutbox';
import { supabase } from './supabase';

/** Evidence upload, in the order the server requires: */

/** Must match `EVIDENCE_BUCKET` in api/app/evidence.py. */
const EVIDENCE_BUCKET = 'evidence';

export interface PhotoBytes {
  bytes: Uint8Array;
  sha256: string;
  sizeBytes: number;
  mimeType: string;
}

export type PhotoReadResult =
  | { ok: true; photo: PhotoBytes }
  | { ok: false; reason: string };

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/** Reads a local photo and measures it, so the server can verify it later. */
export async function readPhotoForUpload(
  uri: string,
): Promise<PhotoReadResult> {
  try {
    const response = await fetch(uri);
    if (!response.ok) {
      return {
        ok: false,
        reason: `Could not read the photo (${response.status}).`,
      };
    }

    const blob = await response.blob();
    const buffer = await new Response(blob).arrayBuffer();
    const digest = await Crypto.digest(
      Crypto.CryptoDigestAlgorithm.SHA256,
      buffer,
    );

    return {
      ok: true,
      photo: {
        bytes: new Uint8Array(buffer),
        sha256: toHex(digest),
        sizeBytes: buffer.byteLength,
        // The camera writes JPEG; blob.type is empty on some Android builds,
        // so fall back rather than declaring a type the server would reject.
        mimeType: blob.type || 'image/jpeg',
      },
    };
  } catch (err) {
    return {
      ok: false,
      reason:
        err instanceof Error ? err.message : 'The photo could not be read.',
    };
  }
}

/** Writes the bytes to the exact path the API issued. */
export async function uploadEvidenceObject(
  path: string,
  photo: PhotoBytes,
): Promise<UploadOutcome> {
  try {
    const { error } = await supabase.storage
      .from(EVIDENCE_BUCKET)
      .upload(path, photo.bytes, {
        contentType: photo.mimeType,
        upsert: false,
      });
    return error ? classifyStorageFailure(error) : { ok: true };
  } catch (err) {
    return classifyStorageFailure(err);
  }
}
