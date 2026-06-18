import { test, expect } from '@playwright/test';

test.describe('DeepFocus Core Flow', () => {
  test('app loads and shows terminal header', async ({ page }) => {
    await page.goto('http://localhost:3000');
    await expect(page.locator('.terminal-header')).toBeVisible();
    await expect(page.locator('.brand-title')).toContainText('DeepFocus');
  });

  test('sidebar navigation works', async ({ page }) => {
    await page.goto('http://localhost:3000');
    await expect(page.locator('.workspace-sidebar')).toBeVisible();
    await page.getByRole('menuitem').filter({ hasText: 'Equity' }).click();
    await page.waitForTimeout(500);
    await expect(page.getByRole('tablist', { name: '个股工作区' })).toBeVisible();
    await expect(page.locator('.fingpt-hub-shell')).toBeVisible();
  });

  test('theme toggle switches between light and dark', async ({ page }) => {
    await page.goto('http://localhost:3000');
    const themeButton = page.locator('.header-actions button').first();
    const html = page.locator('html');
    
    const initialTheme = await html.getAttribute('data-theme');
    await themeButton.click();
    await page.waitForTimeout(300);
    const newTheme = await html.getAttribute('data-theme');
    expect(newTheme).not.toBe(initialTheme);
  });

  test('search stock exists', async ({ page }) => {
    await page.goto('http://localhost:3000');
    const searchInput = page.locator('.header-search input');
    await expect(searchInput).toBeVisible();
  });

  test('stock list renders', async ({ page }) => {
    await page.goto('http://localhost:3000');
    await page.getByRole('menuitem').filter({ hasText: 'Observe' }).click();
    await page.waitForTimeout(800);
    await expect(page.getByRole('tablist', { name: '观察工作区' })).toBeVisible();
    await expect(page.getByRole('button', { name: /观察池/ })).toBeVisible();
  });

  test('equity research page loads with FinGPT interface', async ({ page }) => {
    await page.goto('http://localhost:3000');
    await page.getByRole('menuitem').filter({ hasText: 'Equity' }).click();
    await page.waitForTimeout(600);
    await expect(page.getByRole('button', { name: /体检/ })).toBeVisible();
    await expect(page.locator('.fingpt-hub-shell')).toBeVisible();
  });

  test('responsive mobile menu works', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('http://localhost:3000');
    await expect(page.locator('.terminal-header')).toBeVisible();
  });
});
