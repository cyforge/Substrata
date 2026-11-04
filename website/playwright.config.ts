import { defineConfig } from '@playwright/test'

const baseURL = process.env.DOCS_BASE_URL || 'http://127.0.0.1:4181'

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 15_000 },
  workers: 1,
  use: {
    baseURL,
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHROME_CHANNEL,
    trace: 'retain-on-failure',
  },
  webServer: process.env.DOCS_BASE_URL ? undefined : {
    command: 'npm run preview -- --host 127.0.0.1 --port 4181',
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
