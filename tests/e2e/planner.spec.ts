import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

import { identityByKey, loadIdentityFixture, signIn } from './helpers/auth';

/** Route planner. */

const SCREENSHOT_DIR = resolve(
  process.cwd(),
  'artifacts/reports/planner-screenshots',
);

const fixture = loadIdentityFixture();

function pilotDispatcher() {
  return identityByKey(fixture, 'pilot-dispatcher');
}

/** Two facilities the planner runner has confirmed are connected on the graph. */
const ORIGIN = process.env.E2E_PLANNER_ORIGIN ?? 'Civil Hospital Shillong';
const DESTINATION = process.env.E2E_PLANNER_DESTINATION ?? 'Bethany Hospital';
const ISOLATED_DESTINATION = process.env.E2E_PLANNER_ISOLATED ?? '';

// By their own hook, not by position: the header's language switcher and the
// trip picker are selects too, and either would otherwise be taken for "From".
const originSelect = (page: Page) =>
  page.locator('[data-planner-end="origin"]');
const destinationSelect = (page: Page) =>
  page.locator('[data-planner-end="destination"]');

async function openPlanner(page: Page) {
  await page.goto('/planner');
  // The facility lists arrive with the connectivity summary.
  await expect
    .poll(async () => originSelect(page).locator('option').count(), {
      timeout: 30_000,
      message: 'facility options never loaded',
    })
    .toBeGreaterThan(1);
}

async function plan(page: Page, origin = ORIGIN, destination = DESTINATION) {
  await originSelect(page).selectOption({ label: origin });
  await destinationSelect(page).selectOption({ label: destination });
  await page.getByTestId('plan-route').click();
  await expect
    .poll(
      async () =>
        (await page.locator('[data-alternative-rank]').count()) +
        (await page.getByTestId('no-route').count()),
      { timeout: 45_000, message: 'the planner never answered' },
    )
    .toBeGreaterThan(0);
}

test.describe('Route planner', () => {
  test.setTimeout(150_000);

  test.beforeAll(() => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    const identity = pilotDispatcher();
    await signIn(page, identity.email, identity.password);
  });

  test('S1: a normal delivery gets a feasible route with an ETA range', async ({
    page,
  }) => {
    await openPlanner(page);
    await plan(page);

    const cards = page.locator('[data-alternative-rank]');
    await expect(cards.first()).toBeVisible();
    await expect(
      page.locator('[data-alternative-category="fastest_feasible"]'),
    ).toHaveCount(1);

    // An ETA is a range, never a single figure: the speeds behind it are
    // configured rather than measured.
    await expect(cards.first()).toContainText(/\d+\s*(min|h).*–/s);

    const map = page.getByTestId('route-map');
    await expect(map).toHaveAttribute('data-ready', 'true', {
      timeout: 30_000,
    });
    await expect
      .poll(async () => Number(await map.getAttribute('data-route-count')), {
        timeout: 20_000,
      })
      .toBeGreaterThan(0);

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'planner-feasible.png'),
      fullPage: true,
    });
  });

  test('a dispatcher can explain why a route was recommended', async ({
    page,
  }) => {
    await openPlanner(page);
    await plan(page);

    const first = page.locator('[data-alternative-rank="1"]');
    // The card carries the reason, not just the numbers.
    await expect(first).toContainText(
      /Least total cost|Trades time|Differs from/,
    );
    await expect(first).toContainText(/Deadline margin/);
    await expect(first).toContainText(/confirmed closure|closures to avoid/);
    await expect(first).toContainText(/unverified limit/);
  });

  test('unknown constraints and freshness are stated, never implied', async ({
    page,
  }) => {
    await openPlanner(page);
    await plan(page);

    // A null risk score must read as "not measured", not as "no risk".
    await expect(page.locator('[data-alternative-rank="1"]')).toContainText(
      /No risk score recorded|Highest risk/,
    );
    // The plan-wide coverage caveat.
    await expect(
      page.locator('[data-coverage="unobserved_network"]'),
    ).toBeVisible();
    // Freshness of the plan itself.
    await expect(page.locator('[data-stale]')).toBeVisible();
    // And no unqualified safety claim anywhere on the screen.
    await expect(
      page.getByText(/does not claim a route is safe/),
    ).toBeVisible();
  });

  test('S2/S6: the approval summary lists what is still unresolved', async ({
    page,
  }) => {
    await openPlanner(page);
    await plan(page);

    await page
      .locator('[data-alternative-rank="1"]')
      .getByRole('button', { name: /Approve this route/ })
      .click();

    const dialog = page.locator('dialog[open]');
    await expect(dialog).toBeVisible();
    // Vehicle, route, the snapshot it was costed against, and the unresolved
    // constraints: everything the dispatcher is being asked to accept.
    await expect(dialog).toContainText(/Vehicle/);
    await expect(dialog).toContainText(/Planned against network/);
    await expect(dialog).toContainText(/Cost policy/);
    await expect(dialog).toContainText(
      /UNRESOLVED CONSTRAINTS|Unresolved constraints/i,
    );

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'planner-approval-summary.png'),
      fullPage: true,
    });

    await dialog.getByRole('button', { name: 'Approve', exact: true }).click();
    await expect(page.getByTestId('plan-approved')).toBeVisible({
      timeout: 20_000,
    });

    // An approved plan offers no second approval.
    const approveButtons = page.getByRole('button', {
      name: /Approve this route/,
    });
    for (let index = 0; index < (await approveButtons.count()); index += 1) {
      await expect(approveButtons.nth(index)).toBeDisabled();
    }
  });

  test('S4: an unreachable destination explains the cut instead of drawing a line', async ({
    page,
  }) => {
    test.skip(
      ISOLATED_DESTINATION === '',
      'No isolated facility was prepared for this run',
    );
    await openPlanner(page);
    await plan(page, ORIGIN, ISOLATED_DESTINATION);

    const noRoute = page.getByTestId('no-route');
    await expect(noRoute).toBeVisible();
    await expect(noRoute).toContainText(/No route found/);
    // Reason, exclusions and a next step, not a bare failure.
    await expect(noRoute.locator('[data-reason]').first()).toBeVisible();
    await expect(noRoute.locator('[data-exclusion]').first()).toBeVisible();
    // And nothing is drawn.
    const map = page.getByTestId('route-map');
    if ((await map.count()) > 0) {
      await expect
        .poll(async () => Number(await map.getAttribute('data-route-count')))
        .toBe(0);
    } else {
      await expect(page.getByText('Nothing to draw')).toBeVisible();
    }

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'planner-no-route.png'),
      fullPage: true,
    });
  });

  test('the planner fits a laptop and a phone without sideways scrolling', async ({
    page,
  }) => {
    for (const size of [
      { width: 1152, height: 900 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(size);
      await openPlanner(page);
      const overflow = await page.evaluate(() => {
        const root = document.documentElement;
        return root.scrollWidth - root.clientWidth;
      });
      expect(
        overflow,
        `horizontal overflow at ${size.width}px`,
      ).toBeLessThanOrEqual(0);
    }

    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'planner-phone.png'),
      fullPage: true,
    });
  });
});
