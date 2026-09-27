import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { identityByKey, loadIdentityFixture, signIn } from './helpers/auth';

/** Accessibility, keyboard reach and the three locales. */

const REPORT_DIR = resolve(process.cwd(), 'artifacts/reports');
const REPORT_PATH = resolve(REPORT_DIR, 'accessibility-audit.json');
const SCREENSHOT_DIR = resolve(REPORT_DIR, 'accessibility-screenshots');

/** WCAG 2.1 A and AA. */
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

type Finding = {
  screen: string;
  locale: string;
  serious: number;
  critical: number;
  moderate: number;
  minor: number;
  violations: {
    id: string;
    impact: string | null;
    nodes: number;
    help: string;
  }[];
};

const findings: Finding[] = [];

/** Merge this process's findings into the report on disk. */
function writeReport() {
  mkdirSync(REPORT_DIR, { recursive: true });
  const merged = new Map<string, Finding>();
  if (existsSync(REPORT_PATH)) {
    try {
      const previous = JSON.parse(readFileSync(REPORT_PATH, 'utf8')) as {
        findings?: Finding[];
      };
      for (const finding of previous.findings ?? []) {
        merged.set(`${finding.screen}:${finding.locale}`, finding);
      }
    } catch {
      // A truncated report from an interrupted run is replaced, not trusted.
    }
  }
  for (const finding of findings) {
    merged.set(`${finding.screen}:${finding.locale}`, finding);
  }
  const all = [...merged.values()];
  const blocking = all.reduce(
    (total, finding) => total + finding.critical + finding.serious,
    0,
  );
  writeFileSync(
    REPORT_PATH,
    `${JSON.stringify(
      {
        generated_at: new Date().toISOString(),
        check: 'accessibility',
        standard: TAGS,
        screens_audited: all.length,
        blocking_violations: blocking,
        findings: all,
      },
      null,
      2,
    )}\n`,
    'utf8',
  );
}

/** Screens a dispatcher passes through to get a load moving. */
const DISPATCHER_SCREENS = [
  { path: '/overview', name: 'overview' },
  { path: '/map', name: 'accessibility-map' },
  { path: '/planner', name: 'route-planner' },
  { path: '/deliveries', name: 'deliveries' },
  { path: '/fleet', name: 'fleet' },
  { path: '/incidents', name: 'incidents' },
  { path: '/inspections', name: 'inspections' },
  { path: '/alerts', name: 'alert-inbox' },
  { path: '/data-health', name: 'data-health' },
] as const;

/** A field officer's path: tasks, a report, the queue, the device check. */
const FIELD_SCREENS = [
  { path: '/field/home', name: 'field-home' },
  { path: '/field/report', name: 'field-report' },
  { path: '/sync', name: 'sync-queue' },
  { path: '/permissions', name: 'device-check' },
  { path: '/alerts', name: 'field-alert-inbox' },
] as const;

/**
 * The viewport matrix: desktop sizes, two phone sizes, and 1280 px
 * at 200% zoom, which lays out like a 640 px window.
 */
const VIEWPORTS = [
  { name: '1440', width: 1440, height: 900 },
  { name: 'zoom200', width: 640, height: 400 },
  { name: '390', width: 390, height: 844 },
  { name: '360', width: 360, height: 800 },
] as const;

async function settle(page: Page) {
  // Every screen either renders its data or states why it cannot. Waiting for
  // the heading rather than a network idle keeps map tiles from holding the run.
  await expect(page.getByRole('heading').first()).toBeVisible({
    timeout: 30_000,
  });
  await page.waitForTimeout(400);
}

async function audit(page: Page, screen: string, locale: string) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const count = (impact: string) =>
    results.violations.filter((violation) => violation.impact === impact)
      .length;

  const finding: Finding = {
    screen,
    locale,
    critical: count('critical'),
    serious: count('serious'),
    moderate: count('moderate'),
    minor: count('minor'),
    violations: results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact ?? null,
      nodes: violation.nodes.length,
      help: violation.help,
    })),
  };
  findings.push(finding);
  // Written as the run goes, not in afterAll: a failing screen must still leave
  // its measurements behind, and the numbers are the point of the run.
  writeReport();

  const blocking = results.violations.filter(
    (violation) =>
      violation.impact === 'critical' || violation.impact === 'serious',
  );
  expect(
    blocking,
    `${screen} (${locale}) has blocking violations: ${blocking
      .map(
        (violation) => `${violation.id} on ${violation.nodes.length} node(s)`,
      )
      .join('; ')}`,
  ).toEqual([]);
}

