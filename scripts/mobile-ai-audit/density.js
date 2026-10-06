const { chromium } = require('playwright');
(async () => {
  const base = process.env.AUDIT_BASE || 'http://127.0.0.1:4176';
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 392, height: 700 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const ANSWER = '大盘基本横盘，市场资金以观望为主，节前缩量属于正常现象，不必过度解读。\n\n| 指数 | 收盘点位 | 涨跌幅 |\n| --- | --- | --- |\n| 上证指数 | 3,301.85 | -0.11% |\n\n**总结：** 节前观望为主。';
  await page.route('**/api/agents/tool-research/stream**', r => r.fulfill({
    status: 200, contentType: 'text/event-stream',
    body: `event: final\ndata: ${JSON.stringify({ answer: ANSWER, tool_trace: [], suggestions: ['今天大盘怎么样？'], quota_left: null }) }\n\n`,
  }));
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(700);
  const welcome = await page.evaluate(() => {
    const fs = s => { const el = document.querySelector(s); return el ? getComputedStyle(el).fontSize : null; };
    const w = document.querySelector('.bbt-ai-welcome').getBoundingClientRect();
    const body = document.querySelector('.bbt-ai-chat-body').getBoundingClientRect();
    const proof = fs('.bbt-ai-welcome-proof');
    return {
      inputFS: fs('.bbt-ai-composer textarea'),
      suggFS: fs('.bbt-ai-sugg-grid button'),
      proofFS: proof,
      bodyDisplay: getComputedStyle(document.querySelector('.bbt-ai-chat-body')).display,
      topGap: Math.round(w.top - body.top),
      bottomGap: Math.round(body.bottom - w.bottom),
      wH: Math.round(w.height), bodyH: Math.round(body.height),
    };
  });
  console.log('WELCOME', JSON.stringify(welcome));
  await page.screenshot({ path: 'shots/70-welcome-density.png' });
  await page.fill('.bbt-ai-composer textarea', '测试');
  await page.tap('.bbt-ai-send');
  await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 20000 });
  await page.waitForTimeout(500);
  const answer = await page.evaluate(() => {
    const fs = s => { const el = document.querySelector(s); return el ? getComputedStyle(el).fontSize : null; };
    return { answerP: fs('.bbt-ai-latest-answer .bbt-md-p'), userMsg: fs('.bbt-ai-message--user p'), actBtn: (() => { const b = document.querySelector('.bbt-ai-history-item-actions button'); return b ? getComputedStyle(b).width : null; })() };
  });
  console.log('ANSWER', JSON.stringify(answer));
  await page.screenshot({ path: 'shots/71-answer-density.png' });
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
