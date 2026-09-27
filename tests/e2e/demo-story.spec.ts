import { expect, test, type Browser, type Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { signIn } from './helpers/auth';
import { uniquePng } from './helpers/png';

/**
 * The six-minute demo story walked end to end in a browser, against the demo
 * that `scripts/local_demo.py up` seeded: a medicine consignment on an approved
 * route, its trip running with the driver.
 */

const DEMO_DIR = resolve(process.cwd(), 'artifacts/demo');
const REPORT_PATH = resolve(process.cwd(), 'artifacts/reports/demo_story.json');
const SHOTS = resolve(
  process.cwd(),
  'artifacts/reports/demo-story-screenshots',
);
const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000';

type Accounts = { password: string; people: Record<string, { email: string }> };
type Story = {
  consignment: string;
  trip_id: string;
  route_plan_id: string;
  landslide: {
    segment_id: string;
    road: string;
    latitude: number;
    longitude: number;
  };
};

const accounts = JSON.parse(
  readFileSync(resolve(DEMO_DIR, 'accounts.json'), 'utf8'),
) as Accounts;
const story = JSON.parse(
  readFileSync(resolve(DEMO_DIR, 'story.json'), 'utf8'),
) as Story;
const steps: { step: string; seconds: number; detail?: unknown }[] = [];

async function as(browser: Browser, key: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, accounts.people[key].email, accounts.password);
  return page;
}

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
        body: (await response.json()) as Record<string, unknown>,
      };
    },
    { base: API_BASE, route: path },
  );
}

async function step<T>(name: string, run: () => Promise<T>): Promise<T> {
  const started = Date.now();
  const result = await run();
  steps.push({
    step: name,
    seconds: Math.round((Date.now() - started) / 100) / 10,
  });
  writeFileSync(
    REPORT_PATH,
    `${JSON.stringify({ generated_at: new Date().toISOString(), story, steps }, null, 2)}\n`,
    'utf8',
  );
  return result;
}

