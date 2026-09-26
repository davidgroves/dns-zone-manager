import { expect, test } from '@playwright/test';
import { ensureLoggedIn } from './helpers';

test.describe('NSUPDATE drafts', () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedIn(page);
  });

  test('should save nsupdate paste as a draft in Scheduled Changes', async ({
    page,
  }) => {
    const unique = `nsupdate-e2e-${Date.now()}`;
    await page.locator('button[title="NSUPDATE"]').click();
    await expect(page.locator('h3:has-text("NSUPDATE → Drafts")')).toBeVisible({
      timeout: 5000,
    });

    const script = `zone example.com.
update add ${unique}.example.com. 300 A 192.0.2.77
send
`;
    await page.locator('.modal-body textarea').fill(script);
    await page.locator('.modal-footer button:has-text("Save as Draft(s)")').click();

    await expect(page.locator('h2:has-text("Scheduled Changes")')).toBeVisible({
      timeout: 10000,
    });
    await expect(page).toHaveURL(/view=scheduled/);

    const row = page.locator('.scheduled-table tr', {
      hasText: 'NSUPDATE · example.com',
    });
    await expect(row.first()).toBeVisible({ timeout: 10000 });
    await expect(row.first()).toContainText('draft', { ignoreCase: true });

    // Open detail and confirm the op landed
    await row.first().locator('button:has-text("View")').click();
    await expect(page.locator('#scheduled-change-detail')).toBeVisible({
      timeout: 5000,
    });
    await expect(page.locator('#scheduled-change-detail')).toContainText(unique);
  });
});
