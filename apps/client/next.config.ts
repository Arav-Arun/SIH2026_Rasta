import type { NextConfig } from 'next';

const DEVELOPMENT_PHASE = 'phase-development-server';

/** Map and glyph servers the map screens load from. Nothing else is external. */
const OSM_TILES = 'https://tile.openstreetmap.org';
const MAPLIBRE_GLYPHS = 'https://demotiles.maplibre.org';

function originOf(url: string | undefined): string | null {
  if (!url?.trim()) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/**
 * The Content Security Policy starts restrictive: only approved map, style and
 * image endpoints are allowlisted.
 */
function contentSecurityPolicy(development: boolean): string {
  const api =
    originOf(process.env.NEXT_PUBLIC_API_BASE_URL) ?? 'http://localhost:8000';
  const supabase = originOf(process.env.NEXT_PUBLIC_SUPABASE_URL);
  const connect = [
    "'self'",
    api,
    supabase,
    supabase?.replace(/^http/, 'ws'),
    OSM_TILES,
    MAPLIBRE_GLYPHS,
    ...(development ? ['ws:', 'wss:'] : []),
  ].filter(Boolean);
  const images = ["'self'", 'data:', 'blob:', OSM_TILES, supabase].filter(
    Boolean,
  );

  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${development ? " 'unsafe-eval'" : ''}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${images.join(' ')}`,
    "font-src 'self' data:",
    `connect-src ${connect.join(' ')}`,
    // MapLibre runs its tile workers from blob: URLs; the offline worker is same-origin.
    "worker-src 'self' blob:",
    "child-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
}

export default function config(phase: string): NextConfig {
  const development = phase === DEVELOPMENT_PHASE;
  return {
    async headers() {
      return [
        {
          // A worker script must be revalidated, or an update can sit behind
          // an hour of HTTP caching in browsers that honour it.
          source: '/sw.js',
          headers: [{ key: 'Cache-Control', value: 'no-cache' }],
        },
        {
          source: '/:path*',
          headers: [
            {
              key: 'Content-Security-Policy',
              value: contentSecurityPolicy(development),
            },
            { key: 'X-Content-Type-Options', value: 'nosniff' },
            // The origin only, never a path, and only to other sites.
            {
              key: 'Referrer-Policy',
              value: 'strict-origin-when-cross-origin',
            },
            { key: 'X-Frame-Options', value: 'DENY' },
            // The field report asks for a position and a photo, from this origin only.
            {
              key: 'Permissions-Policy',
              value:
                'geolocation=(self), camera=(self), microphone=(), payment=(), usb=()',
            },
          ],
        },
      ];
    },
  };
}
