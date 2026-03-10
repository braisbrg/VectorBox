import { test, expect } from '@playwright/test';

test.describe('Phase 1 — Infrastructure', () => {
  test('API health check returns ok', async ({ request }) => {
    const response = await request.get('http://localhost:8000/health');
    expect(response.status()).toBeLessThan(503);
    const body = await response.json();
    expect(body.status).toBe('ok');
  });

  test('Frontend loads without console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await page.goto('/');
    // Allow redirect to login
    await page.waitForURL(/login/);
    expect(errors).toHaveLength(0);
  });

  test('Unauthenticated root redirects to /login', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/login/);
  });
});
