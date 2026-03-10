import { test, expect } from '@playwright/test';
import { loginAs, getAuthToken } from './fixtures/auth';

test.describe('Phase 5 — Feed & NLP Search', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page);
  });

  test('Feed loads with >= 3 sections in < 30s', async ({ page }) => {
    const start = Date.now();
    await page.waitForSelector(
      '[id^="feed-section-"]',
      { timeout: 30_000 }
    );
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(30_000);

    const sections = page.locator('[id^="feed-section-"]');
    const count = await sections.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test('Feed section items have vectorbox_score > 0', async ({ page }) => {
    const token = await getAuthToken(page);
    const response = await page.request.get(
      'http://localhost:8000/api/recommendations/feed?country_code=ES',
      { headers: { Cookie: `vectorbox_token=${token}` } }
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
    const token = await getAuthToken(page);
    const response = await page.request.get(
      'http://localhost:8000/api/recommendations/feed?country_code=ES',
      { headers: { Cookie: `vectorbox_token=${token}` } }
    );
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
    const token = await getAuthToken(page);
    const response = await page.request.post(
      'http://localhost:8000/api/search/natural',
      {
        headers: {
          Cookie: `vectorbox_token=${token}`,
          'Content-Type': 'application/json',
        },
        data: {
          query: 'mind bending sci-fi thriller',
          user_id: 0, // will be overridden by token
          country_code: 'ES',
        },
      }
    );
    expect(response.status()).toBe(200);
    const body = await response.json();
    const results = body.results ?? [];
    expect(results.length).toBeGreaterThanOrEqual(5);
  });

  test('Item-to-item search parses intent correctly', async ({ page }) => {
    const token = await getAuthToken(page);
    const response = await page.request.post(
      'http://localhost:8000/api/search/natural',
      {
        headers: {
          Cookie: `vectorbox_token=${token}`,
          'Content-Type': 'application/json',
        },
        data: { query: 'Inception', country_code: 'ES' },
      }
    );
    expect(response.status()).toBe(200);
    const body = await response.json();
    const semanticQuery: string =
      body.intent?.semantic_query ?? body.semantic_query ?? '';
    expect(semanticQuery.toLowerCase()).toContain('inception');
  });

  test('Trident signals run in parallel (total < sum)', async ({ page }) => {
    const token = await getAuthToken(page);
    const start = Date.now();
    const response = await page.request.get(
      'http://localhost:8000/api/recommendations/feed?country_code=ES',
      {
        headers: { Cookie: `vectorbox_token=${token}` },
        timeout: 60_000,
      }
    );
    const elapsed = Date.now() - start;
    expect(response.status()).toBe(200);
    // If running sequentially with 3 signals × ~8s each = 24s minimum
    // Parallel should be well under 15s
    expect(elapsed).toBeLessThan(15_000);
  });
});
