const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 392, height: 700 }, isMobile: true, hasTouch: true })).newPage();
  await page.goto(process.env.AUDIT_BASE || 'http://127.0.0.1:4177', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('nav.bbt-mobile-nav', { timeout: 30000 });
  await page.waitForTimeout(1800);
  await page.tap('nav.bbt-mobile-nav button:has-text("AI")');
  await page.waitForSelector('.bbt-ai-workspace-head h1', { timeout: 15000 });
  await page.waitForTimeout(500);
  const out = await page.evaluate(() => {
    const h1 = document.querySelector('.bbt-ai-workspace-head h1');
    const panel = document.querySelector('.bbt-ai-chat-panel');
    const matched = [];
    for (const sheet of document.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch { continue; }
      const walk = list => { for (const r of list) { if (r.cssRules) walk(r.cssRules); else if (r.selectorText && r.style && r.style.fontSize) { let hit = false; try { hit = h1.matches(r.selectorText); } catch {} if (hit) matched.push({ sel: r.selectorText.slice(0,110), fs: r.style.fontSize, media: r.parentRule && r.parentRule.conditionText || '' }); } } };
      walk(rules);
    }
    return { computed: getComputedStyle(h1).fontSize, matched, panelBorder: getComputedStyle(panel).borderTopWidth, panelBg: getComputedStyle(panel).backgroundColor };
  });
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
