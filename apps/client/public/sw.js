/* RASTA service worker. */

const VERSION = 'rasta-sw-v1';
const SHELL_CACHE = `${VERSION}-shell`;
const ASSET_CACHE = `${VERSION}-assets`;
const PACK_CACHE = `${VERSION}-packs`;
const API_SNAPSHOT_CACHE = `${VERSION}-api-snapshot`;

const OWNED_CACHES = [SHELL_CACHE, ASSET_CACHE, PACK_CACHE, API_SNAPSHOT_CACHE];

/** Enough to open the app and explain itself with no network. */
const SHELL_URLS = [
  '/overview',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
];

/** Where the worker keeps the notification wording the page last gave it. */
const PUSH_COPY_URL = '/__rasta/push-copy';
const DEFAULT_PUSH_COPY = {
  title: 'RASTA: new alert',
  body: 'Open the alert inbox to read it.',
};

/** Header added to a snapshot so a screen can say how old its data is. */
const SNAPSHOT_HEADER = 'x-rasta-snapshot-stored-at';

/* --- what must never be cached ------------------------------------------- */

/** Supabase auth, and anything that looks like a token exchange. */
function isAuthRequest(url) {
  return (
    url.pathname.startsWith('/auth/v1/') ||
    url.pathname.includes('/token') ||
    url.pathname.endsWith('/v1/me')
  );
}

/** A URL carrying its own credential. Storing it outlives the signature. */
function isSignedMedia(url) {
  return (
    url.pathname.includes('/storage/v1/') ||
    url.searchParams.has('token') ||
    url.searchParams.has('X-Amz-Signature') ||
    url.searchParams.has('signature')
  );
}

function isApiRead(url, request) {
  return (
    request.method === 'GET' &&
    /\/v1\//.test(url.pathname) &&
    !isAuthRequest(url)
  );
}

function isPack(url) {
  return url.pathname.startsWith('/packs/');
}

/** Build output: content-hashed, so it can be cached forever once seen. */
function isImmutableAsset(url) {
  return (
    url.pathname.startsWith('/_next/static/') ||
    url.pathname.startsWith('/assets/') ||
    /\.[0-9a-f]{8,}\.(js|css|woff2?|ttf|otf)$/.test(url.pathname)
  );
}

function isDocument(request) {
  return request.mode === 'navigate' || request.destination === 'document';
}

/* --- lifecycle ------------------------------------------------------------ */

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // Individually, so one unreachable URL does not fail the whole install.
      await Promise.all(
        SHELL_URLS.map(async (url) => {
          try {
            await cache.add(new Request(url, { cache: 'reload' }));
          } catch {
            /* Left out of the shell rather than blocking activation. */
          }
        }),
      );
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter(
            (name) =>
              name.startsWith('rasta-sw-') && !OWNED_CACHES.includes(name),
          )
          .map((name) => caches.delete(name)),
      );
      // Not claiming clients here: an open tab keeps the worker it loaded with
      // until the person accepts the update, so a refresh never lands under a
      // half-finished form or a running tracking session.
    })(),
  );
});

self.addEventListener('message', (event) => {
  // The page asks for the swap once it has checked there is nothing pending.
  if (event.data?.type === 'rasta:activate-update') {
    void self.skipWaiting();
  }
  if (event.data?.type === 'rasta:push-copy') {
    // The notification's wording in the reader's language. A headers-only push
    // carries no text, so the worker keeps the last copy the page gave it.
    const copy = {
      title: String(event.data.title ?? ''),
      body: String(event.data.body ?? ''),
    };
    if (copy.title && copy.body) {
      event.waitUntil(
        caches.open(SHELL_CACHE).then((cache) =>
          cache.put(
            PUSH_COPY_URL,
            new Response(JSON.stringify(copy), {
              headers: { 'content-type': 'application/json' },
            }),
          ),
        ),
      );
    }
  }
  if (event.data?.type === 'rasta:clear-local-data') {
    // Sign-out and "discard local unsent data" both land here. Packs are
    // included: they are scoped to the workspace that downloaded them.
    event.waitUntil(
      Promise.all(OWNED_CACHES.map((name) => caches.delete(name))),
    );
  }
});

