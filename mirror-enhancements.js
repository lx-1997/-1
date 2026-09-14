/*
 * Meitou mirror production parity layer.
 *
 * The original prototype grew through several short patches.  This file keeps
 * the source-facing behavior in one place: it normalizes every synced record,
 * renders the same information architecture as the source site, and loads the
 * long transcript payload after the first paint.
 */
(function installMirrorParity(window, document) {
  "use strict";

  if (window.__mirrorParityInstalled) return;
  window.__mirrorParityInstalled = true;

  const SOURCE_ORIGIN = "https://www.jdbinvesting.com";
  const MIRROR_ORIGIN = "https://daocaijing.com";
  const VERSION = "2026.09.14.9";
  // The shell snapshot is enough for the home and tracking surfaces.  The
  // transcript payload is large, so only request it when a reader opens a
  // content-heavy route (or signals intent by hovering/focusing a card).
  const FULL_SNAPSHOT_ROUTES = new Set(["article"]);
  const aliases = {
    trade: "trades",
    qa: "questions",
    question: "questions",
    bookmark: "bookmarks",
    video: "my-videos",
  };
  const moduleLabels = {
    home: "最新研究",
    research: "全部研究",
    opportunities: "行业机会",
    tracking: "美投跟踪",
    macro: "每月宏观报告",
    news: "每日报告",
    etf: "ETF主动选股",
    trades: "实战操作",
    questions: "社区问答",
  };
  const moduleGroups = {
    home: "最新研究",
    research: "最新研究",
    opportunities: "挖掘新机会",
    tracking: "美投跟踪",
    macro: "每月宏观报告",
    news: "每日报告",
    etf: "ETF主动选股",
    trades: "实战操作",
    questions: "社区问答",
  };
  const preferredStockOrder = [
    "NVDA", "SPCX", "NFLX", "TSLA", "SNOW", "HOOD", "ORCL", "PLTR", "SHOP", "MSFT",
    "GS", "TEM", "AAPL", "AMZN", "ANET", "CAT", "COST", "GOOG", "INTC", "META", "TSM",
  ];
  const mag7 = new Set(["AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA", "NVDA"]);
  const sourceQuoteOverrides = {
    NVDA: {
      quoteTime: "2026/09/04 16:00 ET",
      previousClose: "228.45",
      open: "231.14",
      low: "229.63",
      high: "234.76",
      week52: "164.07 – 236.54",
      pe: "24.15",
      target: "$295",
      range: "$217 – $432",
      stance: "非常积极",
      risk: 2,
      track: "深度跟踪",
      trackDuration: "61个月",
      updated: "2026/09/01",
      buyReasons: [
        "AI 基础设施需求仍在扩张，数据中心计算预算保持高位。",
        "GPU 软件生态和开发者黏性构成长期护城河。",
        "新产品迭代持续提升训练与推理的单位效率。",
        "供应链规模和客户覆盖带来更强的议价能力。",
        "当前估值仍可用盈利增长与现金流改善解释。",
      ],
      sellRisks: [
        "客户资本开支放缓会直接影响订单节奏。",
        "大型客户自研芯片可能压缩部分增量需求。",
        "出口限制和地缘政治会带来收入结构变化。",
        "高预期环境下，财报稍低于预期也可能放大波动。",
        "供应链扩产、库存和产品切换需要持续跟踪。",
        "估值对利率和风险偏好变化较为敏感。",
      ],
    },
  };
  const sourceArticleOverrides = {
    "https://www.jdbinvesting.com/tofu/dXlj0-TOFU": { date: "2026/08/19" },
  };

  const htmlEscape = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const escHtml = typeof window.esc === "function" ? window.esc : htmlEscape;
  const escape = (value) => escHtml(value);
  const view = document.getElementById("view");
  const sidebar = document.getElementById("sidebar");

  function stableId(url, prefix) {
    let hash = 7;
    for (const char of String(url || "")) hash = (hash * 31 + char.charCodeAt(0)) | 0;
    return (prefix || "synced") + "-" + Math.abs(hash);
  }

  function cleanTitle(value) {
    return String(value || "同步研究")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s*[|｜]\s*美投\s*$/, "")
      .replace(/\s+-\s+美投\s*$/, "")
      .replace(/[;；]\s*/g, "；")
      .replace(/\s+/g, " ")
      .trim() || "同步研究";
  }

  function stripArticleMeta(value) {
    return String(value || "")
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter(Boolean)
      .filter((line) => !/^\d{1,3}:\d{2}$/.test(line))
      .filter((line) => !/^\d+(?:\.\d+)?k$/i.test(line))
      .filter((line) => !/^\d+\s*(?:天前|小时前|天|小时)$/.test(line))
      .filter((line) => !/^\d{4}\/\d{2}\/\d{2}$/.test(line))
      .filter((line) => !/^已同步YT$/.test(line))
      .filter((line) => !/^\+\s*\d+$/.test(line))
      .filter((line) => !/^(?:挖掘新机会|行业机会|每月宏观报告|每日报告|其他|估值分析|业务调研|5\+2分析|事件解析|ETF主动选股|ETF被动定投|ETF学堂)$/.test(line))
      .join(" ")
      .trim();
  }

  function deriveTitle(raw, synced) {
    const candidate = cleanTitle(synced?.title || raw?.title || "");
    if (candidate && candidate !== "同步研究") return candidate;
    return cleanTitle(stripArticleMeta(raw?.text || synced?.text || ""));
  }

  function inferTag(raw, synced, kind) {
    const candidate = String(raw?.tag || synced?.meta?.tag || "").trim();
    if (candidate && !/^\d+$/.test(candidate) && candidate !== "+") return candidate;
    const text = String(synced?.title || raw?.text || "");
    const ticker = text.match(/\b(?:NVDA|SPCX|MSFT|PLTR|SHOP|PM|COIN|MRVL|TSM|MU|QQQ|SPY|TQQQ|NFLX|TSLA|SNOW|ORCL|META)\b/i)?.[0];
    if (ticker) return ticker.toUpperCase();
    if (kind === "news") return "每日市场";
    if (kind === "etf") return "ETF机会";
    if (kind === "macro") return "宏观";
    if (kind === "opportunities") return "行业机会";
    return "文字稿";
  }

  function inferGroup(kind, raw, existing) {
    if (existing?.group && existing.group !== "同步研究" && existing.group !== "最新研究") return existing.group;
    const text = String(raw?.text || "");
    const labels = ["每月宏观报告", "每日报告", "行业机会", "估值分析", "业务调研", "5+2分析", "事件解析", "ETF主动选股", "ETF被动定投", "ETF学堂"];
    return moduleGroups[kind] || labels.find((label) => text.includes(label)) || "同步研究";
  }

  function meaningfulLead(synced, raw) {
    const transcript = String(synced?.transcript || "").trim();
    if (transcript) return transcript.split(/\n+/).map((line) => line.trim()).filter((line) => line.length > 20)[0]?.slice(0, 190) || transcript.slice(0, 190);
    const text = stripArticleMeta(synced?.text || raw?.text || "");
    return text.slice(0, 190) || "原页面未公开文字稿，已保留页面摘要、封面和公开互动信息。";
  }

  function pageFor(kind) {
    const target = aliases[kind] || kind;
    return window.__mirrorSync?.pages?.find((page) => page.type === target) || null;
  }

  function transcriptFor(url) {
    return window.__mirrorSync?.articleTranscripts?.find((item) => item.url === url) || null;
  }

  function imageSource(image) {
    return image?.local || image?.src || image?.url || "";
  }

  function normalizeChange(raw) {
    const value = String(raw || "");
    const match = value.match(/[+-]?\s*[\d.]+%/);
    if (!match) return "";
    let result = match[0].replace(/\s+/g, "");
    if (!/^[+-]/.test(result)) result = /▼|-/.test(value) ? "-" + result : "+" + result;
    return result;
  }

  function normalizeStock(raw, existing) {
    const text = String(raw?.text || "");
    const prices = [...text.matchAll(/\$[\d,.]+/g)].map((match) => match[0]);
    const rangeMatch = text.match(/(\$?\s*[\d,.]+\s*[–—-]\s*\$?\s*[\d,.]+)/);
    const riskMatch = text.match(/([1-5])级/);
    const nameMatch = text.match(/\n([^\n$]+)\n\$[\d,.]+/);
    const stance = ["非常积极", "积极", "观察", "谨慎", "非常谨慎"].find((item) => text.includes(item));
    const track = ["深度跟踪", "重点跟踪"].find((item) => text.includes(item));
    const date = text.match(/\d{4}\/\d{2}\/\d{2}/)?.[0] || raw?.updatedAt || "";
    const ticker = String(raw?.ticker || existing?.ticker || "").toUpperCase();
    const override = sourceQuoteOverrides[ticker] || {};
    const result = existing || { ticker };
    result.ticker = ticker;
    result.source = raw?.url || result.source || (ticker ? SOURCE_ORIGIN + "/stock/" + ticker : "");
    result.sourceText = text;
    result.name = nameMatch?.[1]?.trim() || raw?.name || result.name || ticker;
    result.price = (prices[0] || raw?.price || result.price || "").replace(/^\$/, "");
    result.target = override.target || prices[1] || raw?.target || result.target || "--";
    result.range = override.range || rangeMatch?.[1]?.replace(/\s+/g, " ") || raw?.range || result.range || "--";
    result.change = normalizeChange(raw?.change || text) || normalizeChange(result.change) || "";
    result.stance = override.stance || stance || raw?.stance || result.stance || "观察";
    result.risk = override.risk || Number(raw?.risk) || Number(riskMatch?.[1]) || Number(result.risk) || 0;
    result.sector = raw?.sector || result.sector || (text.match(/(?:半导体|航空航天|流媒体|电动车|软件|券商|云计算|金融|医疗|消费电子|通信设备|工业|零售|互联网|社交媒体)/)?.[0] || "--");
    result.track = override.track || raw?.track || result.track || track || "--";
    result.updated = override.updated || raw?.updatedAt || result.updated || date;
    result.quoteTime = override.quoteTime || result.quoteTime || "";
    result.previousClose = override.previousClose || result.previousClose || "";
    result.open = override.open || result.open || "";
    result.low = override.low || result.low || "";
    result.high = override.high || result.high || "";
    result.week52 = override.week52 || result.week52 || "";
    result.pe = override.pe || result.pe || "";
    result.trackDuration = override.trackDuration || result.trackDuration || "";
    result.buyReasons = override.buyReasons || result.buyReasons || [];
    result.sellRisks = override.sellRisks || result.sellRisks || [];
    return result;
  }

  function normalizeArticle(raw, kind, synced) {
    const source = raw?.url || synced?.url || "";
    if (!source) return null;
    let item = Array.isArray(window.articles) ? window.articles.find((article) => article.source === source) : null;
    if (!item && typeof articles !== "undefined") item = articles.find((article) => article.source === source);
    if (!item) item = { id: stableId(source, "synced"), source };
    const transcript = String(synced?.transcript || "").trim();
    const snapshotOverride = sourceArticleOverrides[source] || {};
    const rawDate = raw?.date || "";
    const syncedDate = synced?.meta?.date || "";
    item.source = source;
    item.detailFile = raw?.detailFile || synced?.detailFile || item.detailFile || "";
    item.title = deriveTitle(raw, synced) || item.title || "同步研究";
    item.tag = inferTag(raw, synced, kind);
    item.group = inferGroup(kind, raw, item);
    item.date = snapshotOverride.date || syncedDate || rawDate || item.date || "";
    item.views = synced?.meta?.views || raw?.views || item.views || "";
    item.duration = synced?.meta?.duration || raw?.duration || item.duration || "";
    item.lead = meaningfulLead(synced, raw);
    item.blocks = Array.isArray(synced?.transcriptBlocks) ? synced.transcriptBlocks : (item.blocks || []);
    item.body = transcript ? transcript.split(/\n{2,}/).filter(Boolean) : (item.body || []);
    item.images = Array.isArray(synced?.images) && synced.images.length
      ? synced.images
      : Array.isArray(raw?.images) && raw.images.length ? raw.images : (item.images || []);
    item.comments = Array.isArray(synced?.comments) ? synced.comments : (item.comments || []);
    item.restricted = Boolean(synced?.restricted) && !transcript;
    item.sourceText = synced?.text || raw?.text || item.sourceText || "";
    item.modules = Array.from(new Set([...(item.modules || []), kind]));
    return item;
  }

  function applySnapshot(data, options) {
    if (!data || !Array.isArray(data.pages)) return;
    const full = Boolean(options?.full || (data.articleTranscripts || []).length);
    window.__mirrorSync = data;
    const rawByUrl = new Map();
    const moduleByUrl = new Map();
    for (const page of data.pages) {
      const kind = page.type;
      for (const raw of page.articles || []) {
        if (!raw?.url) continue;
        rawByUrl.set(raw.url, raw);
        if (!moduleByUrl.has(raw.url)) moduleByUrl.set(raw.url, []);
        moduleByUrl.get(raw.url).push(kind);
      }
    }
    const transcriptByUrl = new Map((data.articleTranscripts || []).map((item) => [item.url, item]));
    const ordered = [];
    const seen = new Set();
    const pushArticle = (url, kind) => {
      if (!url || seen.has(url)) return;
      const item = normalizeArticle(rawByUrl.get(url) || { url }, kind || moduleByUrl.get(url)?.[0] || "research", transcriptByUrl.get(url));
      if (!item) return;
      seen.add(url);
      ordered.push(item);
    };
    for (const page of data.pages) for (const raw of page.articles || []) pushArticle(raw.url, page.type);
    for (const transcript of data.articleTranscripts || []) pushArticle(transcript.url, moduleByUrl.get(transcript.url)?.[0] || "research");
    if (typeof articles !== "undefined") {
      for (const existing of articles) {
        if (!existing?.source || seen.has(existing.source)) continue;
        const normalized = normalizeArticle(existing, existing.group || "research", transcriptByUrl.get(existing.source));
        if (normalized) { seen.add(normalized.source); ordered.push(normalized); }
      }
      articles.splice(0, articles.length, ...ordered);
      // Normalize a copied upstream `/tofu/<id>` hash to the mirror card id.
      // This keeps the legacy article renderer pointed at the right record
      // while retaining the original URL in the source link.
      if (state?.route === "article" && state.article) {
        const resolved = articleById(state.article);
        if (resolved?.id) state.article = resolved.id;
      }
    }
    const existingStocks = new Map((typeof stocks !== "undefined" ? stocks : []).map((stock) => [stock.ticker, stock]));
    const trackingPage = data.pages.find((page) => page.type === "tracking");
    for (const raw of trackingPage?.stocks || []) normalizeStock(raw, existingStocks.get(String(raw.ticker || "").toUpperCase()));
    const stockList = [...existingStocks.values()].filter((stock) => stock?.ticker);
    stockList.sort((a, b) => {
      const ai = preferredStockOrder.indexOf(a.ticker);
      const bi = preferredStockOrder.indexOf(b.ticker);
      return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
    });
    if (typeof stocks !== "undefined") stocks.splice(0, stocks.length, ...stockList);
    const homeText = String(data.pages.find((page) => page.type === "home")?.text || "");
    const subscriber = homeText.match(/\n(\d{4,})\n订阅用户/)?.[1] || "8709";
    window.__mirrorMeta = {
      version: VERSION,
      updatedAt: data.updatedAt || "",
      full,
      subscriber,
      pages: data.pages.length,
      articles: ordered.length,
      transcripts: (data.articleTranscripts || []).length,
      stalePages: data.sync?.stalePages || [],
      staleArticles: data.sync?.staleArticles || [],
      authConfigured: Boolean(data.sync?.authConfigured ?? data.authConfigured),
      authenticated: Boolean(data.sync?.authenticated ?? data.authenticated),
      authExpired: Boolean(data.sync?.authExpired ?? data.authExpired),
      syncStatus: data.sync?.status || data.status || "ok",
      videoLists: data.sync?.videoLists || null,
      warning: data.sync?.warning || data.warning || "",
    };
    window.dispatchEvent(new CustomEvent("mirror:data-ready", { detail: window.__mirrorMeta }));
    if (window.__mirrorParityReady && typeof window.render === "function") scheduleRender();
  }

  function moduleArticles(kind) {
    const target = aliases[kind] || kind;
    const page = pageFor(target);
    if (!page) return typeof articles !== "undefined" ? articles : [];
    const transcripts = new Map((window.__mirrorSync?.articleTranscripts || []).map((item) => [item.url, item]));
    return (page.articles || []).map((raw) => normalizeArticle(raw, target, transcripts.get(raw.url))).filter(Boolean);
  }

  function articleById(id) {
    if (typeof articles === "undefined") return null;
    const target = String(id || "");
    const exact = articles.find((article) => article.id === target);
    if (exact) return exact;
    // Shared source links sometimes carry the upstream tofu id instead of
    // the mirror's stable `synced-*` id. Resolve both forms to the same card
    // so a copied source URL still opens the intended article.
    const bySourceId = articles.find((article) => {
      const sourceId = String(article.source || "").match(/\/tofu\/([^/?#]+)/)?.[1];
      const detailId = String(article.detailFile || "").match(/\/([^/]+)\.json$/)?.[1];
      return sourceId === target || detailId === target;
    });
    return bySourceId || articles[0] || null;
  }

  function formatSyncTime(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "等待同步";
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(date);
    } catch (_) { return date.toLocaleString("zh-CN"); }
  }

  function syncChip() {
    const meta = window.__mirrorMeta || {};
    const failedLists = Number(meta.videoLists?.failed?.length || 0);
    const degraded = Boolean(meta.authExpired || meta.syncStatus === "degraded" || meta.stalePages?.length || meta.staleArticles?.length || failedLists);
    const status = degraded
      ? (meta.authExpired ? "授权已失效 · 当前展示公开内容" : "同步异常 · 展示最近成功快照")
      : (meta.full ? "完整正文已同步" : (routeNeedsFullSnapshot(state?.route) ? "正文正在同步" : "栏目数据已同步 · 正文按需加载"));
    const title = meta.warning || (degraded ? "部分内容可能不是最新快照" : ("同步自 " + SOURCE_ORIGIN));
    return '<span class="parity-sync-chip' + (degraded ? ' is-degraded' : '') + '" title="' + escape(title) + '">' + escape(status + " · " + formatSyncTime(meta.updatedAt)) + '</span>';
  }

  function cardArticle(article) {
    const item = article || {};
    const image = (item.images || []).find((entry) => entry.kind === "poster") || (item.images || []).find((entry) => imageSource(entry));
    const src = imageSource(image);
    const restricted = Boolean(item.restricted && !(item.blocks || []).length && !(item.body || []).length);
    const label = restricted ? "页面摘要" : (item.tag || "文字稿");
    return '<article class="parity-article-card" data-article="' + escape(item.id || "") + '" role="button" tabindex="0" aria-label="打开 ' + escape(item.title || "同步研究") + '">' +
      '<div class="parity-article-thumb">' + (src ? '<img src="' + escape(src) + '" alt="" loading="lazy" decoding="async" onerror="this.remove()">' : "") +
      '<span class="parity-article-label">' + escape(label) + ' · ' + escape(item.group || "同步研究") + (restricted ? " · 公开摘要" : "") + '</span></div>' +
      '<div class="parity-article-body"><h3>' + escape(item.title || "同步研究") + '</h3><p>' + escape(item.lead || "") + '</p>' +
      '<div class="parity-article-meta"><span>' + escape(item.date || "同步快照") + (item.views ? " · " + escape(item.views) + " 阅读" : "") + '</span><span>' + escape(item.duration || label) + '</span></div></div></article>';
  }

  function sectionHead(title, description, route) {
    return '<div class="parity-section-head"><div><h2>' + escape(title) + '</h2><p>' + escape(description || "") + '</p></div>' +
      (route ? '<button class="parity-more" data-route="' + escape(route) + '">更多 ›</button>' : "") + '</div>';
  }

  function articleGrid(items, className) {
    if (!items.length) return '<div class="module-empty">该栏目暂无可公开同步的文字稿。</div>';
    return '<div class="' + (className || "parity-grid-3") + '">' + items.map(cardArticle).join("") + '</div>';
  }

  function stockChangeClass(change) { return String(change || "").startsWith("+") ? "up" : "down"; }

  function stockRow(stock, compact) {
    const change = String(stock.change || "");
    const risk = Number(stock.risk || 0);
    if (compact) {
      return '<tr class="parity-stock-row" data-stock="' + escape(stock.ticker) + '"><td><button type="button" class="parity-follow" data-follow="' + escape(stock.ticker) + '" aria-label="关注 ' + escape(stock.ticker) + '">' +
        (typeof state !== "undefined" && state.followed?.has(stock.ticker) ? "★" : "⊕") + '</button></td><td class="parity-ticker">' + escape(stock.ticker) + '</td><td class="parity-price">$' + escape(stock.price || "--") + '</td><td><span class="parity-change ' + stockChangeClass(change) + '">' + escape(change || "--") + '</span></td><td><span class="parity-stance ' + (stock.stance === "谨慎" || stock.stance === "非常谨慎" ? "care" : stock.stance === "观察" ? "watch" : "") + '">' + escape(stock.stance || "观察") + '</span></td></tr>';
    }
    return '<tr class="parity-stock-row" data-stock="' + escape(stock.ticker) + '"><td><button type="button" class="parity-follow" data-follow="' + escape(stock.ticker) + '" aria-label="关注 ' + escape(stock.ticker) + '">' +
      (typeof state !== "undefined" && state.followed?.has(stock.ticker) ? "★" : "⊕") + '</button></td><td class="parity-ticker">' + escape(stock.ticker) + '</td><td>' + escape(stock.name || "--") + '</td><td class="parity-price">$' + escape(stock.price || "--") + '</td><td><span class="parity-change ' + stockChangeClass(change) + '">' + escape(change || "--") + '</span></td><td><span class="parity-stance ' + (stock.stance === "谨慎" || stock.stance === "非常谨慎" ? "care" : stock.stance === "观察" ? "watch" : "") + '">' + escape(stock.stance || "观察") + '</span></td><td>' + escape(stock.target || "--") + '</td><td>' + escape(stock.range || "--") + '</td><td class="parity-risk r' + escape(risk) + '">' + (risk ? "▮".repeat(Math.min(risk, 5)) + " " : "") + escape(risk ? risk + "级" : "--") + '</td><td>' + escape(stock.sector || "--") + '</td><td>' + escape(stock.track || "--") + '</td><td>' + escape(stock.updated || "--") + '</td></tr>';
  }

  function homeQuestions() {
    try {
      if (typeof questionRecords === "function") return questionRecords().slice(0, 3);
    } catch (_) {}
    const text = String(pageFor("questions")?.text || "");
    const known = ["为什么相比较nvda和tsm来说，msft amzn goog云类大科技是strong buy，理由是什么呢", "想请教美投君对FICO和 bureau的看法", "加息对美股的影响"];
    return known.filter((title) => text.includes(title)).map((title) => ({ title, body: "公开问答摘要已同步，打开问答栏目查看完整互动。", author: "美投分析师" }));
  }

  function homePulse(kind) {
    try {
      if (typeof pulseRecords === "function") return pulseRecords(kind).slice(0, 3);
    } catch (_) {}
    return [];
  }

  function home() {
    const homePage = pageFor("home");
    const latest = moduleArticles("home").slice(0, 3);
    const opportunities = moduleArticles("opportunities").slice(0, 3);
    const macro = moduleArticles("macro").slice(0, 3);
    const news = moduleArticles("news").slice(0, 4);
    const tracked = (typeof stocks !== "undefined" ? stocks : []).slice(0, 5);
    const questions = homeQuestions();
    const pulses = homePulse("market-diagnosis");
    const sourceText = String(homePage?.text || "");
    const subscriber = window.__mirrorMeta?.subscriber || sourceText.match(/\n(\d{4,})\n订阅用户/)?.[1] || "8709";
    // 只保留能落到站内栏目的横幅（股票 / 研究）。原站的会员推广、APP 下载类
    // 横幅不展示 —— 镜像站不做主站获客，且它们原先会被接到订阅弹窗上。
    const banners = (homePage?.images || []).filter((image) => /home_top_banner_(?:stock|youtube)_/i.test(image.source || ""));
    const bannerGroups = [];
    const bannerByKey = new Map();
    for (const image of banners) {
      const source = String(image.source || "");
      const key = source.replace(/_(?:pc|mobile)(?=\.[a-z0-9]+(?:[?#]|$))/i, "");
      if (!bannerByKey.has(key)) {
        const group = { key, pc: null, mobile: null };
        bannerByKey.set(key, group);
        bannerGroups.push(group);
      }
      const group = bannerByKey.get(key);
      if (/_mobile\./i.test(source)) group.mobile = image;
      else group.pc = image;
    }
    const initialBanner = 0;
    const bannerMarkup = bannerGroups.length ?
      '<section class="parity-hero parity-banner-hero" aria-label="美投精选活动"><div class="parity-banner-viewport">' +
      bannerGroups.map((group, index) => {
        const desktop = group.pc || group.mobile;
        const mobile = group.mobile || desktop;
        const source = String(desktop?.source || "");
        const target = /stock_/i.test(source) ? "tracking" : /youtube_/i.test(source) ? "research" : "membership";
        const action = target === "membership" ? ' data-subscribe="banner"' : ' data-banner-target="' + target + '"';
        return '<button type="button" class="parity-banner-slide ' + (index === initialBanner ? "active" : "") + '" data-banner-slide="' + index + '" aria-hidden="' + (index === initialBanner ? "false" : "true") + '"' + action + ' aria-label="打开精选内容 ' + (index + 1) + '"><picture>' +
          (mobile ? '<source media="(max-width: 760px)" srcset="' + escape(imageSource(mobile)) + '">' : "") +
          (desktop ? '<img src="' + escape(imageSource(desktop)) + '" alt="美投精选内容" decoding="async" fetchpriority="high">' : "") +
          '</picture></button>';
      }).join("") +
      '<div class="parity-banner-dots" role="tablist" aria-label="精选内容切换">' + bannerGroups.map((_, index) => '<button type="button" class="parity-banner-dot ' + (index === initialBanner ? "active" : "") + '" data-parity-banner-index="' + index + '" role="tab" aria-selected="' + (index === initialBanner ? "true" : "false") + '" aria-label="第 ' + (index + 1) + ' 张"></button>').join("") + '</div>' +
      '<span class="sr-only">视频入口已按项目约定替换为文字稿；横幅中的研究内容可进入对应文字阅读页面。</span></div></section>' :
      '<section class="parity-hero parity-banner-hero"><div class="parity-banner-viewport parity-banner-empty"><div class="parity-hero-inner"><div class="parity-eyebrow">美投 · 研究镜像</div><h1>100万人，从这里认识美投</h1><p>公开研究、股票跟踪和文字稿阅读集中在这里。</p></div></div></section>';
    const topicCards = [
      ["AI", "视频集 · 35 期", "近期上新 · 最前沿科技趋势与投资机会"],
      ["看懂川普", "视频集 · 10 期", "看懂美股最大变量，把握市场风险和机会"],
      ["看懂美债", "视频集 · 4 期", "理解利率、美元和美股估值的联动"],
    ];
    const metricCards = [
      ["美投 AI 泡沫指数", "52", "更新于 4 天前", "融合多维数据，综合评估美股 AI 泡沫水平。"],
      ["美投 AI 基本面风险指数", "40", "更新于 4 天前", "融合多维数据，综合评估美股 AI 基本面风险。"],
    ];
    const questionMarkup = questions.length ? questions.map((item) => '<article class="qa parity-home-qa"><div class="qa-head"><span class="parity-badge">' + escape(item.tag || "投资问答") + '</span><small>' + escape(item.date || "公开摘要") + '</small></div><button class="qa-question" data-route="qa"><strong>' + escape(item.title || "投资问答") + '</strong><span>›</span></button><p>' + escape(item.body || "公开问答摘要已同步。") + '</p><small>' + escape(item.author || "美投分析师") + '</small></article>').join("") : '<div class="module-empty">问答摘要正在同步。</div>';
    const pulseMarkup = pulses.length ? pulses.map((item) => '<article class="parity-topic"><b>' + escape(item.ticker || "市场") + ' <span class="parity-change ' + stockChangeClass(item.change) + '">' + escape(item.change || "") + '</span></b><span>' + escape(item.title || "市场诊断摘要") + '</span></article>').join("") : '<article class="parity-topic"><b>市场诊断</b><span>公开异动摘要会在下一次同步后显示。</span></article>';
    return '<div class="parity-home">' +
      bannerMarkup +
      '<section class="parity-stats"><div class="parity-stat"><strong>' + escape(subscriber) + '</strong><span>关注用户（来源快照）</span></div><div class="parity-stat"><strong>150+</strong><span>深度分析视频</span></div><div class="parity-stat"><strong>100+</strong><span>优质社群答疑</span></div><div class="parity-stat"><strong>70%</strong><span>历史盈利胜率</span></div><div class="parity-stat"><strong>15</strong><span>押中暴涨个股</span></div><div class="parity-stat"><strong>20</strong><span>应对市场大回撤</span></div></section>' +
      '<section class="parity-section">' + sectionHead("最新研究", "每周一、周六固定更新，不定期加更 · 视频位置由文字稿阅读器替代", "research") + articleGrid(latest, "parity-grid-3") + '</section>' +
      '<section class="parity-section"><div class="parity-panel">' + sectionHead("美投跟踪股票", "主打个股调研，持续跟踪基本面变化", "tracking") + '<div class="parity-table-wrap"><table class="parity-table"><thead><tr><th>关注</th><th>股票代码</th><th>当前价格</th><th>涨跌幅</th><th>观点倾向</th></tr></thead><tbody>' + (tracked.length ? tracked.map((stock) => stockRow(stock, true)).join("") : '<tr><td colspan="5">暂无股票快照</td></tr>') + '</tbody></table></div></div></section>' +
      '<section class="parity-section">' + sectionHead("行业机会", "选股必备！发掘行业最佳机会", "opportunities") + articleGrid(opportunities, "parity-grid-3") + '</section>' +
      '<section class="parity-section">' + sectionHead("每月宏观报告", "每月一期，持续追踪解读宏观市场", "macro") + articleGrid(macro, "parity-grid-3") + '</section>' +
      '<section class="parity-section">' + sectionHead("市场热点", "从基础知识到专业分析，掌握市场前沿热点", "research") + '<div class="parity-topic-grid">' + topicCards.map((item) => '<article class="parity-topic"><b>· ' + escape(item[0]) + ' ·</b><span>' + escape(item[1]) + '</span><span>' + escape(item[2]) + '</span></article>').join("") + '</div></section>' +
      '<section class="parity-section"><div class="parity-quick-grid"><div class="parity-panel">' + sectionHead("每日新闻总结", "周一至周五，每晚 9 点更新（美东时间）", "news") + '<div class="parity-quick-list">' + (news.length ? news.map((item) => '<div class="parity-news-row" data-article="' + escape(item.id) + '" role="button" tabindex="0"><time>' + escape(item.date || "今日") + '</time><strong>' + escape(item.title) + '</strong><small>' + escape(item.views || "文字稿") + '</small></div>').join("") : '<div class="module-empty">暂无新闻摘要</div>') + '</div></div><div class="parity-panel">' + sectionHead("个股异动解读", "梳理个股异动原因，快速了解市场分歧", "market-diagnosis") + '<div class="parity-topic-grid">' + pulseMarkup + '</div></div></div></section>' +
      '<section class="parity-section"><div class="parity-panel">' + sectionHead("美投独家数据", "用数据视角，帮助你理性判断市场状态", "market-diagnosis") + '<div class="parity-metric-grid">' + metricCards.map((item) => '<article class="parity-metric"><div class="parity-metric-head"><h3>' + escape(item[0]) + '</h3><small>' + escape(item[2]) + '</small></div><div class="parity-metric-value">' + escape(item[1]) + '</div><p>' + escape(item[3]) + '</p><span class="parity-lock">原站未公开完整内容</span></article>').join("") + '</div></div></section>' +
      '<section class="parity-section"><div class="parity-panel">' + sectionHead("每日精选问答", "来自 PRO+ 问答区的真实提问与专业解答", "qa") + '<div class="parity-grid-3">' + questionMarkup + '</div></div></section>' +
      '<section class="parity-section"><div class="parity-panel">' + sectionHead("市场深跌诊断", "监控市场波动，帮助你安心应对风险", "market-diagnosis") + '<div class="parity-topic-grid">' + pulseMarkup + '</div></div></section>' +
      '<div class="parity-disclosure"><strong>镜像说明：</strong>本页面只同步原站公开页面、公开文字稿、图片和摘要；视频播放、源站未公开内容以及支付流程仍由 Daocaijing 账号中心管理。行情数值以页面右上角的来源快照时间为准。</div>' +
      '<footer class="parity-footer"><div><b>美投</b> · 文字研究镜像</div><div><a href="' + SOURCE_ORIGIN + '" target="_blank" rel="noreferrer">打开原站</a><a href="' + MIRROR_ORIGIN + '" target="_blank" rel="noreferrer">账号中心</a><a href="#announcements">公告</a><a href="#qa">帮助与问答</a></div><div>Meitou mirror · public source snapshot · ' + escape(formatSyncTime(window.__mirrorMeta?.updatedAt)) + '</div></footer>' +
      '</div>';
  }

  function filteredStocks() {
    const list = (typeof stocks !== "undefined" ? stocks : []).filter((stock) => {
      const category = typeof state !== "undefined" ? state.stockCategory : "全部";
      if (typeof state !== "undefined" && state.stockTab === "follow" && !state.followed.has(stock.ticker)) return false;
      if (category === "MAG7" && !mag7.has(stock.ticker)) return false;
      if (category === "半导体" && !/半导体/.test(stock.sector || "")) return false;
      if (category === "AI应用" && !/AI|互联网|芯片|半导体|云/.test(stock.sector || "")) return false;
      if (category === "非AI" && /AI|互联网|芯片|半导体|云/.test(stock.sector || "")) return false;
      if (category === "消费" && !/消费|零售/.test(stock.sector || "")) return false;
      if (category === "热门" && !["NVDA", "SPCX", "TSLA", "SNOW", "PLTR", "MSFT"].includes(stock.ticker)) return false;
      if (typeof state !== "undefined" && state.riskFilter && Number(stock.risk) !== Number(state.riskFilter)) return false;
      if (typeof state !== "undefined" && state.stanceFilter && stock.stance !== state.stanceFilter) return false;
      const query = typeof state !== "undefined" ? String(state.query || "").trim().toLowerCase() : "";
      return !query || [stock.ticker, stock.name, stock.sector, stock.stance].join(" ").toLowerCase().includes(query);
    });
    if (typeof state !== "undefined" && state.sortKey && state.sortKey !== "order") {
      const key = state.sortKey;
      const direction = state.sortDir || 1;
      list.sort((a, b) => {
        const av = ["price", "risk", "change"].includes(key) ? Number(String(a[key] || "").replace(/[^0-9.-]/g, "")) : String(a[key] || "");
        const bv = ["price", "risk", "change"].includes(key) ? Number(String(b[key] || "").replace(/[^0-9.-]/g, "")) : String(b[key] || "");
        return (typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), "zh-CN", { numeric: true })) * direction;
      });
    }
    return list;
  }

  function tracking() {
    const categories = ["全部", "我的关注", "热门", "MAG7", "半导体", "AI应用", "非AI", "消费"];
    const rows = filteredStocks();
    const latest = moduleArticles("tracking")[0] || moduleArticles("macro")[0];
    const related = moduleArticles("tracking").slice(1, 9);
    const active = typeof state !== "undefined" && state.stockTab === "follow" ? "我的关注" : (state?.stockCategory || "全部");
    const tabs = categories.map((category) => '<button class="' + (active === category ? "active" : "") + '" data-stock-tab="' + escape(category) + '">' + escape(category) + '</button>').join("");
    const risks = [1, 2, 3, 4, 5].map((risk) => '<button class="' + (state?.riskFilter === String(risk) ? "active" : "") + '" data-risk="' + risk + '">' + risk + '级</button>').join('<span>/</span>');
    const stances = ["非常积极", "积极", "观察", "谨慎", "非常谨慎"].map((stance) => '<button class="' + (state?.stanceFilter === stance ? "active" : "") + '" data-stance="' + escape(stance) + '">' + escape(stance) + '</button>').join('<span>/</span>');
    return '<div class="page parity-tracking-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>股票机会与跟踪</span></div><h1>股票机会与跟踪</h1><p>持续跟踪观点、目标价、估值区间和风险等级。</p></div>' + syncChip() + '</div>' +
      '<div class="tabs"><button class="active" data-route="tracking">美投跟踪</button><button data-route="opportunities">挖掘新机会</button></div>' +
      '<section class="notice card"><div class="notice-main"><strong><span class="notice-check">✓</span>观点已更新</strong><p>公开文字稿与股票快照同步到这里</p></div><div class="cover"' + (latest?.images?.[0] ? ' style="background-image:url(\'' + escape(imageSource(latest.images[0])).replace(/&#39;/g, "\\'") + '\');background-size:cover;background-position:center"' : "") + '>' + escape(latest?.tag || "美投跟踪") + '<br>公开摘要</div><div><div class="notice-title" data-article="' + escape(latest?.id || "") + '" role="button" tabindex="0">' + escape(latest?.title || "最新观点更新") + '</div><div class="notice-date">' + escape(latest?.date || formatSyncTime(window.__mirrorMeta?.updatedAt)) + '</div><span class="tag">月度总结</span><span class="tag">文字稿</span></div><div class="align-right"><button class="more-link" data-route="research">往期研究 ›</button></div></section>' +
      '<section class="panel"><div class="filter-tabs">' + tabs + '</div><div class="filter-row"><div class="filter-group">风险：' + risks + '</div><div class="filter-group">观点倾向：' + stances + '</div><div class="updated">数据更新时间<br><b>' + escape(formatSyncTime(window.__mirrorMeta?.updatedAt)) + ' CST</b></div></div><div class="parity-disclosure" style="margin:0 0 14px">股票价格和涨跌幅来自同步页面公开快照；如果账号服务返回公开行情，会在这里显示“公开行情快照”标签。</div><div class="table-wrap"><table><thead><tr><th>关注</th>' + [["ticker", "股票代码"], ["name", "公司名称"], ["price", "当前价格"], ["change", "涨跌幅"], ["stance", "观点倾向"], ["target", "目标价"], ["range", "估值区间"], ["risk", "风险等级"], ["sector", "行业"], ["track", "跟踪力度"], ["updated", "观点更新于"]].map((item) => '<th><button data-sort="' + item[0] + '">' + item[1] + ' <span>↕</span></button></th>').join("") + '</tr></thead><tbody>' + (rows.length ? rows.map((stock) => stockRow(stock, false)).join("") : '<tr><td colspan="12" class="module-empty">当前筛选下没有股票</td></tr>') + '</tbody></table></div><div class="related"><h3>相关研究</h3><div class="research-tabs">' + ["所有", "财报跟踪", "业务调研", "事件解析", "5+2分析", "估值分析", "其他研究"].map((type, index) => '<button class="' + (index === 0 ? "active" : "") + '" data-research-filter="' + escape(type) + '">' + escape(type) + '</button>').join("") + '</div><div class="research-list">' + (related.length ? related.map((item) => '<div class="research-item" data-article="' + escape(item.id) + '" data-research-group="' + escape(item.group || "") + '"><span class="duration">' + escape(item.duration || "") + '</span><div><strong>' + escape(item.title) + '</strong><small>' + escape(item.views || "") + ' 阅读　' + escape(item.group || "同步研究") + '　' + escape(item.tag || "文字稿") + '</small></div></div>').join("") : '<div class="module-empty">暂无相关研究</div>') + '</div></div></section></div>';
  }

  function opportunities() {
    const list = moduleArticles("opportunities");
    const chips = ["全部", "芯片", "AI", "银行", "数字货币", "稳定币", "机器人", "医疗", "公共事业", "网络安全", "量子计算", "支付", "电力", "减肥药", "生物医药", "房地产", "军工", "零售", "奢侈品", "宠物", "清洁能源", "NFT", "能源", "医美", "元宇宙", "游戏", "大麻", "油罐"];
    return '<div class="page parity-module-page"><div class="crumb"><span class="back">‹</span><span>股票机会与跟踪</span></div><div class="tabs"><button data-route="tracking">美投跟踪</button><button class="active" data-route="opportunities">挖掘新机会</button></div><div class="parity-route-head"><div><h1>挖掘新机会</h1><p>选股必备！发掘行业最佳机会</p></div>' + syncChip() + '</div><section class="parity-panel"><div class="chips">' + chips.map((chip, index) => '<button class="' + (index === 0 ? "active" : "") + '" data-op-chip="' + escape(chip) + '">' + escape(chip) + '</button>').join("") + '</div>' + articleGrid(list.slice(0, 12), "parity-grid-3") + '</section></div>';
  }

  // The source trades page is a rollover feed rather than a transcript list.
  // Render its public cards from the synced page text so this module does not
  // appear empty when the dedicated trade API is unavailable.
  // 同步下来的原站文本里混着会员/订阅类营销话术，镜像站不做主站获客，统一剥掉。
  function stripPromo(value) {
    return String(value || "")
      .replace(/\s+/g, " ")
      .replace(/想看美投君[^ 　]*|订阅解锁[^ 　]*|开启 ?\d+ ?天免费试用|免费试用|立即订阅|开通服务[^ 　]*|下载APP|为你推荐|登录 \| 注册/g, "")
      .replace(/\s{2,}/g, " ")
      .trim();
  }

  // PRO+ 实战操作数据来自 sync.mjs 的 cacheableSearchableHandanList 抓取
  // （payload.trades.posts）。同步失败或尚未落地时退回页面文本解析。
  // 排版复刻原站：卡片头部"美投君/日期/动作"小字 + 大号 ticker + 类型标签，
  // 关联持仓是五列表格（代号/类型/操作/数量/价格），期权合约格式 Sep18'26 470 PUT。
  function parityTradeView() {
    const posts = window.__mirrorSync?.trades?.posts || [];
    const actionCN = { ROLLOVER: "Rollover", OPEN: "开仓", CLOSE: "平仓", ADD: "加仓", ADJUST: "调整", UPDATE: "更新" };
    // 原站期权到期格式 Sep18'26（月日'年）
    const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const contractName = (leg) => {
      // 原站合约行 = ticker 大字 + 下一行 "Sep18'26 470 PUT"（不重复 ticker）
      const parts = String(leg.expirationDate || "").split("/");
      const label = parts.length === 3 ? MONTHS[Number(parts[1]) - 1] + String(Number(parts[2])) + "'" + parts[0].slice(2) : "";
      return [label, leg.strikePrice ?? "--", leg.optionType].filter(Boolean).join(" ");
    };
    const money = (value) => (value === null || value === undefined || value === "") ? "--" : (Number(value) < 0 ? "-$" + Math.abs(value) : "$" + value);
    // 一行持仓腿：代号（含合约）/ 类型 / 操作 / 数量 / 价格 —— 对齐原站关联持仓表
    const legRow = (leg, action) => '<tr><td><b>' + escape(leg.ticker || "--") + '</b>' + (String(leg.positionType || "").toUpperCase() === "OPTION" ? '<span class="trade-contract">' + escape(contractName(leg)) + '</span>' : "") + '</td><td>' + escape(String(leg.positionType || "").toUpperCase() === "OPTION" ? "期权" : "股票") + '</td><td>' + escape(action || (leg.action || "")) + '</td><td>' + escape(leg.volume ?? "--") + '</td><td>' + escape(money(leg.price)) + '</td></tr>';
    const positionTable = (legs, action, groupLabel) => legs.length
      ? '<table class="trade-positions"><thead><tr><th>代号</th><th>类型</th><th>操作</th><th>数量</th><th>价格</th></tr></thead><tbody>'
        + (groupLabel ? '<tr class="trade-pt-group"><td colspan="5">' + escape(groupLabel) + '</td></tr>' : '')
        + legs.map((leg) => legRow(leg, action)).join("") + '</tbody></table>'
      : '<p class="trade-locked">本笔未附持仓明细。</p>';

    // 最新 open 仓位：每只 ticker 取其最新帖子的 previousPositions 快照。
    const openPositionsByTicker = new Map();
    for (const post of posts) {
      const tickers = new Set((post.previousPositions || []).map((leg) => leg.ticker).filter(Boolean));
      for (const ticker of tickers) {
        openPositionsByTicker.set(ticker, (post.previousPositions || []).filter((leg) => leg.ticker === ticker));
      }
    }
    const openLegs = [...openPositionsByTicker.values()].flat().slice(0, 30);
    // open 仓位表对齐原站六列：代号/类型/数量/价格/最近交易时间/关注
    const openPositionTable = openLegs.length
      ? '<table class="trade-positions trade-open-positions"><thead><tr><th>代号</th><th>类型</th><th>数量</th><th>价格</th><th>最近交易时间</th><th>关注</th></tr></thead><tbody>'
        + openLegs.map((leg) => {
          const followed = typeof state !== "undefined" && state.followed?.has(leg.ticker);
          return '<tr><td><b>' + escape(leg.ticker || "--") + '</b>' + (String(leg.positionType || "").toUpperCase() === "OPTION" ? '<span class="trade-contract">' + escape(contractName(leg)) + '</span>' : "") + '</td><td>' + escape(String(leg.positionType || "").toUpperCase() === "OPTION" ? "期权" : "股票") + '</td><td>' + escape(leg.volume ?? "--") + '</td><td>' + escape(money(leg.price)) + '</td><td>' + escape(leg.lastUpdatedAt || "--") + '</td><td><button class="watch-btn mini' + (followed ? " on" : "") + '" data-watch="' + escape(leg.ticker || "") + '">' + (followed ? "★" : "☆") + '</button></td></tr>';
        }).join("")
        + '</tbody></table>'
      : '<p class="trade-locked">当前同步快照里没有 open 仓位数据。</p>';

    const cards = posts.slice(0, 60).map((post, index) => {
      const type = String(post.positionType || (post.transactions?.[0]?.positionType || "STOCK")).toUpperCase();
      const txLegs = post.transactions || [];
      const hasOptionLeg = txLegs.some((tx) => String(tx.positionType || "").toUpperCase() === "OPTION");
      // 原站 tab 语义：期权 = 交易含期权腿；股票 = positionType 为 STOCK 的频道流
      const feed = hasOptionLeg ? "option" : (type === "STOCK" ? "stock" : "channel");
      const actionLabel = actionCN[post.sourceAction] || post.sourceAction || "交易";
      // 原站关联持仓表上方的分组行（如 "MSFT Combo"）
      const linkedPTName = post.linkedPT?.displayName || (txLegs.length > 1 ? (post.ticker || "") + " Combo" : "");
      const detailSections = [];
      if ((post.tradingPhilosophy || []).length) detailSections.push('<div class="trade-detail-block"><h4>交易说明</h4>' + post.tradingPhilosophy.map((line) => '<p>' + escape(line) + '</p>').join("") + '</div>');
      if ((post.tradingPlan || []).length) detailSections.push('<div class="trade-detail-block"><h4>交易计划</h4>' + post.tradingPlan.map((line) => '<p>' + escape(line) + '</p>').join("") + '</div>');
      if (post.riskLevel) detailSections.push('<div class="trade-detail-block"><h4>风险提示（' + escape(post.riskLevel) + '/5）</h4>' + (post.riskWarning || []).map((line) => '<p>' + escape(line) + '</p>').join("") + '</div>');
      const detail = detailSections.join("");
      // 原站图标（内嵌原站同款 SVG path / 图片资源），与 TransactionPost.tsx 结构对齐
      const rolloverIcon = '<svg class="trade-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v3l4-4-4-4v3c-4.42 0-8 3.58-8 8 0 1.57.46 3.03 1.24 4.26L6.7 14.8c-.45-.83-.7-1.79-.7-2.8 0-3.31 2.69-6 6-6m6.76 1.74L17.3 9.2c.44.84.7 1.79.7 2.8 0 3.31-2.69 6-6 6v-3l-4 4 4 4v-3c4.42 0 8-3.58 8-8 0-1.57-.46-3.03-1.24-4.26"></path></svg>';
      const likeIcon = '<svg class="trade-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M16.5 3c-1.74 0-3.41.81-4.5 2.09C10.91 3.81 9.24 3 7.5 3 4.42 3 2 5.42 2 8.5c0 3.78 3.4 6.86 8.55 11.54L12 21.35l1.45-1.32C18.6 15.36 22 12.28 22 8.5 22 5.42 19.58 3 16.5 3m-4.4 15.55-.1.1-.1-.1C7.14 14.24 4 11.39 4 8.5 4 6.5 5.5 5 7.5 5c1.54 0 3.04.99 3.57 2.36h1.87C13.46 5.99 14.96 5 16.5 5c2 0 3.5 1.5 3.5 3.5 0 2.89-3.14 5.74-7.9 10.05"></path></svg>';
      const commentIcon = '<img class="trade-action-icon-img" alt="comment" loading="lazy" width="14" height="14" src="assets/coments_dark.svg">';
      const commentLabel = post.numComment ? "评论 " + escape(post.numComment) : "添加评论";
      return '<article class="trade-card card" data-trade-index="' + index + '" data-trade-type="' + escape(type) + '" data-trade-feed="' + escape(feed) + '">'
        // CardHeader: avatar + 美投君/日期 + 右上动作图标（Rollover 循环箭头）
        + '<div class="trade-head"><img class="trade-avatar" src="assets/mt-channel-logo.jpeg" alt="美投君">'
        + '<div class="trade-meta"><span class="trade-author">美投君</span><span class="trade-date">' + escape(post.createdAt || "") + '</span></div>'
        + '<div class="trade-action-badge">' + rolloverIcon + '<span>' + escape(actionLabel) + '</span></div>'
        + '</div>'
        // CardContent: ticker 大字 + 类型 chip + 关联持仓/订阅提示，全部直出（原站无折叠交互）
        + '<div class="trade-body"><div class="trade-title-row"><p class="trade-ticker">' + escape(post.ticker || "--") + '</p><span class="trade-chip trade-chip-' + escape(type.toLowerCase()) + '">' + escape(type) + '</span></div>'
        + '<div class="trade-detail" id="trade-detail-' + index + '">'
        + (txLegs.length || detail
          ? (txLegs.length ? '<div class="trade-detail-block"><h4>查看关联持仓</h4>' + positionTable(txLegs, null, linkedPTName) + '</div>' : "") + detail
          : '<div class="trade-subscribe"><p>本笔交易的实时持仓明细仅向原站订阅用户披露（镜像同步的可见部分未包含此帖）。</p><a class="primary-inline" href="' + SOURCE_ORIGIN + '/meitouquan/trades" target="_blank" rel="noreferrer">去原站查看</a></div>')
        + '</div></div>'
        // CardActions: 点赞(心形图标) / 评论(svg 图片图标) / 有调整提醒我(实心主色按钮)
        + '<div class="qa-actions trade-actions">'
        + '<button class="trade-react-btn" data-trade-action="like">' + likeIcon + '<span>点赞' + (post.likes ? " " + escape(post.likes) : "") + '</span></button>'
        + '<button class="trade-react-btn" data-trade-action="comment">' + commentIcon + '<span>' + commentLabel + '</span></button>'
        + '<button class="trade-follow-btn" data-trade-action="watch">有调整提醒我</button>'
        + '</div>'
        + '</article>';
    }).join("");
    const emptyCard = posts.length ? "" : '<article class="trade-card card"><div class="trade-head"><h3 class="trade-ticker">--</h3></div><p class="trade-locked">实战操作数据尚未同步完成，稍后会随 15 分钟一次的快照刷新出现。</p></article>';
    // 更新时间格式对齐原站："x 天 HH:MM"
    const tradeUpdated = window.__mirrorSync?.trades?.updatedAt || window.__mirrorMeta?.updatedAt;
    const updatedLabel = (() => {
      if (!tradeUpdated) return "来源页面同步快照";
      const then = new Date(tradeUpdated).getTime();
      if (!Number.isFinite(then)) return "来源页面同步快照";
      const days = Math.floor((Date.now() - then) / 86400000);
      const clock = new Date(then).toTimeString().slice(0, 5);
      return (days > 0 ? days + " 天 " : "") + clock;
    })();
    return '<div class="page parity-module-page parity-trades-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>美投君 · 交易分享</span></div><h1>实战操作</h1><p>同步 PRO+ 实战操作记录：关联持仓、风险提示、交易计划与交易说明。</p></div>' + syncChip() + '</div>'
      + '<div class="trade-layout">'
      + '<section class="parity-panel trade-feed-panel"><div class="filter-tabs trade-tabs">'
      + '<button class="active" data-trade-filter="all">频道交易</button><button data-trade-filter="option">期权</button><button data-trade-filter="stock">股票</button>'
      + '</div><div class="trade-list">' + (cards || emptyCard) + '</div><div class="trade-more">更多</div></section>'
      + '<aside class="parity-panel open-position trade-open-panel"><div class="section-head"><div><h2>美投君的 open 仓位</h2><p>更新时间：' + escape(updatedLabel) + '</p></div></div>'
      + openPositionTable
      + '</aside>'
      + '</div>'
      + '<div class="parity-disclosure"><strong>数据说明：</strong>关联持仓、风险提示、交易计划与交易说明同步自原站实战操作接口；历史帖最多展示 60 笔，互动数据为同步时刻的快照。</div></div>';
  }

  // parity 版实战操作页的面板是 .parity-panel；prototype.html 内联 patch 脚本的
  // trade 处理器挂在 document 捕获阶段，只认 .panel（closest('.panel') 为 null 时
  // 依然 stopImmediatePropagation），事件到不了更深的节点。
  // DOM 捕获顺序是 window → document → … → 目标：在 window 捕获阶段接管，
  // 才能稳定先于 patch 处理器拿到 trade 控件的点击。
  window.addEventListener("click", (event) => {
    const target = event.target?.closest?.("[data-trade-filter],[data-trade-action]");
    if (!target || !target.closest(".parity-trades-page")) return;

    event.preventDefault();
    event.stopImmediatePropagation();

    if (target.hasAttribute("data-trade-filter")) {
      const page = target.closest(".parity-trades-page");
      const key = target.dataset.tradeFilter;
      page?.querySelectorAll("[data-trade-filter]").forEach((button) => button.classList.toggle("active", button === target));
      page?.querySelectorAll(".trade-card").forEach((card) => {
        // 原站 tab 语义：期权 = 交易含期权腿（feed=option）；股票 = 纯股票流（feed=stock）；
        // 频道交易 = 全部 feed。
        const feed = String(card.dataset.tradeFeed || "");
        card.style.display = (key === "all" || feed === key) ? "" : "none";
      });
      return;
    }

    const kind = target.dataset.tradeAction;
    if (kind === "like") {
      // 只更新文字 span，保住按钮里内嵌的心形 SVG 图标
      const span = target.querySelector("span");
      const count = Number((target.textContent.match(/\d+/) || [0])[0]) + 1;
      if (span) span.textContent = "点赞 " + count;
      else target.textContent = "点赞 " + count;
    } else if (typeof window.toast === "function") {
      window.toast(kind === "watch" ? "已开启调整提醒" : "评论入口已打开");
    }
  }, true);



  function stockDetail(ticker) {
    const symbol = String(ticker || "NVDA").toUpperCase();
    const stock = (typeof stocks !== "undefined" ? stocks.find((item) => item.ticker === symbol) : null) || normalizeStock({ ticker: symbol, text: symbol }, { ticker: symbol, name: symbol });
    const quote = sourceQuoteOverrides[symbol] || stock;
    const change = normalizeChange(stock.change) || stock.change || "";
    const related = (typeof articles !== "undefined" ? articles : []).filter((item) => item.tag === symbol || String(item.title || "").toUpperCase().includes(symbol)).slice(0, 8);
    const fallbackRelated = related.length ? related : moduleArticles("tracking").slice(0, 6);
    const facts = [["昨收", quote.previousClose], ["今开", quote.open], ["最低", quote.low], ["最高", quote.high], ["52周", quote.week52], ["PE", quote.pe]];
    const tabs = ["概览", "异动", "财报跟踪", "业务调研", "行业分析", "估值分析", "问答 PRO+", "实操 PRO+"];
    const buyReasons = quote.buyReasons || [];
    const sellRisks = quote.sellRisks || [];
    const quoteQuality = quote.quoteTime ? "原站公开快照" : "跟踪页面快照";
    const panelText = quote.quoteTime ? "价格、估值和观点来自原站公开股票页快照。" : "该股票的详细行情字段尚未从公开同步响应返回，当前保留跟踪页快照。";
    return '<div class="page parity-stock-page"><div class="crumb"><button class="more-link" data-route="tracking">‹ 返回股票机会与跟踪</button></div><section class="parity-stock-head"><div class="parity-stock-identity"><div class="parity-stock-logo">' + escape(symbol.slice(0, 2)) + '</div><div><h1>' + escape(symbol) + '</h1><p>' + escape(stock.name || symbol) + ' · ' + escape(stock.sector || "") + '</p></div></div><div class="parity-quote"><strong>$' + escape(String(stock.price || "--").replace(/^\$/, "")) + '</strong> <span class="' + (String(change).startsWith("+") ? "up" : "down") + '">' + escape(change || "--") + '</span><small>' + escape(quoteQuality + (quote.quoteTime ? " · " + quote.quoteTime : "")) + '</small><button class="watch-btn ' + (typeof state !== "undefined" && state.followed?.has(symbol) ? "on" : "") + '" data-watch="' + escape(symbol) + '">' + (typeof state !== "undefined" && state.followed?.has(symbol) ? "★ 已关注" : "☆ 关注") + '</button></div></section>' +
      '<div class="parity-quote-facts">' + facts.map((fact) => '<div class="parity-quote-fact"><span>' + escape(fact[0]) + '</span><b>' + escape(fact[1] || "--") + '</b></div>').join("") + '</div>' +
      '<div class="parity-stock-layout"><section class="parity-chart"><h3>价格走势 · 1 年</h3><small>示意图 · 仅用于阅读布局，实时行情以公开快照为准</small><svg viewBox="0 0 760 235" preserveAspectRatio="none" role="img" aria-label="价格走势示意图"><defs><linearGradient id="parityStockGradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#0b9b83" stop-opacity=".28"/><stop offset="1" stop-color="#0b9b83" stop-opacity="0"/></linearGradient></defs><path d="M0 187 C65 166 85 184 133 142 S210 156 257 111 S344 139 392 95 S473 130 523 72 S617 111 760 31 L760 235 L0 235Z" fill="url(#parityStockGradient)"/><path d="M0 187 C65 166 85 184 133 142 S210 156 257 111 S344 139 392 95 S473 130 523 72 S617 111 760 31" fill="none" stroke="#07977f" stroke-width="4"/><text x="6" y="227" class="chart-axis">1年前</text><text x="366" y="227" class="chart-axis">6个月前</text><text x="710" y="227" class="chart-axis">现在</text></svg></section><section class="parity-summary"><h3>美投观点</h3><div class="parity-summary-grid"><div><span>观点倾向</span><b>' + escape(quote.stance || stock.stance || "观察") + '</b></div><div><span>目标价</span><b>' + escape(quote.target || stock.target || "--") + '</b></div><div><span>估值区间</span><b>' + escape(quote.range || stock.range || "--") + '</b></div><div><span>风险等级</span><b class="parity-risk r' + escape(quote.risk || stock.risk || 0) + '">' + escape(quote.risk ? quote.risk + "级" : "--") + '</b></div><div><span>行业</span><b>' + escape(stock.sector || "--") + '</b></div><div><span>跟踪力度</span><b>' + escape(quote.track || stock.track || "--") + '</b></div><div><span>跟踪时长</span><b>' + escape(quote.trackDuration || "--") + '</b></div><div><span>观点更新</span><b>' + escape(quote.updated || stock.updated || "--") + '</b></div></div></section></div>' +
      '<div class="parity-stock-tabs">' + tabs.map((tab, index) => '<button class="' + (index === 0 ? "active" : "") + '" data-stock-section="' + escape(tab) + '">' + escape(tab) + '</button>').join("") + '</div><section class="parity-stock-panel stock-tab-content" data-parity-stock-panel="true" data-active-tab="概览"><h3>概览</h3><p>' + escape(panelText) + '</p></section>' +
      '<section class="parity-reasons"><section class="buy"><h3>看多理由</h3>' + (buyReasons.length ? '<ul>' + buyReasons.map((reason) => '<li>' + escape(reason) + '</li>').join("") + '</ul>' : '<p class="page-subtitle">公开页面暂未返回详细看多理由。</p>') + '</section><section class="sell"><h3>主要风险</h3>' + (sellRisks.length ? '<ul>' + sellRisks.map((risk) => '<li>' + escape(risk) + '</li>').join("") + '</ul>' : '<p class="page-subtitle">公开页面暂未返回详细风险清单。</p>') + '</section></section>' +
      '<section class="related parity-stock-related"><div class="section-head"><div><h2>相关研究</h2><p>按股票代码和标题匹配同步文字稿 · ' + escape(quoteQuality) + '</p></div></div>' + articleGrid(fallbackRelated, "parity-grid-3") + '</section><div class="parity-disclosure"><strong>数据说明：</strong>此页把原站公开股票页的字段映射到镜像阅读界面；未返回的字段显示为“--”，不会用估算值替代。</div></div>';
  }

  function parityListPage(kind) {
    const target = aliases[kind] || kind;
    const titles = { macro: "宏观市场解读", news: "新闻 Free & 异动", etf: "ETF投资", research: "全部研究", favorites: "我的收藏", bookmarks: "我的收藏", notes: "我的笔记", trades: "实战操作" };
    const isFavorites = kind === "favorites" || target === "favorites" || target === "bookmarks";
    const isNotes = kind === "notes" || target === "notes";
    let list = isFavorites ? (typeof articles !== "undefined" ? articles.filter((item) => state.favorites.has(item.id)) : []) : isNotes ? (typeof articles !== "undefined" ? articles.filter((item) => state.notes[item.id]) : []) : moduleArticles(target);
    const query = typeof state !== "undefined" ? String(state.query || "").trim().toLowerCase() : "";
    if (query) list = list.filter((item) => [item.title, item.lead, item.group, item.tag].join(" ").toLowerCase().includes(query));
    const page = pageFor(target);
    const title = titles[target] || moduleLabels[target] || "投资研究列表";
    const subtitle = target === "news" ? "周一至周五，每晚 9 点更新（美东时间）" : "所有视频内容已替换为可检索文字稿";
    return '<div class="page parity-module-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>' + escape(title) + '</span></div><h1>' + escape(title) + '</h1><p>' + escape(subtitle) + '</p></div>' + syncChip() + '</div>' + (page?.text ? '<div class="parity-disclosure"><strong>来源摘要：</strong>' + escape(String(page.text).split(/\n+/).filter((line) => line.trim().length > 14).slice(0, 3).join(" · ")) + '</div>' : "") + '<section class="parity-panel"><div class="parity-list-toolbar"><input id="listSearch" value="' + escape(typeof state !== "undefined" ? state.query : "") + '" placeholder="在本模块中搜索文字稿" autocomplete="off"><select id="listSort"><option value="date">最新发布</option><option value="views">阅读最多</option><option value="duration">时长最长</option></select><span class="parity-list-count">共 ' + list.length + ' 篇文字稿</span></div><div class="' + (list.length ? "parity-grid-3" : "module-empty") + '" id="listGrid">' + (list.length ? list.map(cardArticle).join("") : "该模块暂无可公开同步的独立文字稿。") + '</div></section></div>';
  }

  function qa() {
    const records = homeQuestions();
    const page = pageFor("questions");
    return '<div class="page parity-module-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>美投答疑 &amp; 社区互动</span></div><h1>美投答疑 &amp; 社区互动</h1><p>来自 PRO+ 问答区的真实提问与专业解答</p></div>' + syncChip() + '</div>' + (page?.text ? '<div class="parity-disclosure"><strong>来源摘要已同步：</strong>' + escape(String(page.text).split(/\n+/).filter((line) => line.length > 18).slice(0, 2).join(" · ")) + '</div>' : "") + '<section class="panel"><div class="filter-tabs qa-tabs"><button class="active" data-qa-filter="recommended">推荐</button><button data-qa-filter="all">全部问答</button><button data-qa-filter="mine">我的关注</button></div><div class="qa-full-toolbar"><input id="qaSearch" placeholder="搜索问题、作者或标签" autocomplete="off"><span class="updated">公开摘要</span></div><div class="qa-list" id="qaList">' + (records.length ? records.map((item, index) => '<article class="qa qa-card" data-qa-index="' + index + '"><div class="qa-head"><span class="parity-badge">' + escape(item.tag || "投资问答") + '</span><small>' + escape(item.date || "") + '</small></div><button class="qa-question" data-qa-toggle="' + index + '" aria-expanded="false"><strong>' + escape(item.title || "未命名问题") + '</strong><span>⌄</span></button><div class="qa-answer"><p>' + escape(item.body || "公开回答摘要已同步。") + '</p><small>' + escape(item.author || "研究用户") + '</small></div></article>').join("") : '<div class="module-empty">公开问答正在同步。</div>') + '</div></section></div>';
  }

  function ownedPage(kind) {
    const isVideos = kind === "my-videos";
    const title = isVideos ? "我的视频" : "公告";
    const page = pageFor(kind);
    const summary = String(page?.text || "").split(/\n+/).filter((line) => line.trim().length > 8).slice(-3).join(" · ");
    if (isVideos) {
      return '<div class="page parity-owned-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>我的</span></div><h1>' + title + '</h1><p>查看本地收藏与已同步内容</p></div>' + syncChip() + '</div><section class="parity-owned"><h2>这里还没有内容</h2><p>登录 Daocaijing 后，会员权益会在同源账号中同步；镜像站只展示已授权返回的内容。</p><button class="primary-inline" data-account-action="login">登录 Daocaijing</button></section></div>';
    }
    return '<div class="page parity-owned-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>我的</span></div><h1>' + title + '</h1><p>账号公告与镜像同步说明</p></div>' + syncChip() + '</div><section class="parity-panel"><article class="parity-topic"><b>频道公告</b><span>' + escape(summary || "当前没有新的公开公告。") + '</span></article><div class="parity-disclosure" style="margin-top:14px"><strong>镜像更新：</strong>公开栏目、文字稿和图片按同步时间更新；会员、支付和账号通知请以 Daocaijing 账号中心为准。</div></section></div>';
  }

  function generic() {
    if (state.route === "my-videos" || state.route === "announcements") return ownedPage(state.route);
    if (state.route === "research") return parityListPage("research");
    const page = pageFor(state.route);
    const list = moduleArticles(state.route).slice(0, 24);
    return '<div class="page parity-module-page"><div class="parity-route-head"><div><div class="crumb"><span class="back">‹</span><span>' + escape((typeof navMap !== "undefined" && navMap[state.route]) || state.route) + '</span></div><h1>' + escape((typeof navMap !== "undefined" && navMap[state.route]) || "模块") + '</h1><p>同步页面内容，视频位置以文字稿阅读器替代。</p></div>' + syncChip() + '</div>' + (list.length ? '<section class="parity-panel">' + articleGrid(list, "parity-grid-3") + '</section>' : '<section class="parity-owned"><h2>该模块暂无独立文字稿</h2><p>' + escape(String(page?.text || "公开页面摘要会在下一次同步后补充。").slice(0, 380)) + '</p></section>') + '</div>';
  }

  function updateMeta(route) {
    const article = route === "article" && typeof articles !== "undefined" ? articleById(state.article) : null;
    const titles = { home: "美投", tracking: "股票机会与跟踪 · 美投", opportunities: "行业机会 · 美投", macro: "宏观市场解读 · 美投", news: "新闻 Free & 异动 · 美投", etf: "ETF投资 · 美投", qa: "美投答疑 & 社区互动 · 美投", stock: ((state.ticker || "NVDA") + "股票分析 · 美投"), article: (article?.title ? cleanTitle(article.title) + " · 美投" : "文字研究 · 美投"), research: "全部研究 · 美投", favorites: "我的收藏 · 美投", bookmarks: "我的收藏 · 美投", notes: "我的笔记 · 美投", trades: "实战操作 · 美投", trade: "实战操作 · 美投", "my-videos": "我的视频 · 美投", announcements: "公告 · 美投" };
    document.title = titles[route] || ((typeof navMap !== "undefined" && navMap[route]) || "美投") + " · 美投";
    const description = article ? "" + cleanTitle(article.title) + "：公开文字稿、页面图片和同步元数据的镜像阅读页。" : "美投公开研究镜像：同步股票跟踪、宏观报告、行业机会、新闻、问答和文字稿阅读。";
    let meta = document.querySelector('meta[name="description"]');
    if (!meta) { meta = document.createElement("meta"); meta.name = "description"; document.head.appendChild(meta); }
    meta.content = description;
    let canonical = document.querySelector('link[rel="canonical"]');
    if (!canonical) { canonical = document.createElement("link"); canonical.rel = "canonical"; document.head.appendChild(canonical); }
    canonical.href = MIRROR_ORIGIN + "/meitou/";
    const og = (property, content) => { let node = document.querySelector('meta[property="' + property + '"]'); if (!node) { node = document.createElement("meta"); node.setAttribute("property", property); document.head.appendChild(node); } node.content = content; };
    og("og:title", document.title); og("og:description", description); og("og:url", MIRROR_ORIGIN + "/meitou/");
  }

  function decorateStockPanel() {
    if (typeof state === "undefined" || state.route !== "stock") return;
    const panel = view?.querySelector("[data-parity-stock-panel]");
    if (!panel) return;
    const label = panel.dataset.activeTab || "概览";
    const symbol = String(state.ticker || "NVDA").toUpperCase();
    const stock = typeof stocks !== "undefined" ? stocks.find((item) => item.ticker === symbol) : null;
    const related = typeof articles !== "undefined" ? articles.filter((item) => item.tag === symbol || String(item.title || "").toUpperCase().includes(symbol)).slice(0, 4) : [];
    const content = {
      "概览": "价格、观点、目标价和估值区间来自当前公开股票页快照。",
      "异动": "异动摘要请前往“新闻 Free & 异动”与“市场诊断”栏目查看。",
      "财报跟踪": "财报跟踪会随下一次公开同步补充收入、利润率和目标价变化。",
      "业务调研": related.length ? "已同步 " + related.length + " 篇与 " + symbol + " 相关的业务研究。" : "该股票暂未返回独立业务调研文字稿。",
      "行业分析": "行业页面以公开研究的行业标签和来源摘要为准。",
      "估值分析": "估值区间和目标价来自当前同步快照，未返回的字段显示为“--”。",
      "问答 PRO+": "问答正文需要 Daocaijing 账号中心返回相应权限；公开摘要可在问答栏目查看。",
      "实操 PRO+": "实操内容由 Daocaijing 会员权益管理，镜像站保留公开入口和权限提示。",
    };
    const next = '<h3>' + escape(label) + '</h3><p>' + escape(content[label] || "该栏目已切换，公开数据会在下一次同步后补充。") + '</p>' + (label === "业务调研" && related.length ? '<div class="research-list">' + related.map((item) => '<div class="research-item" data-article="' + escape(item.id) + '"><strong>' + escape(item.title) + '</strong></div>').join("") + '</div>' : "");
    if (panel.innerHTML !== next) panel.innerHTML = next;
  }

  // 以下栏目已按项目要求下线。旧链接/外部书签仍会命中这些 route，
  // 统一收敛回首页，避免出现"未知模块"空页。
  const RETIRED_ROUTES = new Set(["school", "beginner", "options", "discord"]);

  function parityRender() {
    if (!view || typeof state === "undefined") return;
    if (RETIRED_ROUTES.has(state.route)) {
      state.route = "home";
      state.query = "";
      try { history.replaceState(null, "", location.pathname + location.search + "#home"); } catch (_) {}
    }
    setActive?.();
    updateMeta(state.route);
    let html;
    if (state.route === "home") html = home();
    else if (state.route === "tracking") html = tracking();
    else if (state.route === "opportunities") html = opportunities();
    else if (state.route === "stock") html = stockDetail(state.ticker);
    else if (state.route === "article") html = typeof articlePage === "function" ? articlePage(state.article) : generic();
    else if (state.route === "qa") html = qa();
    else if (["trades", "trade"].includes(state.route)) html = parityTradeView();
    else if (["macro", "news", "etf", "research", "favorites", "notes"].includes(state.route)) html = parityListPage(state.route);
    else if (state.route === "whisper" || state.route === "market-diagnosis") html = typeof pulseView === "function" ? pulseView(state.route) : generic();
    else html = generic();
    view.innerHTML = html;
    try {
      bindBannerCarousel();
      if (typeof bindView === "function") bindView();
      if (typeof hydrateComments === "function") hydrateComments();
      if (typeof decorateInteractive === "function") decorateInteractive();
      if (typeof addModuleShortcuts === "function") addModuleShortcuts();
      if (typeof decorateCourseMedia === "function") decorateCourseMedia();
      if (typeof eagerArticleImages === "function") eagerArticleImages();
      if (typeof decorateRestrictedCards === "function") decorateRestrictedCards();
      if (typeof decorateReadingMeta === "function") decorateReadingMeta();
      if (typeof decorateReadingActions === "function") decorateReadingActions();
      if (typeof decorateRecommendGroups === "function") decorateRecommendGroups();
      decorateStockPanel();
    } catch (error) {
      console.error("mirror parity render failed", error);
    }
    updateMeta(state.route);
    normalizeParityStockRows();
    if (state.route === "stock") setTimeout(decorateStockPanel, 0);
    requestFullSnapshot(state.route);
  }

  function parityParseRoute() {
    const hash = String(window.location.hash || "").replace(/^#/, "");
    const parts = hash.split("/").filter(Boolean);
    let route = parts[0] || "";
    if (!route) {
      const pathname = String(window.location.pathname || "");
      const clean = pathname.replace(/\/+$/, "");
      if (/\/stock\/[A-Za-z]+$/i.test(clean)) { route = "stock"; state.ticker = clean.split("/").pop().toUpperCase(); }
      else if (/\/article\//i.test(clean)) { route = "article"; state.article = clean.split("/").pop(); }
      else if (/\/tracking(?:\.html)?$/i.test(clean)) route = "tracking";
      else route = "home";
    }
    if (route === "article") state.article = parts[1] || state.article || "hbm";
    else if (route === "stock") state.ticker = (parts[1] || state.ticker || "NVDA").toUpperCase();
    state.route = route || "home";
    parityRender();
  }

  function scheduleRender() {
    clearTimeout(window.__mirrorParityRenderTimer);
    window.__mirrorParityRenderTimer = setTimeout(() => parityRender(), 0);
  }

  function normalizeParityStockRows() {
    // The legacy prototype decorates every [data-stock] element as a button.
    // Keep the row itself a table row and expose the follow control as the
    // single keyboard target inside it.
    document.querySelectorAll("tr.parity-stock-row").forEach((row) => {
      row.removeAttribute("role");
      row.removeAttribute("tabindex");
    });
  }

  function idle(callback) {
    if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(callback, { timeout: 1600 });
    else setTimeout(callback, 700);
  }

  function articleDetailFile(id) {
    const target = String(id || "");
    if (!target) return "";
    // 前端 articles 里的条目（含 prototype.html 硬编码的）只有 id 和 source(原始 URL)，
    // 没有 detailFile；而同步快照 page.articles 里的条目只有 url + detailFile，id 常为空。
    // 所以正确的匹配链是：id -> source(url) -> 快照里的 detailFile。
    // 否则 remote-<hash>、hbm、macro-bond 这类 id 一律查不到，就会掉进全量快照兜底。
    let wantedUrl = "";
    if (typeof articles !== "undefined" && Array.isArray(articles)) {
      const item = articles.find((article) => article && String(article.id) === target);
      wantedUrl = String(item?.source || item?.url || "");
    }

    const candidates = [];
    for (const page of window.__mirrorSync?.pages || []) {
      for (const article of (page.articles || [])) {
        const df = article?.detailFile;
        if (!df) continue;
        const url = String(article.url || "");
        if (wantedUrl && url === wantedUrl) return df;   // 最准：按原始 URL 精确匹配
        candidates.push({ df, url, article });
      }
    }

    // 兜底：target 本身是 url slug 或 detailFile 文件名（如 /article/hOzy2-TOFU 直达）
    for (const { df, url, article } of candidates) {
      if (article.id && article.id === target) return df;
      const stem = String(df).replace(/^article-pages\//, "").replace(/\.json$/, "");
      if (stem === target || stem.replace(/-TOFU$/, "") === target) return df;
      if (url && url.includes("/tofu/" + target)) return df;
    }
    return "";
  }

  function articleLoadingStatus(message, error = false) {
    const toastNode = document.getElementById("toast");
    if (!toastNode) return;
    toastNode.textContent = message;
    toastNode.classList.toggle("error", error);
    toastNode.classList.add("show");
    if (!error) setTimeout(() => toastNode.classList.remove("show"), 1800);
  }

  async function loadArticleDetail(id) {
    const target = String(id || "");
    if (!target || window.__mirrorArticleDetails?.has(target) || window.__mirrorArticleDetailLoading) return;
    const detailFile = articleDetailFile(target);
    // 单篇找不到时不再偷偷拉 51MB 全量快照 —— 全量是背景增强，不能掩盖用户正在打开的那篇。
    // 全量已通过 idle 路径独立触发一次（见 requestFullSnapshot 的非 article 分支）。
    if (!detailFile) {
      articleLoadingStatus("暂未同步全文", true);
      return;
    }
    window.__mirrorArticleDetailLoading = true;
    articleLoadingStatus("正在加载文字稿…");
    try {
      const response = await fetch(detailFile, { cache: "no-cache" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const detail = await response.json();
      const sync = window.__mirrorSync || { pages: [], articleTranscripts: [] };
      const transcripts = Array.isArray(sync.articleTranscripts) ? sync.articleTranscripts.slice() : [];
      const index = transcripts.findIndex((item) => item.url === detail.url);
      if (index >= 0) transcripts[index] = { ...transcripts[index], ...detail };
      else transcripts.push(detail);
      sync.articleTranscripts = transcripts;
      window.__mirrorSync = sync;
      if (!window.__mirrorArticleDetails) window.__mirrorArticleDetails = new Set();
      window.__mirrorArticleDetails.add(target);
      const raw = (sync.pages || []).flatMap((page) => page.articles || []).find((article) => article.url === detail.url) || { url: detail.url };
      normalizeArticle(raw, "research", detail);
      articleLoadingStatus("文字稿已加载");
      parityRender();
    } catch (error) {
      window.__mirrorFullError = String(error?.message || error);
      articleLoadingStatus("文字稿加载失败，保留当前摘要", true);
      console.warn("单篇文字稿加载失败", error);
    } finally {
      window.__mirrorArticleDetailLoading = false;
    }
  }

  function routeNeedsFullSnapshot(route) {
    const target = aliases[route] || route;
    return FULL_SNAPSHOT_ROUTES.has(route) || FULL_SNAPSHOT_ROUTES.has(target);
  }

  function requestFullSnapshot(route, options) {
    if (!routeNeedsFullSnapshot(route) || window.__mirrorMeta?.full || window.__mirrorFullLoading) return;
    const run = () => {
      // 文章页只走 article-pages/*.json 单篇路径，不再触发 51MB 全量快照。
      if (state?.route === "article") loadArticleDetail(state.article);
    };
    if (options?.eager || route === "article") Promise.resolve().then(run);
    else idle(run);
  }

  function patchGlobals() {
    const replacements = { home, tracking, opportunities, stockDetail, moduleArticles, cardArticle, listPage: parityListPage, qa, generic, render: parityRender, parseRoute: parityParseRoute };
    for (const [name, fn] of Object.entries(replacements)) {
      try { window[name] = fn; } catch (_) {}
      try { eval(name + " = fn"); } catch (_) {}
    }
    // The old decorator turns every .home-hero into a fixed-height banner and
    // overwrites the original stats.  Our parity hero owns that surface.
    try { window.decorateHomeMedia = function decorateHomeMediaParity() {}; eval("decorateHomeMedia = window.decorateHomeMedia"); } catch (_) {}
    try { window.decorateTrackingNotice = function decorateTrackingNoticeParity() {}; eval("decorateTrackingNotice = window.decorateTrackingNotice"); } catch (_) {}
  }

  function patchTopbar() {
    const notice = document.getElementById("noticeBtn");
    if (notice) notice.onclick = () => { if (typeof routeTo === "function") routeTo("announcements"); };
  }

  function activateBanner(index) {
    const slides = [...document.querySelectorAll(".parity-banner-slide")];
    const dots = [...document.querySelectorAll(".parity-banner-dot")];
    if (!slides.length) return;
    const next = Math.max(0, Math.min(Number(index) || 0, slides.length - 1));
    slides.forEach((slide, position) => {
      const active = position === next;
      slide.classList.toggle("active", active);
      slide.setAttribute("aria-hidden", String(!active));
    });
    dots.forEach((dot, position) => {
      const active = position === next;
      dot.classList.toggle("active", active);
      dot.setAttribute("aria-selected", String(active));
    });
    restartBannerAutoplay();
  }

  let bannerAutoplayTimer = null;
  function restartBannerAutoplay() {
    if (bannerAutoplayTimer) {
      clearInterval(bannerAutoplayTimer);
      bannerAutoplayTimer = null;
    }
    const viewport = document.querySelector(".parity-banner-viewport");
    const slides = [...document.querySelectorAll(".parity-banner-slide")];
    if (!viewport || slides.length < 2 || document.hidden || viewport.matches(":hover") || viewport.contains(document.activeElement)) return;
    bannerAutoplayTimer = setInterval(() => {
      if (document.hidden || viewport.matches(":hover") || viewport.contains(document.activeElement)) return;
      const active = slides.findIndex((slide) => slide.classList.contains("active"));
      activateBanner((active + 1 + slides.length) % slides.length);
    }, 5000);
  }

  function bindBannerCarousel() {
    const viewport = document.querySelector(".parity-banner-viewport");
    if (!viewport) {
      if (bannerAutoplayTimer) {
        clearInterval(bannerAutoplayTimer);
        bannerAutoplayTimer = null;
      }
      return;
    }
    if (viewport.dataset.bannerBound !== "true") {
      viewport.dataset.bannerBound = "true";
      viewport.addEventListener("mouseenter", () => {
        if (bannerAutoplayTimer) { clearInterval(bannerAutoplayTimer); bannerAutoplayTimer = null; }
      });
      viewport.addEventListener("mouseleave", restartBannerAutoplay);
      viewport.addEventListener("focusin", () => {
        if (bannerAutoplayTimer) { clearInterval(bannerAutoplayTimer); bannerAutoplayTimer = null; }
      });
      viewport.addEventListener("focusout", () => setTimeout(restartBannerAutoplay, 0));
      let pointerStart = null;
      viewport.addEventListener("pointerdown", (event) => { pointerStart = event.clientX; });
      viewport.addEventListener("pointerup", (event) => {
        if (pointerStart == null) return;
        const delta = event.clientX - pointerStart;
        pointerStart = null;
        if (Math.abs(delta) < 40) return;
        const slides = [...viewport.querySelectorAll(".parity-banner-slide")];
        const active = slides.findIndex((slide) => slide.classList.contains("active"));
        activateBanner(active + (delta < 0 ? 1 : -1));
      });
    }
    restartBannerAutoplay();
  }

  document.addEventListener("visibilitychange", restartBannerAutoplay);

  document.addEventListener("click", (event) => {
    const storyButton = event.target?.closest?.("[data-options-story]");
    if (storyButton) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const panel = storyButton.closest(".course-hero");
      const full = panel?.querySelector(".options-story-full");
      if (full) {
        const open = full.hasAttribute("hidden");
        full.toggleAttribute("hidden", !open);
        storyButton.textContent = open ? "收起全文⌃" : "展开阅读全文⌄";
      }
      return;
    }
    const dot = event.target?.closest?.("[data-parity-banner-index]");
    if (dot) {
      event.preventDefault();
      event.stopImmediatePropagation();
      activateBanner(dot.dataset.parityBannerIndex);
      return;
    }
    const slide = event.target?.closest?.(".parity-banner-slide");
    const target = slide?.dataset.bannerTarget;
    if (slide && target) {
      event.preventDefault();
      event.stopImmediatePropagation();
      activateBanner(slide.dataset.bannerSlide);
      if (typeof routeTo === "function") routeTo(target);
    }
  }, true);

  // Broken remote images should not leave a browser error icon in the reader.
  // The source snapshot can contain an asset URL that was removed upstream;
  // removing only the affected mirror image keeps the surrounding text intact.
  document.addEventListener("error", (event) => {
    const image = event.target;
    if (image?.tagName === "IMG" && image.closest(".parity-article-thumb,.reader-cover,.reader-poster,.inline-figure")) image.remove();
  }, true);

  // Prefetch on an explicit reading signal.  This keeps a quiet home visit
  // lightweight while making a card feel instant for mouse and keyboard users.
  const requestOnArticleIntent = (event) => {
    if (event.target?.closest?.("[data-article]")) requestFullSnapshot("article", { eager: true });
  };
  document.addEventListener("pointerover", requestOnArticleIntent, { passive: true });
  document.addEventListener("focusin", requestOnArticleIntent);
  document.addEventListener("keydown", (event) => {
    const follow = event.target?.closest?.(".parity-follow[data-follow]");
    if (!follow || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    follow.click();
  });

  const stockObserver = new MutationObserver(() => decorateStockPanel());
  if (view) stockObserver.observe(view, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-active-tab"] });

  window.addEventListener("mirror:data-ready", () => { updateMeta(state?.route || "home"); scheduleRender(); });
  window.addEventListener("hashchange", () => setTimeout(parityParseRoute, 0));
  window.addEventListener("popstate", () => setTimeout(parityParseRoute, 0));

  patchGlobals();
  patchTopbar();
  window.__mirrorParityReady = true;

  // The inline bootstrap may already have loaded the lite payload.  If it has
  // not, use the same payload here so an alternate entry point still works.
  if (window.__mirrorSync?.pages) applySnapshot(window.__mirrorSync, { full: Boolean(window.__mirrorSync.articleTranscripts?.length) });
  else fetch("site-data-lite.json", { cache: "no-cache" }).then((response) => response.ok ? response.json() : Promise.reject(new Error("lite snapshot unavailable"))).then((data) => applySnapshot(data, { full: false })).catch(() => {});
  parityParseRoute();
  requestFullSnapshot(state?.route, { eager: state?.route === "article" });

  window.MirrorParity = {
    version: VERSION,
    sourceOrigin: SOURCE_ORIGIN,
    applySnapshot,
    render: parityRender,
    normalizeArticle,
    normalizeStock,
  };
})(window, document);
