const path = require('path');
const { chromium } = require('playwright');
const OUT = path.join(__dirname, 'shots');

const ANSWER = [
  '## 昨日复盘要点',
  '',
  '大盘基本横盘——上证仅微跌 0.11%，沪深 300 甚至微涨，市场资金以观望、持币过节为主，大幅做空意愿有限，节前的特征如下表所示。',
  '',
  '| 指数 | 收盘点位 | 涨跌幅 | 成交额(亿) | 领涨板块 | 领跌板块 |',
  '| --- | --- | --- | --- | --- | --- |',
  '| 上证指数 (999999) | 3,301.85 | -0.11% | 5,432 | 银行 | 军工 |',
  '| 深证成指 (399001) | 3,142.56 | +0.08% | 6,214 | 电子 | 煤炭 |',
  '| 创业板指 (399006) | 1,741.67 | +0.18% | 2,845 | 医药 | 钢铁 |',
  '| 科创50 (000688) | 2,410.04 | -0.22% | 987 | 芯片 | 房地产 |',
  '| 沪深300 (000300) | 4,345.21 | +0.03% | 4,102 | 消费 | 电力 |',
  '',
  '- 大盘基本横盘——上证仅微跌 0.11%，沪深 300 甚至微涨，市场资金以观望、持币过节为主，大幅做空意愿有限。',
  '- 成长相对抗跌——创业板指小幅收红（+0.18%），明显风格在科技与红利间拉锯。',
  '',
  '---',
  '',
  '**总结一句话：** 昨天A股没开，不需要复盘。真正该注意的是假期外围波动 + 海外市场AI泡沫预警，这决定了下周一开盘的情绪（考）。',
].join('\n');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await ctx.newPage();
  await page.route('**/api/agents/tool-research/stream**', route => route.fulfill({
    status: 200, contentType: 'text/event-stream',
    body: `event: final\ndata: ${JSON.stringify({ answer: ANSWER, tool_trace: [ {tool:'get_daily_brief', ok:true, summary:'复盘已生成'}, {tool:'get_market_snapshot', ok:true, summary:'指数快照已核对'} ], suggestions: ['今天大盘怎么样？','现在哪些板块最热？','看看今日涨停天梯'], quota_left: null, rounds: 2 }) }\n\n`,
  }));
  await page.goto(process.env.AUDIT_BASE || 'https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-composer', { timeout: 15000 });
  await page.tap('.bbt-ai-sugg-grid button, .bbt-ai-sugg button >> nth=0').catch(() => {});
  await page.waitForTimeout(300);
  await page.tap('.bbt-ai-send');
  await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 20000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, '40-overflow-answer.png') });

  const diag = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const bad = [];
    document.querySelectorAll('.bbt-ai-workspace *').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > vw + 1 || r.left < -1)) {
        bad.push({ cls: (el.className || '').toString().slice(0, 60), tag: el.tagName, w: Math.round(r.width), l: Math.round(r.left), rgt: Math.round(r.right) });
      }
    });
    const scrollers = [];
    document.querySelectorAll('.bbt-ai-workspace *').forEach(el => {
      if (el.scrollWidth > el.clientWidth + 2) {
        const cs = getComputedStyle(el);
        scrollers.push({ cls: (el.className || '').toString().slice(0, 60), scrollW: el.scrollWidth, clientW: el.clientWidth, overflowX: cs.overflowX });
      }
    });
    return { vw, overflowing: bad.slice(0, 14), clippedScrollers: scrollers.slice(0, 10) };
  });
  console.log(JSON.stringify(diag, null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
