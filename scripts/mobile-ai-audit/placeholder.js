/* 验证窄屏短占位：注入补丁 CSS + 改 placeholder 属性，截 composer 区域 */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const PATCH_CSS = `@media (max-width:820px){.terminal-only-root .bbt-with-sidebar.bbt-side-ai{height:var(--df-vvh,100dvh)!important}.bbt-side-ai .bbt-ai-workspace{height:calc(var(--df-vvh,100dvh) - 154px)!important}.bbt-side-ai .bbt-ai-workspace-eyebrow,.bbt-side-ai .bbt-ai-workspace-head p,.bbt-side-ai .bbt-ai-harness-guide,.bbt-side-ai .bbt-ai-chat-head,.bbt-side-ai .bbt-ai-live{display:none!important}.bbt-side-ai .bbt-ai-workspace-head-actions button{white-space:nowrap}}`;
  await page.goto('https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.addStyleTag({ content: PATCH_CSS });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(3000);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--df-vvh', window.innerHeight + 'px');
    document.querySelector('.bbt-ai-composer textarea').setAttribute('placeholder', '问行情、估值、财报…');
  });
  await page.waitForTimeout(600);
  const box = await page.locator('.bbt-ai-composer').boundingBox();
  await page.screenshot({ path: 'shots/12-patched-composer.png', clip: { x: 0, y: box.y - 12, width: 390, height: box.height + 60 } });
  console.log('composer box', JSON.stringify(box));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
