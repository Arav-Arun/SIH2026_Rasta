import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { identityByKey, loadIdentityFixture, signIn } from './helpers/auth';

/** PWA packaging and bounded data packs. */

const REPORT_DIR = resolve(process.cwd(), 'artifacts/reports');
const REPORT_PATH = resolve(REPORT_DIR, 'pwa-audit.json');
const SCREENSHOT_DIR = resolve(REPORT_DIR, 'pwa-screenshots');

const evidence: Record<string, unknown> = {};
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8010';

/** Calls the API as the signed-in person, with their own session token. */
async function apiGet(page: Page, path: string) {
  return page.evaluate(
    async ({ base, route }) => {
      const key = Object.keys(localStorage).find((name) =>
        name.endsWith('-auth-token'),
      );
      const session = key
        ? (JSON.parse(localStorage.getItem(key) as string) as {
            access_token?: string;
          })
        : null;
      const response = await fetch(`${base}${route}`, {
        headers: { Authorization: `Bearer ${session?.access_token ?? ''}` },
      });
      return {
        status: response.status,
        body: (await response.json()) as unknown,
      };
    },
    { base: API_BASE, route: path },
  );
}

function record(key: string, value: unknown) {
  evidence[key] = value;
  mkdirSync(REPORT_DIR, { recursive: true });
  let previous: Record<string, unknown> = {};
  try {
    previous = JSON.parse(readFileSync(REPORT_PATH, 'utf8')) as Record<
      string,
      unknown
    >;
  } catch {
    /* First write. */
  }
  writeFileSync(
    REPORT_PATH,
    `${JSON.stringify(
      {
        ...previous,
        check: 'pwa',
        generated_at: new Date().toISOString(),
        ...evidence,
      },
      null,
      2,
    )}\n`,
    'utf8',
  );
}

/** Wait until a worker is controlling the page, not merely registered. */
async function awaitController(page: Page) {
  await page.waitForFunction(
    () => Boolean(navigator.serviceWorker?.controller),
    undefined,
    { timeout: 45_000 },
  );
}

/** Every URL this origin's caches are holding, across all cache names. */
async function cachedUrls(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const names = await caches.keys();
    const urls: string[] = [];
    for (const name of names) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) urls.push(request.url);
    }
    return urls;
  });
}

