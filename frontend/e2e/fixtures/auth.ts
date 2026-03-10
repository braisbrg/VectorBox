import { Page } from '@playwright/test';

export const QA_USER = {
  username: 'qa_vecbox',
  pin: '1234',
};

export const QA_USER_2 = {
  username: 'qa_tester_idor',
  pin: '5678',
};

export async function loginAs(
  page: Page,
  username = QA_USER.username,
  pin = QA_USER.pin
): Promise<void> {
  await page.goto('/login');
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/pin/i).fill(pin);
  await page.getByRole('button', { name: /login/i }).click();
  // Wait for redirect away from login
  await page.waitForURL((url) => !url.pathname.includes('/login'), {
    timeout: 10_000,
  });
}

export async function registerUser(
  page: Page,
  username: string,
  pin: string
): Promise<void> {
  await page.goto('/register');
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/^pin$/i).fill(pin);
  await page.getByLabel(/confirm/i).fill(pin);
  await page.getByRole('button', { name: /create/i }).click();
}

export async function getAuthToken(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const token = cookies.find((c) => c.name === 'vectorbox_token');
  return token?.value ?? '';
}
