import { expect, test } from '@playwright/test'

test('payment lifecycle renders its state diagram', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))

  await page.goto('/docs/getting-started/payment-lifecycle')
  await expect(page.getByRole('heading', { name: 'Payment lifecycle', exact: true })).toBeVisible()

  const diagram = page.locator('[data-v-mermaid] svg')
  await expect(diagram).toBeVisible()
  for (const state of ['pending', 'settled', 'uncertain', 'failed']) {
    await expect(diagram).toContainText(state)
  }
  await expect(page.locator('[data-v-mermaid-error]')).toHaveCount(0)
  expect(errors).toEqual([])
})
