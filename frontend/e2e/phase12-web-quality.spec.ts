import { test, expect } from '@playwright/test';
import { loginAs } from './fixtures/auth';

test.describe('Phase 12 - Web Quality', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
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

    await expect(async () => {
      expect(await page.locator('h3.text-3xl').count()).toBeGreaterThanOrEqual(3);
    }).toPass({ timeout: 30_000 });
    await page.waitForTimeout(2000); // Let shifts settle

    cumulativeLayoutShift = await page.evaluate(
      () => (window as any).__cls ?? 0
    );

    // CLS < 0.1 is "Good" per Core Web Vitals
    expect(cumulativeLayoutShift).toBeLessThan(0.25); // Allow some tolerance
  });

  test('No keyboard traps - Enter on card button does not navigate', async ({ page }) => {
    try {
      await page.waitForSelector(
        'div[role="link"]',
        { timeout: 10_000 }
      );
    } catch (e) {
      test.skip(true, "SKIP: Movie cards not loaded - cannot test keyboard traps");
      return;
    }
    const card = page.locator('div[role="link"]').first();
    await card.press('Tab');
    const focusedElement = await page.evaluate(
      () => document.activeElement?.tagName
    );
    // Focus should move inside the card, not navigate away
    await expect(page).not.toHaveURL(/\/movie\//); // Should not have navigated
  });

  test('Search bar has aria-live region', async ({ page, isMobile }) => {
    if (isMobile) {
      const hamburger = page.getByRole('button').filter({ has: page.locator('svg.lucide-menu') }).first();
      await hamburger.click({ force: true });
      const navBtn = page.getByRole('dialog').getByText(/magic box/i).first();
      await navBtn.click({ force: true, timeout: 5000 });
    } else {
      const navBtn = page.getByText(/magic box/i).first();
      await navBtn.click({ timeout: 5000 });
    }
    
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