test.describe('The demo story', () => {
  test.describe.configure({ timeout: 600_000 });

  test('report offline → confirm closure → replan → driver acknowledges', async ({
    browser,
  }) => {
    mkdirSync(SHOTS, { recursive: true });
    const runId = `demo-${Date.now().toString(36)}`;

    // 1:10 to 2:00: the field officer reports the landslide with no network.
    const officer = await as(browser, 'officer');
    await step('officer queues the report offline', async () => {
      await officer.goto('/field/report');
      await expect(
        officer.getByRole('heading', { level: 1, name: 'New field report' }),
      ).toBeVisible({ timeout: 60_000 });
      await officer.context().setOffline(true);
      await officer
        .locator('[data-report-type]')
        .selectOption('landslide_debris');
      await officer
        .getByLabel('Latitude')
        .fill(String(story.landslide.latitude));
      await officer
        .getByLabel('Longitude')
        .fill(String(story.landslide.longitude));
      const chooser = officer.waitForEvent('filechooser');
      await officer.locator('[data-add-photo]').click();
      await (
        await chooser
      ).setFiles({
        name: 'landslide.png',
        mimeType: 'image/png',
        buffer: uniquePng(runId),
      });
      await expect(officer.locator('[data-photo-size]')).toBeVisible();
      await officer
        .locator('[data-report-note]')
        .fill(`Debris across the road, both lanes blocked (${runId})`);
      await officer.locator('[data-queue-report]').click();
      await expect(officer.locator('[data-queued-report]')).toBeVisible();
      await officer.screenshot({
        path: resolve(SHOTS, '1-officer-queued-offline.png'),
        fullPage: true,
      });
    });

    // 2:00 to 2:35: the network returns; the queue empties by itself.
    const incidentId = await step(
      'the report reaches the control room once',
      async () => {
        await officer.context().setOffline(false);
        await officer.goto('/sync');
        await expect(officer.getByText('Everything is sent')).toBeVisible({
          timeout: 90_000,
        });
        const listing = await apiGet(officer, '/v1/incidents?limit=100');
        const ours = (
          (listing.body.incidents ?? []) as {
            id: string;
            note?: string | null;
          }[]
        ).filter((incident) => incident.note?.includes(runId));
        expect(ours, 'the report arrives exactly once').toHaveLength(1);
        return ours[0].id;
      },
    );

    // The dispatcher verifies it and closes the road.
    const dispatcher = await as(browser, 'dispatcher');
    await step('dispatcher confirms the closure with evidence', async () => {
      await dispatcher.goto('/incidents');
      await dispatcher
        .locator(`[data-incident-id="${incidentId}"]`)
        .click({ timeout: 60_000 });
      await dispatcher
        .locator('input[value="confirm_closure"]')
        .check({ force: true });
      const segment = dispatcher.locator(
        `[data-segment-id="${story.landslide.segment_id}"]`,
      );
      await segment.check({ timeout: 60_000 });
      await dispatcher
        .locator('textarea')
        .first()
        .fill('Photo shows debris across both lanes; confirmed closed.');
      await dispatcher.getByRole('button', { name: 'Record decision' }).click();
      await expect(
        dispatcher.locator(`[data-incident-id="${incidentId}"]`),
      ).toBeVisible();
      await dispatcher.screenshot({
        path: resolve(SHOTS, '2-dispatcher-decided.png'),
        fullPage: true,
      });
    });

    // 2:35 to 3:35: the alert says the route was withdrawn; plan and approve a new one.
    await step(
      'dispatcher replans from the alert and gives the trip its new route',
      async () => {
        await dispatcher.goto('/alerts');
        const replan = dispatcher.locator(
          `[data-replan-trip="${story.trip_id}"]`,
        );
        await expect(replan).toBeVisible({ timeout: 60_000 });
        await replan.click();
        await expect(dispatcher.locator('[data-planner-trip]')).toHaveValue(
          story.trip_id,
          {
            timeout: 60_000,
          },
        );
        await dispatcher.getByTestId('plan-route').click();
        const first = dispatcher.locator('[data-alternative-rank="1"]');
        await expect(first).toBeVisible({ timeout: 60_000 });
        await dispatcher.screenshot({
          path: resolve(SHOTS, '3-planner-alternatives.png'),
          fullPage: true,
        });
        await first.getByRole('button', { name: /Approve this route/ }).click();
        await dispatcher
          .locator('dialog[open]')
          .getByRole('button', { name: 'Approve', exact: true })
          .click();
        await expect(dispatcher.getByTestId('plan-bound')).toBeVisible({
          timeout: 30_000,
        });
      },
    );

    // The new route really avoids the closed road, and the trip carries it.
    const newPlanId = await step(
      'the trip carries a new approved route that avoids the closure',
      async () => {
        const trips = await apiGet(dispatcher, '/v1/trips?limit=200');
        const trip = (
          (trips.body.trips ?? []) as {
            id: string;
            route_plan_id: string | null;
            route_plan_status: string | null;
            status: string;
          }[]
        ).find((row) => row.id === story.trip_id);
        expect(trip?.route_plan_status).toBe('approved');
        expect(trip?.route_plan_id).not.toBe(story.route_plan_id);
        expect(trip?.status).toBe('active');
        const plan = await apiGet(
          dispatcher,
          `/v1/route-plans/${trip?.route_plan_id}`,
        );
        const planBody = (plan.body.plan ?? plan.body) as {
          chosen_alternative_id?: string;
          alternatives: { id: string; segment_ids: string[] }[];
        };
        const chosen =
          planBody.alternatives.find(
            (row) => row.id === planBody.chosen_alternative_id,
          ) ?? planBody.alternatives[0];
        expect(chosen.segment_ids).not.toContain(story.landslide.segment_id);
        return trip?.route_plan_id ?? '';
      },
    );

    // 3:35 to 4:20: the driver is told, acknowledges, and sees the new route.
    const driver = await as(browser, 'driver');
    await step(
      'driver acknowledges the withdrawn-route alert and sees the new route',
      async () => {
        await driver.goto('/alerts');
        const alert = driver.locator('[data-alert-status="delivered"]').first();
        await expect(alert).toBeVisible({ timeout: 60_000 });
        await alert.getByRole('button', { name: 'Acknowledge' }).click();
        await expect(
          driver.locator('[data-alert-status="acknowledged"]').first(),
        ).toBeVisible({
          timeout: 30_000,
        });
        await driver.goto('/driver/trip');
        await expect(driver.getByText('Approved route').first()).toBeVisible({
          timeout: 60_000,
        });
        // The route itself, not just its heading: summary and the network it was approved on.
        await expect(
          driver.getByText(/against network version/).first(),
        ).toBeVisible({
          timeout: 60_000,
        });
        await driver.screenshot({
          path: resolve(SHOTS, '4-driver-new-route.png'),
          fullPage: true,
        });
      },
    );

    // 4:20 to 5:35: the control room's picture, and the evidence behind it.
    await step(
      'deliveries, alerts and data health show the outcome',
      async () => {
        for (const path of ['/deliveries', '/alerts', '/data-health']) {
          await dispatcher.goto(path);
          await expect(
            dispatcher.getByRole('heading', { level: 1 }).first(),
          ).toBeVisible({
            timeout: 60_000,
          });
        }
        // What the presenter points at: both sources recorded, never live, and
        // the roads scored by the named baseline model.
        await expect(
          dispatcher.locator('[data-source-state="recorded"]'),
        ).toHaveCount(2, {
          timeout: 60_000,
        });
        await expect(
          dispatcher.locator('[data-source-state="live"]'),
        ).toHaveCount(0);
        await expect(
          dispatcher.locator('[data-coverage-risk]').first(),
        ).toBeVisible();
        await expect(dispatcher.getByText(/baseline-v1/).first()).toBeVisible();
        await dispatcher.screenshot({
          path: resolve(SHOTS, '5-data-health.png'),
          fullPage: true,
        });
      },
    );

    steps.push({
      step: 'result',
      seconds: 0,
      detail: { incidentId, newPlanId },
    });
    writeFileSync(
      REPORT_PATH,
      `${JSON.stringify({ generated_at: new Date().toISOString(), story, steps, passed: true }, null, 2)}\n`,
      'utf8',
    );
  });
});
