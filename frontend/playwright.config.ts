import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['html', { outputFolder: 'e2e/reports' }],
    ['list']
  ],
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    // 1. Setup project
    { 
      name: 'setup', 
      dependencies: ['Desktop Chrome', 'Mobile Safari', 'Mobile Chrome'],
      testMatch: /auth\.setup\.ts/ 
    },

    // 2. Base projects (for Phase 1 & 2 - Auth and Infra)
    {
      name: 'Desktop Chrome',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /phase(1|2|7)-.*\.spec\.ts/,
    },
    {
      name: 'Mobile Safari',
      use: { ...devices['iPhone SE'] },
      testMatch: /phase(1|2|7)-.*\.spec\.ts/,
    },
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
      testMatch: /phase(1|2|7)-.*\.spec\.ts/,
    },

    // 3. Authenticated projects (for Phases 3, 4, 5, 12)
    {
      name: 'Desktop Chrome (authed)',
      use: { 
        ...devices['Desktop Chrome'],
        storageState: 'e2e/.auth/user.json',
      },
      dependencies: ['setup'],
      testMatch: /phase(3|4|5|12)-.*\.spec\.ts/,
    },
    {
      name: 'Mobile Safari (authed)',
      use: { 
        ...devices['iPhone SE'],
        storageState: 'e2e/.auth/user.json',
      },
      dependencies: ['setup'],
      testMatch: /phase(3|4|5|12)-.*\.spec\.ts/,
    },
    {
      name: 'Mobile Chrome (authed)',
      use: { 
        ...devices['Pixel 5'],
        storageState: 'e2e/.auth/user.json',
      },
      dependencies: ['setup'],
      testMatch: /phase(3|4|5|12)-.*\.spec\.ts/,
    },
  ],
});
