import { expect, test } from '@playwright/test';

import {
  continueToWorkspace,
  expectWorkspaceVerified,
  identityByKey,
  loadIdentityFixture,
  signIn,
  signOutFromSignIn,
} from './helpers/auth';

const fixture = loadIdentityFixture();

test.describe('Auth bootstrap and route access', () => {
  test('dispatcher signs in, bootstraps /v1/me, and opens overview', async ({
    page,
  }) => {
    const dispatcher = identityByKey(fixture, 'dispatcher');

    await signIn(page, dispatcher.email, dispatcher.password);
    await expectWorkspaceVerified(page);
    await continueToWorkspace(page, '/overview');
    await expect(
      page.getByRole('heading', {
        level: 1,
        name: 'Logistics accessibility overview',
      }),
    ).toBeVisible();
  });

  test('dispatcher can open planner and driver is denied', async ({ page }) => {
    const dispatcher = identityByKey(fixture, 'dispatcher');
    const driver = identityByKey(fixture, 'driver');

    await signIn(page, dispatcher.email, dispatcher.password);
    await expectWorkspaceVerified(page);
    await continueToWorkspace(page, '/overview');
    await page.goto('/planner');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Route planner' }),
    ).toBeVisible();

    await page.goto('/sign-in');
    await signOutFromSignIn(page);
    await expect(page.getByLabel('Email')).toBeVisible();

    await signIn(page, driver.email, driver.password);
    await expectWorkspaceVerified(page);
    await page.goto('/planner');
    await expect(
      page.getByText('This view is not available to this role'),
    ).toBeVisible();
    await expect(page.getByText('Access denied')).toBeVisible();

    await page.goto('/sign-in');
    await signOutFromSignIn(page);
  });

  test('driver can open an allowed mobile route', async ({ page }) => {
    const driver = identityByKey(fixture, 'driver');

    await signIn(page, driver.email, driver.password);
    await expectWorkspaceVerified(page);
    await page.goto('/driver/trip');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Your trips' }),
    ).toBeVisible();
  });

  test('no-scope account is denied operational workspace access', async ({
    page,
  }) => {
    const noScope = identityByKey(fixture, 'no-scope');

    await signIn(page, noScope.email, noScope.password);
    await expect(
      page.getByText('This account has no workspace access'),
    ).toBeVisible();
    await page.goto('/overview');
    await expect(page.getByText('Workspace access required')).toBeVisible();
    await expect(page.getByText('No operational data is shown')).toBeVisible();
  });

  test('sign out closes operational routes again', async ({ page }) => {
    const dispatcher = identityByKey(fixture, 'dispatcher');

    await signIn(page, dispatcher.email, dispatcher.password);
    await expectWorkspaceVerified(page);
    await continueToWorkspace(page, '/overview');
    await page.goto('/sign-in');
    await signOutFromSignIn(page);
    await expect(page.getByLabel('Email')).toBeVisible();

    await page.goto('/overview');
    await expect(page.getByText('Workspace access required')).toBeVisible();
  });
});
