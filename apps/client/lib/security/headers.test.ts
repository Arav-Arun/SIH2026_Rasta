import { matchHeaders } from 'vinext/config/config-matchers';
import { describe, expect, it } from 'vitest';

import config from '../../next.config';

const requestContext = {
  headers: new Headers(),
  cookies: {},
  query: new URLSearchParams(),
  host: 'localhost',
};

async function headersFor(pathname: string) {
  const configured = (await config('phase-production-build').headers?.()) ?? [];
  // The config declares no `has` or `missing` conditions, so the plain shape is enough.
  const rules = configured.map(({ source, headers }) => ({ source, headers }));
  const matched = matchHeaders(
    pathname,
    rules,
    requestContext,
    { basePath: '', hadBasePath: true },
    () => {},
  );
  return new Map(
    matched.map((header) => [header.key.toLowerCase(), header.value]),
  );
}

describe('security headers', () => {
  // Vinext does not match `/:path*` against `/`, which once left the landing
  // page without any of these. The root is checked by name for that reason.
  it.each(['/', '/sign-in', '/map', '/planner'])(
    '%s carries the full set',
    async (pathname) => {
      const headers = await headersFor(pathname);
      for (const name of [
        'content-security-policy',
        'x-content-type-options',
        'referrer-policy',
        'x-frame-options',
        'permissions-policy',
      ]) {
        expect(headers.has(name), `${pathname} is missing ${name}`).toBe(true);
      }
    },
  );

  it('lets the demo video load only from the privacy-friendly YouTube domain', async () => {
    const policy = (await headersFor('/')).get('content-security-policy') ?? '';
    expect(policy).toContain('frame-src https://www.youtube-nocookie.com;');
    expect(policy).not.toMatch(/frame-src[^;]*www\.youtube\.com/);
    expect(policy).toContain("frame-ancestors 'none'");
  });
});
