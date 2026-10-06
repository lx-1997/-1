const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const PATCH_CSS = `@media (max-width:820px){.terminal-only-root .bbt-with-sidebar.bbt-side-ai{height:var(--df-vvh,100dvh)!important}.bbt-side-ai .bbt-ai-workspace{height:calc(var(--df-vvh,100dvh) - 154px)!important}.bbt-side-ai .bbt-ai-workspace-eyebrow,.bbt-side-ai .bbt-ai-workspace-head p,.bbt-side-ai .bbt-ai-harness-guide,.bbt-side-ai .bbt-ai-chat-head,.bbt-side-ai .bbt-ai-live{display:none!important}body.df-kb-open .bbt-mobile-nav{display:none!important}body.df-kb-open .terminal-only-root .bbt-with-sidebar.bbt-side-ai{padding-bottom:0!important}}`;
  await page.goto('https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.addStyleTag({ content: PATCH_CSS });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(3000);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--df-vvh', '500px');
    document.body.classList.add('df-kb-open');
  });
  await page.waitForTimeout(500);
  const kb = await page.evaluate(() => {
    const rect = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
    return { appH: Math.round(document.querySelector('.bbt-with-sidebar.bbt-side-ai').getBoundingClientRect().height), composer: rect('.bbt-ai-composer'), note: rect('.bbt-ai-composer-note'), nav: rect('.bbt-mobile-nav'), body: rect('.bbt-ai-chat-body') };
  });
  console.log('KEYBOARD vvh=500', JSON.stringify(kb));
  await page.screenshot({ path: 'shots/11-patched-keyboard.png' });
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
