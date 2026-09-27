import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  continueToWorkspace,
  expectWorkspaceVerified,
  identityByKey,
  loadIdentityFixture,
  signIn,
} from './helpers/auth';

/** Operational overview and accessibility map. */

const SCREENSHOT_DIR = resolve(
  process.cwd(),
  'artifacts/reports/command-map-screenshots',
);

const fixture = loadIdentityFixture();

/** The pilot-district grant; the seed-district dispatcher sees no road graph. */
function pilotDispatcher() {
  return identityByKey(fixture, 'pilot-dispatcher');
}

async function openMap(page: Page, { expectSegments = true } = {}) {
  await page.goto('/map');
  const map = page.getByTestId('network-map');
  await expect(map).toBeVisible({ timeout: 30_000 });
  // `data-ready` means the style loaded. Segments arrive on the bbox query that
  // follows, so wait for those separately rather than asserting on an empty map.
  await expect(map).toHaveAttribute('data-ready', 'true', { timeout: 30_000 });

  if (expectSegments) {
    await expect
      .poll(async () => Number(await map.getAttribute('data-segment-count')), {
        timeout: 45_000,
        message: 'map never received any segments',
      })
      .toBeGreaterThan(0);
  }
}

test.beforeAll(() => {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

test.describe('Command overview and accessibility map', () => {
  // Sign-in, the /v1/me bootstrap and the first bbox query run against a real
  // stack on a cold dev server; the default 30s is not enough for that chain.
  test.setTimeout(120_000);

  test.beforeEach(async ({ page }) => {
    const dispatcher = pilotDispatcher();
    await signIn(page, dispatcher.email, dispatcher.password);
    await expectWorkspaceVerified(page);
    await continueToWorkspace(page, '/overview');
  });

  test('overview reports endpoint-backed counts with their source and age', async ({
    page,
  }) => {
    await expect(
      page.getByText('Logistics accessibility overview'),
    ).toBeVisible();

    // Every number on this screen comes from an endpoint. Where one does not
    // exist yet the screen prints an em dash and a reason, never a zero.
    const summaries = page.getByRole('region', {
      name: 'Operational summaries',
    });
    await expect(summaries).toBeVisible({ timeout: 30_000 });
    await expect(summaries.getByText('Reachable facilities')).toBeVisible();

    // Provenance: a freshness label is always present.
    await expect(page.getByText(/Updated|Never updated/).first()).toBeVisible();

    await page.setViewportSize({ width: 1280, height: 900 });
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/overview-1280.png`,
      fullPage: true,
    });
  });

  test('map renders the pilot network and separates passability from risk', async ({
    page,
  }) => {
    await openMap(page);

    const segmentCount = await page
      .getByTestId('network-map')
      .getAttribute('data-segment-count');
    expect(Number(segmentCount)).toBeGreaterThan(0);

    // Passability and predicted risk are distinct controls, never one scale.
    await expect(
      page.getByRole('group', { name: 'Road status' }),
    ).toBeVisible();

    // The legend must name every state, including unknown.
    for (const label of ['Closed', 'Restricted', 'Unknown', 'Open']) {
      await expect(
        page
          .getByTestId('map-legend')
          .getByText(label, { exact: false })
          .first(),
      ).toBeVisible();
    }

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/map-1280.png`,
      fullPage: false,
    });
  });

  test('selecting a segment opens evidence, risk and history', async ({
    page,
  }) => {
    await openMap(page);

    // The list view exposes the same rows as the map and is keyboard-reachable,
    // so the selection path is driven through it rather than by clicking canvas.
    await page.getByRole('button', { name: 'List view' }).click();

    const firstSegment = page.getByRole('button', { name: /Select/i }).first();
    await expect(firstSegment).toBeVisible({ timeout: 30_000 });
    await firstSegment.click();

    const drawer = page.getByTestId('evidence-drawer');
    await expect(drawer).toBeVisible({ timeout: 15_000 });

    await expect(
      drawer.getByRole('tab', { name: 'Current evidence' }),
    ).toBeVisible();
    await expect(
      drawer.getByRole('tab', { name: 'Future risk' }),
    ).toBeVisible();
    await expect(drawer.getByRole('tab', { name: 'History' })).toBeVisible();

    // Risk is not implemented yet, so the tab must state that rather than
    // render a number the reviewer could mistake for a prediction.
    await drawer.getByRole('tab', { name: 'Future risk' }).click();
    // The panel states both that risk is unavailable and why, so match the first.
    await expect(
      drawer.getByText(/not available|unavailable/i).first(),
    ).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/map-drawer-1280.png`,
      fullPage: false,
    });
  });

  test('map is operable by keyboard alone', async ({ page }) => {
    await openMap(page);

    // Tab until a control inside the map screen takes focus, proving the screen
    // is reachable without a pointer.
    let focusedTag = '';
    for (let index = 0; index < 40; index += 1) {
      await page.keyboard.press('Tab');
      focusedTag = await page.evaluate(() => {
        const active = document.activeElement;
        return active
          ? `${active.tagName}:${active.getAttribute('role') ?? ''}`
          : '';
      });
      if (/BUTTON|INPUT|A:/.test(focusedTag)) break;
    }
    expect(focusedTag).not.toBe('');

    // The focused element must have a visible focus ring, not just :focus.
    const hasVisibleFocus = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      if (!active) return false;
      const style = getComputedStyle(active);
      return style.outlineStyle !== 'none' || style.boxShadow !== 'none';
    });
    expect(hasVisibleFocus).toBe(true);
  });

  test('map renders at phone width without horizontal scroll', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openMap(page);

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflows).toBe(false);

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/map-390.png`,
      fullPage: false,
    });
  });

  test('map states an error and offers retry when the API is refused', async ({
    page,
  }) => {
    // A refused read must surface as an error the operator can act on, never as
    // an empty map that reads like "no closures".
    await page.route('**/v1/network/segments**', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'service_unavailable', message: 'Upstream is down.' },
        }),
      }),
    );

    await page.goto('/map');
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole('button', { name: /Try again|Retry/i }),
    ).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/map-error-1280.png`,
      fullPage: false,
    });
  });
});
