import { test as setup } from '@playwright/test';
import { loginAs, QA_USER } from './fixtures/auth';

const authFile = 'e2e/.auth/user.json';

setup('authenticate qa_vecbox', async ({ page }) => {
  await loginAs(page, QA_USER.username, QA_USER.pin);
  // Save signed-in state so authed projects reuse this session
  await page.context().storageState({ path: authFile });
});
