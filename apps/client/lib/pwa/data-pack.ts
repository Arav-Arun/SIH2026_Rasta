/** Downloading and verifying the bounded offline pack. */

import {
  readLocalValue,
  subscribeLocalValue,
  writeLocalValue,
} from '@/lib/local-store';

const MANIFEST_URL = '/packs/manifest.json';
const PACK_STORAGE_KEY = 'rasta.datapack.active';

export type PackManifestEntry = {
  pack_id: string;
  version: string;
  url: string;
  bytes: number;
  sha256: string;
  created_at: string;
  treat_as_current_until: string;
  mode: string;
  bbox: { north: number; south: number; east: number; west: number };
  counts: { edges: number; nodes: number; facilities: number };
  attribution: string;
  license: string;
  notice: string;
};

type ActivePack = {
  pack_id: string;
  version: string;
  sha256: string;
  bytes: number;
  created_at: string;
  treat_as_current_until: string;
  activated_at: string;
  counts: PackManifestEntry['counts'];
  attribution: string;
  license: string;
};

type DownloadOutcome =
  | { ok: true; pack: ActivePack }
  | {
      ok: false;
      reason: string;
      code: 'unreachable' | 'checksum_mismatch' | 'unreadable';
    };

/** What the app is currently holding, if anything. */
export function readActivePack(): ActivePack | null {
  const raw = readLocalValue(PACK_STORAGE_KEY);
  // `useSyncExternalStore` compares snapshots by reference.
  if (raw === lastRaw) return lastPack;
  lastRaw = raw;
  try {
    lastPack = raw ? (JSON.parse(raw) as ActivePack) : null;
  } catch {
    lastPack = null;
  }
  return lastPack;
}

let lastRaw: string | null = null;
let lastPack: ActivePack | null = null;

export function subscribeToActivePack(listener: () => void) {
  return subscribeLocalValue(PACK_STORAGE_KEY, listener);
}

export async function fetchManifest(
  signal?: AbortSignal,
): Promise<PackManifestEntry[]> {
  const response = await fetch(MANIFEST_URL, { signal, cache: 'no-store' });
  if (!response.ok)
    throw new Error(`The pack manifest is not available (${response.status}).`);
  const body = (await response.json()) as { packs?: PackManifestEntry[] };
  return body.packs ?? [];
}

async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Fetch one pack and activate it only if its bytes hash to the manifest value.
 */
export async function downloadPack(
  entry: PackManifestEntry,
  options: {
    signal?: AbortSignal;
    onProgress?: (fraction: number) => void;
  } = {},
): Promise<DownloadOutcome> {
  let bytes: ArrayBuffer;
  try {
    const response = await fetch(entry.url, { signal: options.signal });
    if (!response.ok) {
      return {
        ok: false,
        code: 'unreachable',
        reason: `The pack could not be downloaded (${response.status}). The pack you already have is unchanged.`,
      };
    }
    bytes = await readWithProgress(response, entry.bytes, options.onProgress);
  } catch {
    return {
      ok: false,
      code: 'unreachable',
      reason:
        'The download did not finish. The pack you already have is unchanged.',
    };
  }

  const checksum = await sha256Hex(bytes);
  if (checksum !== entry.sha256) {
    return {
      ok: false,
      code: 'checksum_mismatch',
      reason:
        'The downloaded pack does not match its published checksum, so it was discarded. ' +
        'The pack you already have is unchanged.',
    };
  }

  // Only parsed after the checksum matched: the hash is over the bytes as
  // published, and parsing first would mean trusting content not yet verified.
  try {
    JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch {
    return {
      ok: false,
      code: 'unreadable',
      reason:
        'The pack matched its checksum but could not be read, so it was discarded.',
    };
  }

  const pack: ActivePack = {
    pack_id: entry.pack_id,
    version: entry.version,
    sha256: entry.sha256,
    bytes: entry.bytes,
    created_at: entry.created_at,
    treat_as_current_until: entry.treat_as_current_until,
    activated_at: new Date().toISOString(),
    counts: entry.counts,
    attribution: entry.attribution,
    license: entry.license,
  };
  writeLocalValue(PACK_STORAGE_KEY, JSON.stringify(pack));
  if (readActivePack()?.version !== pack.version) {
    return {
      ok: false,
      code: 'unreadable',
      reason: 'The pack was verified but this browser would not keep it.',
    };
  }
  return { ok: true, pack };
}

async function readWithProgress(
  response: Response,
  expectedBytes: number,
  onProgress?: (fraction: number) => void,
): Promise<ArrayBuffer> {
  if (!response.body || !onProgress) return response.arrayBuffer();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      chunks.push(value);
      received += value.byteLength;
      // Reported against the manifest's own byte count, so the bar is accurate
      // even when the server sends no Content-Length.
      onProgress(expectedBytes > 0 ? Math.min(1, received / expectedBytes) : 0);
    }
  }
  const merged = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged.buffer;
}

/** Days until the pack's topology should be treated as out of date. */
export function packAgeDays(pack: ActivePack, now = new Date()): number {
  return Math.floor(
    (now.getTime() - new Date(pack.created_at).getTime()) / 86_400_000,
  );
}

export function packIsStale(pack: ActivePack, now = new Date()): boolean {
  return now.getTime() > new Date(pack.treat_as_current_until).getTime();
}

export function forgetActivePack(): void {
  writeLocalValue(PACK_STORAGE_KEY, null);
}
