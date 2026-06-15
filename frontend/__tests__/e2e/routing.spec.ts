import { expect, test } from '@playwright/test';

/**
 * URL-based navigation E2E tests.
 *
 * These tests verify that:
 * 1. URLs reflect the current navigation state
 * 2. Navigating to a URL restores the correct state
 * 3. Auth redirects preserve the intended destination
 */

test.describe('URL-Based Navigation', () => {
  test.describe('Without Authentication', () => {
    test.beforeEach(async ({ page }) => {
      // Clear stored credentials
      await page.goto('/');
      await page.evaluate(() => localStorage.clear());
    });

    test('should show login screen when navigating with zone param', async ({
      page,
    }) => {
      await page.goto('/?zone=example.com.');

      // Should show login screen
      await expect(page.locator('.login-container')).toBeVisible();
    });

    test('should preserve zone param and redirect after login', async ({
      page,
    }) => {
      // Navigate to a specific zone URL without auth
      await page.goto('/?zone=example.com.');

      // Login
      await page
        .locator('input[placeholder="Enter your API key"]')
        .fill('demo-api-key-12345');
      await page.locator('button:has-text("Sign in with API Key")').click();

      // Should be authenticated and on the zone
      await expect(page.locator('.app-container')).toBeVisible({
        timeout: 15000,
      });

      // Should have example.com zone selected (active class)
      await expect(
        page.locator('.zone-item.active:has-text("example.com")'),
      ).toBeVisible({ timeout: 15000 });

      // URL should reflect the zone
      await expect(page).toHaveURL(/zone=example\.com\./);
    });

    test('should preserve search params and redirect after login', async ({
      page,
    }) => {
      // Navigate to a search URL without auth
      await page.goto('/?q=www&type=A&all=true');

      // Login
      await page
        .locator('input[placeholder="Enter your API key"]')
        .fill('demo-api-key-12345');
      await page.locator('button:has-text("Sign in with API Key")').click();

      // Should be authenticated
      await expect(page.locator('.app-container')).toBeVisible({
        timeout: 15000,
      });

      // Should show search results
      await expect(
        page.locator('.card-header h2:has-text("Search Results")'),
      ).toBeVisible({ timeout: 15000 });

      // URL should reflect search params
      await expect(page).toHaveURL(/q=www/);
    });
  });

  test.describe('With Authentication', () => {
    test.beforeEach(async ({ page }) => {
      // Clear stored credentials and login fresh
      await page.goto('/');
      await page.evaluate(() => localStorage.clear());
      await page.goto('/');

      // Wait for login form
      const apiKeyInput = page.locator(
        'input[placeholder="Enter your API key"]',
      );
      await expect(apiKeyInput).toBeVisible({ timeout: 10000 });

      // Login
      await apiKeyInput.fill('demo-api-key-12345');
      await page.locator('button:has-text("Sign in with API Key")').click();

      // Wait for authentication
      await expect(page.locator('.app-container')).toBeVisible({
        timeout: 15000,
      });

      // Wait for zones to load
      await expect(page.locator('.zone-item').first()).toBeVisible({
        timeout: 30000,
      });
    });

    test('should update URL when selecting a zone', async ({ page }) => {
      // Click on example.com zone
      await page.locator('.zone-item:has-text("example.com")').click();

      // Wait for records to load
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });

      // URL should contain zone parameter
      await expect(page).toHaveURL(/zone=example\.com\./);
    });

    test('should update URL when paginating zones', async ({ page }) => {
      // If there are multiple pages, navigate to next page
      const nextButton = page.locator(
        '.zone-pagination-controls button[title="Next page"]',
      );
      if (await nextButton.isEnabled()) {
        await nextButton.click();
        await page.waitForTimeout(500);

        // URL should contain page parameter
        await expect(page).toHaveURL(/page=2/);
      }
    });

    test('should update URL when searching within a zone', async ({ page }) => {
      // Select a zone
      await page.locator('.zone-item:has-text("example.com")').click();
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });

      // Perform a search
      const searchInput = page.locator('.search-box input[type="text"]');
      await searchInput.fill('www');
      await page.waitForTimeout(500); // Debounce delay

      // URL should contain search query
      await expect(page).toHaveURL(/q=www/);
      // URL should still contain zone
      await expect(page).toHaveURL(/zone=example\.com\./);
    });

    test('should update URL when searching all zones', async ({ page }) => {
      // Check the "All Zones" checkbox
      await page.locator('input[type="checkbox"]').check();

      // Perform a search
      const searchInput = page.locator('.search-box input[type="text"]');
      await searchInput.fill('test');
      await page.waitForTimeout(500);

      // URL should contain all=true
      await expect(page).toHaveURL(/all=true/);
      await expect(page).toHaveURL(/q=test/);
    });

    test('should update URL when filtering by record type', async ({
      page,
    }) => {
      // Select a zone first
      await page.locator('.zone-item:has-text("example.com")').click();
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });

      // Search with a type filter
      const typeSelect = page.locator('.header-actions select').first();
      await typeSelect.selectOption('A');
      await page.waitForTimeout(500);

      // Type something to trigger search
      const searchInput = page.locator('.search-box input[type="text"]');
      await searchInput.fill('test');
      await page.waitForTimeout(500);

      // URL should contain type parameter
      await expect(page).toHaveURL(/type=A/);
    });

    test('should clear URL when clearing search', async ({ page }) => {
      // Select a zone and search
      await page.locator('.zone-item:has-text("example.com")').click();
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });

      const searchInput = page.locator('.search-box input[type="text"]');
      await searchInput.fill('www');
      await page.waitForTimeout(500);

      // Verify search params are in URL
      await expect(page).toHaveURL(/q=www/);

      // Clear the search
      await searchInput.clear();
      await page.waitForTimeout(500);

      // URL should not contain search query
      await expect(page).not.toHaveURL(/q=/);
      // But should still have zone
      await expect(page).toHaveURL(/zone=example\.com\./);
    });

    test('should clear URL on logout', async ({ page }) => {
      // Select a zone
      await page.locator('.zone-item:has-text("example.com")').click();
      await expect(page).toHaveURL(/zone=/);

      // Logout
      await page.locator('button[title="Logout"]').click();

      // Should show login screen
      await expect(page.locator('.login-container')).toBeVisible({
        timeout: 10000,
      });

      // URL should be clean (or just have the base path)
      const url = page.url();
      expect(url.includes('zone=')).toBe(false);
    });
  });

  test.describe('Direct URL Navigation', () => {
    test.beforeEach(async ({ page }) => {
      // Login first
      await page.goto('/');
      await page.evaluate(() => localStorage.clear());
      await page.goto('/');

      const apiKeyInput = page.locator(
        'input[placeholder="Enter your API key"]',
      );
      await expect(apiKeyInput).toBeVisible({ timeout: 10000 });
      await apiKeyInput.fill('demo-api-key-12345');
      await page.locator('button:has-text("Sign in with API Key")').click();
      await expect(page.locator('.app-container')).toBeVisible({
        timeout: 15000,
      });
      await expect(page.locator('.zone-item').first()).toBeVisible({
        timeout: 30000,
      });
    });

    test('should navigate directly to a zone via URL', async ({ page }) => {
      // Navigate directly to a zone
      await page.goto('/?zone=example.com.');

      // Should have the zone selected
      await expect(
        page.locator('.zone-item.active:has-text("example.com")'),
      ).toBeVisible({ timeout: 15000 });

      // Should show records
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });
    });

    test('should navigate directly to a global search via URL', async ({
      page,
    }) => {
      // Navigate directly to a global search
      await page.goto('/?q=www&all=true&type=A');

      // Should show search results
      await expect(
        page.locator('.card-header h2:has-text("Search Results")'),
      ).toBeVisible({ timeout: 15000 });

      // The "All Zones" checkbox should be checked
      await expect(page.locator('input[type="checkbox"]')).toBeChecked();
    });

    test('should navigate directly to a zone search via URL', async ({
      page,
    }) => {
      // Navigate directly to a zone with search
      await page.goto('/?zone=example.com.&q=www');

      // Should have the zone selected
      await expect(
        page.locator('.zone-item.active:has-text("example.com")'),
      ).toBeVisible({ timeout: 15000 });

      // Search input should have the query
      const searchInput = page.locator('.search-box input[type="text"]');
      await expect(searchInput).toHaveValue('www');
    });

    test('should restore page position from URL', async ({ page }) => {
      // First, select a zone to get pagination
      await page.goto('/?zone=example.com.');
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });

      // Check if there are multiple pages
      const pageInput = page.locator('.pagination-controls .page-input');
      if ((await pageInput.count()) > 0) {
        // Navigate to page 2 via URL
        await page.goto('/?zone=example.com.&page=2');

        // Should be on page 2
        await expect(pageInput).toHaveValue('2');
      }
    });
  });

  test.describe('URL Sharing', () => {
    test('should produce shareable URLs', async ({ page }) => {
      // Login
      await page.goto('/');
      await page.evaluate(() => localStorage.clear());
      await page.goto('/');

      const apiKeyInput = page.locator(
        'input[placeholder="Enter your API key"]',
      );
      await expect(apiKeyInput).toBeVisible({ timeout: 10000 });
      await apiKeyInput.fill('demo-api-key-12345');
      await page.locator('button:has-text("Sign in with API Key")').click();
      await expect(page.locator('.app-container')).toBeVisible({
        timeout: 15000,
      });
      await expect(page.locator('.zone-item').first()).toBeVisible({
        timeout: 30000,
      });

      // Navigate to a specific state
      await page.locator('.zone-item:has-text("example.com")').click();
      await expect(page.locator('.card .table-container')).toBeVisible({
        timeout: 15000,
      });

      // Get the current URL
      const shareableUrl = page.url();

      // Clear localStorage to simulate a new user
      await page.evaluate(() => localStorage.clear());

      // Navigate to the shareable URL
      await page.goto(shareableUrl);

      // Should show login screen (not authenticated)
      await expect(page.locator('.login-container')).toBeVisible();

      // Login again
      await page
        .locator('input[placeholder="Enter your API key"]')
        .fill('demo-api-key-12345');
      await page.locator('button:has-text("Sign in with API Key")').click();

      // Should navigate to the same zone
      await expect(
        page.locator('.zone-item.active:has-text("example.com")'),
      ).toBeVisible({ timeout: 15000 });
    });
  });
});
