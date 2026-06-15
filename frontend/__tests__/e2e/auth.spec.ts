import { expect, test } from '@playwright/test';

/**
 * Authentication E2E tests.
 *
 * These tests verify the login flow using API key authentication.
 * Note: Azure AD tests would require mocking or a test tenant.
 */

test.describe('Authentication', () => {
  test.beforeEach(async ({ page }) => {
    // Clear any stored credentials
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
  });

  test('should show login screen when not authenticated', async ({ page }) => {
    await page.goto('/');

    // Should show the login container
    await expect(page.locator('.login-container')).toBeVisible();
    await expect(page.locator('h1:has-text("DNS Zone Editor")')).toBeVisible();

    // Should have API key input
    await expect(
      page.locator('input[placeholder="Enter your API key"]'),
    ).toBeVisible();

    // Should have sign in button
    await expect(
      page.locator('button:has-text("Sign in with API Key")'),
    ).toBeVisible();
  });

  test('should show error for invalid API key', async ({ page }) => {
    await page.goto('/');

    // Enter an invalid API key
    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('invalid-key');
    await page.locator('button:has-text("Sign in with API Key")').click();

    // Should show error message (API will reject invalid key)
    await expect(page.locator('.text-danger')).toBeVisible({ timeout: 10000 });
  });

  test('should authenticate with valid API key', async ({ page }) => {
    await page.goto('/');

    // Enter the test API key (from examples/env.dev)
    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();

    // Should show the main application
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });

    // Login container should be hidden
    await expect(page.locator('.login-container')).not.toBeVisible();
  });

  test('should allow login via Enter key', async ({ page }) => {
    await page.goto('/');

    // Enter API key and press Enter
    const input = page.locator('input[placeholder="Enter your API key"]');
    await input.fill('demo-api-key-12345');
    await input.press('Enter');

    // Should authenticate
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });
  });

  test('should persist authentication across page reload', async ({ page }) => {
    await page.goto('/');

    // Login
    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });

    // Reload the page
    await page.reload();

    // Should still be authenticated (key stored in localStorage)
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });
  });

  test('should allow logout', async ({ page }) => {
    await page.goto('/');

    // Login first
    await page
      .locator('input[placeholder="Enter your API key"]')
      .fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 10000,
    });

    // Find and click logout button
    await page.locator('button[title="Logout"]').click();

    // Should show login screen again
    await expect(page.locator('.login-container')).toBeVisible({
      timeout: 10000,
    });
  });
});
