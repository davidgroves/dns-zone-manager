import { expect, test } from '@playwright/test';

/**
 * Search functionality E2E tests.
 *
 * These tests verify zone-level and global search features.
 */

test.describe('Search Functionality', () => {
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

    // Wait for zones to load
    await expect(page.locator('.zone-item').first()).toBeVisible({
      timeout: 10000,
    });
  });

  test('should search within a zone', async ({ page }) => {
    // Select example.com zone
    await page.locator('.zone-item:has-text("example.com")').click();

    // Wait for records to load (requires backend on port 8000)
    await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });

    // Find the search input
    const searchInput = page.locator('.search-box input[type="text"]');
    await expect(searchInput).toBeVisible();

    // Search for www
    await searchInput.fill('www');
    await page.waitForTimeout(500); // Debounce delay

    // Results should update
    await expect(page.locator('.card')).toBeVisible();
  });

  test('should clear search', async ({ page }) => {
    // Select a zone
    await page.locator('.zone-item').first().click();
    await expect(page.locator('.card table')).toBeVisible({ timeout: 15000 });

    // Perform a search
    const searchInput = page.locator('.search-box input[type="text"]');
    await searchInput.fill('test');
    await page.waitForTimeout(500);

    // Clear the search
    await searchInput.clear();
    await page.waitForTimeout(500);

    // Table should still be visible
    await expect(page.locator('.card table')).toBeVisible();
  });

  test('should filter by record type', async ({ page }) => {
    // Select example.com zone
    await page.locator('.zone-item:has-text("example.com")').click();
    await expect(page.locator('.card table')).toBeVisible({ timeout: 15000 });

    // Find the type filter dropdown
    const typeSelect = page.locator('.header-actions select').first();
    if ((await typeSelect.count()) > 0) {
      await typeSelect.selectOption('A');
      await page.waitForTimeout(500);

      // Table should still be visible
      await expect(page.locator('.card')).toBeVisible();
    }
  });

  test('should filter by type without search query and show results', async ({
    page,
  }) => {
    // Select example.com zone
    await page.locator('.zone-item:has-text("example.com")').click();
    await expect(page.locator('.card table')).toBeVisible({ timeout: 15000 });

    // Verify we have records visible before filtering
    const recordRowsBefore = page.locator('.card table tbody tr');
    const countBefore = await recordRowsBefore.count();
    expect(countBefore).toBeGreaterThan(0);

    // Find the type filter dropdown and select A records
    const typeSelect = page.locator('.header-actions select').first();
    await typeSelect.selectOption('A');

    // Wait for the filter to be applied
    await page.waitForTimeout(800);

    // Should show search/filter results with some A records
    // The key assertion: we should have results, not an empty state
    const resultsTable = page.locator('.card table tbody tr');
    await expect(resultsTable.first()).toBeVisible({ timeout: 5000 });

    // Verify at least one result is shown
    const countAfter = await resultsTable.count();
    expect(countAfter).toBeGreaterThan(0);

    // Verify all visible records are type A
    const typeLabels = page.locator(
      '.card table tbody tr td .record-type, .card table tbody tr td span.record-type',
    );
    const typeCount = await typeLabels.count();
    expect(typeCount).toBeGreaterThan(0);

    for (let i = 0; i < typeCount; i++) {
      const typeText = await typeLabels.nth(i).textContent();
      expect(typeText).toBe('A');
    }
  });
});
