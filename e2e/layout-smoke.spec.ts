import { expect, Page, test } from '@playwright/test';

const CONTENT_SECTIONS = ['市场快讯', '每日复盘', '深度文章', '机构纪要', '投行研报'];

type LayoutReport = {
  badVisible: Array<{ className: string; right: number; tagName: string; text: string; width: number }>;
  pageOverflow: number;
  uncontrolledTables: number;
  viewport: number;
};

async function openCurrentSection(page: Page, label: string, isMobile: boolean) {
  if (isMobile) {
    const openMenu = page.getByRole('button', { name: '打开导航' }).first();
    if (await openMenu.isVisible().catch(() => false)) await openMenu.click();
  }
  const section = page.getByRole('button', { name: new RegExp(label) }).first();
  await expect(section, `${label} navigation button`).toBeVisible();
  await section.click();
  await page.waitForTimeout(250);
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
        if (['auto', 'hidden', 'scroll'].includes(style.overflowX) && rect.left >= -4 && rect.right <= viewport + 4) return true;
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
      pageOverflow: document.documentElement.scrollWidth - viewport,
      uncontrolledTables,
      viewport,
    };
  });
}

async function auditSections(page: Page, isMobile: boolean) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);
  const reports: Array<{ label: string; report: LayoutReport }> = [];
  for (const section of CONTENT_SECTIONS) {
    await openCurrentSection(page, section, isMobile);
    reports.push({ label: section, report: await collectLayoutReport(page) });
  }
  return reports;
}

function assertNoLayoutFailures(reports: Array<{ label: string; report: LayoutReport }>) {
  const failures = reports.filter(({ report }) => report.pageOverflow > 2 || report.badVisible.length > 0 || report.uncontrolledTables > 0);
  expect(failures, JSON.stringify(failures, null, 2)).toEqual([]);
}

test('current terminal sections stay within the desktop viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 920 });
  assertNoLayoutFailures(await auditSections(page, false));
});

test('current terminal sections stay within the mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  assertNoLayoutFailures(await auditSections(page, true));
});
