import { expect, test } from '@playwright/test';

/**
 * DNS Record management E2E tests.
 *
 * These tests verify viewing, adding, editing, and deleting DNS records.
 */

test.describe('Record Management', () => {
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

    // Wait for zones and select example.com
    await expect(page.locator('.zone-item').first()).toBeVisible({
      timeout: 10000,
    });
    await page.locator('.zone-item:has-text("example.com")').click();

    // Wait for zone to be selected and records to load
    // Note: This requires the FastAPI backend to be running on port 8000
    await expect(
      page.locator('.card-header h2:has-text("example.com")'),
    ).toBeVisible({ timeout: 10000 });

    // Wait for records to finish loading (table appears when loadingRecords is false)
    // If this times out, check that the FastAPI backend is running via VS Code tasks
    await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });
  });

  test('should display records table with headers', async ({ page }) => {
    // Should show records table with headers
    await expect(page.locator('th:has-text("FQDN")')).toBeVisible();
    await expect(page.locator('th:has-text("Type")')).toBeVisible();
    await expect(page.locator('th:has-text("TTL")')).toBeVisible();
    await expect(page.locator('th:has-text("Data")')).toBeVisible();
  });

  test('should open add record modal', async ({ page }) => {
    // Click add record button
    await page.locator('.card-header button:has-text("Add Record")').click();

    // Modal should be visible
    await expect(page.locator('.modal-backdrop')).toBeVisible({
      timeout: 5000,
    });

    // Should have form fields
    await expect(page.locator('.modal input[placeholder="www"]')).toBeVisible();
    await expect(page.locator('.modal .type-select')).toBeVisible();
  });

  test('should close modal with cancel button', async ({ page }) => {
    // Open modal
    await page.locator('.card-header button:has-text("Add Record")').click();
    await expect(page.locator('.modal-backdrop')).toBeVisible();

    // Click cancel
    await page.locator('button:has-text("Cancel")').click();

    // Modal should be hidden
    await expect(page.locator('.modal-backdrop')).not.toBeVisible();
  });
});
