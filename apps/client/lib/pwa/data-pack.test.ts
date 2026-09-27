// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  downloadPack,
  forgetActivePack,
  packAgeDays,
  packIsStale,
  readActivePack,
  type PackManifestEntry,
} from './data-pack';
import { decideUpdate } from './service-worker';

const BODY = JSON.stringify({ pack_id: 'pilot-shillong', edges: [] });

/** The real sha256 of BODY, computed with the same API the code uses. */
async function digestOf(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(text),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

function entry(overrides: Partial<PackManifestEntry> = {}): PackManifestEntry {
  return {
    pack_id: 'pilot-shillong',
    version: 'osm-shillong-test',
    url: '/packs/pilot-shillong.osm-shillong-test.json',
    bytes: BODY.length,
    sha256: 'replaced-by-the-test',
    created_at: '2026-09-01T00:00:00Z',
    treat_as_current_until: '2026-11-30T00:00:00Z',
    mode: 'recorded',
    bbox: { north: 25.585, south: 25.56, east: 91.9, west: 91.875 },
    counts: { edges: 2860, nodes: 1236, facilities: 30 },
    attribution: '© OpenStreetMap contributors',
    license: 'ODbL-1.0',
    notice: 'Baseline topology only.',
    ...overrides,
  };
}

function respondWith(body: string, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body, { status })),
  );
}

describe('downloading a data pack', () => {
  beforeEach(() => {
    forgetActivePack();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    forgetActivePack();
  });

  it('activates a pack whose bytes match the published checksum', async () => {
    respondWith(BODY);
    const result = await downloadPack(entry({ sha256: await digestOf(BODY) }));

    expect(result.ok).toBe(true);
    expect(readActivePack()?.version).toBe('osm-shillong-test');
  });

  it('reads the same snapshot until the active pack changes', async () => {
    // useSyncExternalStore compares by reference: a fresh object per read made
    // the settings screen re-render forever once a pack was activated.
    respondWith(BODY);
    await downloadPack(entry({ sha256: await digestOf(BODY) }));
    const first = readActivePack();
    expect(readActivePack()).toBe(first);

    forgetActivePack();
    expect(readActivePack()).toBeNull();
  });

  it('discards a pack whose checksum does not match, and keeps the previous one', async () => {
    // Arrive at a known-good active pack first.
    respondWith(BODY);
    await downloadPack(entry({ sha256: await digestOf(BODY) }));
    const before = readActivePack();
    expect(before).not.toBeNull();

    // Now the server serves different bytes under the same manifest entry,
    // a corrupted transfer, a truncated file, or a substituted pack.
    respondWith(`${BODY} tampered`);
    const result = await downloadPack(
      entry({ version: 'osm-shillong-newer', sha256: await digestOf(BODY) }),
    );

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe('checksum_mismatch');
    // The point of the check: the pack that was working is still the active one.
    expect(readActivePack()).toEqual(before);
  });

  it('leaves the previous pack active when the download never finishes', async () => {
    respondWith(BODY);
    await downloadPack(entry({ sha256: await digestOf(BODY) }));
    const before = readActivePack();

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down');
      }),
    );
    const result = await downloadPack(
      entry({ version: 'osm-shillong-newer', sha256: await digestOf(BODY) }),
    );

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe('unreachable');
    expect(readActivePack()).toEqual(before);
  });

  it('records nothing at all when the very first download fails', async () => {
    respondWith('', 503);
    const result = await downloadPack(entry({ sha256: await digestOf(BODY) }));

    expect(result.ok).toBe(false);
    // Not a half-activated pack, and not an empty one presented as present.
    expect(readActivePack()).toBeNull();
  });
});

describe('how old a pack is', () => {
  const pack = {
    pack_id: 'pilot-shillong',
    version: 'v',
    sha256: 'x',
    bytes: 1,
    created_at: '2026-09-01T00:00:00Z',
    treat_as_current_until: '2026-11-30T00:00:00Z',
    activated_at: '2026-09-01T00:00:00Z',
    counts: { edges: 1, nodes: 1, facilities: 1 },
    attribution: '© OpenStreetMap contributors',
    license: 'ODbL-1.0',
  };

  it('reports age in whole days so a screen can show it', () => {
    expect(packAgeDays(pack, new Date('2026-09-26T00:00:00Z'))).toBe(25);
  });

  it('is not stale inside its guidance window', () => {
    expect(packIsStale(pack, new Date('2026-10-15T00:00:00Z'))).toBe(false);
  });

  it('is stale after it, rather than being hidden', () => {
    // A stale pack keeps working and says it is stale. Hiding it would leave a
    // driver with no map at all and no explanation.
    expect(packIsStale(pack, new Date('2026-12-01T00:00:00Z'))).toBe(true);
  });
});

describe('when an update may be offered', () => {
  it('holds a downloaded update while anything is waiting to be sent', () => {
    expect(decideUpdate(true, true, 2)).toEqual({
      kind: 'update_held',
      pending: 2,
    });
  });

  it('holds it when the outbox cannot be read, as if something were in it', () => {
    expect(decideUpdate(true, true, null)).toEqual({
      kind: 'update_held',
      pending: 1,
    });
  });

  it('offers it once nothing is pending', () => {
    expect(decideUpdate(true, true, 0)).toEqual({ kind: 'update_waiting' });
  });

  it('says nothing when there is no update or no worker', () => {
    expect(decideUpdate(true, false, 3)).toEqual({ kind: 'idle' });
    expect(decideUpdate(false, true, 0)).toEqual({ kind: 'unsupported' });
  });
});
