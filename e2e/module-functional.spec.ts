import { expect, Page, test } from '@playwright/test';

const SECTIONS = ['市场快讯', '每日复盘', '深度文章', '机构纪要', '投行研报'];

async function openSection(page: Page, label: string, mobile: boolean) {
  if (mobile) {
    const openMenu = page.getByRole('button', { name: '打开导航' }).first();
    if (await openMenu.isVisible().catch(() => false)) await openMenu.click();
  }
  const item = page.getByRole('button', { name: new RegExp(label) }).first();
  await expect(item, `${label} should be visible`).toBeVisible();
  await item.click();
  await page.waitForTimeout(250);
}

async function assertFunctionalSurface(page: Page, label: string) {
  const bodyText = await page.locator('body').innerText();
  expect(bodyText.replace(/\s+/g, ' ').trim().length, `${label} should render meaningful text`).toBeGreaterThan(40);
  await expect(page.locator('iframe#webpack-dev-server-client-overlay')).toHaveCount(0);
}

async function auditSections(page: Page, mobile: boolean) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(600);
  for (const section of SECTIONS) {
    await openSection(page, section, mobile);
    await assertFunctionalSurface(page, section);
  }
}

test('desktop: every current terminal content section renders and accepts navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await auditSections(page, false);
});

test('mobile: quick navigation and current content sections render', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('navigation', { name: '移动端快捷导航' })).toBeVisible();
  await page.getByRole('button', { name: 'AI', exact: true }).click();
  await expect(page.getByRole('region', { name: 'AI 对话工作区' })).toBeVisible();
  const input = page.getByRole('textbox', { name: '向 AI 投研助手提问' });
  await input.fill('请用一句话说明当前工作区');
  await expect(input).toHaveValue('请用一句话说明当前工作区');
  await auditSections(page, true);
});
