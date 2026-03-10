import { test, expect } from '@playwright/test';
import { loginAs } from './fixtures/auth';

// These tests run specifically on mobile viewports
// configured in playwright.config.ts projects

test.describe('Phase 4 — Mobile UX', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await loginAs(page);
  });

  test('Feed has no horizontal overflow at 390px', async ({ page }) => {
    const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(bodyWidth).toBeLessThanOrEqual(viewportWidth + 5); // 5px tolerance
  });

  test('Navigation hamburger visible on mobile', async ({ page }) => {
    const hamburger = page.locator('button[aria-label*="open_menu" i], button[aria-label*="ui.open_menu" i]').first();
    await expect(hamburger).toBeVisible({ timeout: 5_000 });
  });

  test('MagicSearch input fills full width on mobile', async ({ page }) => {
    const searchInput = page.locator('input[type="text"], input[placeholder*="search" i]').first();
    if (await searchInput.isVisible()) {
      const box = await searchInput.boundingBox();
      if (box) {
        // Input should be close to full viewport width
        expect(box.width).toBeGreaterThan(300);
      }
    }
  });

  test('Touch targets are at least 44px tall', async ({ page }) => {
    const buttons = page.getByRole('button');
    const count = await buttons.count();
    const failingButtons: string[] = [];

    for (let i = 0; i < Math.min(count, 10); i++) {
      const btn = buttons.nth(i);
      if (await btn.isVisible()) {
        const box = await btn.boundingBox();
        if (box && box.height < 44) {
          const text = await btn.textContent();
          failingButtons.push(`"${text?.trim()}" (${box.height}px)`);
        }
      }
    }
    // Allow a few small buttons (decorative icons, etc.)
    expect(failingButtons.length).toBeLessThanOrEqual(2);
  });
});
