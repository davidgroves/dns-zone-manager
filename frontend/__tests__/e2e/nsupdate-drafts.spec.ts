import { expect, test } from '@playwright/test';

test.describe('NSUPDATE drafts', () => {
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
