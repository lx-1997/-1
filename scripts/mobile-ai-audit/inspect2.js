const { chromium } = require('playwright');
(async () => {
  const base = process.env.AUDIT_BASE || 'http://127.0.0.1:4177';
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 392, height: 700 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2200);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(600);
  const info = await page.evaluate(() => {
    const pick = el => { if (!el) return null; const cs = getComputedStyle(el); const r = el.getBoundingClientRect(); return { cls: (el.className||'').toString().slice(0,44), border: cs.borderTopWidth + ' ' + cs.borderTopColor, radius: cs.borderRadius, bg: cs.backgroundColor, top: Math.round(r.top), h: Math.round(r.height) }; };
    const h1 = document.querySelector('.bbt-ai-workspace-head h1');
    const body = document.querySelector('.bbt-ai-chat-body');
    const w = document.querySelector('.bbt-ai-welcome');
    return {
      h1: { fs: getComputedStyle(h1).fontSize, cls: h1.className },
      grid: pick(document.querySelector('.bbt-ai-workspace-grid')),
      chatPanel: pick(document.querySelector('.bbt-ai-chat-panel')),
      latest: pick(document.querySelector('.bbt-ai-latest-answer')),
      chatBody: { ...pick(body), scrollTop: body.scrollTop, scrollH: body.scrollHeight, clientH: body.clientHeight },
      welcome: pick(w),
      head: pick(document.querySelector('.bbt-ai-workspace-head')),
    };
  });
  console.log(JSON.stringify(info, null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
