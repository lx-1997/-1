/* 模拟验证：在线上页注入与本地改动等价的 CSS，截「改后」欢迎页 + 提问态 */
const path = require('path');
const { chromium } = require('playwright');

const PATCH_CSS = `
  @media (max-width: 820px) {
    .terminal-only-root .bbt-with-sidebar.bbt-side-ai { height: var(--df-vvh, 100dvh) !important; }
    .bbt-side-ai .bbt-ai-workspace { height: calc(var(--df-vvh, 100dvh) - 154px) !important; }
    .bbt-side-ai .bbt-ai-workspace-eyebrow,
    .bbt-side-ai .bbt-ai-workspace-head p,
    .bbt-side-ai .bbt-ai-harness-guide,
    .bbt-side-ai .bbt-ai-chat-head,
    .bbt-side-ai .bbt-ai-live { display: none !important; }
    .bbt-side-ai .bbt-ai-workspace-head-actions button { white-space: nowrap; }
    body.df-kb-open .bbt-mobile-nav { display: none !important; }
    body.df-kb-open .terminal-only-root .bbt-with-sidebar.bbt-side-ai { padding-bottom: 0 !important; }
  }
`;

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    locale: 'zh-CN',
  });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    // 模拟 JS 键盘适配写入的变量与 body 类（欢迎态默认视口）
    const sync = () => {
      document.documentElement.style.setProperty('--df-vvh', window.innerHeight + 'px');
    };
    // 尽早注册
    window.addEventListener('DOMContentLoaded', sync);
    setTimeout(sync, 0);
    new MutationObserver(() => {
      if (!document.body) return;
      // 模拟 textarea 高度兜底
      const ta = document.querySelector('.bbt-ai-composer textarea');
      if (ta && ta.value && ta.value.includes('\\n')) { /* 多行场景由人工验证 */ }
    }).observe(document.documentElement, { childList: true, subtree: true });
  });
  await page.goto('https://daocaijing.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.addStyleTag({ content: PATCH_CSS });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(3500);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-workspace', { timeout: 15000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(__dirname, 'shots', '10-patched-welcome.png') });

  const metrics = await page.evaluate(() => {
    const rect = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height) }; };
    return {
      vvh: document.documentElement.style.getPropertyValue('--df-vvh'),
      head: rect('.bbt-ai-workspace-head'),
      harness: rect('.bbt-ai-harness-guide'),
      chatHead: rect('.bbt-ai-chat-head'),
      welcome: rect('.bbt-ai-welcome'),
      composer: rect('.bbt-ai-composer'),
      note: rect('.bbt-ai-composer-note'),
      nav: rect('.bbt-mobile-nav'),
      suggBtn: rect('.bbt-ai-sugg-grid button'),
      headBtnText: document.querySelector('.bbt-ai-workspace-head-actions button')?.textContent,
      headBtnH: Math.round(document.querySelector('.bbt-ai-workspace-head-actions button')?.getBoundingClientRect().height || 0),
    };
  });
  console.log('PATCHED METRICS', JSON.stringify(metrics, null, 1));

  // 模拟键盘：把 vvh 压到 500 并打 df-kb-open
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--df-vvh', '500');
    document.body.classList.add('df-kb-open');
  });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(__dirname, 'shots', '11-patched-keyboard.png') });
  const kb = await page.evaluate(() => {
    const rect = s => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
    return { composer: rect('.bbt-ai-composer'), nav: rect('.bbt-mobile-nav'), app: rect('.bbt-with-sidebar.bbt-side-ai') };
  });
  console.log('KEYBOARD SIM', JSON.stringify(kb));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
