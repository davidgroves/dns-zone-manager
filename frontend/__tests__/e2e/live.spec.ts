import { expect, test } from '@playwright/test';
import { ensureLoggedIn } from './helpers';

/**
 * Live WebSocket updates should patch the zone table without a full navigation.
 */
test.describe('Live zone updates', () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedIn(page);
  });

  test('should flash and update a visible record when it changes via API', async ({
    page,
    request,
  }) => {
    await page.locator('.zone-item:has-text("example.com")').click();
    await expect(page.locator('.card table tbody tr').first()).toBeVisible({
      timeout: 30000,
    });

    const firstRow = page.locator('.card table tbody tr').first();
    const nameText = (
      await firstRow.locator('.record-name').innerText()
    ).trim();
    const typeText = (
      await firstRow.locator('.record-type').innerText()
    ).trim();
    const originalData = (
      await firstRow.locator('.record-data').innerText()
    ).trim();
    const originalTtl = Number(
      (await firstRow.locator('.record-ttl').innerText()).trim(),
    );

    // Only mutate simple single-value A/AAAA/TXT rows to keep restore easy.
    test.skip(
      !['A', 'AAAA', 'TXT'].includes(typeText),
      `first row type ${typeText} not suitable for live mutate`,
    );

    const newValue =
      typeText === 'A'
        ? '192.0.2.200'
        : typeText === 'AAAA'
          ? '2001:db8::200'
          : `"live-e2e-${Date.now()}"`;

    const replace = await request.put(
      'http://localhost:8000/v1/zones/example.com./rrsets',
      {
        data: {
          name: nameText.endsWith('.') ? nameText : `${nameText}.`,
          ttl: originalTtl || 300,
          type: typeText,
          rdclass: 'IN',
          records: [newValue],
        },
      },
    );
    expect(replace.ok()).toBeTruthy();

    await expect(firstRow.locator('.record-data')).toContainText(
      newValue.replace(/^"|"$/g, ''),
      { timeout: 10000 },
    );
    await expect(firstRow).toHaveClass(/live-flash/, { timeout: 2000 });

    // Restore original value
    await request.put('http://localhost:8000/v1/zones/example.com./rrsets', {
      data: {
        name: nameText.endsWith('.') ? nameText : `${nameText}.`,
        ttl: originalTtl || 300,
        type: typeText,
        rdclass: 'IN',
        records: originalData.split(', ').map((v) => v.trim()),
      },
    });
  });
});
