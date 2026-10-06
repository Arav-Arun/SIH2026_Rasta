// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';

import type { ServerWorkspace } from './identity';
import {
  OFFLINE_IDENTITY_MAX_AGE_MS,
  forgetVerifiedWorkspace,
  recallVerifiedWorkspace,
  rememberVerifiedWorkspace,
} from './offline-identity';

const WORKSPACE: ServerWorkspace = {
  capabilities: ['incident:create'],
  districts: [],
  displayName: 'Field officer',
  identity: {
    active: true,
    grants: [
      {
        role: 'field_officer',
        districtId: 'd1',
        validFrom: '2026-01-01T00:00:00Z',
      },
    ],
    organizationId: 'o1',
    profileId: 'p1',
    userId: 'u1',
  },
  locale: 'en',
  organizationMode: 'local_demo',
  serverTime: '2026-09-26T08:00:00Z',
};
const VERIFIED = new Date('2026-09-26T08:00:00Z');

describe('the last server-confirmed workspace, for offline starts', () => {
  afterEach(() => forgetVerifiedWorkspace());

  it('is recalled for the same user inside its age limit', () => {
    rememberVerifiedWorkspace('u1', WORKSPACE, VERIFIED);
    const later = new Date(VERIFIED.getTime() + 60 * 60 * 1000);
    expect(
      recallVerifiedWorkspace('u1', later)?.workspace.identity.profileId,
    ).toBe('p1');
  });

  it('is never recalled for another user on the same device', () => {
    rememberVerifiedWorkspace('u1', WORKSPACE, VERIFIED);
    expect(recallVerifiedWorkspace('u2', VERIFIED)).toBeNull();
  });

  it('ages out rather than opening screens indefinitely', () => {
    rememberVerifiedWorkspace('u1', WORKSPACE, VERIFIED);
    const tooLate = new Date(
      VERIFIED.getTime() + OFFLINE_IDENTITY_MAX_AGE_MS + 1,
    );
    expect(recallVerifiedWorkspace('u1', tooLate)).toBeNull();
  });

  it('is gone after sign-out or a refusal', () => {
    rememberVerifiedWorkspace('u1', WORKSPACE, VERIFIED);
    forgetVerifiedWorkspace();
    expect(recallVerifiedWorkspace('u1', VERIFIED)).toBeNull();
  });

  it('holds no token', () => {
    rememberVerifiedWorkspace('u1', WORKSPACE, VERIFIED);
    const raw =
      window.localStorage.getItem('rasta.lastVerifiedWorkspace.v1') ?? '';
    expect(raw).not.toMatch(/access_token|refresh_token|eyJ/);
  });
});
