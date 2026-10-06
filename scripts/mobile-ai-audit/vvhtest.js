const { chromium } = require('playwright');
(async () => {
  const base = process.env.AUDIT_BASE || 'http://127.0.0.1:4175';
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 392, height: 650 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 45000 });
  // 模拟过期 vvh: 键盘态残留下的小值
  await page.evaluate(() => document.documentElement.style.setProperty('--df-vvh', '300px'));
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(800);
  const m = await page.evaluate(() => {
    const ws = document.querySelector('.bbt-ai-workspace').getBoundingClientRect();
    const nav = document.querySelector('nav.bbt-mobile-nav').getBoundingClientRect();
    const shell = document.querySelector('.bbt-with-sidebar').getBoundingClientRect();
    const h1 = document.querySelector('.bbt-ai-workspace-head h1').getBoundingClientRect();
    return { shellH: Math.round(shell.height), wsBottom: Math.round(ws.bottom), navTop: Math.round(nav.top), gap: Math.round(nav.top - ws.bottom), h1Top: Math.round(h1.top), kbOpen: document.body.classList.contains('df-kb-open'), vvh: getComputedStyle(document.documentElement).getPropertyValue('--df-vvh') };
  });
  console.log(JSON.stringify(m));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
