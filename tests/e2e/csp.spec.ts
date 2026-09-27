import { expect, test, type Page } from '@playwright/test';

import { identityByKey, loadIdentityFixture, signIn } from './helpers/auth';

/**
 * The client's Content Security Policy is on every page and nothing the product
 * does trips it.
 */

const DISPATCHER_SCREENS = [
  '/overview',
  '/map',
  '/planner',
  '/incidents',
  '/inspections',
  '/deliveries',
  '/fleet',
  '/alerts',
  '/data-health',
];
const OFFICER_SCREENS = ['/field/home', '/field/report', '/sync'];

type Violation = { page: string; directive: string; blocked: string };

async function watchViolations(page: Page, violations: Violation[]) {
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
      const log = ((window as unknown as { __csp?: unknown[] }).__csp ??= []);
      log.push({
        directive: event.violatedDirective,
        blocked: event.blockedURI,
      });
    });
  });
  page.on('console', (message) => {
    const text = message.text();
    if (
      /Content Security Policy|Refused to (load|connect|execute|apply|frame|create)/i.test(
        text,
      )
    ) {
      violations.push({
        page: page.url(),
        directive: 'console',
        blocked: text.slice(0, 300),
      });
    }
  });
}

async function collect(page: Page, violations: Violation[]) {
  const seen = await page.evaluate(
    () =>
      (
        window as unknown as {
          __csp?: { directive: string; blocked: string }[];
        }
      ).__csp ?? [],
  );
  for (const item of seen) violations.push({ page: page.url(), ...item });
}

async function visit(page: Page, path: string, violations: Violation[]) {
  const response = await page.goto(path);
  const policy = response?.headers()['content-security-policy'] ?? '';
  expect(policy, `CSP on ${path}`).toContain("frame-ancestors 'none'");
  expect(policy, `CSP on ${path}`).toContain("object-src 'none'");
  expect(response?.headers()['x-content-type-options']).toBe('nosniff');
  expect(response?.headers()['x-frame-options']).toBe('DENY');
  await page.locator('#main-content').waitFor({ timeout: 30_000 });
  // Give data queries, map tiles and workers a moment to run into the policy.
  await page.waitForTimeout(1_500);
  await collect(page, violations);
}

test.describe('Content security policy', () => {
  // A dev server compiles each screen on first visit.
  test.describe.configure({ timeout: 120_000 });

  test('the dispatcher screens load everything they need under the policy', async ({
    page,
  }) => {
    const violations: Violation[] = [];
    await watchViolations(page, violations);
    const fixture = loadIdentityFixture();
    const dispatcher = identityByKey(fixture, 'pilot-dispatcher');
    await signIn(page, dispatcher.email, dispatcher.password);
    await collect(page, violations);
    for (const path of DISPATCHER_SCREENS) {
      await visit(page, path, violations);
    }
    expect(violations).toEqual([]);
  });

  test('the field officer screens, a photo preview included, stay within it', async ({
    page,
  }) => {
    const violations: Violation[] = [];
    await watchViolations(page, violations);
    const fixture = loadIdentityFixture();
    const officer = identityByKey(fixture, 'pilot-officer');
    await signIn(page, officer.email, officer.password);
    for (const path of OFFICER_SCREENS) {
      await visit(page, path, violations);
    }
    // A chosen photo is previewed from a blob: URL, which the policy allows.
    await page.goto('/field/report');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Take or choose a photo' }).click();
    await (
      await chooser
    ).setFiles({
      name: 'photo.png',
      mimeType: 'image/png',
      buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
        'base64',
      ),
    });
    await expect(page.locator('img[src^="blob:"]').first()).toBeVisible({
      timeout: 15_000,
    });
    await collect(page, violations);
    expect(violations).toEqual([]);
  });

  test('another site cannot frame the application', async ({ page }) => {
    const response = await page.goto('/sign-in');
    expect(response?.headers()['content-security-policy']).toContain(
      "frame-ancestors 'none'",
    );
    expect(response?.headers()['x-frame-options']).toBe('DENY');
  });
});
