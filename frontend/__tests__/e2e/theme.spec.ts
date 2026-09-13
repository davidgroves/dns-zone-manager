import { expect, test } from '@playwright/test';

/**
 * Theme / branding E2E tests.
 *
 * Relies on the default /ui/config theme payload from the running backend.
 */

test.describe('Theme', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
  });

  test('login screen shows app name and theme toggle', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.login-container')).toBeVisible();
    await expect(page.locator('.login-card h1')).toContainText(/DNS Zone/);

    const toggle = page.locator('.login-card button:has-text("Light mode"), .login-card button:has-text("Dark mode")');
    await expect(toggle.first()).toBeVisible();
  });

  test('toggling mode updates data-theme and survives reload', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => document.documentElement.dataset.theme);

    const before = await page.evaluate(
      () => document.documentElement.dataset.theme,
    );
    const toggle = page
      .locator('.login-card button:has-text("Light mode"), .login-card button:has-text("Dark mode")')
      .first();
    await toggle.click();

    const after = await page.evaluate(
      () => document.documentElement.dataset.theme,
    );
    expect(after).not.toBe(before);
    expect(after === 'dark' || after === 'light').toBe(true);

    await page.reload();
    await page.waitForFunction(() => document.documentElement.dataset.theme);
    const persisted = await page.evaluate(
      () => document.documentElement.dataset.theme,
    );
    expect(persisted).toBe(after);
  });

  test('home button returns to zone list', async ({ page }) => {
    await page.goto('/');
    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();
    await expect(page.locator('.app-container')).toBeVisible({ timeout: 10000 });

    // Open scheduled view (leaves the zone list)
    await page.locator('button:has-text("Scheduled")').click();
    await expect(page.locator('h2:has-text("Scheduled Changes")')).toBeVisible({
      timeout: 10000,
    });

    await page.locator('[data-testid="home-button"]').click();
    await expect(page.locator('h2:has-text("Scheduled Changes")')).toHaveCount(0);
    await expect(page.locator('.zone-list, .zone-item, .sidebar')).toBeVisible();
  });

  test('authenticated header theme toggle works', async ({ page }) => {
    await page.goto('/');
    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();
    await expect(page.locator('.app-container')).toBeVisible({ timeout: 10000 });

    const before = await page.evaluate(
      () => document.documentElement.dataset.theme,
    );
    await page.locator('[data-testid="theme-toggle"]').click();
    const after = await page.evaluate(
      () => document.documentElement.dataset.theme,
    );
    expect(after).not.toBe(before);
  });
});
