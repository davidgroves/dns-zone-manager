import { expect, type Page } from '@playwright/test';

/** Whether the running backend requires an API key (or other login). */
export async function backendRequiresLogin(page: Page): Promise<boolean> {
  const response = await page.request.get('http://localhost:8000/ui/config');
  if (!response.ok()) {
    // Fall back to assuming login is required so failures are obvious.
    return true;
  }
  const config = await response.json();
  return Boolean(config.apiKeyEnabled || config.azureEnabled || config.proxyAuthEnabled);
}

/**
 * Reach the authenticated app shell.
 *
 * When the devcontainer (or any config) has auth disabled, there is no login
 * screen — wait for the app directly. Otherwise sign in with the demo API key.
 */
export async function ensureLoggedIn(page: Page): Promise<void> {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/');

  if (!(await backendRequiresLogin(page))) {
    await expect(page.locator('.app-container')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('.zone-item').first()).toBeVisible({
      timeout: 30000,
    });
    return;
  }

  const apiKeyInput = page.locator('input[placeholder="Enter your API key"]');
  await expect(apiKeyInput).toBeVisible({ timeout: 10000 });
  await expect(apiKeyInput).toBeEnabled();
  await apiKeyInput.fill('demo-api-key-12345');
  await page.locator('button:has-text("Sign in with API Key")').click();
  await expect(page.locator('.app-container')).toBeVisible({ timeout: 15000 });
  await expect(page.locator('.zone-item').first()).toBeVisible({
    timeout: 30000,
  });
}
