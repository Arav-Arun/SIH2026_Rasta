import { expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export type E2EIdentity = {
  key: string;
  email: string;
  password: string;
};

export type E2EIdentityFixture = {
  identities: E2EIdentity[];
};

const DEFAULT_FIXTURE_PATH = resolve(
  process.cwd(),
  'artifacts/e2e/e2e_identities.json',
);

export function loadIdentityFixture(
  fixturePath = process.env.E2E_IDENTITY_FIXTURE ?? DEFAULT_FIXTURE_PATH,
): E2EIdentityFixture {
  const raw = readFileSync(fixturePath, 'utf8');
  return JSON.parse(raw) as E2EIdentityFixture;
}

export function identityByKey(
  fixture: E2EIdentityFixture,
  key: string,
): E2EIdentity {
  const identity = fixture.identities.find(
    (candidate) => candidate.key === key,
  );
  if (!identity) {
    throw new Error(`Missing E2E identity key: ${key}`);
  }
  return identity;
}

export async function signIn(page: Page, email: string, password: string) {
  await page.goto('/sign-in');
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  // A page that was already signed in keeps its session in memory until it
  // reloads, and shows "signed in as…" instead of the form.
  await page.reload();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(
    page.getByRole('button', { name: 'Sign out locally' }),
  ).toBeVisible({ timeout: 15_000 });
}

export async function signOutFromSignIn(page: Page) {
  await page.getByRole('button', { name: 'Sign out locally' }).click();
}

export async function expectWorkspaceVerified(page: Page) {
  const continueButton = page.getByRole('button', {
    name: 'Continue to workspace',
  });
  const retryButton = page.getByRole('button', { name: 'Check access again' });
  const deadline = Date.now() + 30_000;

  while (Date.now() < deadline) {
    if (await continueButton.isVisible()) {
      return;
    }
    // `isVisible` resolves immediately but `isEnabled` auto-waits for the
    // element to be attached.
    if (await retryButton.isVisible()) {
      try {
        if (await retryButton.isEnabled({ timeout: 1_000 })) {
          await retryButton.click({ timeout: 2_000 });
        }
      } catch {
        // It disappeared between the two checks, which is what happens when
        // the bootstrap completes. Loop and re-read.
      }
    }
    await page.waitForTimeout(250);
  }

  await expect(continueButton).toBeVisible();
}

export async function continueToWorkspace(page: Page, path = '/overview') {
  await page.getByRole('button', { name: 'Continue to workspace' }).click();
  await page.waitForURL(`**${path}`);
}
