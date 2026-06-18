import { expect, Page, test } from '@playwright/test';
import { E2E_WORKSPACE_SECTIONS } from './workspaces';

type SurfaceReport = {
  activeTabText: string;
  hasOverlay: boolean;
  headingCount: number;
  interactiveCount: number;
  textLength: number;
};

test.describe.configure({ timeout: 180_000 });

async function expectNoRuntimeOverlay(page: Page) {
  await expect(page.locator('iframe#webpack-dev-server-client-overlay')).toHaveCount(0);
}

async function openMobileMenuIfNeeded(page: Page, isMobile: boolean) {
  if (!isMobile) return;

  const menuButton = page.getByRole('button', { name: 'menu' });
  if (await menuButton.count() === 1) {
    await menuButton.click();
    await page.waitForTimeout(150);
  }
}

async function openSidebarSection(page: Page, label: string, isMobile: boolean) {
  await openMobileMenuIfNeeded(page, isMobile);

  const menuContainer = page.locator('.workspace-sidebar, .ant-drawer').filter({ has: page.getByRole('menu') });
  const scopedItem = menuContainer.getByRole('menuitem').filter({ hasText: label });
  const scopedCount = await scopedItem.count();
  const menuItem = scopedCount > 0 ? scopedItem.first() : page.getByRole('menuitem').filter({ hasText: label }).first();

  await expect(menuItem, `${label} menu item`).toBeVisible();
  await menuItem.click();
  await page.waitForTimeout(300);
  await expectNoRuntimeOverlay(page);
}

async function collectSurfaceReport(page: Page): Promise<SurfaceReport> {
  return page.evaluate(() => {
    const root = document.querySelector('.workspace-section-body') || document.querySelector('.workspace-content') || document.body;
    const text = (root.textContent || '').replace(/\s+/g, ' ').trim();
    const activeTab = document.querySelector('.workspace-section-tab.active');

    return {
      activeTabText: (activeTab?.textContent || '').replace(/\s+/g, ' ').trim(),
      hasOverlay: Boolean(document.querySelector('iframe#webpack-dev-server-client-overlay, .webpack-dev-server-client-overlay')),
      headingCount: root.querySelectorAll('h1,h2,h3,h4,h5,[role="heading"],.ant-card-head-title').length,
      interactiveCount: root.querySelectorAll('button,input,textarea,select,[role="button"],[role="tab"],.ant-table,iframe').length,
      textLength: text.length,
    };
  });
}

async function assertFunctionalSurface(page: Page, label: string) {
  await expectNoRuntimeOverlay(page);
  const report = await collectSurfaceReport(page);

  expect(report.hasOverlay, `${label} should not show runtime overlay`).toBe(false);
  expect(report.textLength, `${label} should render meaningful text`).toBeGreaterThan(8);
  expect(
    report.headingCount + report.interactiveCount,
    `${label} should expose a heading or interactive surface`
  ).toBeGreaterThan(0);
}

async function exerciseRepresentativeControls(page: Page, label: string) {
  const body = page.locator('.workspace-section-body, .workspace-content').last();

  if (/Research|工作台|体检/.test(label)) {
    const textArea = body.locator('textarea').first();
    if (await textArea.isVisible().catch(() => false)) {
      await textArea.fill(`自动化模拟：${label}`);
      await expect(textArea).toHaveValue(/自动化模拟/);
      await page.keyboard.press('Escape').catch(() => undefined);
      await page.evaluate(() => {
        if (document.activeElement instanceof HTMLElement) {
          document.activeElement.blur();
        }
      });
    }
  }

  if (/回测/.test(label)) {
    const newBacktest = page.getByRole('button', { name: /新建回测/ });
    if (await newBacktest.isVisible().catch(() => false)) {
      await newBacktest.click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      const cancelButton = dialog.getByRole('button', { name: /取消|Cancel/ });
      if (await cancelButton.isVisible().catch(() => false)) {
        await cancelButton.click();
      } else {
        await page.locator('.ant-modal-close').click();
      }
      await expect(page.getByRole('dialog')).toHaveCount(0);
    }
  }

  await page.keyboard.press('Escape').catch(() => undefined);
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  });
  await expectNoRuntimeOverlay(page);
}

async function auditWorkspaceModules(page: Page, isMobile: boolean) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);

  for (const section of E2E_WORKSPACE_SECTIONS) {
    await openSidebarSection(page, section.menu, isMobile);
    await assertFunctionalSurface(page, section.menu);
    await exerciseRepresentativeControls(page, section.menu);

    if (!section.tablist) {
      continue;
    }

    const tablist = page.getByRole('tablist', { name: section.tablist });
    await expect(tablist).toBeVisible();

    for (const tab of section.tabs) {
      await tablist.getByRole('button', { name: tab, exact: true }).click({ timeout: 5000 });
      await page.waitForTimeout(300);
      const label = `${section.menu} / ${tab}`;
      await assertFunctionalSurface(page, label);
      await exerciseRepresentativeControls(page, label);
    }
  }
}

test('desktop: every workspace module renders and accepts safe interactions', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await auditWorkspaceModules(page, false);
});

test('mobile: every workspace module renders and accepts safe interactions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await auditWorkspaceModules(page, true);
});
