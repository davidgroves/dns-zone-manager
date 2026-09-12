import { expect, test, type Page } from '@playwright/test';

test.describe('Audit Logs', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/');

    const apiKeyInput = page.locator('input[placeholder="Enter your API key"]');
    await expect(apiKeyInput).toBeVisible({ timeout: 10000 });
    await apiKeyInput.fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();

    await expect(page.locator('.app-container')).toBeVisible({ timeout: 15000 });
  });

  test('should open audit page from header', async ({ page }) => {
    await page.locator('button[title="Audit Logs"]').click();
    await expect(page.locator('h2:has-text("Audit Logs")')).toBeVisible({
      timeout: 5000,
    });
    await expect(page).toHaveURL(/view=audit/);
    await expect(page.locator('.card-body.scheduled-view-body')).toBeVisible();
  });

  test('should show edited draft in audit table and open change detail', async ({
    page,
  }) => {
    const unique = `audit-e2e-${Date.now()}`;
    await createDraft(page, unique);

    await page.locator('button[title="Scheduled Changes"]').click();
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    await row.locator('button[title="Edit"]').click();
    await expect(page.locator('h3:has-text("Edit Scheduled Change")')).toBeVisible({
      timeout: 5000,
    });
    const renamed = `E2E ${unique}-renamed`;
    await page.locator('.modal-body input[type="text"]').first().fill(renamed);
    await page.locator('.modal-footer button:has-text("Save Changes")').click();
    await expect(page.locator('h3:has-text("Edit Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });

    await page.locator('button[title="Audit Logs"]').click();
    await expect(page.locator('h2:has-text("Audit Logs")')).toBeVisible({
      timeout: 5000,
    });

    const auditTable = page.locator('#audit-events-table');
    await expect(auditTable).toBeVisible({ timeout: 10000 });
    await expect(page.locator('.card-body.scheduled-view-body')).toBeVisible();
    await expect(page.locator('.audit-pagination #audit-result-summary')).toContainText(/events?/);
    // Pagination sits above the table (below filters).
    const filtersBox = await page.locator('.audit-filters').boundingBox();
    const paginationBox = await page.locator('.audit-pagination').boundingBox();
    const tableBox = await page.locator('#audit-events-table').boundingBox();
    expect(filtersBox).not.toBeNull();
    expect(paginationBox).not.toBeNull();
    expect(tableBox).not.toBeNull();
    expect(paginationBox!.y).toBeGreaterThan(filtersBox!.y);
    expect(tableBox!.y).toBeGreaterThan(paginationBox!.y);

    // Search for the renamed change
    await page.locator('.audit-filters input[placeholder="Search…"]').fill(renamed);
    await page.locator('.audit-filters button:has-text("Search")').click();

    const auditRow = auditTable.locator('tr', { hasText: renamed });
    await expect(auditRow.first()).toBeVisible({ timeout: 10000 });
    await expect(auditTable.locator('tr', { hasText: 'updated' }).first()).toBeVisible();
    await expect(page.locator('.audit-pagination #audit-result-summary')).toContainText(/events?/);
    await expect(auditRow.first().locator('td').first()).not.toContainText('UTC');

    await auditRow.first().locator('button.btn-link').click();
    await expect(page.locator('h2:has-text("Scheduled Changes")')).toBeVisible({
      timeout: 5000,
    });
    await expect(page.locator('#scheduled-change-detail h3')).toHaveText(renamed, {
      timeout: 10000,
    });
  });
});

async function createDraft(page: Page, unique: string) {
  await page.locator('button.atomic-toggle').click();
  await expect(page.locator('button.atomic-toggle.active')).toBeVisible();

  await page.locator('.zone-item:has-text("example.com")').click();
  await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });

  await page.locator('.card-header button:has-text("Add Record")').click();
  await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });

  await page.locator('.modal-body input').first().fill(unique);
  await page.locator('.modal-body textarea, .modal-body input').last().fill('192.0.2.88');
  await page.locator('.modal-footer button.btn-success').click();

  await expect(page.locator('.atomic-indicator')).toBeVisible({ timeout: 5000 });
  await page.locator('.atomic-indicator').click();
  await page.locator('button:has-text("Save as Change")').click();
  await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeVisible({
    timeout: 5000,
  });
  await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}`);
  await page.locator('.modal-footer button:has-text("Save Change")').click();
  await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
    timeout: 10000,
  });
}
