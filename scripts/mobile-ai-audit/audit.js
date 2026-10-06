/* 移动端 AI 问答视觉审计：iPhone 尺寸视口访问线上站，截欢迎页/提问中/回答后三态 + 布局指标 */
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
const BASE = process.env.AUDIT_BASE || 'https://daocaijing.com';

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    locale: 'zh-CN',
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(4000); // 首屏数据
  await page.screenshot({ path: path.join(OUT, '01-home.png') });

  // 点底部导航 AI
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-workspace', { timeout: 15000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(OUT, '02-ai-welcome.png') });

  // 布局指标
  const metrics = await page.evaluate(() => {
    const rect = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height), width: Math.round(r.width), display: cs.display, visible: r.width > 0 && r.height > 0 }; };
    return {
      innerW: window.innerWidth, innerH: window.innerHeight,
      vvH: window.visualViewport ? Math.round(window.visualViewport.height) : null,
      docScrollY: Math.round(window.scrollY),
      workspace: rect('.bbt-ai-workspace'),
      harnessGuide: rect('.bbt-ai-harness-guide'),
      head: rect('.bbt-ai-workspace-head'),
      historyPanel: rect('.bbt-ai-history-panel'),
      chatPanel: rect('.bbt-ai-chat-panel'),
      chatBody: rect('.bbt-ai-chat-body'),
      composer: rect('.bbt-ai-composer'),
      composerNote: rect('.bbt-ai-composer-note'),
      mobileNav: rect('.bbt-mobile-nav'),
      suggestionCount: document.querySelectorAll('.bbt-ai-sugg-grid button').length,
      historyItemCount: document.querySelectorAll('.bbt-ai-history-item').length,
    };
  });
  console.log('WELCOME METRICS', JSON.stringify(metrics, null, 1));

  // 横向滚动提示卡片（欢迎页 suggestion 是横滑的）
  // 点第一个建议 → 文案进输入框（不自动发送）
  const sug = page.locator('.bbt-ai-sugg-grid button').first();
  if (await sug.count()) {
    await sug.tap();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, '03-ai-primed.png') });
  }
  // 发送
  await page.tap('.bbt-ai-send');
  await page.waitForTimeout(3500);
  await page.screenshot({ path: path.join(OUT, '04-ai-busy-early.png') });
  await page.waitForTimeout(6000);
  await page.screenshot({ path: path.join(OUT, '05-ai-busy-mid.png') });

  // 等回答
  try {
    await page.waitForSelector('.bbt-ai-latest-answer', { timeout: 120000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, '06-ai-answer.png') });
    // 滚到回答底部看 composer 是否被遮
    await page.evaluate(() => { const b = document.querySelector('.bbt-ai-chat-body'); if (b) b.scrollTop = b.scrollHeight; });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, '07-ai-answer-bottom.png') });
    const ansMetrics = await page.evaluate(() => {
      const rect = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height) }; };
      return { composer: rect('.bbt-ai-composer'), mobileNav: rect('.bbt-mobile-nav'), chatBody: rect('.bbt-ai-chat-body'), innerH: window.innerHeight };
    });
    console.log('ANSWER METRICS', JSON.stringify(ansMetrics, null, 1));
  } catch (e) {
    console.log('ANSWER TIMEOUT', e.message.slice(0, 120));
    await page.screenshot({ path: path.join(OUT, '06-ai-answer-timeout.png') });
  }

  // 键盘弹起模拟（聚焦输入框，看 visualViewport 变化不行于 headless，但可看 resize 行为）
  // 收集追问 chips 可见性
  const followups = await page.evaluate(() => {
    const fu = document.querySelector('.bbt-ai-followups');
    if (!fu) return null;
    const r = fu.getBoundingClientRect();
    return { count: fu.querySelectorAll('button').length, top: Math.round(r.top), visible: r.height > 0 };
  });
  console.log('FOLLOWUPS', JSON.stringify(followups));
  console.log('CONSOLE ERRORS', JSON.stringify(errors.slice(0, 8), null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
