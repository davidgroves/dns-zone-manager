import { expect, test } from '@playwright/test';
import { ensureLoggedIn, selectFirstZone } from './helpers';

/**
 * Search E2E — asserts filtering outcomes, not mere presence.
 */

test.describe('Search', () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedIn(page);
    await selectFirstZone(page);
  });

  test('should search within a zone', async ({ page }) => {
    const input = page.locator('.search-box input[type="text"]');
    await expect(input).toBeVisible({ timeout: 10000 });
    await input.fill('www');
    // Search mode replaces the zone records view
    await expect(page).toHaveURL(/q=www|search=www/, { timeout: 10000 });
    await expect(page.locator('.card table tbody tr').first()).toBeVisible({
      timeout: 15000,
    });
    const text = (await page.locator('.card table tbody').innerText()).toLowerCase();
    expect(text).toContain('www');
  });

  test('should clear search and restore zone records', async ({ page }) => {
    const input = page.locator('.search-box input[type="text"]');
    await input.fill('www');
    await expect(page.locator('.card table tbody tr').first()).toBeVisible({
      timeout: 15000,
    });
    await input.clear();
    // Leaving search mode returns to the selected zone's records table
    await expect(page.locator('.card-header button:has-text("Add Record")')).toBeVisible({
      timeout: 15000,
    });
    await expect(page.locator('.card table tbody tr').first()).toBeVisible();
  });

  test('should filter by record type without search query', async ({ page }) => {
    // The type filter sits next to the search box
    const select = page.locator('header select').last();
    await expect(select).toBeVisible({ timeout: 10000 });
    await select.selectOption('A');
    await expect(page.locator('.card table tbody tr').first()).toBeVisible({
      timeout: 10000,
    });
    const rows = page.locator('.card table tbody tr');
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < Math.min(count, 5); i++) {
      await expect(rows.nth(i).locator('.record-type')).toHaveText('A');
    }
  });
});
