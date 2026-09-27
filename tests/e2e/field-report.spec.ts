import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { identityByKey, loadIdentityFixture, signIn } from './helpers/auth';
import { uniquePng } from './helpers/png';

/**
 * Offline field report in the browser: a report with a photo, filed with no
 * network.
 */

const REPORT_DIR = resolve(process.cwd(), 'artifacts/reports');
const SCREENSHOT_DIR = resolve(REPORT_DIR, 'at04-screenshots');
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

test.describe('Offline field report (browser)', () => {
  test.setTimeout(180_000);

  test('a report queued offline arrives once, with its photo verified', async ({
    page,
    context,
  }) => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
    const runId = `at04-${Date.now().toString(36)}`;
    // A per-test scratch file, not evidence: it lives in Playwright's output dir.
    const photoPath = test.info().outputPath(`${runId}.png`);
    writeFileSync(photoPath, uniquePng(runId));

    const officer = identityByKey(loadIdentityFixture(), 'pilot-officer');
    await signIn(page, officer.email, officer.password);
    await page.goto('/field/report');
    await expect(
      page.getByRole('heading', { level: 1, name: 'New field report' }),
    ).toBeVisible({
      timeout: 30_000,
    });

    // Everything from here until the report is queued happens with no network.
    await context.setOffline(true);
    await page.locator('[data-report-type]').selectOption('landslide_debris');
    await page.getByLabel('Latitude').fill('25.5788');
    await page.getByLabel('Longitude').fill('91.8933');
    await expect(
      page.locator('[data-location-source="manual_pin"]'),
    ).toBeVisible();

    const chooser = page.waitForEvent('filechooser');
    await page.locator('[data-add-photo]').click();
    await (await chooser).setFiles(photoPath);
    await expect(page.locator('[data-photo-size]')).toBeVisible();
    await page
      .locator('[data-report-note]')
      .fill(`Debris across both lanes (${runId})`);
    await expect(page.locator('[data-draft-saved="saved"]')).toBeVisible();

    await page.locator('[data-queue-report]').click();
    await expect(page.locator('[data-queued-report]')).toBeVisible();
    // Nothing can have been sent: the browser is offline.
    await expect(page.locator('[data-report-step="report"]')).toHaveAttribute(
      'data-step-state',
      'waiting',
    );
    await expect(page.locator('[data-report-step="photo"]')).toHaveAttribute(
      'data-step-state',
      'waiting',
    );
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'queued-offline.png'),
      fullPage: true,
    });

    // Close the app with the report still on the device, then reconnect.
    await page.close();
    await context.setOffline(false);

    const reopened = await context.newPage();
    await reopened.goto('/sync');
    // The runner sends on start; the queue empties only when all three steps land.
    await expect(reopened.getByText('Everything is sent')).toBeVisible({
      timeout: 60_000,
    });
    await reopened.screenshot({
      path: resolve(SCREENSHOT_DIR, 'sent-after-reconnect.png'),
      fullPage: true,
    });

    // Exactly one report reached the control room.
    const listing = await apiGet(reopened, '/v1/incidents?limit=100');
    expect(listing.status).toBe(200);
    const incidents = (
      listing.body as { incidents: { id: string; note?: string | null }[] }
    ).incidents;
    const ours = incidents.filter((incident) => incident.note?.includes(runId));
    expect(ours, 'the report must arrive exactly once').toHaveLength(1);

    const detail = await apiGet(reopened, `/v1/incidents/${ours[0].id}`);
    expect(detail.status).toBe(200);
    const incident = detail.body as {
      type: string;
      location_source?: string | null;
      location?: { latitude: number; longitude: number } | null;
      attachments: { upload_status: string; mime_type: string }[];
    };
    expect(incident.type).toBe('landslide_debris');
    expect(incident.location_source).toBe('manual_pin');
    expect(incident.attachments, 'one photo, not a duplicate').toHaveLength(1);
    expect(incident.attachments[0].upload_status).toBe('verified');
    expect(incident.attachments[0].mime_type).toBe('image/png');

    writeFileSync(
      resolve(REPORT_DIR, 'at04-browser.json'),
      `${JSON.stringify(
        {
          generated_at: new Date().toISOString(),
          acceptance: 'offline field report (browser)',
          run_id: runId,
          incident_id: ours[0].id,
          reports_with_this_run_id: ours.length,
          attachments: incident.attachments,
          location_source: incident.location_source,
          passed: true,
        },
        null,
        2,
      )}\n`,
      'utf8',
    );
  });

  test('switching language in the middle of a report keeps what was entered', async ({
    page,
  }) => {
    const officer = identityByKey(loadIdentityFixture(), 'pilot-officer');
    await signIn(page, officer.email, officer.password);
    await page.goto('/field/report');
    await expect(
      page.getByRole('heading', { level: 1, name: 'New field report' }),
    ).toBeVisible({
      timeout: 30_000,
    });
    const note = 'Water over the road near the bridge';
    await page.locator('[data-report-type]').selectOption('flooding');
    await page.getByLabel('Latitude').fill('25.5711');
    await page.getByLabel('Longitude').fill('91.8801');
    await page.locator('[data-report-note]').fill(note);
    await expect(page.locator('[data-draft-saved="saved"]')).toBeVisible();

    const coordinates = page.locator('input[inputmode="decimal"]');
    for (const locale of ['hi', 'as']) {
      // The control a person would use, mid-report.
      await page.locator('[data-language-select]').first().selectOption(locale);
      await expect(page.locator('html')).toHaveAttribute('lang', locale);
      // The labels changed…
      await expect(
        page.getByRole('heading', { level: 1, name: 'New field report' }),
      ).toHaveCount(0);
      // …and nothing entered was lost or reformatted.
      await expect(page.locator('[data-report-type]')).toHaveValue('flooding');
      await expect(coordinates.nth(0)).toHaveValue('25.5711');
      await expect(coordinates.nth(1)).toHaveValue('91.8801');
      await expect(page.locator('[data-report-note]')).toHaveValue(note);
      // The screen says the translation is unreviewed rather than passing it off.
      await expect(page.getByRole('note').first()).toBeVisible();
    }

    // The draft outlives a reload in the new language too.
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('lang', 'as');
    await expect(page.locator('[data-report-note]')).toHaveValue(note, {
      timeout: 30_000,
    });
    await expect(coordinates.nth(0)).toHaveValue('25.5711');
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'draft-after-locale-switch-as.png'),
      fullPage: true,
    });
  });

  test('a dispatcher reports a road from the map with a GPS fix, and sees it land', async ({
    page,
    context,
  }) => {
    const dispatcher = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, dispatcher.email, dispatcher.password);

    // A real road from the pilot graph, as the map's evidence drawer would link it.
    const segments = await apiGet(
      page,
      '/v1/network/segments?bbox=91.87,25.56,91.91,25.59&limit=1',
    );
    expect(segments.status).toBe(200);
    const [road] = (
      segments.body as {
        features: { id: string; properties: { name?: string | null } }[];
      }
    ).features;
    expect(road).toBeTruthy();

    // The browser's own geolocation, granted and fixed, stands in for the device.
    await context.grantPermissions(['geolocation']);
    await context.setGeolocation({
      latitude: 25.5712,
      longitude: 91.8801,
      accuracy: 18,
    });

    await page.goto(`/field/report?segment=${road.id}`);
    await expect(
      page.getByRole('heading', { level: 1, name: 'New field report' }),
    ).toBeVisible({
      timeout: 30_000,
    });
    // The road is named and its midpoint arrives as a pin, not as a fix.
    await expect(page.getByText('Road chosen on the map:')).toBeVisible();
    await expect(
      page.locator('[data-location-source="manual_pin"]'),
    ).toBeVisible();

    await page.locator('[data-use-gps]').click();
    await expect(
      page.locator('[data-location-source="device_gps"]'),
    ).toContainText('±18 m');
    await page.locator('[data-report-type]').selectOption('road_damage');
    await page.locator('[data-queue-report]').click();

    // Online, so it goes straight away; the page says so only once the server has it.
    await expect(page.locator('[data-report-step="report"]')).toHaveAttribute(
      'data-step-state',
      'done',
      { timeout: 30_000 },
    );
    await expect(page.locator('[data-report-step="report"]')).toContainText(
      'Received by the control room',
    );
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'dispatcher-report-received.png'),
      fullPage: true,
    });
  });
});
