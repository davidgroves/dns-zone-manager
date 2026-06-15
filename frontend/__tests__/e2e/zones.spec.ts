import { expect, test } from '@playwright/test';

/**
 * Zone management E2E tests.
 *
 * These tests verify zone listing, selection, and pagination.
 */

test.describe('Zone Management', () => {
  test.beforeEach(async ({ page }) => {
    // Clear stored credentials and login fresh
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/');

    // Wait for login form to be fully ready
    const apiKeyInput = page.locator('input[placeholder="Enter your API key"]');
    await expect(apiKeyInput).toBeVisible({ timeout: 10000 });
    await expect(apiKeyInput).toBeEnabled();

    // Fill and submit login
    await apiKeyInput.fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();

    // Wait for successful authentication
    await expect(page.locator('.app-container')).toBeVisible({
      timeout: 15000,
    });

    // Wait for zones to load from the API (this is the key step)
    await expect(page.locator('.zone-item').first()).toBeVisible({
      timeout: 30000,
    });
  });

  test('should display zone list', async ({ page }) => {
    // Should show zones in the sidebar (already waited in beforeEach)
    await expect(page.locator('.zone-list')).toBeVisible();
    await expect(page.locator('.zone-item').first()).toBeVisible();
  });

  test('should filter zones', async ({ page }) => {
    // Filter by typing
    await page.locator('.zone-filter-input').fill('example');

    // Should filter the zone list
    await expect(page.locator('.zone-item')).toHaveCount(1, { timeout: 5000 });

    // Clear filter
    await page.locator('.zone-filter-clear').click();

    // Should restore the full list
    await expect(page.locator('.zone-item').first()).toBeVisible();
  });

  test('should select a zone and display records', async ({ page }) => {
    // Click on a zone
    await page.locator('.zone-item').first().click();

    // Should show records in the main content area (requires backend on port 8000)
    await expect(page.locator('.card .table-container')).toBeVisible({
      timeout: 30000,
    });

    // Should show the zone name in the header
    await expect(page.locator('.card-header h2')).toBeVisible();
  });

  test('should show zone pagination controls', async ({ page }) => {
    // Should show pagination info
    await expect(page.locator('.zone-pagination-info')).toBeVisible();
  });

  test('should highlight selected zone', async ({ page }) => {
    // Click on a zone
    await page.locator('.zone-item').first().click();

    // The clicked zone should have active class
    await expect(page.locator('.zone-item.active')).toBeVisible();
  });
});
