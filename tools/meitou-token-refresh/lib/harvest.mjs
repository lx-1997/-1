// 用持久化 Chrome profile 打开源站，抓取当前会话的 Bearer token 并当场验证它的会员权限。
//
// 关键点：Apple 登录的二次认证只能在有头浏览器里人工完成一次（onboard.mjs），
// 之后 profile 里的会话长期有效，定时任务只需无头打开页面即可拿到新签发的 token。
import path from "node:path";
import { chromium } from "playwright-core";
import { RUNTIME_HOME, hoursLeft } from "./config.mjs";

const HANDAN_CID = process.env.MEITOU_HANDAN_CID || "18e1deb7-87a4-5ded-9c49-73e4356621b6";

const HANDAN_PROBE_QUERY = `query Query($cid: String!, $page: Int!, $ptId: String!, $positionType: PositionType!, $tags: String!, $riskLevel: String!, $keywords: String!) {
  cacheableSearchableHandanList(cid: $cid, page: $page, ptID: $ptId, positionType: $positionType, tags: $tags, riskLevel: $riskLevel, keywords: $keywords) {
    totalHits
    data { id createdAt freeContent { ticker } tradingInfo { transactions { action } } }
  }
}`;

export function jwtExp(token) {
  try {
    const payload = token.split(".")[1];
    const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
    const json = JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
    return Number(json.exp) || 0;
  } catch {
    return 0;
  }
}

export async function harvestToken(cfg, { headless = true, deadlineMs = 60000, verbose = true, nudge = true } = {}) {
  const profileDir = path.join(RUNTIME_HOME, "chrome-profile");
  const say = (...args) => { if (verbose) console.log("[" + new Date().toISOString() + "]", ...args); };

  const context = await chromium.launchPersistentContext(profileDir, {
    channel: cfg.chromeChannel,
    headless,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    args: ["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled"],
  });

  const captured = new Map();
  const onRequest = (request) => {
    try {
      if (!/jdbinvesting\.com/.test(request.url())) return;
      const auth = request.headers()["authorization"] || "";
      if (!/^Bearer\s+/i.test(auth)) return;
      const token = auth.replace(/^Bearer\s+/i, "").trim();
      if (token.split(".").length === 3) captured.set(token, { at: Date.now(), url: request.url() });
    } catch { /* 忽略 */ }
  };
  context.on("request", onRequest);

  const result = {
    token: "",
    source: "",
    exp: 0,
    hoursLeft: null,
    pageUrl: "",
    seenTokens: 0,
    validation: null,
  };

  try {
    const page = context.pages()[0] || (await context.newPage());
    const readLocal = () =>
      page.evaluate((key) => {
        try { return localStorage.getItem(key) || ""; } catch { return ""; }
      }, cfg.localStorageKey).catch(() => "");

    await page.goto(cfg.sourceOrigin + "/", { waitUntil: "domcontentloaded", timeout: 60000 })
      .catch((error) => say("首页加载告警:", error.message));

    const deadline = Date.now() + deadlineMs;
    let lastNudge = 0;
    let raised = false;
    let best = null;
    while (Date.now() < deadline) {
      const candidates = [];
      const local = await readLocal();
      if (local) candidates.push({ token: local, source: "localStorage" });
      for (const [token, meta] of captured) candidates.push({ token, source: "request:" + meta.url.split("?")[0] });
      const scored = candidates
        .map((item) => ({ ...item, exp: jwtExp(item.token) }))
        .filter((item) => item.exp > 0)
        .sort((a, b) => b.exp - a.exp);
      best = scored[0] || null;
      if (best && (hoursLeft(best.exp) ?? -1) >= cfg.freshTokenHours) break;
      // 逼站点用 refresh 流程换一张新票：打开需要登录的页面。
      // ⚠️ 交互式登录向导必须关掉：每 15 秒导航一次会把用户正在填的登录弹窗刷掉。
      // 交互式登录：只在开始时置顶一次，之后安静等待（避免反复抢用户焦点）
      if (!headless && !raised) {
        raised = true;
        await page.bringToFront().catch(() => {});
      }
      if (nudge && Date.now() - lastNudge > 15000) {
        lastNudge = Date.now();
        say("已抓到的 token 不够新，访问需要授权的页面触发续签…");
        await page.goto(cfg.sourceOrigin + cfg.tokenProbePath, { waitUntil: "domcontentloaded", timeout: 45000 })
          .catch((error) => say("授权页加载告警:", error.message));
      }
      await page.waitForTimeout(2000);
    }

    result.pageUrl = page.url();
    result.seenTokens = captured.size;
    if (!best) {
      result.pageUrl = page.url();
      return result;
    }
    result.token = best.token;
    result.source = best.source;
    result.exp = best.exp;
    result.hoursLeft = hoursLeft(best.exp);

    // 当场验证：这条 token 能不能从 gqlrealauth 拿到会员交易明细
    try {
      result.validation = await page.evaluate(
        async ({ url, token, cid, query }) => {
          const res = await fetch(url, {
            method: "POST",
            headers: { "content-type": "application/json", authorization: "Bearer " + token },
            body: JSON.stringify({
              operationName: "Query",
              variables: { cid, keywords: "", ptId: "", positionType: "ALL", riskLevel: "[]", tags: "[]", page: 0 },
              query,
            }),
          });
          const text = await res.text();
          let json = null;
          try { json = JSON.parse(text); } catch { /* 保留原始文本 */ }
          const list = json?.data?.cacheableSearchableHandanList;
          const posts = list?.data || [];
          return {
            status: res.status,
            totalHits: list?.totalHits ?? null,
            posts: posts.length,
            postsWithTransactions: posts.filter((p) => (p.tradingInfo?.transactions || []).length > 0).length,
            error: json?.errors?.[0]?.message || (res.ok ? "" : text.slice(0, 120)),
          };
        },
        { url: cfg.authGqlUrl, token: best.token, cid: HANDAN_CID, query: HANDAN_PROBE_QUERY },
      );
    } catch (error) {
      result.validation = { status: 0, error: error.message };
    }
    return result;
  } finally {
    context.off("request", onRequest);
    await context.close().catch(() => {});
  }
}
