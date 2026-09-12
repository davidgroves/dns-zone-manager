import { expect, test, type Page } from '@playwright/test';

test.describe('Scheduled Changes', () => {
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

  test('should open scheduled changes view', async ({ page }) => {
    await page.locator('button[title="Scheduled Changes"]').click();
    await expect(page.locator('h2:has-text("Scheduled Changes")')).toBeVisible({
      timeout: 5000,
    });
    await expect(page.locator('.status-filter-toggle')).toContainText(
      'Draft, Scheduled, Failed',
    );
  });

  test('should filter by multiple statuses via checkbox menu', async ({ page }) => {
    const unique = `filter-e2e-${Date.now()}`;
    await createDraft(page, unique);

    await page.locator('button[title="Scheduled Changes"]').click();
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    // Uncheck Draft — draft row should disappear under the default active filter.
    await setStatusFilterChecked(page, 'Draft', false);
    await expect(row).toHaveCount(0);

    await setStatusFilterChecked(page, 'Draft', true);
    await expect(row).toBeVisible({ timeout: 10000 });

    await ensureStatusFilters(page, ['Applied']);
    page.once('dialog', (dialog) => dialog.accept());
    await row.getByRole('button', { name: 'Apply Now', exact: true }).click();
    await expect(row.locator('.status-badge', { hasText: /^applied$/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('should queue a change, save as draft, then apply now', async ({ page }) => {
    await page.locator('button.atomic-toggle').click();
    await expect(page.locator('button.atomic-toggle.active')).toBeVisible();

    await page.locator('.zone-item:has-text("example.com")').click();
    await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });

    await page.locator('.card-header button:has-text("Add Record")').click();
    await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });

    const unique = `sched-e2e-${Date.now()}`;
    await page.locator('.modal-body input').first().fill(unique);

    const recordsField = page.locator('.modal-body textarea, .modal-body input').last();
    await recordsField.fill('192.0.2.99');

    await page.locator('.modal-footer button.btn-success').click();

    await expect(page.locator('.atomic-indicator')).toBeVisible({ timeout: 5000 });
    await page.locator('.atomic-indicator').click();
    await expect(page.locator('h3:has-text("Atomic Changes")')).toBeVisible();

    await page.locator('button:has-text("Save as Change")').click();
    await expect(
      page.locator('h3:has-text("Save as Scheduled Change")'),
    ).toBeVisible({ timeout: 5000 });

    await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}`);
    await page.locator('button:has-text("Save Change")').click();

    await page.locator('button[title="Scheduled Changes"]').click();
    await expect(page.locator('h2:has-text("Scheduled Changes")')).toBeVisible({
      timeout: 5000,
    });
    // Scope to the table: the success toast also contains the change name.
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    page.once('dialog', (dialog) => dialog.accept());
    await row.locator('button:has-text("Apply Now")').click();

    await expect(page.locator('.toast.success, .toast-message').first()).toBeVisible({
      timeout: 15000,
    });
  });

  test('should edit an existing draft change', async ({ page }) => {
    const unique = `edit-e2e-${Date.now()}`;
    await createDraft(page, unique);

    await page.locator('button[title="Scheduled Changes"]').click();
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    await row.locator('button[title="Edit"]').click();
    await expect(page.locator('h3:has-text("Edit Scheduled Change")')).toBeVisible({
      timeout: 5000,
    });

    // The modal is pre-filled from the stored change.
    const nameInput = page.locator('.modal-body input[type="text"]').first();
    await expect(nameInput).toHaveValue(`E2E ${unique}`);
    await expect(page.locator('.modal-body .schedule-op-row')).toHaveCount(1);

    await nameInput.fill(`E2E ${unique} renamed`);

    // Append a second ADD operation.
    await page.locator('.modal-body button:has-text("+ Add")').first().click();
    await expect(page.locator('.modal-body .schedule-op-row')).toHaveCount(2);
    const newOp = page.locator('.modal-body .schedule-op-row').nth(1);
    await newOp.locator('input[placeholder="name"]').fill(`${unique}-extra`);
    await newOp.locator('textarea[placeholder="One value per line"]').fill('192.0.2.88');

    await page.locator('.modal-footer button:has-text("Save Changes")').click();

    await expect(page.locator('h3:has-text("Edit Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });
    const renamed = page.locator('.scheduled-table tr', {
      hasText: `E2E ${unique} renamed`,
    });
    await expect(renamed).toBeVisible({ timeout: 10000 });
    await expect(renamed.locator('td').nth(4)).toHaveText('2');

    page.once('dialog', (dialog) => dialog.accept());
    await renamed.locator('button[title="Cancel change"]').click();
  });

  test('should view an applied change detail above the table', async ({ page }) => {
    // Create and apply a draft so we have a known applied row.
    const unique = `view-applied-${Date.now()}`;
    await createDraft(page, unique);

    await page.locator('button[title="Scheduled Changes"]').click();
    await ensureStatusFilters(page, ['Applied']);
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    page.once('dialog', (dialog) => dialog.accept());
    await row.getByRole('button', { name: 'Apply Now', exact: true }).click();
    await expect(row.locator('.status-badge', { hasText: /^applied$/i })).toBeVisible({
      timeout: 15000,
    });

    await row.getByRole('button', { name: 'View', exact: true }).click();
    const detail = page.locator('#scheduled-change-detail');
    await expect(detail).toBeVisible({ timeout: 5000 });
    await expect(detail.locator('h4:has-text("Operations")')).toBeVisible();
    await expect(detail).toContainText(unique);
    // Applied changes skip dry-run preview.
    await expect(detail.locator('.scheduled-preview')).toHaveCount(0);
    await expect(detail.getByRole('button', { name: 'Refresh preview' })).toHaveCount(0);

    const detailBox = await detail.boundingBox();
    const tableBox = await page.locator('.scheduled-table').boundingBox();
    expect(detailBox).not.toBeNull();
    expect(tableBox).not.toBeNull();
    expect(detailBox!.y).toBeLessThan(tableBox!.y);
  });

  test('should open revert modal and revert an applied change', async ({ page }) => {
    const unique = `revert-e2e-${Date.now()}`;
    await createDraft(page, unique);

    await page.locator('button[title="Scheduled Changes"]').click();
    await ensureStatusFilters(page, ['Applied', 'Reverted']);
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    page.once('dialog', (dialog) => dialog.accept());
    await row.getByRole('button', { name: 'Apply Now', exact: true }).click();
    await expect(row.locator('.status-badge', { hasText: /^applied$/i })).toBeVisible({
      timeout: 15000,
    });

    await expect(row.getByRole('button', { name: 'Apply Now', exact: true })).toHaveCount(0);
    await expect(row.getByRole('button', { name: 'Revert', exact: true })).toBeVisible();
    await expect(row.getByRole('button', { name: 'Preview', exact: true })).toHaveCount(0);
    // Primary action is leftmost; View follows (Preview is folded into View).
    const textButtons = row.locator('.scheduled-actions > .scheduled-primary-action .btn, .scheduled-actions > .btn:not(.btn-icon)');
    await expect(textButtons.nth(0)).toHaveText('Revert');
    await expect(textButtons.nth(1)).toHaveText('View');
    await expect(textButtons).toHaveCount(2);
    await row.getByRole('button', { name: 'Revert', exact: true }).click();
    const modal = page.locator('.modal-backdrop', {
      has: page.locator('h3:has-text("Revert scheduled change")'),
    });
    await expect(modal).toBeVisible({ timeout: 5000 });
    await expect(modal.locator('.revert-warning')).toBeVisible();
    await expect(modal.locator('.revert-warning')).toContainText(
      'This reverts this scheduled change only',
    );
    await expect(modal.locator('.atomic-queue-action.delete')).toBeVisible();
    await expect(modal).toContainText(unique);

    await modal.getByRole('button', { name: 'Revert Now', exact: true }).click();
    await expect(modal).toBeHidden({ timeout: 15000 });
    await expect(row.locator('.status-badge', { hasText: /^reverted$/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('should mark change as failed when a prerequisite fails', async ({ page }) => {
    const unique = `fail-prereq-${Date.now()}`;
    await openScheduleModalFromQueue(page, unique);

    // Intentionally impossible: YXRRSET requires an RRset that does not exist.
    await page
      .locator('.form-group-header', { hasText: 'Additional prerequisites' })
      .getByRole('button', { name: '+ Add' })
      .click();
    const prereq = page.locator('.modal-body .prereq-row').first();
    await expect(prereq).toBeVisible();
    await prereq.locator('select').selectOption('yxrrset');
    await prereq.locator('input[placeholder="name"]').fill(`missing-${unique}`);
    await prereq.locator('input[placeholder="type"]').fill('A');

    await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}`);
    await page.locator('.modal-footer button:has-text("Save Change")').click();
    await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });

    await page.locator('button[title="Scheduled Changes"]').click();
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });
    await expect(row.locator('.status-badge', { hasText: /^draft$/i })).toBeVisible();

    // View auto-runs preview for editable drafts.
    await row.getByRole('button', { name: 'View', exact: true }).click();
    const detail = page.locator('#scheduled-change-detail');
    await expect(detail).toBeVisible({ timeout: 5000 });
    await expect(detail).toContainText(`yxrrset missing-${unique}`);
    await expect(detail.locator('h4:has-text("Preview")')).toBeVisible({ timeout: 10000 });
    await expect(detail.locator('li.text-danger').first()).toBeVisible();
    await expect(detail.locator('li.text-danger').first()).toContainText(/yxrrset|missing-/i);
    await expect(row.locator('.status-badge', { hasText: /^draft$/i })).toBeVisible();

    await expect(detail.getByRole('button', { name: 'Refresh preview', exact: true })).toBeVisible();
    await detail.getByRole('button', { name: 'Refresh preview', exact: true }).click();
    await expect(detail.locator('h4:has-text("Preview")')).toBeVisible({ timeout: 10000 });
    await expect(row.locator('.status-badge', { hasText: /^draft$/i })).toBeVisible();

    page.once('dialog', (dialog) => dialog.accept());
    await row.getByRole('button', { name: 'Apply Now', exact: true }).click();

    await expect(row.locator('.status-badge', { hasText: /^failed$/i })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.locator('.toast.error, .toast-message').first()).toBeVisible({
      timeout: 5000,
    });

    // Failed changes remain editable (retry) but cannot be reverted.
    await expect(row.getByRole('button', { name: 'Apply Now', exact: true })).toBeVisible();
    await expect(row.getByRole('button', { name: 'Revert', exact: true })).toHaveCount(0);

    await row.getByRole('button', { name: 'View', exact: true }).click();
    await expect(detail).toBeVisible();
    await expect(detail.locator('.status-badge', { hasText: /^failed$/i })).toBeVisible();
    await expect(detail.locator('p').filter({ hasText: 'Error:' })).toBeVisible();
  });

  test('should fail apply when auto NXRRSET conflicts with an existing record', async ({
    page,
  }) => {
    // www.example.com already has an A record; auto-prereqs add NXRRSET for ADD.
    const unique = `fail-nxrrset-${Date.now()}`;
    await openScheduleModalFromQueue(page, unique);

    const op = page.locator('.modal-body .schedule-op-row').first();
    await op.locator('input[placeholder="name"]').fill('www');
    await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}`);
    await page.locator('.modal-footer button:has-text("Save Change")').click();
    await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });

    await page.locator('button[title="Scheduled Changes"]').click();
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });

    page.once('dialog', (dialog) => dialog.accept());
    await row.getByRole('button', { name: 'Apply Now', exact: true }).click();
    await expect(row.locator('.status-badge', { hasText: /^failed$/i })).toBeVisible({
      timeout: 15000,
    });

    await row.getByRole('button', { name: 'View', exact: true }).click();
    const detail = page.locator('#scheduled-change-detail');
    await expect(detail.locator('p').filter({ hasText: 'Error:' })).toBeVisible();
  });

  test('should fail atomically when a 4-op change has mixed prerequisite results', async ({
    page,
  }) => {
    // Four operations in one scheduled change:
    //   1. ADD new host  — NXRRSET would pass
    //   2. ADD new host  — NXRRSET would pass
    //   3. ADD www       — NXRRSET fails (A already exists)
    //   4. DELETE ghost  — YXRRSET fails (RRset absent)
    // DDNS is atomic: any prereq failure fails the whole change; neither
    // successful ADD is applied.
    const unique = `mixed4-${Date.now()}`;
    const ok1 = `${unique}-ok1`;
    const ok2 = `${unique}-ok2`;
    const missing = `${unique}-missing`;

    await openScheduleModalFromQueue(page, ok1);

    const opsHeader = page.locator('.form-group-header', { hasText: 'Operations' });
    await opsHeader.getByRole('button', { name: '+ Add' }).click();
    await opsHeader.getByRole('button', { name: '+ Add' }).click();
    await opsHeader.getByRole('button', { name: '+ Add' }).click();
    await expect(page.locator('.modal-body .schedule-op-row')).toHaveCount(4);

    const fillAdd = async (index: number, name: string, ip: string) => {
      const row = page.locator('.modal-body .schedule-op-row').nth(index);
      await row.locator('select[aria-label="Action"]').selectOption('add');
      await row.locator('input[placeholder="name"]').fill(name);
      await row.locator('input[placeholder="type"]').fill('A');
      await row.locator('textarea[placeholder="One value per line"]').fill(ip);
    };

    await fillAdd(0, ok1, '192.0.2.11');
    await fillAdd(1, ok2, '192.0.2.12');
    await fillAdd(2, 'www', '192.0.2.13');

    const deleteOp = page.locator('.modal-body .schedule-op-row').nth(3);
    await deleteOp.locator('select[aria-label="Action"]').selectOption('delete');
    await deleteOp.locator('input[placeholder="name"]').fill(missing);
    await deleteOp.locator('input[placeholder="type"]').fill('A');

    await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}`);
    await page.locator('.modal-footer button:has-text("Save Change")').click();
    await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });

    await page.locator('button[title="Scheduled Changes"]').click();
    const row = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}` });
    await expect(row).toBeVisible({ timeout: 10000 });
    await expect(row.locator('td').nth(4)).toHaveText('4');

    await row.getByRole('button', { name: 'View', exact: true }).click();
    const detail = page.locator('#scheduled-change-detail');
    await expect(detail).toBeVisible({ timeout: 5000 });
    await expect(detail.locator('.scheduled-op-list li')).toHaveCount(4);
    await expect(detail.locator('.atomic-queue-action.add')).toHaveCount(3);
    await expect(detail.locator('.atomic-queue-action.delete')).toHaveCount(1);

    // View auto-includes preview for editable changes.
    const preview = detail.locator('.scheduled-preview');
    await expect(preview).toBeVisible({ timeout: 10000 });
    await expect(preview.getByRole('button', { name: 'Refresh preview', exact: true })).toBeVisible();

    const previewItems = preview.locator('ul').first().locator('li');
    await expect(previewItems).toHaveCount(4);

    const passed = preview.locator('li.text-success');
    const failed = preview.locator('li.text-danger');
    await expect(passed).toHaveCount(2);
    await expect(failed).toHaveCount(2);

    await expect(passed.nth(0)).toContainText(/add can proceed \(pass\)/i);
    await expect(passed.nth(1)).toContainText(/add can proceed \(pass\)/i);
    await expect(failed.nth(0)).toContainText(/already exists — add would fail/i);
    await expect(failed.nth(1)).toContainText(/does not exist — delete would fail/i);

    // Capture how mixed pass/fail preview looks in the detail panel.
    await detail.screenshot({
      path: 'test-results/scheduled-mixed-4op-preview.png',
    });

    page.once('dialog', (dialog) => dialog.accept());
    await row.getByRole('button', { name: 'Apply Now', exact: true }).click();
    await expect(row.locator('.status-badge', { hasText: /^failed$/i })).toBeVisible({
      timeout: 15000,
    });

    await row.getByRole('button', { name: 'View', exact: true }).click();
    await expect(detail.locator('.status-badge', { hasText: /^failed$/i })).toBeVisible();
    await expect(detail.locator('p').filter({ hasText: 'Error:' })).toBeVisible();
    await expect(detail.locator('.scheduled-preview')).toBeVisible({ timeout: 10000 });
    await detail.screenshot({
      path: 'test-results/scheduled-mixed-4op-failed.png',
    });

    // Neither "would-succeed" ADD was written — UPDATE is all-or-nothing.
    const checkAbsent = async (name: string) => {
      const response = await page.request.get(
        `http://localhost:8000/v1/zones/example.com./rrsets?name=${encodeURIComponent(name)}&type=A`,
        { headers: { 'X-API-Key': 'demo-api-key-12345' } },
      );
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      const rrsets = Array.isArray(body) ? body : body.rrsets || [];
      expect(rrsets).toEqual([]);
    };
    await checkAbsent(ok1);
    await checkAbsent(ok2);
  });

  test('should link draft conflicts and ignore applied ones', async ({ page }) => {
    const unique = `conflict-link-${Date.now()}`;
    const shared = `${unique}-host`;

    // First draft touching shared name.
    await openScheduleModalFromQueue(page, shared);
    await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}-a`);
    await page.locator('.modal-footer button:has-text("Save Change")').click();
    await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });

    // Second draft touching the same name.
    await openScheduleModalFromQueue(page, shared);
    // Atomic mode may already be on from the previous queue; force a second draft.
    await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}-b`);
    await page.locator('.modal-footer button:has-text("Save Change")').click();
    await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
      timeout: 10000,
    });

    await page.locator('button[title="Scheduled Changes"]').click();
    const rowB = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}-b` });
    await expect(rowB).toBeVisible({ timeout: 10000 });

    await rowB.getByRole('button', { name: 'View', exact: true }).click();
    const detail = page.locator('#scheduled-change-detail');
    await expect(detail).toBeVisible({ timeout: 5000 });
    await expect(detail.locator('h4:has-text("Conflicts")')).toBeVisible({ timeout: 10000 });
    const conflictLink = detail.locator('.scheduled-conflict-link', {
      hasText: `E2E ${unique}-a`,
    });
    await expect(conflictLink).toBeVisible();

    await conflictLink.click();
    await expect(detail.locator('h3')).toHaveText(`E2E ${unique}-a`, { timeout: 10000 });
    await expect(detail.locator('.status-badge', { hasText: /^draft$/i })).toBeVisible();

    // Apply A so it is no longer a draft/scheduled/failed conflict source.
    const rowA = page.locator('.scheduled-table tr', { hasText: `E2E ${unique}-a` });
    await ensureStatusFilters(page, ['Applied']);
    page.once('dialog', (dialog) => dialog.accept());
    await rowA.getByRole('button', { name: 'Apply Now', exact: true }).click();
    await expect(rowA.locator('.status-badge', { hasText: /^applied$/i })).toBeVisible({
      timeout: 15000,
    });

    await rowB.getByRole('button', { name: 'View', exact: true }).click();
    await expect(detail.locator('h3')).toHaveText(`E2E ${unique}-b`, { timeout: 10000 });
    // Applied change must not appear as a conflict.
    await expect(
      detail.locator('.scheduled-conflict-link', { hasText: `E2E ${unique}-a` }),
    ).toHaveCount(0);
  });

  test('should colour-code actions and show operation column headers', async ({
    page,
  }) => {
    const unique = `cols-e2e-${Date.now()}`;
    await openScheduleModalFromQueue(page, unique);

    await expect(page.locator('.schedule-op-headers')).toBeVisible();
    await expect(page.locator('.schedule-op-headers')).toContainText('Action');
    await expect(page.locator('.schedule-op-headers')).toContainText('Name');
    await expect(page.locator('.schedule-op-headers')).toContainText('Type');
    await expect(page.locator('.schedule-op-headers')).toContainText('TTL');

    const action = page.locator('.schedule-op-action').first();
    await expect(action).toHaveClass(/add/);
    await expect(page.locator('.schedule-op-ttl').first()).toBeVisible();

    await action.selectOption('delete');
    await expect(action).toHaveClass(/delete/);
    await expect(page.locator('.schedule-op-ttl').first()).toBeVisible();

    await action.selectOption('replace');
    await expect(action).toHaveClass(/replace/);
  });

  test('should render schedule modal checkboxes inline with their labels', async ({
    page,
  }) => {
    const unique = `align-e2e-${Date.now()}`;
    await openScheduleModalFromQueue(page, unique);

    const checkLabel = page.locator('.modal-body label.form-check').first();
    const checkbox = checkLabel.locator('input[type="checkbox"]');
    const text = checkLabel.locator('span');

    const boxRect = await checkbox.boundingBox();
    const textRect = await text.boundingBox();
    const labelRect = await checkLabel.boundingBox();
    expect(boxRect).not.toBeNull();
    expect(textRect).not.toBeNull();
    expect(labelRect).not.toBeNull();

    // The checkbox keeps its intrinsic size rather than stretching to the
    // full-width sizing that text inputs use.
    expect(boxRect!.width).toBeLessThan(30);
    expect(boxRect!.width).toBeLessThan(labelRect!.width / 2);

    // Checkbox sits to the left of its text, vertically centred against it.
    expect(boxRect!.x + boxRect!.width).toBeLessThanOrEqual(textRect!.x + 1);
    const boxCentre = boxRect!.y + boxRect!.height / 2;
    const textCentre = textRect!.y + textRect!.height / 2;
    expect(Math.abs(boxCentre - textCentre)).toBeLessThan(6);
  });
});

/** Queue a single record add and open the schedule modal from it. */
async function openScheduleModalFromQueue(page: Page, unique: string) {
  await page.locator('button.atomic-toggle').click();
  await expect(page.locator('button.atomic-toggle.active')).toBeVisible();

  await page.locator('.zone-item:has-text("example.com")').click();
  await expect(page.locator('.card table')).toBeVisible({ timeout: 30000 });

  await page.locator('.card-header button:has-text("Add Record")').click();
  await expect(page.locator('.modal-backdrop')).toBeVisible({ timeout: 5000 });

  await page.locator('.modal-body input').first().fill(unique);
  await page.locator('.modal-body textarea, .modal-body input').last().fill('192.0.2.99');
  await page.locator('.modal-footer button.btn-success').click();

  await expect(page.locator('.atomic-indicator')).toBeVisible({ timeout: 5000 });
  await page.locator('.atomic-indicator').click();
  await page.locator('button:has-text("Save as Change")').click();
  await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeVisible({
    timeout: 5000,
  });
}

/** Create a draft change named `E2E ${unique}` from a freshly queued record. */
async function createDraft(page: Page, unique: string) {
  await openScheduleModalFromQueue(page, unique);
  await page.locator('.modal-body input[type="text"]').first().fill(`E2E ${unique}`);
  await page.locator('.modal-footer button:has-text("Save Change")').click();
  await expect(page.locator('h3:has-text("Save as Scheduled Change")')).toBeHidden({
    timeout: 10000,
  });
}

/** Ensure the given status labels are checked in the multi-select filter. */
async function ensureStatusFilters(page: Page, labels: string[]) {
  const menu = page.locator('.status-filter-menu');
  if (!(await menu.isVisible())) {
    await page.locator('.status-filter-toggle').click();
    await expect(menu).toBeVisible();
  }
  for (const label of labels) {
    await setStatusFilterChecked(page, label, true, false);
  }
  await page.locator('.status-filter-toggle').click();
  await expect(menu).toBeHidden();
}

async function setStatusFilterChecked(
  page: Page,
  label: string,
  checked: boolean,
  closeMenu = true,
) {
  const menu = page.locator('.status-filter-menu');
  if (!(await menu.isVisible())) {
    await page.locator('.status-filter-toggle').click();
    await expect(menu).toBeVisible();
  }
  const option = menu.locator('.status-filter-option', {
    has: page.locator('span', { hasText: new RegExp(`^${label}$`) }),
  });
  const checkbox = option.locator('input[type="checkbox"]');
  if ((await checkbox.isChecked()) !== checked) {
    await option.click();
  }
  if (checked) {
    await expect(checkbox).toBeChecked();
  } else {
    await expect(checkbox).not.toBeChecked();
  }
  if (closeMenu) {
    await page.locator('.status-filter-toggle').click();
    await expect(menu).toBeHidden();
  }
}
