import { Page, expect } from '@playwright/test';

export const QA_USER = {
  username: 'qa_vecbox',
  pin: '1234',
};

export const QA_USER_2 = {
  username: 'qa_tester_idor',
  pin: '5678',
};

/**
 * Shared authentication helpers for E2E tests.
 * Standardized across Mobile Safari (WebKit) and Chromium.
 */

export async function loginAs(page: Page, username = QA_USER.username, pin = QA_USER.pin) {
    await page.goto('/login');

    const userField = page.locator('[data-testid="login-username"]');
    const pinField = page.locator('[data-testid="login-pin"]');
    const submitBtn = page.locator('[data-testid="login-submit"]');

    // WebKit Fix: Click before fill to ensure focus/state propagation
    await userField.click();
    await userField.fill(username);
    
    await pinField.click();
    await pinField.fill(pin);

    // Critical Wait: Ensure React state updates Button disabled property
    await expect(submitBtn).toBeEnabled({ timeout: 3000 });
    await submitBtn.click();

    // Verify redirect away from login
    await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 15000 });
}

export async function registerUser(page: Page, username: string, pin: string) {
    await page.goto('/register');

    const userField = page.locator('[data-testid="register-username"]');
    const pinField = page.locator('[data-testid="register-pin"]');
    const confirmField = page.locator('[data-testid="register-confirm-pin"]');
    const submitBtn = page.locator('[data-testid="register-submit"]');

    // WebKit compatible sequence
    await userField.click();
    await userField.fill(username);

    await pinField.click();
    await pinField.fill(pin);

    await confirmField.click();
    await confirmField.fill(pin);

    // Ensure state propagates
    await expect(submitBtn).toBeEnabled({ timeout: 3000 });
    await submitBtn.click();

    // Wait for redirect of onboarding transition
    await page.waitForURL(url => !url.pathname.startsWith('/register'), { timeout: 10000 });
}

export async function getAuthToken(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const tokenCookie = cookies.find(c => c.name === 'vectorbox_token');
  return tokenCookie ? tokenCookie.value : '';
}
