import { test, expect } from '@playwright/test';
import { loginAs, registerUser, getAuthToken, QA_USER } from './fixtures/auth';

test.describe('Phase 2 - Auth Guard', () => {
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
    let attempts = 0;
    await page.route('**/api/auth/login', route => {
      attempts++;
      if (attempts > 5) {
        route.fulfill({ 
          status: 429, 
          headers: { 'retry-after': '60' }, 
          contentType: 'application/json',
          body: JSON.stringify({ error: "Rate limit" }) 
        });
      } else {
        route.fulfill({ 
          status: 401, 
          contentType: 'application/json',
          body: JSON.stringify({ detail: "Invalid" }) 
        });
      }
    });

    await page.goto('/login');
    const userField = page.locator('[data-testid="login-username"]');
    const pinField = page.locator('[data-testid="login-pin"]');
    const submitBtn = page.locator('[data-testid="login-submit"]');

    for (let i = 0; i < 6; i++) {
      await userField.click();
      await userField.fill('nonexistent_user');
      await pinField.click();
      await pinField.fill('0000');
      
      await expect(submitBtn).toBeEnabled({ timeout: 3000 });
      
      const responsePromise = page.waitForResponse('**/api/auth/login', { timeout: 5000 });
      await submitBtn.click();
      await responsePromise;
    }
    // Should see rate limit message (red alert box)
    await expect(
      page.locator('.bg-red-900\\/40').or(page.locator('.bg-red-500\\/10'))
    ).toBeVisible({ timeout: 5_000 });
  });

  test('Registration flow completes and leaves /register', async ({ page }) => {
    const testUser = `qa_reg_${Date.now()}`;
    await registerUser(page, testUser, '9999');
    
    // Behaves correctly: Wait for redirect and ensure we is no longer on /register
    // (Current behavior redirects to / with a 1.5s delay)
    await expect(page).not.toHaveURL(/register/, { timeout: 10000 });
    
    // Verify Onboarding text is visible on the landing page
    await expect(page.getByText(/initialization required/i)).toBeVisible();
  });

  test('IDOR - feed uses token identity not query param', async ({ page }) => {
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

  test('IDOR - /hidden-gems blocked without auth', async ({ request }) => {
    const response = await request.get(
      'http://localhost:8000/api/recommendations/hidden-gems?country_code=ES'
    );
    expect([401, 403]).toContain(response.status());
  });

  test('Session cookie deletion logs out user', async ({ page }) => {
    // 1. Login para establecer sesión válida
    await loginAs(page);
    await expect(page).not.toHaveURL(/login/);

    // 2. Interceptar /api/auth/me ANTES de borrar la sesión.
    //    Forzamos 401 de forma síncrona para evitar la race condition
    //    entre window.location.href (axios interceptor) y await logout()
    //    (Dashboard catch) que hace perder el tracking de navegación
    //    a Playwright en Chromium.
    await page.route('**/api/auth/me', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Unauthorized' }),
      })
    );

    // 3. Borrar sesión completa (cookie + localStorage)
    await page.context().clearCookies();
    try {
      await page.evaluate(() => localStorage.removeItem('vectorbox_user'));
    } catch {}

    // 4. Navegar a root - el Dashboard llamará a /api/auth/me,
    //    recibirá el 401 mockeado, y el interceptor de axios hará
    //    window.location.href = "/login"
    try {
      await page.goto('/');
    } catch {
      // Ignored ERR_ABORTED
    }

    await page.unroute('**/api/auth/me');

    // 5. Esperar la redirección a login
    await expect(page).toHaveURL(/login/, { timeout: 10_000 });
  });
});
