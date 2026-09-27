import type { ApiErrorEnvelope, MeGetResponse } from './contracts';

const DEFAULT_API_BASE_URL = 'http://localhost:8000';

export type ApiErrorKind =
  | 'configuration'
  | 'network'
  | 'invalid_response'
  | 'session'
  | 'forbidden'
  | 'unavailable'
  | 'http';

export class ApiClientError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly kind: ApiErrorKind;
  readonly requestId: string | null;
  readonly status: number | null;

  constructor({
    code,
    details = {},
    kind,
    message,
    requestId = null,
    status = null,
  }: {
    code: string;
    details?: Record<string, unknown>;
    kind: ApiErrorKind;
    message: string;
    requestId?: string | null;
    status?: number | null;
  }) {
    super(message);
    this.name = 'ApiClientError';
    this.code = code;
    this.details = details;
    this.kind = kind;
    this.requestId = requestId;
    this.status = status;
  }
}

type CurrentIdentityRequestOptions = {
  accessToken: string;
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
};

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isRole(value: unknown): boolean {
  return (
    typeof value === 'string' &&
    [
      'state_coordinator',
      'district_dispatcher',
      'field_officer',
      'driver',
      'admin',
      'reviewer',
    ].includes(value)
  );
}

function isMeResponse(value: unknown): value is MeGetResponse {
  if (!isPlainRecord(value)) return false;

  const profile = value.profile;
  const organization = value.organization;
  if (!isPlainRecord(profile) || !isPlainRecord(organization)) return false;
  if (
    !isNonEmptyString(profile.id) ||
    !isNonEmptyString(profile.user_id) ||
    !isNonEmptyString(profile.display_name) ||
    !isNonEmptyString(profile.locale) ||
    typeof profile.active !== 'boolean' ||
    !isNonEmptyString(organization.id) ||
    !isNonEmptyString(organization.name) ||
    !['local_demo', 'hosted_demo', 'pilot'].includes(
      String(organization.mode),
    ) ||
    !Array.isArray(value.roles) ||
    !Array.isArray(value.capabilities) ||
    !value.capabilities.every(isNonEmptyString) ||
    !isNonEmptyString(value.server_time)
  ) {
    return false;
  }

  return value.roles.every((role) => {
    if (!isPlainRecord(role)) return false;
    return (
      isNonEmptyString(role.id) &&
      isRole(role.role) &&
      (role.district_id === null || isNonEmptyString(role.district_id)) &&
      isNonEmptyString(role.valid_from) &&
      (role.valid_to === null || isNonEmptyString(role.valid_to))
    );
  });
}

function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!isPlainRecord(value) || !isPlainRecord(value.error)) return false;
  const error = value.error;
  return (
    isNonEmptyString(error.code) &&
    isNonEmptyString(error.message) &&
    isNonEmptyString(error.request_id) &&
    (error.details === undefined || isPlainRecord(error.details))
  );
}

function errorKindFor(status: number, code: string): ApiErrorKind {
  if (status === 401) return 'session';
  if (status === 403) return 'forbidden';
  if (status === 503 || code === 'identity_bootstrap_unavailable') {
    return 'unavailable';
  }
  return 'http';
}

function safeResponseError(
  status: number,
  requestId: string | null,
  payload: unknown,
): ApiClientError {
  if (isApiErrorEnvelope(payload)) {
    return new ApiClientError({
      code: payload.error.code,
      details: payload.error.details ?? {},
      kind: errorKindFor(status, payload.error.code),
      message: payload.error.message,
      requestId: payload.error.request_id || requestId,
      status,
    });
  }

  return new ApiClientError({
    code: 'unexpected_response',
    kind: errorKindFor(status, 'unexpected_response'),
    message: 'The service returned an unexpected response.',
    requestId,
    status,
  });
}

async function readJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.toLowerCase().includes('application/json')) return undefined;
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/**
 * Resolve a browser-safe API origin. The public build configuration never
 * accepts credentials, path components, or a non-HTTP(S) protocol here.
 */
export function resolveApiBaseUrl(configuredValue?: string): string {
  const candidate =
    configuredValue?.trim() ||
    process.env.NEXT_PUBLIC_API_BASE_URL?.trim() ||
    DEFAULT_API_BASE_URL;

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new ApiClientError({
      code: 'invalid_api_base_url',
      kind: 'configuration',
      message: 'The API address is not configured as a valid HTTP(S) origin.',
    });
  }

  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== '/' ||
    parsed.search ||
    parsed.hash
  ) {
    throw new ApiClientError({
      code: 'invalid_api_base_url',
      kind: 'configuration',
      message:
        'The API address must be an HTTP(S) origin without credentials or a path.',
    });
  }

  return parsed.origin;
}

