import { afterEach, describe, expect, it, vi } from 'vitest';

import { deriveKey, newUuid } from './ids';

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('write identifiers', () => {
  it('are UUIDs, the only kind of key the API accepts', () => {
    expect(newUuid()).toMatch(UUID);
    expect(newUuid()).not.toBe(newUuid());
  });

  it('are still UUIDs where randomUUID is missing, as on plain-HTTP LAN pages', () => {
    const getRandomValues = globalThis.crypto.getRandomValues.bind(
      globalThis.crypto,
    );
    vi.stubGlobal('crypto', { getRandomValues });
    const key = newUuid();
    expect(key).toMatch(UUID);
    expect(key[14]).toBe('4');
  });

  it('derive the same key for the same step, and a different one for another', () => {
    const base = '11111111-1111-4111-8111-111111111111';
    expect(deriveKey(base, 'renew:att-1')).toMatch(UUID);
    expect(deriveKey(base, 'renew:att-1')).toBe(deriveKey(base, 'renew:att-1'));
    expect(deriveKey(base, 'renew:att-1')).not.toBe(
      deriveKey(base, 'renew:att-2'),
    );
    expect(deriveKey(base, 'renew:att-1')).not.toBe(
      deriveKey('22222222-2222-4222-8222-222222222222', 'renew:att-1'),
    );
  });

  it('derive exactly what the phone app derives, so both clients agree', () => {
    // The same value is pinned in apps/mobile/services/reportOutbox.test.ts.
    expect(
      deriveKey('11111111-1111-4111-8111-111111111111', 'complete:att-1'),
    ).toBe('3625921c-929c-8276-a704-326336827cd5');
  });
});