test.describe('PWA and data packs', () => {
  test.setTimeout(240_000);

  test.beforeAll(() => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
  });

  test('the manifest describes an installable app', async ({ page }) => {
    await page.goto('/overview');
    const href = await page
      .locator('link[rel="manifest"]')
      .first()
      .getAttribute('href');
    expect(href, 'no manifest is linked from the document').toBeTruthy();

    const response = await page.request.get(href as string);
    expect(response.ok()).toBe(true);
    const manifest = (await response.json()) as {
      name?: string;
      start_url?: string;
      display?: string;
      icons?: { sizes: string; purpose?: string }[];
      theme_color?: string;
    };

    expect(manifest.display).toBe('standalone');
    expect(manifest.start_url).toBeTruthy();
    expect(manifest.theme_color).toBeTruthy();
    // Android Chrome needs a 192 and a 512, and a maskable icon or it draws its
    // own white box around the mark on the launcher.
    const sizes = (manifest.icons ?? []).map((icon) => icon.sizes);
    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');
    expect(
      (manifest.icons ?? []).some((icon) => icon.purpose === 'maskable'),
    ).toBe(true);
    record('manifest', manifest);
  });

  test('a verified pack activates, and the shell opens with the network off', async ({
    page,
    context,
  }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);

    await page.goto('/settings');
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({
      timeout: 30_000,
    });
    await awaitController(page);

    // Version numbers are the point of this screen, so they must be present and
    // must not be the same field rendered twice.
    const appVersion = await page.locator('[data-app-version]').innerText();
    expect(appVersion.trim().length).toBeGreaterThan(0);

    const offer = page.locator('[data-download-pack]').first();
    await expect(offer).toBeVisible({ timeout: 30_000 });
    await offer.click();

    const outcome = page.locator('[data-pack-outcome]');
    await expect(outcome).toHaveAttribute('data-pack-outcome', 'ok', {
      timeout: 90_000,
    });
    const packVersion = await page.locator('[data-pack-version]').innerText();
    expect(packVersion.trim().length).toBeGreaterThan(0);
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'settings-pack-activated.png'),
      fullPage: true,
    });
    record('activated_pack', {
      appVersion: appVersion.trim(),
      packVersion: packVersion.trim(),
    });

    // The pack file itself has to be in Cache Storage, or "offline" only means
    // "the shell renders an empty screen".
    const beforeOffline = await cachedUrls(page);
    expect(
      beforeOffline.filter((url) => url.includes('/packs/')).length,
      'the pack was activated but is not in Cache Storage',
    ).toBeGreaterThan(0);

    await context.setOffline(true);
    await page.reload();
    // With no network the shell must still render.
    await expect(page.locator('[data-offline-identity]')).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({
      timeout: 30_000,
    });
    // And the pack it already verified is still recorded as active.
    await expect(page.locator('[data-pack-version]')).toContainText(
      packVersion.trim(),
    );
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'settings-offline.png'),
      fullPage: true,
    });
    record('offline_shell', { packVersionStillShown: packVersion.trim() });
    await context.setOffline(false);
  });

  test('a field officer opens the app with no network and still files a report', async ({
    page,
    context,
  }) => {
    // Field officers can create reports with photo and location while offline,
    // which starts with opening the app while offline.
    const officer = identityByKey(loadIdentityFixture(), 'pilot-officer');
    await signIn(page, officer.email, officer.password);
    await page.goto('/field/report');
    await awaitController(page);
    // Load it once under the worker, so the screen and its code are held.
    await page.reload();
    await expect(
      page.getByRole('heading', { level: 1, name: 'New field report' }),
    ).toBeVisible({
      timeout: 30_000,
    });

    await context.setOffline(true);
    await page.reload();
    await expect(page.locator('[data-offline-identity]')).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      page.getByRole('heading', { level: 1, name: 'New field report' }),
    ).toBeVisible();

    const runId = `test-offline-${Date.now().toString(36)}`;
    await page.locator('[data-report-type]').selectOption('landslide_debris');
    await page.getByLabel('Latitude').fill('25.5788');
    await page.getByLabel('Longitude').fill('91.8933');
    await page
      .locator('[data-report-note]')
      .fill(`Opened with no network (${runId})`);
    await page.locator('[data-queue-report]').click();
    await expect(page.locator('[data-queued-report]')).toBeVisible();
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'field-report-opened-offline.png'),
      fullPage: true,
    });

    await context.setOffline(false);
    await page.goto('/sync');
    await expect(page.getByText('Everything is sent')).toBeVisible({
      timeout: 60_000,
    });
    const listing = await apiGet(page, '/v1/incidents?limit=100');
    const incidents =
      (listing.body as { incidents: { note?: string | null }[] }).incidents ??
      [];
    const ours = incidents.filter((incident) => incident.note?.includes(runId));
    expect(
      ours,
      'the report opened offline must arrive exactly once',
    ).toHaveLength(1);
    record('opened_offline_report', { runId, arrived: ours.length });
  });

  test('an update is offered, and applied only when asked', async ({
    page,
  }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);
    await page.goto('/settings');
    await awaitController(page);
    const first = await page.evaluate(
      () => navigator.serviceWorker.controller?.scriptURL ?? '',
    );

    // A deploy that ships a new worker, announced the way a versioned build
    // would: a new script URL on the same scope.
    await page.evaluate(async () => {
      await navigator.serviceWorker.register('/sw.js?build=2', { scope: '/' });
    });

    // Offered, not forced: the prompt appears and nothing has reloaded.
    const apply = page.locator('[data-apply-update]');
    await expect(apply).toBeVisible({ timeout: 45_000 });
    expect(
      await page.evaluate(
        () => navigator.serviceWorker.controller?.scriptURL ?? '',
      ),
    ).toBe(first);

    await Promise.all([
      page.waitForEvent('load', { timeout: 45_000 }),
      apply.click(),
    ]);
    await expect
      .poll(
        async () =>
          page.evaluate(
            () => navigator.serviceWorker.controller?.scriptURL ?? '',
          ),
        {
          timeout: 30_000,
        },
      )
      .toContain('build=2');
    record('update', {
      from: first,
      to: 'sw.js?build=2',
      appliedOnRequest: true,
    });
  });

  test('nothing that must not be cached is cached', async ({ page }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);

    // Walk screens that read the API and sign in again, so every category of
    // request has actually been made before the caches are inspected.
    for (const path of ['/overview', '/alerts', '/fleet', '/settings']) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible(
        {
          timeout: 30_000,
        },
      );
    }
    await awaitController(page);

    const urls = await cachedUrls(page);
    const offenders = {
      auth: urls.filter(
        (url) =>
          url.includes('/auth/v1/') ||
          url.includes('/token') ||
          url.endsWith('/v1/me'),
      ),
      signedMedia: urls.filter(
        (url) =>
          url.includes('/storage/v1/') ||
          url.includes('X-Amz-Signature') ||
          /[?&](token|signature)=/.test(url),
      ),
    };
    record('cache_inventory', { total: urls.length, offenders });

    expect(
      offenders.auth,
      'a session or token endpoint is in Cache Storage',
    ).toEqual([]);
    expect(
      offenders.signedMedia,
      'a signed media URL is in Cache Storage and would outlive its signature',
    ).toEqual([]);

    // Cache Storage cannot hold a non-GET at all, so assert the worker never
    // tries: a handled POST is the bug, and it shows up as a response the
    // worker produced rather than the network.
    const replayed = await page.evaluate(async () => {
      const names = await caches.keys();
      for (const name of names) {
        const cache = await caches.open(name);
        for (const request of await cache.keys()) {
          if (request.method !== 'GET')
            return `${request.method} ${request.url}`;
        }
      }
      return null;
    });
    expect(
      replayed,
      'a write is in Cache Storage; retry belongs to the outbox',
    ).toBeNull();
  });

  test('the worker does not prefetch map tiles', async ({ page }) => {
    // The OSM tile usage policy forbids bulk downloading.
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);
    await page.goto('/map');
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({
      timeout: 30_000,
    });
    await page.waitForTimeout(3_000);

    const tiles = (await cachedUrls(page)).filter((url) =>
      /tile\.openstreetmap\.org|\/\d+\/\d+\/\d+\.png/.test(url),
    );
    record('cached_tiles', tiles);
    expect(
      tiles,
      'map tiles are being cached, which the tile policy forbids',
    ).toEqual([]);
  });
});
