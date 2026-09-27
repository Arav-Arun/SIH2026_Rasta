import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

import { identityByKey, loadIdentityFixture, signIn } from './helpers/auth';

/** Deliveries and fleet screens. */

const SCREENSHOT_DIR = resolve(
  process.cwd(),
  'artifacts/reports/deliveries-screenshots',
);

const fixture = loadIdentityFixture();

/** The pilot-district grant; the logistics check writes into that district. */
function pilotDispatcher() {
  return identityByKey(fixture, 'pilot-dispatcher');
}

async function openDeliveries(page: Page) {
  await page.goto('/deliveries');
  // The list arrives on the first poll after the workspace bootstraps.
  await expect
    .poll(async () => page.locator('[data-consignment-status]').count(), {
      timeout: 30_000,
      message: 'consignments never loaded',
    })
    .toBeGreaterThan(0);
}

/** The consignment the logistics check left partially delivered. */
function partialRow(page: Page) {
  return page
    .locator('[data-consignment-status="partially_delivered"]')
    .first();
}

test.describe('Deliveries and fleet', () => {
  test.setTimeout(120_000);

  test.beforeAll(() => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    const identity = pilotDispatcher();
    await signIn(page, identity.email, identity.password);
  });

  test('a request with one short consignment is not called fulfilled', async ({
    page,
  }) => {
    await openDeliveries(page);

    const request = page.locator('[data-request-status]').first();
    await expect(request).toBeVisible();
    // The whole point of the rollup: some of it arrived, so the facility has
    // not been supplied. Anything claiming "Fulfilled" here would be a lie.
    await expect(request).toHaveAttribute(
      'data-request-status',
      /partially_fulfilled|planned|open/,
    );
    await expect(request).not.toHaveAttribute(
      'data-request-status',
      'fulfilled',
    );
    await expect(request).toContainText(/of \d+ consignments delivered/);
  });

  test('a short line is shown as short, against what was ordered', async ({
    page,
  }) => {
    await openDeliveries(page);
    await partialRow(page).click();

    const manifest = page.getByRole('table');
    await expect(manifest).toBeVisible();

    // At least one line arrived short, and the gap is stated rather than left
    // for the reader to subtract.
    const short = page.locator('[data-shortfall]').first();
    await expect(short).toBeVisible();
    await expect(short).toContainText(/Short by/);

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'consignment-partial-receipt.png'),
      fullPage: true,
    });
  });

  test('the receipt is a recorded statement, not the trip status', async ({
    page,
  }) => {
    await openDeliveries(page);
    await partialRow(page).click();

    const receipt = page.locator('[data-receipt-status]');
    await expect(receipt).toBeVisible();
    await expect(receipt).toHaveAttribute(
      'data-receipt-status',
      'partially_delivered',
    );
    // Who took delivery is part of the record; a GPS fix could never supply it.
    await expect(receipt).toContainText(/Received/);
  });

  test('a consignment with no trip shows no delivered quantities', async ({
    page,
  }) => {
    await openDeliveries(page);

    const draft = page.locator('[data-consignment-status="draft"]');
    if ((await draft.count()) === 0) {
      test.skip(true, 'No draft consignment in this dataset');
    }
    await draft.first().click();

    // Nothing has been handed over, so the manifest must not imply otherwise.
    await expect(page.locator('[data-shortfall]')).toHaveCount(0);
    await expect(page.locator('[data-receipt-status]')).toHaveCount(0);
  });

  test('the fleet lists vehicles and marks the synthetic ones', async ({
    page,
  }) => {
    await page.goto('/fleet');

    await expect
      .poll(async () => page.locator('[data-vehicle-active]').count(), {
        timeout: 30_000,
        message: 'vehicles never loaded',
      })
      .toBeGreaterThan(0);

    const synthetic = page.locator('[data-vehicle-source="synthetic"]');
    if ((await synthetic.count()) > 0) {
      // Clear labelling: a demo vehicle must never read as a real one.
      await expect(synthetic.first()).toContainText(/Simulated/i);
    }

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'fleet-roster.png'),
      fullPage: true,
    });
  });

  test('trips say whose they are', async ({ page }) => {
    await page.goto('/fleet');
    await expect
      .poll(async () => page.locator('[data-trip-status]').count(), {
        timeout: 30_000,
        message: 'trips never loaded',
      })
      .toBeGreaterThan(0);

    // A dispatcher sees the district's trips; a driver sees only their own.
    await expect(page.getByText('Trips across your districts')).toBeVisible();
  });

  test('deliveries fit a phone without sideways scrolling', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openDeliveries(page);
    await partialRow(page).click();
    await expect(page.getByRole('table')).toBeVisible();

    const overflow = await page.evaluate(() => {
      const root = document.documentElement;
      return {
        page: root.scrollWidth - root.clientWidth,
        table: (() => {
          const t = document.querySelector('table');
          return t ? t.scrollWidth - t.clientWidth : 0;
        })(),
      };
    });
    expect(overflow.page).toBeLessThanOrEqual(0);
    expect(overflow.table).toBeLessThanOrEqual(0);

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'deliveries-phone.png'),
      fullPage: true,
    });
  });
});
