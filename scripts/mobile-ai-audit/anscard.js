const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 392, height: 700 }, isMobile: true, hasTouch: true })).newPage();
  const ANSWER = '大盘基本横盘。\n\n**总结一句话：** 节前观望为主。';
  await page.route('**/api/agents/tool-research/stream**', r => r.fulfill({ status: 200, contentType: 'text/event-stream', body: `event: final\ndata: ${JSON.stringify({ answer: ANSWER, tool_trace: [], suggestions: [], quota_left: null }) }\n\n` }));
  await page.goto(process.env.AUDIT_BASE || 'http://127.0.0.1:4178', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2000);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.fill('.bbt-ai-composer textarea', '测试');
  await page.tap('.bbt-ai-send');
  await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 20000 });
  await page.waitForTimeout(400);
  const out = await page.evaluate(() => {
    const pick = el => { if (!el) return null; const cs = getComputedStyle(el); return { cls: (el.className||'').toString().slice(0,50), border: cs.borderTopWidth+' '+cs.borderTopColor, radius: cs.borderRadius, bg: cs.backgroundColor }; };
    return {
      latest: pick(document.querySelector('.bbt-ai-latest-answer')),
      asstDiv: pick(document.querySelector('.bbt-ai-message--assistant > div')),
      msg: pick(document.querySelector('.bbt-ai-message--assistant')),
      md: pick(document.querySelector('.bbt-ai-latest-answer .bbt-md')),
    };
  });
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
