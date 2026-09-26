import { expect, test } from '@playwright/test';
import { ensureLoggedIn } from './helpers';

/**
 * Zone management E2E tests.
 *
 * These tests verify zone listing, selection, and pagination.
 */

test.describe('Zone Management', () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedIn(page);
  });

  test('should display zone list', async ({ page }) => {
    // Should show zones in the sidebar (already waited in beforeEach)
    await expect(page.locator('.zone-list')).toBeVisible();
    await expect(page.locator('.zone-item').first()).toBeVisible();
  });

  test('should filter zones', async ({ page }) => {
    const before = await page.locator('.zone-item').count();
    expect(before).toBeGreaterThan(0);

    // Non-matching filter empties the list
    await page.locator('.zone-filter-input').fill('zzz-no-such-zone');
    await expect(page.locator('.zone-item')).toHaveCount(0, { timeout: 5000 });

    // Clear restores the full list
    await page.locator('.zone-filter-clear').click();
    await expect(page.locator('.zone-item')).toHaveCount(before, { timeout: 5000 });
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
