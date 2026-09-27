/** Notifications on this device. */

import { registerPush, revokePush } from '@/lib/api/operations';
import { newUuid } from '@/lib/ids';

import { serviceWorkerSupported } from './service-worker';

export type PushSupport = 'supported' | 'no_api' | 'no_service_worker';

/** What this browser can do, before anyone is asked for permission. */
export function pushSupport(): PushSupport {
  if (
    typeof window === 'undefined' ||
    !('Notification' in window) ||
    !('PushManager' in window) ||
    !('serviceWorker' in navigator)
  ) {
    return 'no_api';
  }
  return serviceWorkerSupported() ? 'supported' : 'no_service_worker';
}

/** The public key as the Push API wants it: raw bytes from base64url. */
function applicationServerKey(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/** What the API stores for a browser subscription: endpoint and client keys. */
function subscriptionBody(subscription: PushSubscription, userAgent?: string) {
  const json = subscription.toJSON();
  const keys: Record<string, string> = {};
  for (const [name, value] of Object.entries(json.keys ?? {})) {
    if (typeof value === 'string') keys[name] = value;
  }
  return {
    platform: 'web' as const,
    endpoint: subscription.endpoint,
    keys,
    user_agent: userAgent?.slice(0, 400) ?? null,
  };
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (pushSupport() !== 'supported') return null;
  return (await navigator.serviceWorker.getRegistration()) ?? null;
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const worker = await registration();
  if (!worker) return null;
  return worker.pushManager.getSubscription();
}

/** The notification's wording, in the reader's language, kept by the worker. */
type NotificationCopy = { title: string; body: string };

async function sendCopy(copy: NotificationCopy): Promise<void> {
  const worker = await registration();
  worker?.active?.postMessage({ type: 'rasta:push-copy', ...copy });
}

type EnableOutcome =
  | { kind: 'subscribed' }
  | { kind: 'denied' }
  | { kind: 'no_worker' }
  | { kind: 'failed'; reason: string };

/**
 * Ask for permission, subscribe with the push service, and register the
 * subscription with the API.
 */
export async function enablePush(options: {
  accessToken: string;
  publicKey: string;
  copy: NotificationCopy;
}): Promise<EnableOutcome> {
  const worker = await registration();
  if (!worker) return { kind: 'no_worker' };
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return { kind: 'denied' };

  let subscription: PushSubscription;
  try {
    subscription =
      (await worker.pushManager.getSubscription()) ??
      (await worker.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: applicationServerKey(options.publicKey),
      }));
  } catch (error) {
    return {
      kind: 'failed',
      reason: error instanceof Error ? error.message : String(error),
    };
  }

  try {
    await registerPush(
      { accessToken: options.accessToken },
      subscriptionBody(subscription, navigator.userAgent),
      newUuid(),
    );
  } catch (error) {
    await subscription.unsubscribe().catch(() => undefined);
    return {
      kind: 'failed',
      reason: error instanceof Error ? error.message : String(error),
    };
  }
  await sendCopy(options.copy);
  return { kind: 'subscribed' };
}

/**
 * Stop notifications on this device: the browser subscription first, so the
 * device stops receiving even when the API cannot be reached, then the server's
 * record.
 */
export async function disablePush(accessToken: string | null): Promise<void> {
  const subscription = await currentSubscription().catch(() => null);
  if (!subscription) return;
  const endpoint = subscription.endpoint;
  await subscription.unsubscribe().catch(() => undefined);
  if (accessToken) {
    await revokePush({ accessToken }, endpoint).catch(() => undefined);
  }
}
