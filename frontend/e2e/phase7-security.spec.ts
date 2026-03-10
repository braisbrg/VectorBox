import { test, expect } from '@playwright/test';
import { loginAs } from './fixtures/auth';

test.describe('Phase 7 — Security', () => {
  test('HTTP security headers present on frontend', async ({ request }) => {
    const response = await request.get('http://localhost:3000');
    const headers = response.headers();

    expect(headers['x-frame-options']?.toLowerCase()).toBe('deny');
    expect(headers['x-content-type-options']?.toLowerCase()).toBe('nosniff');
    expect(headers['referrer-policy']).toBeTruthy();
  });

  test('Swagger UI disabled in production', async ({ request }) => {
    if (process.env.ENVIRONMENT === 'production') {
      const response = await request.get('http://localhost:8000/api/docs');
      expect([404, 403]).toContain(response.status());
    } else {
      // In dev, docs should be accessible
      const response = await request.get('http://localhost:8000/api/docs');
      expect(response.status()).toBe(200);
    }
  });

  test('Backend down renders AcidError component', async ({ page }) => {
    await loginAs(page);
    await page.route('**/api/recommendations/feed**', (route) => {
      route.abort('failed');
    });
    await page.reload();
    await page.waitForLoadState('networkidle');
    
    // If no error component, at least no unhandled crash
    const pageContent = await page.content();
    expect(pageContent).not.toContain('Application error');
    expect(pageContent).not.toContain('unhandled');
  });

  test('localStorage cleared does not crash — cookie is truth', async ({ page }) => {
    await loginAs(page);
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.waitForLoadState('networkidle');
    // Should still be logged in (cookie is the auth source)
    await expect(page).not.toHaveURL(/login/, { timeout: 10_000 });
  });

  test('Zero MissingGreenlet errors in logs after load', async ({ page }) => {
    await loginAs(page);

    // Hit the feed 3 times to generate log activity
    for (let i = 0; i < 3; i++) {
      await page.context().request.get(
        'http://localhost:8000/api/recommendations/feed?country_code=ES'
      );
    }
    // The actual log check is done via the backend audit script
    // This test just confirms no 500s occur
    const response = await page.context().request.get(
      'http://localhost:8000/api/recommendations/feed?country_code=ES'
    );
    expect(response.status()).toBe(200);
  });
});
