const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  await page.goto('https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(600);
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--df-vvh', '500px');
    document.body.classList.add('df-kb-open');
  });
  await page.waitForTimeout(400);
  const r = await page.evaluate(() => {
    const ws = document.querySelector('.bbt-ai-workspace');
    const app = document.querySelector('.bbt-with-sidebar.bbt-side-ai');
    return {
      varOnHtml: document.documentElement.style.getPropertyValue('--df-vvh'),
      varComputed: getComputedStyle(document.documentElement).getPropertyValue('--df-vvh'),
      kbClass: document.body.className.includes('df-kb-open'),
      wsHeight: Math.round(ws.getBoundingClientRect().height),
      wsComputedHeight: getComputedStyle(ws).height,
      appComputedHeight: getComputedStyle(app).height,
      mq820: window.matchMedia('(max-width: 820px)').matches,
    };
  });
  console.log(JSON.stringify(r, null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
