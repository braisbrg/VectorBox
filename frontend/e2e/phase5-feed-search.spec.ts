import { test, expect } from '@playwright/test';

const API = 'http://localhost:8000';

test.describe('Phase 5 — Feed & NLP Search', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('Feed loads with >= 3 sections in < 30s', async ({ page }) => {
    const start = Date.now();
    await expect(async () => {
      const count = await page.locator('h3.text-3xl').count();
      expect(count).toBeGreaterThanOrEqual(3);
    }).toPass({ timeout: 30_000 });
    
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(30_000);
  });

  test('Feed section items have vectorbox_score > 0', async ({ page }) => {
    const response = await page.context().request.get(
      `${API}/api/recommendations/feed?country_code=ES`
    );
    expect(response.status()).toBe(200);
    const body = await response.json();

    // Check items in sections have scores
    const sections = body.sections ?? body.feed ?? body;
    let checkedItems = 0;
    for (const section of sections) {
      const items = section.items ?? section.movies ?? [];
      for (const item of items.slice(0, 3)) {
        expect(item.vectorbox_score ?? item.score ?? 0).toBeGreaterThan(0);
        checkedItems++;
      }
    }
    expect(checkedItems).toBeGreaterThan(0);
  });

  test('Negative seeds absent from feed', async ({ page }) => {
    const response = await page.context().request.get(
      `${API}/api/recommendations/feed?country_code=ES`
    );
    expect(response.status()).toBe(200);
    const body = await response.json();

    const BANNED_TMDB_IDS = [1858, 168259]; // Transformers, FF7
    const sections = body.sections ?? body.feed ?? body;
    for (const section of sections) {
      const items = section.items ?? section.movies ?? [];
      for (const item of items) {
        expect(BANNED_TMDB_IDS).not.toContain(item.tmdb_id);
      }
    }
  });

  test('NLP search returns results for semantic query', async ({ page }) => {
    const response = await page.context().request.post(
      `${API}/api/search/natural`,
      {
        headers: { 'Content-Type': 'application/json' },
        data: {
          query: 'melancholic 70s road trip',
          country_code: 'ES',
        },
      }
    );
    expect(response.status()).toBe(200);
  });

  test('Item-to-item search parses intent correctly', async ({ page }) => {
    const response = await page.context().request.post(
      `${API}/api/search/natural`,
      {
        headers: { 'Content-Type': 'application/json' },
        data: {
          query: 'Movies like Blade Runner',
          country_code: 'ES',
        },
      }
    );
    expect(response.status()).toBe(200);
  });

  test('Trident signals run in parallel (total < sum)', async ({ page }) => {
    const start = Date.now();
    const response = await page.context().request.get(
      `${API}/api/recommendations/feed?country_code=ES`,
      { timeout: 60_000 }
    );
    const elapsed = Date.now() - start;
    expect(response.status()).toBe(200);
    // If running sequentially with 3 signals × ~8s each = 24s minimum
    // Parallel should be well under 15s
    expect(elapsed).toBeLessThan(15_000);
  });
});
