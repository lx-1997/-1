/* 聚焦探针：抓 AI 提问时的网络请求与响应码 */
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    locale: 'zh-CN',
  });
  const page = await ctx.newPage();
  const reqs = [];
  page.on('response', async r => {
    const u = r.url();
    if (u.includes('/api/')) reqs.push(`${r.status()} ${r.request().method()} ${u.slice(0, 140)}`);
  });
  await page.goto('https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer textarea', { timeout: 15000 });
  reqs.length = 0;  // 只看发送后的
  await page.fill('.bbt-ai-composer textarea', '今天大盘怎么样');
  await page.tap('.bbt-ai-send');
  await page.waitForTimeout(9000);
  console.log(reqs.join('\n'));
  const err = await page.evaluate(() => document.querySelector('.bbt-ai-workspace-error')?.textContent || '');
  console.log('ERRTEXT:', err.slice(0, 200));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
