import { expect, Page, test } from '@playwright/test';
import { E2E_WORKSPACE_SECTIONS } from './workspaces';

type LayoutReport = {
  badVisible: Array<{
    className: string;
    right: number;
    tagName: string;
    text: string;
    width: number;
  }>;
  docScroll: number;
  overlay: boolean;
  pageOverflow: number;
  uncontrolledTables: number;
  viewport: number;
};

test.describe.configure({ timeout: 120_000 });

async function openMobileMenuIfNeeded(page: Page, isMobile: boolean) {
  if (!isMobile) return;

  const menuButton = page.getByRole('button', { name: 'menu' });
  if (await menuButton.count() === 1) {
    await menuButton.click();
    await page.waitForTimeout(200);
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
  await page.waitForTimeout(350);
}

async function collectLayoutReport(page: Page): Promise<LayoutReport> {
  return page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;

    const isVisible = (element: Element) => {
      if (!(element instanceof HTMLElement)) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };

    const hasScrollBoundary = (element: Element) => {
      let current = element.parentElement;
      while (current && current !== document.body && current !== document.documentElement) {
        const style = window.getComputedStyle(current);
        const rect = current.getBoundingClientRect();
        if (['auto', 'hidden', 'scroll'].includes(style.overflowX) && rect.left >= -4 && rect.right <= viewport + 4) {
          return true;
        }
        current = current.parentElement;
      }
      return false;
    };

    const badVisible = Array.from(document.querySelectorAll('body *'))
      .filter(isVisible)
      .map(element => {
        const rect = element.getBoundingClientRect();
        return {
          className: typeof (element as HTMLElement).className === 'string' ? (element as HTMLElement).className.slice(0, 100) : '',
          element,
          right: Math.round(rect.right),
          tagName: element.tagName,
          text: (element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80),
          width: Math.round(rect.width),
        };
      })
      .filter(item => item.right > viewport + 4 && !hasScrollBoundary(item.element))
      .slice(0, 8)
      .map(({ element: _element, ...item }) => item);

    const uncontrolledTables = Array.from(document.querySelectorAll('.ant-table-wrapper')).filter(wrapper => {
      const content = wrapper.querySelector('.ant-table-content');
      if (!(content instanceof HTMLElement)) return false;
      const style = window.getComputedStyle(content);
      return content.scrollWidth > content.clientWidth + 2 && !['auto', 'hidden', 'scroll'].includes(style.overflowX);
    }).length;

    return {
      badVisible,
      docScroll: document.documentElement.scrollWidth,
      overlay: Boolean(document.querySelector('iframe#webpack-dev-server-client-overlay, .webpack-dev-server-client-overlay, [data-nextjs-dialog-overlay]')),
      pageOverflow: document.documentElement.scrollWidth - viewport,
      uncontrolledTables,
      viewport,
    };
  });
}

async function auditAllModules(page: Page, isMobile: boolean) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);

  const reports: Array<{ label: string; report: LayoutReport }> = [];
  for (const section of E2E_WORKSPACE_SECTIONS) {
    await openSidebarSection(page, section.menu, isMobile);
    reports.push({ label: section.menu, report: await collectLayoutReport(page) });

    if (!section.tablist) {
      continue;
    }

    const tablist = page.getByRole('tablist', { name: section.tablist });
    await expect(tablist).toBeVisible();
    for (const tab of section.tabs) {
      await tablist.getByRole('button', { name: tab, exact: true }).click({ timeout: 5000 });
      await page.waitForTimeout(350);
      reports.push({ label: `${section.menu} / ${tab}`, report: await collectLayoutReport(page) });
    }
  }

  return reports;
}

test('all sidebar modules stay within the desktop viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 920 });
  const reports = await auditAllModules(page, false);
  const failures = reports.filter(({ report }) => report.overlay || report.pageOverflow > 0 || report.badVisible.length > 0 || report.uncontrolledTables > 0);

  expect(failures).toEqual([]);
});

test('all sidebar modules stay within the mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const reports = await auditAllModules(page, true);
  const failures = reports.filter(({ report }) => report.overlay || report.pageOverflow > 0 || report.badVisible.length > 0 || report.uncontrolledTables > 0);

  expect(failures).toEqual([]);
});
