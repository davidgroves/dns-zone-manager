import { expect, test } from '@playwright/test';
import { backendRequiresLogin, ensureLoggedIn } from './helpers';

/**
 * Authentication E2E tests.
 *
 * When the devcontainer has auth disabled these assert anonymous access.
 * When API key auth is enabled they exercise the login flow.
 */

test.describe('Authentication', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
  });

  test('opens the app without a login screen when auth is disabled', async ({
    page,
  }) => {
    test.skip(
      await backendRequiresLogin(page),
      'Auth is enabled; login-screen tests cover that mode',
    );
    await page.goto('/');
    await expect(page.locator('.app-container')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('.login-container')).toHaveCount(0);
    await expect(page.locator('button[title="Logout"]')).toHaveCount(0);
  });

  test('should show login screen when not authenticated', async ({ page }) => {
    test.skip(!(await backendRequiresLogin(page)), 'Auth disabled in this environment');
    await page.goto('/');

    await expect(page.locator('.login-container')).toBeVisible();
    await expect(page.locator('h1:has-text("DNS Zone Editor")')).toBeVisible();
    await expect(
      page.locator('input[placeholder="Enter your API key"]'),
    ).toBeVisible();
    await expect(
      page.locator('button:has-text("Sign in with API Key")'),
    ).toBeVisible();
  });

  test('should show error for invalid API key', async ({ page }) => {
    test.skip(!(await backendRequiresLogin(page)), 'Auth disabled in this environment');
    await page.goto('/');

    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('invalid-key');
    await page.locator('button:has-text("Sign in with API Key")').click();

    await expect(page.locator('.text-danger')).toBeVisible({ timeout: 10000 });
  });

  test('should authenticate with valid API key', async ({ page }) => {
    test.skip(!(await backendRequiresLogin(page)), 'Auth disabled in this environment');
    await page.goto('/');

    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();

    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });
    await expect(page.locator('.login-container')).not.toBeVisible();
  });

  test('should allow login via Enter key', async ({ page }) => {
    test.skip(!(await backendRequiresLogin(page)), 'Auth disabled in this environment');
    await page.goto('/');

    const input = page.locator('input[placeholder="Enter your API key"]');
    await input.fill('demo-api-key-12345');
    await input.press('Enter');

    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });
  });

  test('should persist authentication across page reload', async ({ page }) => {
    await ensureLoggedIn(page);
    await page.reload();
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });
  });

  test('should allow logout', async ({ page }) => {
    test.skip(!(await backendRequiresLogin(page)), 'Auth disabled in this environment');
    await ensureLoggedIn(page);

    await page.locator('button[title="Logout"]').click();

    await expect(page.locator('.login-container')).toBeVisible({
      timeout: 10000,
    });
  });
});
