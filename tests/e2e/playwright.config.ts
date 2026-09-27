import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:3000';

// A Chromium binary already on the machine (a CI image, a container with a
// preinstalled browser whose build differs from the one this Playwright pins).
const executablePath = process.env.E2E_CHROMIUM_EXECUTABLE || undefined;

export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ['list'],
    [
      'html',
      {
        open: 'never',
        outputFolder: '../../artifacts/reports/playwright-html',
      },
    ],
  ],
  outputDir: '../../artifacts/reports/playwright-results',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Video needs Playwright's bundled ffmpeg, which is part of the same large
    // download the browser channel above avoids.
    video: process.env.E2E_VIDEO === '1' ? 'retain-on-failure' : 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Use the browser already installed on the machine.
        channel: executablePath
          ? undefined
          : (process.env.E2E_BROWSER_CHANNEL ?? 'chrome'),
        launchOptions: executablePath ? { executablePath } : undefined,
      },
    },
  ],
});
