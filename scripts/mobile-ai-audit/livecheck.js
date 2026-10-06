/* 真实部署键盘验证：不注入任何 CSS，仅模拟 JS 效果（写 --df-vvh + body.df-kb-open） */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  await page.goto('https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(3000);
  const cssHref = await page.evaluate(() => Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(l => l.getAttribute('href')).find(h => h && h.includes('css/main')) || '');
  console.log('live css:', cssHref);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(800);
  // 欢迎态指标（应无副标题/横幅/面板头）
  const welcome = await page.evaluate(() => {
    const vis = s => { const el = document.querySelector(s); if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    return { subtitle: vis('.bbt-ai-workspace-head p'), harness: vis('.bbt-ai-harness-guide'), chatHead: vis('.bbt-ai-chat-head'), eyebrow: vis('.bbt-ai-workspace-eyebrow') };
  });
  console.log('WELCOME hidden-check', JSON.stringify(welcome));
  // 键盘模拟
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--df-vvh', '500px');
    document.body.classList.add('df-kb-open');
  });
  await page.waitForTimeout(500);
  const kb = await page.evaluate(() => {
    const rect = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
    return { appH: Math.round(document.querySelector('.bbt-with-sidebar.bbt-side-ai').getBoundingClientRect().height), composer: rect('.bbt-ai-composer'), note: rect('.bbt-ai-composer-note'), nav: rect('.bbt-mobile-nav'), subtitle: rect('.bbt-ai-workspace-head p') };
  });
  console.log('KEYBOARD vvh=500', JSON.stringify(kb));
  await page.screenshot({ path: 'shots/20-live-keyboard.png' });
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
