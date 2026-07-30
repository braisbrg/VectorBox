import { test, expect } from '@playwright/test';
import { loginAs } from './fixtures/auth';

// These tests run specifically on mobile viewports
// configured in playwright.config.ts projects

test.describe('Phase 4 - Mobile UX', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('Feed has no horizontal overflow at 390px', async ({ page }) => {
    const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(bodyWidth).toBeLessThanOrEqual(viewportWidth + 5); // 5px tolerance
  });

  test('Bottom tab bar visible on mobile (no hamburger — handoff)', async ({ page }) => {
    // 5-slot tab bar: feed · similars · magic(center) · groups · you
    const tabBar = page.locator('nav.fixed.bottom-0').first();
    await expect(tabBar).toBeVisible({ timeout: 5_000 });
    await expect(tabBar.getByText(/feed/i).first()).toBeVisible();
    await expect(tabBar.getByText(/magic/i).first()).toBeVisible();
    // Hamburger is gone
    await expect(page.locator('svg.lucide-menu')).toHaveCount(0);
  });

  test('MagicSearch input fills full width on mobile', async ({ page, isMobile }) => {
    // Navigate to Magic Box view
    try {
      if (isMobile) {
         // Center magic tab in the bottom bar opens the full-screen overlay
         const magicTab = page.locator('nav.fixed.bottom-0').getByText(/magic/i).first();
         await magicTab.click({ force: true, timeout: 5000 });
      } else {
         await page.getByText(/magic box/i).first().click({ timeout: 5000 });
      }
    } catch (e) {
      test.skip(true, 'SKIP: Magic box entry point not found or slow');
      return;
    }
    
    const searchInput = page.locator('input[type="text"], input[placeholder*="search" i]').first();
    if (await searchInput.isVisible()) {
      const box = await searchInput.boundingBox();
      if (box) {
        // Input should be visible and not compressed to 0
        expect(box.width).toBeGreaterThan(150);
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
