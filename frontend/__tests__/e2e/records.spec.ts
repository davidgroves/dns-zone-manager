import { expect, test } from '@playwright/test';
import { ensureLoggedIn } from './helpers';

/**
 * DNS Record management E2E tests.
 *
 * These tests verify viewing, adding, editing, and deleting DNS records.
 */

test.describe('Record Management', () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedIn(page);
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