/** How long one API request may take. */
export const REQUEST_TIMEOUT_MS = 30_000;

/** The caller's signal, plus the deadline. `timedOut` tells the two apart. */
function withDeadline(
  signal: AbortSignal | undefined,
  ms = REQUEST_TIMEOUT_MS,
) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, ms);
  const cancel = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    done: () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
    },
  };
}

export type ApiRequestOptions = {
  accessToken: string;
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
  /** Query string values; null/undefined entries are omitted. */
  query?: Record<string, string | number | boolean | null | undefined>;
  method?: 'GET' | 'POST';
  body?: unknown;
  /** Required on every write; the caller generates a UUID before queueing. */
  idempotencyKey?: string;
};

function assertToken(accessToken: string, fetchImpl: unknown) {
  if (
    !isNonEmptyString(accessToken) ||
    accessToken !== accessToken.trim() ||
    /\s/.test(accessToken)
  ) {
    throw new ApiClientError({
      code: 'invalid_client_session',
      kind: 'session',
      message: 'A valid session is required.',
    });
  }
  if (typeof fetchImpl !== 'function') {
    throw new ApiClientError({
      code: 'fetch_unavailable',
      kind: 'configuration',
      message: 'This client cannot contact the API in the current environment.',
    });
  }
}

/** Authenticated JSON request. */
export async function apiRequest(
  path: string,
  {
    accessToken,
    baseUrl,
    fetchImpl = globalThis.fetch,
    signal,
    query,
    method = 'GET',
    body,
    idempotencyKey,
  }: ApiRequestOptions,
): Promise<{ payload: unknown; requestId: string | null; status: number }> {
  assertToken(accessToken, fetchImpl);
  const url = new URL(path, resolveApiBaseUrl(baseUrl));
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined) continue;
    url.searchParams.set(key, String(value));
  }
  const headers: Record<string, string> = {
    Accept: 'application/json',
    Authorization: `Bearer ${accessToken}`,
  };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

  const deadline = withDeadline(signal);
  let response: Response;
  let payload: unknown;
  try {
    response = await fetchImpl(url.toString(), {
      cache: 'no-store',
      credentials: 'omit',
      headers,
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: deadline.signal,
    });
    payload = await readJson(response);
  } catch (error) {
    // The caller cancelled: theirs to handle, as before.
    if (signal?.aborted && !deadline.timedOut()) throw error;
    throw new ApiClientError({
      code: deadline.timedOut() ? 'api_timeout' : 'api_unreachable',
      kind: 'network',
      message: deadline.timedOut()
        ? 'The service took too long to answer. It will be tried again.'
        : 'The service could not be reached. Check the connection and try again.',
    });
  } finally {
    deadline.done();
  }
  const requestId = response.headers.get('x-request-id');
  if (!response.ok)
    throw safeResponseError(response.status, requestId, payload);
  return { payload, requestId, status: response.status };
}

/**
 * Fetch the only server-authoritative identity bootstrap endpoint. A browser
 * session is never treated as a role grant until this call returns valid data.
 */
export async function getCurrentIdentity({
  accessToken,
  baseUrl,
  fetchImpl = globalThis.fetch,
  signal,
}: CurrentIdentityRequestOptions): Promise<MeGetResponse> {
  if (
    !isNonEmptyString(accessToken) ||
    accessToken !== accessToken.trim() ||
    /\s/.test(accessToken)
  ) {
    throw new ApiClientError({
      code: 'invalid_client_session',
      kind: 'session',
      message: 'A valid session is required.',
    });
  }
  if (typeof fetchImpl !== 'function') {
    throw new ApiClientError({
      code: 'fetch_unavailable',
      kind: 'configuration',
      message: 'This client cannot contact the API in the current environment.',
    });
  }

  const apiBaseUrl = resolveApiBaseUrl(baseUrl);
  const deadline = withDeadline(signal);
  let response: Response;
  let payload: unknown;
  try {
    response = await fetchImpl(`${apiBaseUrl}/v1/me`, {
      cache: 'no-store',
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      method: 'GET',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: deadline.signal,
    });
    payload = await readJson(response);
  } catch {
    throw new ApiClientError({
      code: deadline.timedOut() ? 'api_timeout' : 'api_unreachable',
      kind: 'network',
      message:
        'The identity service could not be reached. Check the connection and try again.',
    });
  } finally {
    deadline.done();
  }

  const requestId = response.headers.get('x-request-id');
  if (!response.ok)
    throw safeResponseError(response.status, requestId, payload);
  if (!isMeResponse(payload)) {
    throw new ApiClientError({
      code: 'invalid_identity_response',
      kind: 'invalid_response',
      message: 'The identity service returned an invalid response.',
      requestId,
      status: response.status,
    });
  }

  return payload;
}
