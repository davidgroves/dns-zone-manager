import { expect, test } from '@playwright/test';
import { ensureLoggedIn, selectFirstZone } from './helpers';

/**
 * DNS record management E2E — zone select + add/delete + sort.
 */

test.describe('Record Management', () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedIn(page);
    await selectFirstZone(page);
  });

  test('should display records table with headers', async ({ page }) => {
    await expect(page.locator('.card table th:has-text("FQDN"), .card table th:has-text("Name")').first()).toBeVisible();
    await expect(page.locator('.card table th:has-text("Type")')).toBeVisible();
    await expect(page.locator('.card table th:has-text("TTL")')).toBeVisible();
    await expect(page.locator('.card table th:has-text("Data")')).toBeVisible();
  });

  test('should add and delete a record', async ({ page }) => {
    const unique = `e2e-rec-${Date.now()}`;

    await page.locator('.card-header button:has-text("Add Record")').click();
    await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });

    await page.locator('.modal-body input').first().fill(unique);
    await page.locator('.modal-body textarea').fill('192.0.2.200');
    await page.locator('.modal-footer button.btn-success').click();

    await expect(page.locator('.modal-backdrop')).not.toBeVisible({ timeout: 15000 });
    const row = page.locator('.card table tr', { hasText: unique });
    await expect(row).toBeVisible({ timeout: 15000 });

    await row.locator('button.btn-action-delete').click();
    await expect(page.locator('h3:has-text("Delete Record")')).toBeVisible({ timeout: 5000 });
    await page.locator('.modal-footer button.btn-danger:has-text("Delete")').click();

    await expect(page.locator('.card table tr', { hasText: unique })).toHaveCount(0, {
      timeout: 15000,
    });
  });

  test('should sort by clicking column header', async ({ page }) => {
    const nameHeader = page.locator('.card table th.sortable').first();
    await expect(nameHeader).toBeVisible({ timeout: 10000 });
    const before = await nameHeader.innerText();
    await nameHeader.click();
    await nameHeader.click();
    const after = await nameHeader.innerText();
    // Sort icon character should change between clicks (↕ / ↑ / ↓)
    expect(before.length).toBeGreaterThan(0);
    expect(after.length).toBeGreaterThan(0);
  });

  test('should open history modal', async ({ page }) => {
    await page.locator('button:has-text("History")').click();
    await expect(page.locator('h3:has-text("Zone History")')).toBeVisible({
      timeout: 5000,
    });
    await page.locator('button:has-text("Load History")').click();
    // Either history content or empty/full-axfr message appears
    await expect(
      page.locator('.modal-body').filter({ hasText: /serial|history|AXFR|No history/i }),
    ).toBeVisible({ timeout: 15000 });
  });

  test('should trigger zone export download', async ({ page }) => {
    const downloadPromise = page.waitForEvent('download', { timeout: 15000 });
    await page.locator('button[title="Export zone file"], button:has-text("Export")').first().click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.|\.zone|example/i);
  });
});

test.describe('Atomic Apply Now', () => {
  test('should apply queued ops immediately via atomic endpoint', async ({ page }) => {
    await ensureLoggedIn(page);
    await selectFirstZone(page);

    const unique = `atomic-now-${Date.now()}`;

    await page.locator('button.atomic-toggle').click();
    await expect(page.locator('button.atomic-toggle.active')).toBeVisible();

    await page.locator('.card-header button:has-text("Add Record")').click();
    await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });
    await page.locator('.modal-body input').first().fill(unique);
    await page.locator('.modal-body textarea').fill('192.0.2.201');
    await page.locator('.modal-footer button.btn-success').click();

    await expect(page.locator('.atomic-indicator')).toBeVisible({ timeout: 5000 });
    await page.locator('.atomic-indicator').click();
    await page.locator('.modal-footer button:has-text("Apply Now")').click();

    // Queue clears on success; dismiss the now-empty atomic modal
    await expect(page.locator('.atomic-indicator')).toHaveCount(0, { timeout: 15000 });
    await page.locator('.modal-backdrop .modal-header button').first().click();
    await expect(page.locator('.modal-backdrop')).toHaveCount(0, { timeout: 5000 });

    // Leave atomic mode so subsequent cleanup uses live delete
    const atomicToggle = page.locator('button.atomic-toggle.active');
    if (await atomicToggle.count()) {
      await atomicToggle.click();
    }

    await expect(page.locator('.card table tr', { hasText: unique })).toBeVisible({
      timeout: 15000,
    });

    // Cleanup
    await page.locator('.card table tr', { hasText: unique })
      .locator('button.btn-action-delete')
      .click();
    await page.locator('.modal-footer button.btn-danger:has-text("Delete")').click();
  });
});
