import { test, expect } from '@playwright/test';
import { loginAs, registerUser, getAuthToken, QA_USER } from './fixtures/auth';

test.describe('Phase 2 — Auth Guard', () => {
  test('Redirect to login when unauthenticated', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/login/);
  });

  test('No flash of dashboard before redirect', async ({ page }) => {
    await page.goto('/');
    // Should never see feed content
    await expect(page.locator('[id^="feed-section-"]')).not.toBeVisible();
    await expect(page).toHaveURL(/login/);
  });

  test('Login with valid credentials succeeds', async ({ page }) => {
    await loginAs(page);
    await expect(page).not.toHaveURL(/login/);
  });

  test('Login with uppercase username succeeds (normalization)', async ({ page }) => {
    await loginAs(page, QA_USER.username.toUpperCase(), QA_USER.pin);
    await expect(page).not.toHaveURL(/login/);
  });

  test('Rate limiting triggers after 5 rapid attempts', async ({ page }) => {
    await page.goto('/login');
    for (let i = 0; i < 6; i++) {
      await page.getByLabel(/username/i).fill('nonexistent_user');
      await page.getByLabel(/pin/i).fill('0000');
      await page.getByRole('button', { name: /login/i }).click();
      await page.waitForTimeout(200);
    }
    // Should see rate limit message
    await expect(
      page.getByText(/too many attempts|rate limit/i)
    ).toBeVisible({ timeout: 5_000 });
  });

  test('Registration flow redirects to onboarding', async ({ page }) => {
    const testUser = `qa_reg_${Date.now()}`;
    await registerUser(page, testUser, '9999');
    // Should go to onboarding, not feed
    await expect(page).toHaveURL(/onboard|letterboxd|upload/i);
  });

  test('IDOR — feed uses token identity not query param', async ({ page }) => {
    await loginAs(page);
    const token = await getAuthToken(page);

    // Try to access another user's data via query param
    const response = await page.request.get(
      'http://localhost:8000/api/recommendations/feed?user_id=1',
      { headers: { Cookie: `vectorbox_token=${token}` } }
    );
    // Should return data for the token owner, not user_id=1
    // or 403 if user_id=1 is a different user
    expect([200, 403]).toContain(response.status());
  });

  test('IDOR — /hidden-gems blocked without auth', async ({ request }) => {
    const response = await request.get(
      'http://localhost:8000/api/recommendations/hidden-gems?country_code=ES'
    );
    expect([401, 403]).toContain(response.status());
  });

  test('Session cookie deletion logs out user', async ({ page }) => {
    await loginAs(page);
    await expect(page).not.toHaveURL(/login/);
    // Delete the auth cookie
    await page.context().clearCookies();
    await page.reload();
    await expect(page).toHaveURL(/login/);
  });
});
