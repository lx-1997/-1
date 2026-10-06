const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const ANSWER = '## 昨日复盘要点\n\n大盘基本横盘——上证仅微跌 0.11%，沪深 300 甚至微涨，市场资金以观望为主。\n\n| 指数 | 收盘点位 | 涨跌幅 | 成交额(亿) | 领涨板块 | 领跌板块 |\n| --- | --- | --- | --- | --- | --- |\n| 上证指数 (999999) | 3,301.85 | -0.11% | 5,432 | 银行 | 军工 |\n| 深证成指 (399001) | 3,142.56 | +0.08% | 6,214 | 电子 | 煤炭 |\n\n---\n\n**总结一句话：** 节前观望为主，关注节后量能变化。';
  await page.route('**/api/agents/tool-research/stream**', route => route.fulfill({
    status: 200, contentType: 'text/event-stream',
    body: `event: final\ndata: ${JSON.stringify({ answer: ANSWER, tool_trace: [], suggestions: [], quota_left: null }) }\n\n`,
  }));
  await page.goto(process.env.AUDIT_BASE || 'https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.fill('.bbt-ai-composer textarea', '测试问题');
  await page.tap('.bbt-ai-send');
  await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 20000 });
  await page.waitForTimeout(500);
  const info = await page.evaluate(() => {
    const pick = el => { if (!el) return null; const cs = getComputedStyle(el); const r = el.getBoundingClientRect(); return { cls: (el.className || '').toString().slice(0, 50), w: Math.round(r.width), scrollW: el.scrollWidth, minW: cs.minWidth, maxW: cs.maxWidth, ovX: cs.overflowX }; };
    return {
      vw: document.documentElement.clientWidth,
      wrap: pick(document.querySelector('.bbt-ai-latest-answer .bbt-md-table-wrap')),
      table: pick(document.querySelector('.bbt-ai-latest-answer .bbt-md-table')),
      answer: pick(document.querySelector('.bbt-ai-latest-answer')),
      md: (() => { const m = document.querySelector('.bbt-ai-latest-answer .bbt-md'); if (!m) return null; const cs = getComputedStyle(m); const r = m.getBoundingClientRect(); return { w: Math.round(r.width), width: cs.width, position: cs.position, display: cs.display, offsetParentCls: (m.offsetParent && m.offsetParent.className || '').toString().slice(0,40), float: cs.cssFloat, flexShrink: cs.flexShrink }; })(),
      msg: pick(document.querySelector('.bbt-ai-latest-answer').closest('.bbt-ai-message')),
      msgParent: (() => { const m = document.querySelector('.bbt-ai-latest-answer').closest('.bbt-ai-message'); if (!m) return null; const p = m.parentElement; const cs = getComputedStyle(p); return { cls: (p.className||'').toString().slice(0,50), w: Math.round(p.getBoundingClientRect().width), display: cs.display, minW: cs.minWidth }; })(),
      hr: !!document.querySelector('.bbt-ai-latest-answer .bbt-md-hr'),
      msgEl: (() => { const a = document.querySelector('.bbt-ai-latest-answer .bbt-ai-message'); if (!a) return null; const cs = getComputedStyle(a); const r = a.getBoundingClientRect(); return { w: Math.round(r.width), minW: cs.minWidth, maxW: cs.maxWidth, width: cs.width, display: cs.display }; })(),
      divEl: (() => { const m = document.querySelector('.bbt-ai-latest-answer .bbt-md'); const d = m.parentElement; const cs = getComputedStyle(d); const r = d.getBoundingClientRect(); return { cls: (d.className||'').toString().slice(0,40), w: Math.round(r.width), minW: cs.minWidth, maxW: cs.maxWidth, width: cs.width, display: cs.display }; })(),
      chatBody: (() => { const b = document.querySelector('.bbt-ai-chat-body'); const cs = getComputedStyle(b); return { display: cs.display, flexDirection: cs.flexDirection, w: Math.round(b.getBoundingClientRect().width), contentW: b.clientWidth }; })(),
      answerParentCls: (() => { const a = document.querySelector('.bbt-ai-latest-answer'); const p = a.parentElement; const cs = getComputedStyle(p); return { cls: (p.className||'').toString().slice(0,60), display: cs.display, w: Math.round(p.getBoundingClientRect().width) }; })(),
      mdPath: (() => { const m = document.querySelector('.bbt-ai-latest-answer .bbt-md'); const path = []; let e = m; while (e && path.length < 6) { path.push((e.className||e.tagName||'').toString().slice(0,30)); e = e.parentElement; } return path; })(),
    };
  });
  console.log(JSON.stringify(info, null, 1));
  await page.screenshot({ path: 'shots/50-fixed-answer.png' });
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
