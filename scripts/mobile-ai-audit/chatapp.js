const { chromium } = require('playwright');
(async () => {
  const base = process.env.AUDIT_BASE || 'http://127.0.0.1:4177';
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 392, height: 700 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const ANSWER = '大盘基本横盘，市场资金以观望为主，节前缩量属于正常现象，不必过度解读。\n\n| 指数 | 收盘点位 | 涨跌幅 | 成交额 |\n| --- | --- | --- | --- |\n| 上证指数 | 3,301.85 | -0.11% | 5,432 |\n| 深证成指 | 3,142.56 | +0.08% | 6,214 |\n\n- 大盘基本横盘——上证仅微跌 0.11%，沪深 300 甚至微涨，市场资金以观望、持币过节为主。\n- 成长相对抗跌——创业板指小幅收红（+0.18%），风格在科技与红利间拉锯。\n\n**总结一句话：** 节前观望为主，关注节后量能变化与外围市场情绪。';
  await page.route('**/api/agents/tool-research/stream**', r => r.fulfill({
    status: 200, contentType: 'text/event-stream',
    body: `event: final\ndata: ${JSON.stringify({ answer: ANSWER, tool_trace: [{tool:'get_daily_brief',ok:true,summary:'复盘已核对'}], suggestions: ['今天大盘怎么样？', '现在哪些板块最热？', '看看今日涨停天梯'], quota_left: null }) }\n\n`,
  }));
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.waitForTimeout(700);
  await page.screenshot({ path: 'shots/80-chatapp-welcome.png' });
  await page.fill('.bbt-ai-composer textarea', '昨天A股复盘里最该注意什么？');
  await page.tap('.bbt-ai-send');
  await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 20000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: 'shots/81-chatapp-answer.png' });
  const check = await page.evaluate(() => {
    const fs = s => { const el = document.querySelector(s); return el ? getComputedStyle(el).fontSize : null; };
    const avatar = document.querySelector('.bbt-ai-message--user > span');
    const wsBg = getComputedStyle(document.querySelector('.bbt-ai-workspace')).backgroundColor;
    const sendBtn = document.querySelector('.bbt-ai-composer .bbt-ai-send, .bbt-ai-composer > button');
    const sb = sendBtn ? sendBtn.getBoundingClientRect() : null;
    return { userAvatarHidden: avatar ? getComputedStyle(avatar).display === 'none' : null, wsBg, answerFS: fs('.bbt-ai-latest-answer .bbt-md-p'), userFS: fs('.bbt-ai-message--user p'), sendW: sb ? Math.round(sb.width) : null, sendRound: sb ? Math.round(sb.width) === Math.round(sb.height) : null, arrow: (() => { const s = document.querySelector('.bbt-ai-sugg-grid button span'); return s ? getComputedStyle(s).display : 'no-sugg'; })() };
  });
  console.log(JSON.stringify(check, null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
