import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';

import { describe, expect, it, vi } from 'vitest';

/**
 * The service worker's push and notification-click handlers, run from
 * the real public/sw.js against a stand-in worker global.
 */

type Listener = (event: Record<string, unknown>) => void;
const ORIGIN = 'https://rasta.test';

function loadWorker() {
  const listeners = new Map<string, Listener>();
  const stores = new Map<string, Map<string, Response>>();
  const caches = {
    open: async (name: string) => {
      const store = stores.get(name) ?? new Map<string, Response>();
      stores.set(name, store);
      return {
        match: async (key: string) => store.get(key)?.clone(),
        put: async (key: string, response: Response) => {
          store.set(key, response);
        },
      };
    },
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
  };
  const showNotification = vi.fn(async () => undefined);
  const openWindow = vi.fn(async () => null);
  const windows: {
    url: string;
    focus: () => Promise<unknown>;
    navigate: ReturnType<typeof vi.fn>;
  }[] = [];
  const skipWaiting = vi.fn();
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, listener: Listener) =>
      listeners.set(type, listener),
    registration: { showNotification },
    clients: { matchAll: vi.fn(async () => windows), openWindow },
    skipWaiting,
  };
  const source = readFileSync(join(__dirname, '../../public/sw.js'), 'utf8');
  runInNewContext(source, { self, caches, Response, URL, console });

  async function dispatch(type: string, event: Record<string, unknown>) {
    const pending: Promise<unknown>[] = [];
    listeners.get(type)?.({
      ...event,
      waitUntil: (promise: Promise<unknown>) => pending.push(promise),
    });
    await Promise.all(pending);
  }

  function addWindow(url: string) {
    const navigate = vi.fn(async () => undefined);
    const client = { url, navigate, focus: async () => client };
    windows.push(client);
    return client;
  }

  return {
    dispatch,
    showNotification,
    openWindow,
    addWindow,
    skipWaiting,
    caches,
  };
}

function pushEvent(data?: unknown) {
  return {
    data: data === undefined ? null : { json: () => data },
  };
}

describe('service worker notifications', () => {
  it('a headers-only push shows one notification that opens the inbox', async () => {
    const worker = loadWorker();
    await worker.dispatch('push', pushEvent());
    expect(worker.showNotification).toHaveBeenCalledTimes(1);
    const [title, options] = worker.showNotification.mock
      .calls[0] as unknown as [
      string,
      { body: string; tag: string; data: { url: string } },
    ];
    expect(title).toBe('RASTA: new alert');
    expect(options.body).toBe('Open the alert inbox to read it.');
    expect(options.tag).toBe('rasta-alert');
    expect(options.data.url).toBe('/alerts');
  });

  it('uses the wording the page last gave it, in the reader language', async () => {
    const worker = loadWorker();
    await worker.dispatch('message', {
      data: {
        type: 'rasta:push-copy',
        title: 'RASTA: नया अलर्ट',
        body: 'पढ़ने के लिए अलर्ट इनबॉक्स खोलें।',
      },
    });
    await worker.dispatch('push', pushEvent());
    const [title] = worker.showNotification.mock.calls[0] as unknown as [
      string,
    ];
    expect(title).toBe('RASTA: नया अलर्ट');
  });

  it('never takes anyone off the app, whatever a payload names', async () => {
    const worker = loadWorker();
    const cases: [unknown, string][] = [
      ['https://evil.test/phish', '/alerts'],
      ['//evil.test/phish', '/alerts'],
      ['/\\evil.test', '/alerts'],
      ['javascript:alert(1)', '/alerts'],
      [42, '/alerts'],
      ['/incidents?status=pending', '/incidents?status=pending'],
    ];
    for (const [deepLink, expected] of cases) {
      worker.showNotification.mockClear();
      await worker.dispatch('push', pushEvent({ deep_link: deepLink }));
      const [, options] = worker.showNotification.mock.calls[0] as unknown as [
        string,
        { data: { url: string } },
      ];
      expect(options.data.url, String(deepLink)).toBe(expected);
    }
  });

  it('a click focuses the open app and takes it to the inbox', async () => {
    const worker = loadWorker();
    worker.addWindow('https://elsewhere.test/page');
    const app = worker.addWindow(`${ORIGIN}/overview`);
    const close = vi.fn();
    await worker.dispatch('notificationclick', {
      notification: { close, data: { url: '/alerts' } },
    });
    expect(close).toHaveBeenCalled();
    expect(app.navigate).toHaveBeenCalledWith('/alerts');
    expect(worker.openWindow).not.toHaveBeenCalled();
  });

  it('a click with no app open opens one at the inbox', async () => {
    const worker = loadWorker();
    await worker.dispatch('notificationclick', {
      notification: { close: vi.fn(), data: { url: 'https://evil.test/' } },
    });
    expect(worker.openWindow).toHaveBeenCalledWith('/alerts');
  });
});

describe('service worker updates', () => {
  it('waits to take over until the page asks, after its own checks', async () => {
    const worker = loadWorker();
    await worker.dispatch('message', { data: { type: 'something-else' } });
    expect(worker.skipWaiting).not.toHaveBeenCalled();
    await worker.dispatch('message', {
      data: { type: 'rasta:activate-update' },
    });
    expect(worker.skipWaiting).toHaveBeenCalledTimes(1);
  });

  it("removes an older version's caches when it activates, and keeps its own", async () => {
    const worker = loadWorker();
    await worker.caches.open('rasta-sw-v0-shell');
    await worker.caches.open('rasta-sw-v1-shell');
    await worker.caches.open('someone-elses-cache');
    await worker.dispatch('activate', {});
    const names = await worker.caches.keys();
    expect(names).not.toContain('rasta-sw-v0-shell');
    expect(names).toContain('rasta-sw-v1-shell');
    expect(names).toContain('someone-elses-cache');
  });

  it('clears everything it holds on sign-out', async () => {
    const worker = loadWorker();
    await worker.caches.open('rasta-sw-v1-packs');
    await worker.caches.open('rasta-sw-v1-api-snapshot');
    await worker.dispatch('message', {
      data: { type: 'rasta:clear-local-data' },
    });
    expect(await worker.caches.keys()).toEqual([]);
  });
});
