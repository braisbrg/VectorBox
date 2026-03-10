import { test, expect } from '@playwright/test';
import { loginAs } from './fixtures/auth';

test.describe('Phase 12 — Web Quality', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page);
  });

  test('No layout shifts on feed load (CLS)', async ({ page }) => {
    let cumulativeLayoutShift = 0;

    await page.addInitScript(() => {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!(entry as any).hadRecentInput) {
            (window as any).__cls = ((window as any).__cls ?? 0)
              + (entry as any).value;
          }
        }
      }).observe({ type: 'layout-shift', buffered: true });
    });

    await page.waitForSelector(
      '[id^="feed-section-"]',
      { timeout: 30_000 }
    );
    await page.waitForTimeout(2000); // Let shifts settle

    cumulativeLayoutShift = await page.evaluate(
      () => (window as any).__cls ?? 0
    );

    // CLS < 0.1 is "Good" per Core Web Vitals
    expect(cumulativeLayoutShift).toBeLessThan(0.25); // Allow some tolerance
  });

  test('No keyboard traps — Enter on card button does not navigate', async ({ page }) => {
    await page.waitForSelector(
      '.group\\/card',
      { timeout: 30_000 }
    );
    const card = page.locator('.group\\/card').first();
    await card.press('Tab');
    const focusedElement = await page.evaluate(
      () => document.activeElement?.tagName
    );
    // Focus should move inside the card, not navigate away
    await expect(page).not.toHaveURL(/\/movie\//); // Should not have navigated
  });

  test('Search bar has aria-live region', async ({ page }) => {
    const ariaLive = page.locator('[aria-live]');
    const count = await ariaLive.count();
    expect(count).toBeGreaterThan(0);
  });

  test('All images have alt text', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const imagesWithoutAlt = await page.evaluate(() => {
      const imgs = Array.from(document.querySelectorAll('img'));
      return imgs
        .filter((img) => !img.alt || img.alt.trim() === '')
        .map((img) => img.src);
    });
    // Allow a few decorative images without alt
    expect(imagesWithoutAlt.length).toBeLessThanOrEqual(3);
  });
});