/* --- strategies ----------------------------------------------------------- */

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

async function networkFirstDocument(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cache = await caches.open(SHELL_CACHE);
    const hit =
      (await cache.match(request)) ?? (await cache.match('/overview'));
    if (hit) return hit;
    throw error;
  }
}

async function networkFirstApiRead(request) {
  const cache = await caches.open(API_SNAPSHOT_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) {
      // Stamped with the time it was stored, so a screen serving it offline can
      // say how old it is instead of showing it as current.
      const body = await response.clone().blob();
      const headers = new Headers(response.headers);
      headers.set(SNAPSHOT_HEADER, new Date().toISOString());
      await cache.put(
        request,
        new Response(body, {
          status: response.status,
          statusText: response.statusText,
          headers,
        }),
      );
    }
    return response;
  } catch (error) {
    const hit = await cache.match(request);
    if (hit) return hit;
    throw error;
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Cross-origin: only Supabase auth and signed media reach here, and both are
  // network-only. Everything else is left to the browser untouched.
  if (url.origin !== self.location.origin) {
    if (isAuthRequest(url) || isSignedMedia(url)) return;
    return;
  }

  // Never this worker's business.
  if (request.method !== 'GET') return;
  if (isAuthRequest(url) || isSignedMedia(url)) return;

  if (isPack(url)) {
    // Versioned in the filename and checksum-verified by the page before it is
    // used, so once fetched it never needs re-fetching.
    event.respondWith(cacheFirst(request, PACK_CACHE));
    return;
  }
  if (isImmutableAsset(url)) {
    event.respondWith(cacheFirst(request, ASSET_CACHE));
    return;
  }
  if (isApiRead(url, request)) {
    event.respondWith(networkFirstApiRead(request));
    return;
  }
  if (isDocument(request)) {
    event.respondWith(networkFirstDocument(request));
  }
});

/* --- notifications -------------------------------------------------------- */

/**
 * Where a notification may take someone: a path inside this app, nothing else.
 * Anything that is not a plain same-origin path lands on the inbox.
 */
function safeDeepLink(value) {
  if (
    typeof value !== 'string' ||
    !value.startsWith('/') ||
    value.startsWith('//')
  ) {
    return '/alerts';
  }
  if (value.includes('\\')) return '/alerts';
  try {
    const url = new URL(value, self.location.origin);
    return url.origin === self.location.origin
      ? `${url.pathname}${url.search}`
      : '/alerts';
  } catch {
    return '/alerts';
  }
}

async function notificationCopy() {
  try {
    const cache = await caches.open(SHELL_CACHE);
    const hit = await cache.match(PUSH_COPY_URL);
    if (hit) {
      const copy = await hit.json();
      if (typeof copy?.title === 'string' && typeof copy?.body === 'string')
        return copy;
    }
  } catch {
    /* The default wording is still a correct notification. */
  }
  return DEFAULT_PUSH_COPY;
}

self.addEventListener('push', (event) => {
  event.waitUntil(
    (async () => {
      // The server sends headers only, so there is normally no data. A payload,
      // if one ever arrives, may name where to go and nothing more.
      let data = {};
      try {
        data = event.data ? event.data.json() : {};
      } catch {
        data = {};
      }
      const copy = await notificationCopy();
      await self.registration.showNotification(copy.title, {
        body: copy.body,
        // One notification however many alerts arrived: the inbox has them all.
        tag: 'rasta-alert',
        renotify: true,
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-192.png',
        data: { url: safeDeepLink(data?.deep_link) },
      });
    })(),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = safeDeepLink(event.notification.data?.url);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: 'window',
        includeUncontrolled: true,
      });
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        const focused = await client.focus();
        if ('navigate' in focused)
          await focused.navigate(target).catch(() => undefined);
        return;
      }
      await self.clients.openWindow(target);
    })(),
  );
});
