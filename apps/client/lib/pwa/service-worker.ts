/** Registering the service worker, and deciding when it is safe to swap it. */

type UpdateState =
  | { kind: 'unsupported' }
  | { kind: 'idle' }
  | { kind: 'update_waiting' }
  | { kind: 'update_held'; pending: number };

/**
 * Whether a downloaded update may be offered: only once it is downloaded *and*
 * nothing is waiting to be sent, because the reload it needs discards whatever
 * is in flight.
 */
export function decideUpdate(
  supported: boolean,
  waiting: boolean,
  pending: number | null,
): UpdateState {
  if (!supported) return { kind: 'unsupported' };
  if (!waiting) return { kind: 'idle' };
  if (pending === null || pending > 0)
    return { kind: 'update_held', pending: pending ?? 1 };
  return { kind: 'update_waiting' };
}

const SERVICE_WORKER_URL = '/sw.js';

/** Whether to register at all. */
export function serviceWorkerSupported(): boolean {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator))
    return false;
  const { protocol, hostname } = window.location;
  if (protocol === 'https:') return true;
  const loopback =
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '[::1]';
  return loopback && process.env.NEXT_PUBLIC_ENABLE_SERVICE_WORKER === '1';
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!serviceWorkerSupported()) return null;
  try {
    return await navigator.serviceWorker.register(SERVICE_WORKER_URL, {
      scope: '/',
    });
  } catch {
    // A failed registration is not an error the person can act on: the app
    // works online without it. It must not block anything.
    return null;
  }
}

/** Apply a waiting update. Only called once the caller has checked it is safe. */
export function applyUpdate(registration: ServiceWorkerRegistration): void {
  registration.waiting?.postMessage({ type: 'rasta:activate-update' });
}

/** Drop every cache this worker owns, including downloaded packs. */
export async function clearLocalCaches(): Promise<void> {
  if (!('serviceWorker' in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration();
  registration?.active?.postMessage({ type: 'rasta:clear-local-data' });
}
