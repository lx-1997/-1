import { test, expect } from '@playwright/test';

test.describe('Daocaijing Terminal Core Flow', () => {
  test('app loads the current terminal shell', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'Daocaijing 金融终端' })).toBeVisible();
    await expect(page.getByRole('complementary', { name: '主导航' })).toBeVisible();
    await expect(page.getByRole('button', { name: '搜索股票、行业或资讯' })).toBeVisible();
    await expect(page.getByRole('button', { name: '实时资讯' })).toBeVisible();
  });

  test('sidebar navigation switches between the main content types', async ({ page }) => {
    await page.goto('/');

    await page.getByRole('button', { name: '市场快讯' }).click();
    await expect(page.getByRole('button', { name: '实时资讯' })).toBeVisible();

    await page.getByRole('button', { name: '深度文章' }).click();
    await expect(page.getByPlaceholder(/搜索深度文章/)).toBeVisible();

    await page.getByRole('button', { name: '投行研报' }).click();
    await expect(page.getByPlaceholder(/在线搜全球研报/)).toBeVisible();

    await page.getByRole('button', { name: '机构纪要' }).click();
    await expect(page.getByText(/机构纪要/).first()).toBeVisible();
  });

  test('theme toggle switches between light and dark', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '更多' }).click();
    const themeButton = page.getByRole('button', { name: /切换到深色主题|切换到浅色主题/ }).first();
    const html = page.locator('html');
    const initialTheme = await html.getAttribute('data-theme');
    await themeButton.click();
    await expect(html).not.toHaveAttribute('data-theme', initialTheme || '');
  });

  test('command palette opens from the global search button', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '搜索股票、行业或资讯' }).click();
    await expect(page.locator('.bbt-palette-input')).toBeVisible();
    await page.locator('.bbt-palette-input').fill('苹果');
    await expect(page.locator('.bbt-palette-input')).toHaveValue('苹果');
    await page.keyboard.press('Escape');
    await expect(page.locator('.bbt-palette-input')).toHaveCount(0);
  });

  test('popup preferences expose multi-topic and keyword filtering', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '更多' }).click();
    await page.getByRole('button', { name: /弹窗提醒/ }).click();
    await expect(page.getByText('提醒级别')).toBeVisible();
    await expect(page.getByText('内容类型（可多选）')).toBeVisible();
    await expect(page.getByText('关键词（可多选，命中任意一个就弹窗）')).toBeVisible();
  });

  test('mobile quick navigation and more menu remain usable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await expect(page.getByRole('navigation', { name: '移动端快捷导航' })).toBeVisible();
    await page.getByRole('button', { name: '更多', exact: true }).click();
    await expect(page.getByRole('button', { name: /弹窗提醒/ })).toBeVisible();
    const nativeRow = page.getByRole('button', { name: /后台常驻提醒/ });
    await expect(nativeRow).toBeVisible();
    await expect(nativeRow).toBeDisabled();
  });
});
