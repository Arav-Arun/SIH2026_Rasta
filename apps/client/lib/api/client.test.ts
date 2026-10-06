import { describe, expect, it, vi } from 'vitest';

import {
  ApiClientError,
  REQUEST_TIMEOUT_MS,
  apiRequest,
  getCurrentIdentity,
  resolveApiBaseUrl,
} from './client';

const identityResponse = {
  capabilities: ['route:plan'],
  organization: {
    id: 'organization-1',
    mode: 'local_demo',
    name: 'Synthetic local demonstration',
  },
  profile: {
    active: true,
    display_name: 'Test user',
    id: 'profile-1',
    locale: 'en',
    user_id: 'user-1',
  },
  roles: [
    {
      district_id: 'district-1',
      id: 'grant-1',
      role: 'district_dispatcher',
      valid_from: '2026-09-12T00:00:00Z',
      valid_to: null,
    },
  ],
  server_time: '2026-09-12T00:00:00Z',
};

describe('API identity client', () => {
  it('sends a bearer token only to the configured API origin', async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify(identityResponse), {
          headers: {
            'content-type': 'application/json',
            'x-request-id': 'req_ok',
          },
          status: 200,
        }),
    );

    const result = await getCurrentIdentity({
      accessToken: 'compact.jwt.token',
      baseUrl: 'https://api.example.test',
      fetchImpl,
    });

    expect(result.profile.user_id).toBe('user-1');
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://api.example.test/v1/me',
      expect.objectContaining({
        headers: {
          Accept: 'application/json',
          Authorization: 'Bearer compact.jwt.token',
        },
        method: 'GET',
      }),
    );
  });

  it('keeps the defined server error code and request id', async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'identity_bootstrap_unavailable',
              details: {},
              message: 'Identity bootstrap is not configured.',
              request_id: 'req_api_503',
            },
          }),
          {
            headers: {
              'content-type': 'application/json',
              'x-request-id': 'req_header_503',
            },
            status: 503,
          },
        ),
    );

    await expect(
      getCurrentIdentity({
        accessToken: 'compact.jwt.token',
        baseUrl: 'https://api.example.test',
        fetchImpl,
      }),
    ).rejects.toMatchObject({
      code: 'identity_bootstrap_unavailable',
      kind: 'unavailable',
      requestId: 'req_api_503',
      status: 503,
    });
  });

  it('rejects an unexpected successful identity document', async () => {
    await expect(
      getCurrentIdentity({
        accessToken: 'compact.jwt.token',
        baseUrl: 'https://api.example.test',
        fetchImpl: async () =>
          new Response(JSON.stringify({ profile: {} }), {
            headers: { 'content-type': 'application/json' },
            status: 200,
          }),
      }),
    ).rejects.toMatchObject({
      code: 'invalid_identity_response',
      kind: 'invalid_response',
      status: 200,
    });
  });

  it('rejects unsafe API base URLs and malformed tokens before a request', () => {
    expect(() => resolveApiBaseUrl('https://api.example.test/v1')).toThrow(
      ApiClientError,
    );
    expect(() =>
      resolveApiBaseUrl('https://user:pass@api.example.test'),
    ).toThrow(ApiClientError);
  });

  it('does not call fetch when the API origin is unsafe', async () => {
    const fetchImpl = vi.fn();

    await expect(
      getCurrentIdentity({
        accessToken: 'compact.jwt.token',
        baseUrl: 'https://api.example.test/untrusted-path',
        fetchImpl,
      }),
    ).rejects.toMatchObject({
      code: 'invalid_api_base_url',
      kind: 'configuration',
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe('a request that never answers', () => {
  /** A fetch that hangs until its signal is aborted, as a stalled link does. */
  function hangingFetch() {
    return vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(
              new DOMException('The operation was aborted.', 'AbortError'),
            ),
          );
        }),
    );
  }

  it('ends as a retryable network failure at the deadline, not never', async () => {
    vi.useFakeTimers();
    try {
      const pending = apiRequest('/v1/incidents', {
        accessToken: 'compact.jwt.token',
        baseUrl: 'https://api.example.test',
        fetchImpl: hangingFetch() as unknown as typeof fetch,
        method: 'POST',
        body: {},
        idempotencyKey: '00000000-0000-4000-8000-000000000001',
      });
      const outcome = expect(pending).rejects.toMatchObject({
        code: 'api_timeout',
        kind: 'network',
      });
      await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
      await outcome;
    } finally {
      vi.useRealTimers();
    }
  });

  it("still lets the caller cancel, and reports that as the caller's abort", async () => {
    const controller = new AbortController();
    const pending = apiRequest('/v1/incidents', {
      accessToken: 'compact.jwt.token',
      baseUrl: 'https://api.example.test',
      fetchImpl: hangingFetch() as unknown as typeof fetch,
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('bounds the identity check too, so the app does not wait on it forever', async () => {
    vi.useFakeTimers();
    try {
      const pending = getCurrentIdentity({
        accessToken: 'compact.jwt.token',
        baseUrl: 'https://api.example.test',
        fetchImpl: hangingFetch() as unknown as typeof fetch,
      });
      const outcome = expect(pending).rejects.toBeInstanceOf(ApiClientError);
      await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
      await outcome;
    } finally {
      vi.useRealTimers();
    }
  });
});
