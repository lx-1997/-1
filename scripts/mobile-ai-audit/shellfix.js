const { chromium } = require('playwright');
(async () => {
  const base = process.env.AUDIT_BASE || 'https://daocaijing.com';
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  for (const vp of [{ w: 392, h: 650 }, { w: 390, h: 844 }]) {
    const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
    const page = await ctx.newPage();
    const ANSWER = '大盘基本横盘，市场资金以观望为主。\n\n| 指数 | 收盘点位 | 涨跌幅 | 成交额 | 领涨 | 领跌 |\n| --- | --- | --- | --- | --- | --- |\n| 上证指数 (999999) | 3,301.85 | -0.11% | 5,432 | 银行 | 军工 |\n\n**总结：** 节前观望为主。';
    await page.route('**/api/agents/tool-research/stream**', r => r.fulfill({
      status: 200, contentType: 'text/event-stream',
      body: `event: final\ndata: ${JSON.stringify({ answer: ANSWER, tool_trace: [], suggestions: ['今天大盘怎么样？', '现在哪些板块最热？'], quota_left: null }) }\n\n`,
    }));
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
    await page.waitForTimeout(2500);
    await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
    await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
    await page.waitForTimeout(800);
    const welcome = await page.evaluate(() => {
      const r = s => { const el = document.querySelector(s); if (!el) return null; const b = el.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height) }; };
      const ws = r('.bbt-ai-workspace'); const nav = r('nav.bbt-mobile-nav');
      const h1 = document.querySelector('.bbt-ai-workspace-head h1');
      return { ws, nav, gap: nav && ws ? nav.top - ws.bottom : null, h1Top: h1 ? Math.round(h1.getBoundingClientRect().top) : null, innerH: innerHeight };
    });
    // 提问进入回答态
    await page.fill('.bbt-ai-composer textarea', '测试');
    await page.tap('.bbt-ai-send');
    await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 20000 });
    await page.waitForTimeout(600);
    const answer = await page.evaluate(() => {
      const r = s => { const el = document.querySelector(s); if (!el) return null; const b = el.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom) }; };
      const ws = r('.bbt-ai-workspace'); const nav = r('nav.bbt-mobile-nav'); const comp = r('.bbt-ai-composer');
      const item = document.querySelector('.bbt-ai-history-item');
      let overlap = null;
      if (item) {
        const main = item.querySelector('.bbt-ai-history-item-main');
        const act = item.querySelector('.bbt-ai-history-item-actions');
        if (main && act) {
          const mr = main.querySelector('span, b, div') ? main.querySelector('span, b, div').getBoundingClientRect() : main.getBoundingClientRect();
          const ar = act.getBoundingClientRect();
          overlap = Math.round(mr.right - ar.left);
        }
      }
      return { gap: nav && ws ? nav.top - ws.bottom : null, compBottom: comp ? comp.bottom : null, navTop: nav ? nav.top : null, textVsActions: overlap };
    });
    console.log(`[${vp.w}x${vp.h}] welcome:`, JSON.stringify(welcome));
    console.log(`[${vp.w}x${vp.h}] answer:`, JSON.stringify(answer));
    await page.screenshot({ path: `shots/6${vp.h}-state.png` });
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
