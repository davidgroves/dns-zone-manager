import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './frontend/__tests__/e2e',

  // Run tests serially to avoid race conditions with shared backend
  fullyParallel: false,
  workers: 1,

  // Fail the build on CI if you accidentally left test.only in the source code
  forbidOnly: !!process.env.CI,

  // Retry failed tests
  retries: process.env.CI ? 2 : 1,

  // Reporter to use
  reporter: [['html', { open: 'never' }], ['list']],

  // Global timeout for each test
  timeout: 30000,

  // Shared settings for all the projects
  use: {
    // Base URL to use in actions like `await page.goto('/')`
    baseURL: 'http://localhost:5173',

    // Collect trace when retrying the failed test
    trace: 'on-first-retry',

    // Take screenshot on failure
    screenshot: 'only-on-failure',

    // Increase action timeout for slow operations
    actionTimeout: 10000,
  },

  // Configure projects for major browsers
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    {
      name: 'ipad',
      use: { ...devices['iPad Pro 11 landscape'] },
    },
  ],

  // Run your local dev server before starting the tests
  // Note: This only starts the frontend. The backend MUST be running separately!
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    // Always reuse existing server - the test:e2e:check script verifies servers are running
    reuseExistingServer: true,
    timeout: 120 * 1000,
  },
});
