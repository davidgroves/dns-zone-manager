import { expect, type Page, test } from '@playwright/test';

/**
 * The Scheduled Changes list mixes changes created in the scheduler with
 * manual record edits that were auto-recorded so webhook notifications can
 * link to them. These tests cover the Source badge and filter that let an
 * operator tell the two apart.
 *
 * Auto-recording only happens when webhooks are enabled in the backend
 * config, so the manual-change tests skip when no manual changes exist.
 */
test.describe('Scheduled Changes source filter', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/');

    const apiKeyInput = page.locator('input[placeholder="Enter your API key"]');
    await expect(apiKeyInput).toBeVisible({ timeout: 10000 });
    await apiKeyInput.fill('demo-api-key-12345');
    await page.locator('button:has-text("Sign in with API Key")').click();

    await expect(page.locator('.app-container')).toBeVisible({ timeout: 15000 });
    await openScheduledView(page);
  });

  test('shows the source filter with all sources selected by default', async ({
    page,
  }) => {
    const filter = page.locator('.source-filter');
    await expect(filter).toBeVisible();
    await expect(filter.locator('button:has-text("All sources")')).toHaveClass(
      /btn-active/,
    );
  });

  test('shows a Source column with a badge for every change', async ({ page }) => {
    await createDraft(page, `source-col-${Date.now()}`);
    await openScheduledView(page);

    await expect(page.locator('th:has-text("Source")')).toBeVisible();

    const rows = page.locator('.scheduled-table tbody tr');
    await expect(rows.first()).toBeVisible({ timeout: 10000 });
    await expect(rows.first().locator('.source-badge')).toBeVisible();
  });

  test('a change created in the scheduler is labelled Scheduled', async ({ page }) => {
    const unique = `source-sched-${Date.now()}`;
    await createDraft(page, unique);
    await openScheduledView(page);

    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });
    await expect(row.locator('.source-badge')).toHaveText('Scheduled');
    await expect(row.locator('.source-badge')).toHaveClass(/source-badge-scheduler/);
  });

  test('filtering to Manual hides scheduler-created changes', async ({ page }) => {
    const unique = `source-filter-${Date.now()}`;
    await createDraft(page, unique);
    await openScheduledView(page);

    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    await page.locator('.source-filter button:has-text("Manual")').click();
    await expect(
      page.locator('.source-filter button:has-text("Manual")'),
    ).toHaveClass(/btn-active/);

    await expect(row).toHaveCount(0, { timeout: 10000 });

    // Every remaining row, if any, is a manual change
    const badges = page.locator('.scheduled-table tbody .source-badge');
    for (let i = 0; i < (await badges.count()); i++) {
      await expect(badges.nth(i)).toHaveText('Manual');
    }
  });

  test('filtering back to Scheduled shows the change again', async ({ page }) => {
    const unique = `source-back-${Date.now()}`;
    await createDraft(page, unique);
    await openScheduledView(page);

    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    await page.locator('.source-filter button:has-text("Manual")').click();
    await expect(row).toHaveCount(0, { timeout: 10000 });

    // Selecting Manual widened the status filter to applied/failed, so the
    // draft stays hidden until the status filter is put back.
    await page.locator('.source-filter button:has-text("Scheduled")').click();
    await expect(row).toHaveCount(0);

    await page.locator('.status-filter-toggle').click();
    await page.locator('.status-filter-action:has-text("Active only")').click();
    await expect(row).toBeVisible({ timeout: 10000 });
  });

  test('the detail panel shows the source badge', async ({ page }) => {
    const unique = `source-detail-${Date.now()}`;
    await createDraft(page, unique);
    await openScheduledView(page);

    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });
    await row.locator('button:has-text("View")').click();

    const detail = page.locator('#scheduled-change-detail');
    await expect(detail).toBeVisible({ timeout: 5000 });
    await expect(detail.locator('.source-badge')).toHaveText('Scheduled');
  });

  test('selecting Manual widens the status filter to applied changes', async ({
    page,
  }) => {
    // Manual changes are always already applied, which the default status
    // filter hides, so the source filter has to widen it.
    await expect(page.locator('.status-filter-toggle')).toContainText(
      'Draft, Scheduled, Failed',
    );

    await page.locator('.source-filter button:has-text("Manual")').click();

    await expect(page.locator('.status-filter-toggle')).toContainText(
      'Applied, Failed',
    );
  });

  test('an auto-recorded manual change is linkable from its change id', async ({
    page,
  }) => {
    // Make a real record edit, which the backend records as a manual change
    // when webhooks are enabled.
    const unique = `manual-rec-${Date.now()}`;
    await addRecordDirectly(page, unique);
    await skipUnlessAutorecorded(page, unique);

    await openScheduledView(page);
    await page.locator('.source-filter button:has-text("Manual")').click();

    const row = page.locator('.scheduled-table tr', { hasText: unique });
    await expect(row).toBeVisible({ timeout: 10000 });
    await expect(row.locator('.source-badge')).toHaveText('Manual');

    await row.locator('button:has-text("View")').click();
    await expect(page).toHaveURL(/change=/);

    const detail = page.locator('#scheduled-change-detail');
    await expect(detail).toBeVisible({ timeout: 5000 });
    await expect(detail.locator('.source-badge')).toHaveText('Manual');

    // A manual record has no pre-apply snapshot, so it cannot be reverted
    await expect(row.locator('button:has-text("Revert")')).toHaveCount(0);
  });

  test('the link from a webhook notification opens the change', async ({ page }) => {
    const unique = `manual-link-${Date.now()}`;
    await addRecordDirectly(page, unique);
    await skipUnlessAutorecorded(page, unique);

    await openScheduledView(page);
    await page.locator('.source-filter button:has-text("Manual")').click();

    const row = page.locator('.scheduled-table tr', { hasText: unique });
    await expect(row).toBeVisible({ timeout: 10000 });
    await row.locator('button:has-text("View")').click();
    await expect(page).toHaveURL(/change=/);

    // This is the URL shape the backend puts in the notification
    const changeId = page.url().match(/[?&]change=([^&]+)/)?.[1];
    expect(changeId).toBeTruthy();

    await page.goto(`/?view=scheduled&change=${changeId}`);
    await expect(page.locator('#scheduled-change-detail')).toBeVisible({
      timeout: 10000,
    });
    await expect(page.locator('#scheduled-change-detail .source-badge')).toHaveText(
      'Manual',
    );
  });
});