async function setLocale(page: Page, locale: string) {
  await page.evaluate((value) => {
    localStorage.setItem('rasta.locale', value);
    window.dispatchEvent(new StorageEvent('storage', { key: 'rasta.locale' }));
  }, locale);
  await page.reload();
}

test.describe('Accessibility and locales', () => {
  test.setTimeout(240_000);

  test.beforeAll(() => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
  });

  test.afterAll(() => {
    writeReport();
  });

  test('the dispatcher flow has no serious or critical violation', async ({
    page,
  }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);

    for (const screen of DISPATCHER_SCREENS) {
      await page.goto(screen.path);
      await settle(page);
      await audit(page, screen.name, 'en');
      await page.screenshot({
        path: resolve(SCREENSHOT_DIR, `${screen.name}-en.png`),
        fullPage: true,
      });
    }
  });

  test('the same screens hold up in Hindi and Assamese', async ({ page }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);

    // Longer Devanagari and Assamese strings are where a fixed-width control
    // overflows or a label stops matching its input.
    for (const locale of ['hi', 'as']) {
      await page.goto('/overview');
      await setLocale(page, locale);
      for (const screen of [
        { path: '/overview', name: 'overview' },
        { path: '/planner', name: 'route-planner' },
        { path: '/alerts', name: 'alert-inbox' },
      ]) {
        await page.goto(screen.path);
        await settle(page);
        await expect(page.locator('html')).toHaveAttribute('lang', locale);
        await audit(page, screen.name, locale);
        await page.screenshot({
          path: resolve(SCREENSHOT_DIR, `${screen.name}-${locale}.png`),
          fullPage: true,
        });
      }
    }
  });

  test('the field officer and driver screens have no serious or critical violation', async ({
    page,
  }) => {
    const officer = identityByKey(loadIdentityFixture(), 'pilot-officer');
    await signIn(page, officer.email, officer.password);
    for (const screen of FIELD_SCREENS) {
      await page.goto(screen.path);
      await settle(page);
      await audit(page, screen.name, 'en');
      await page.screenshot({
        path: resolve(SCREENSHOT_DIR, `${screen.name}-en.png`),
        fullPage: true,
      });
    }

    const driver = identityByKey(loadIdentityFixture(), 'driver');
    await signIn(page, driver.email, driver.password);
    await page.goto('/driver/trip');
    await settle(page);
    await audit(page, 'driver-trips', 'en');
    await page.screenshot({
      path: resolve(SCREENSHOT_DIR, 'driver-trips-en.png'),
      fullPage: true,
    });
  });

  test('no screen scrolls sideways at any size in the matrix', async ({
    page,
  }) => {
    // Horizontal scrolling on a phone hides controls off the edge; at 200% zoom
    // it is the WCAG reflow failure. Measured, not eyeballed, on every screen.
    const dispatcher = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, dispatcher.email, dispatcher.password);
    const overflowing: string[] = [];
    const matrix: { viewport: string; screen: string; overflowPx: number }[] =
      [];
    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      for (const screen of [
        ...DISPATCHER_SCREENS,
        { path: '/field/report', name: 'field-report' },
      ]) {
        await page.goto(screen.path);
        await settle(page);
        const overflowPx = await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        );
        matrix.push({
          viewport: viewport.name,
          screen: screen.name,
          overflowPx,
        });
        if (overflowPx > 1)
          overflowing.push(`${screen.name}@${viewport.name}: ${overflowPx}px`);
        if (viewport.name === '390' || viewport.name === '360') {
          await page.screenshot({
            path: resolve(
              SCREENSHOT_DIR,
              `${screen.name}-${viewport.name}.png`,
            ),
            fullPage: false,
          });
        }
      }
    }
    writeFileSync(
      resolve(REPORT_DIR, 'test-viewport-matrix.json'),
      `${JSON.stringify({ generated_at: new Date().toISOString(), viewports: VIEWPORTS, results: matrix }, null, 2)}\n`,
      'utf8',
    );
    expect(overflowing, 'these screens scroll sideways').toEqual([]);
  });

  test('the field report form is complete and visible by keyboard alone', async ({
    page,
  }) => {
    const officer = identityByKey(loadIdentityFixture(), 'pilot-officer');
    await signIn(page, officer.email, officer.password);
    await page.goto('/field/report');
    await settle(page);
    // Exactly one main landmark exists; the walk starts inside it.
    await expect(page.getByRole('main')).toHaveCount(1);
    await page.locator('#main-content').focus();

    const reached: string[] = [];
    const ringless: string[] = [];
    for (let step = 0; step < 30; step += 1) {
      await page.keyboard.press('Tab');
      const focused = await page.evaluate(() => {
        const element = document.activeElement as HTMLElement | null;
        if (!element || element === document.body) return null;
        const style = getComputedStyle(element);
        return {
          description: `${element.tagName.toLowerCase()}:${
            element.getAttribute('aria-label') ??
            element.textContent?.trim().slice(0, 30) ??
            ''
          }`,
          inForm: Boolean(element.closest('main')),
          hasRing:
            (style.outlineStyle !== 'none' &&
              parseFloat(style.outlineWidth) > 0) ||
            style.boxShadow !== 'none',
        };
      });
      if (!focused || !focused.inForm) break;
      reached.push(focused.description);
      if (!focused.hasRing) ringless.push(focused.description);
    }
    // Type, GPS, both coordinates, photo, note, queue and discard.
    expect(reached.length, reached.join(' | ')).toBeGreaterThanOrEqual(7);
    expect(
      ringless,
      'these controls took focus with no visible indicator',
    ).toEqual([]);
  });

  test('every control on the planner is reachable and visible by keyboard', async ({
    page,
  }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);
    await page.goto('/planner');
    await settle(page);

    // Walk the tab order and record what receives focus. A control that takes
    // focus without a visible ring is unusable for anyone not using a mouse.
    const reached: string[] = [];
    const ringless: string[] = [];
    for (let step = 0; step < 40; step += 1) {
      await page.keyboard.press('Tab');
      const focused = await page.evaluate(() => {
        const element = document.activeElement as HTMLElement | null;
        if (!element || element === document.body) return null;
        const style = getComputedStyle(element);
        const describe = () => {
          const label =
            element.getAttribute('aria-label') ??
            element.getAttribute('name') ??
            element.getAttribute('type') ??
            element.textContent?.trim().slice(0, 30) ??
            '';
          return `${element.tagName.toLowerCase()}[${element.className
            .toString()
            .slice(0, 40)}]:${label}`;
        };
        return {
          description: describe(),
          // An outline or a ring drawn with box-shadow both count.
          hasRing:
            (style.outlineStyle !== 'none' &&
              parseFloat(style.outlineWidth) > 0) ||
            style.boxShadow !== 'none',
        };
      });
      if (!focused) break;
      reached.push(focused.description);
      if (!focused.hasRing) ringless.push(focused.description);
    }

    expect(reached.length, 'the tab order reached nothing').toBeGreaterThan(5);
    expect(
      ringless,
      'these controls took focus with no visible indicator',
    ).toEqual([]);
  });

  test('a skip link puts the keyboard straight into the content', async ({
    page,
  }) => {
    const identity = identityByKey(loadIdentityFixture(), 'pilot-dispatcher');
    await signIn(page, identity.email, identity.password);
    await page.goto('/overview');
    await settle(page);

    await page.keyboard.press('Tab');
    const first = page.locator(':focus');
    await expect(first).toBeVisible();
    // The first stop must be the skip link, not the eleventh navigation entry.
    await expect(first).toHaveAttribute('href', '#main-content');
  });
});