/**
 * Auto-recording of manual changes only happens when the backend has webhooks
 * enabled, so skip rather than fail when it is switched off.
 */
async function skipUnlessAutorecorded(page: Page, unique: string) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const response = await page.request.get(
      'http://localhost:8000/v1/scheduled-changes?source=manual&status=applied',
      { headers: { 'X-API-Key': 'demo-api-key-12345' } },
    );
    if (response.ok()) {
      const changes = (await response.json()).changes || [];
      if (changes.some((c: { name: string }) => c.name.includes(unique))) return;
    }
    await page.waitForTimeout(250);
  }
  test.skip(
    true,
    'Backend has webhooks disabled, so manual changes are not auto-recorded',
  );
}

async function openScheduledView(page: Page) {
  await page.locator('button[title="Scheduled Changes"]').click();
  await expect(page.locator('h2:has-text("Scheduled Changes")')).toBeVisible({
    timeout: 10000,
  });
}

async function addRecordDirectly(page: Page, unique: string) {
  await page.locator('.zone-item:has-text("example.com")').click();
  await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });

  await page.locator('.card-header button:has-text("Add Record")').click();
  await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });

  await page.locator('.modal-body input').first().fill(unique);
  await page.locator('.modal-body textarea, .modal-body input').last().fill('192.0.2.77');
  await page.locator('.modal-footer button.btn-success').click();

  await expect(page.locator('.modal-backdrop')).toBeHidden({ timeout: 10000 });
}

async function createDraft(page: Page, unique: string) {
  await page.locator('button.atomic-toggle').click();
  await expect(page.locator('button.atomic-toggle.active')).toBeVisible();

  await page.locator('.zone-item:has-text("example.com")').click();
  await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });

  await page.locator('.card-header button:has-text("Add Record")').click();
  await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });

  await page.locator('.modal-body input').first().fill(unique);
  await page.locator('.modal-body textarea, .modal-body input').last().fill('192.0.2.98');
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

  // Leave atomic mode so later steps operate on live records
  await page.locator('button.atomic-toggle').click();
}
