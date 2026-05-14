#!/usr/bin/env node
"use strict";

const fs = require("node:fs/promises");
const fssync = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { createHash, randomInt, randomUUID } = require("node:crypto");
const mammoth = require("mammoth");
const { PDFParse } = require("pdf-parse");

const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, "tool-public");
const RUN_DIR = path.join(ROOT, ".tool-runs");
const DEFAULT_PORT = Number(process.env.PORT || 3927);
const CORS_ORIGIN = process.env.RESEARCH_WORKBENCH_CORS_ORIGIN || "*";
const API_BASE = "https://api.zsxq.com/v2";
const WEB_ORIGIN = "https://wx.zsxq.com";
const X_VERSION = "2.91.0";
const DEFAULT_ANALYSIS_PROMPT = [
  "请用中文解读这份海外投行研报，面向投资研究场景输出：",
  "1. 一句话结论",
  "2. 核心观点",
  "3. 关键数据、催化剂或时间点",
  "4. 风险点",
  "5. 可跟踪标的、行业或主题",
  "6. 原文证据，尽量标注页码、标题或表格位置",
].join("\n");
const jobs = new Map();
const extractionCache = new Map();

function corsHeaders(headers = {}) {
  return {
    "access-control-allow-origin": CORS_ORIGIN,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type",
    ...headers,
  };
}

function send(res, status, body, type = "application/json; charset=utf-8") {
  const payload = type.startsWith("application/json") ? JSON.stringify(body) : body;
  res.writeHead(status, corsHeaders({
    "content-type": type,
    "cache-control": "no-store",
  }));
  res.end(payload);
}

function parseJson(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 10_000_000) {
        reject(new Error("请求过大"));
        req.destroy();
      }
    });
    req.on("end", () => {
      if (!body.trim()) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch {
        reject(new Error("JSON 格式错误"));
      }
    });
  });
}

function safeJoin(base, target) {
  const resolved = path.resolve(base, target || "");
  const relative = path.relative(base, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("路径越界");
  return resolved;
}

function redact(text) {
  return String(text)
    .replace(/zsxq_access_token=[^;\s'"]+/g, "zsxq_access_token=[REDACTED]")
    .replace(/([?&]token=)[^&\s'"]+/gi, "$1[REDACTED]")
    .replace(/(authorization:\s*)[^\n\r]+/gi, "$1[REDACTED]");
}

function pushLog(job, chunk) {
  const text = redact(chunk.toString());
  const lines = text.split(/\r?\n/).filter((line) => line.length);
  for (const line of lines) {
    const item = { at: new Date().toISOString(), line };
    job.logs.push(item);
    if (job.logs.length > 800) job.logs.shift();
    for (const client of job.clients) {
      client.write(`event: log\ndata: ${JSON.stringify(item)}\n\n`);
    }
  }
}

function emitJob(job, event = "state") {
  const snapshot = publicJob(job);
  for (const client of job.clients) {
    client.write(`event: ${event}\ndata: ${JSON.stringify(snapshot)}\n\n`);
  }
}

function publicJob(job) {
  return {
    id: job.id,
    status: job.status,
    startedAt: job.startedAt,
    endedAt: job.endedAt || null,
    code: job.code,
    signal: job.signal,
    config: job.publicConfig,
    logCount: job.logs.length,
  };
}

function buildArgs(payload, curlFile, selectionFile = "") {
  const group = String(payload.group || "88888142214212").trim();
  const args = ["zsxq-downloader.js", "--group", group];

  const tag = String(payload.tag || "").trim();
  const hashtagId = String(payload.hashtagId || "").trim();
  const out = String(payload.out || "downloads/海外投行报告").trim();
  const ext = String(payload.ext || "pdf").trim();
  const limit = Number(payload.limit || 0);
  const maxPages = Number(payload.maxPages || 0);

  if (tag) args.push("--tag", tag);
  if (hashtagId) args.push("--hashtag-id", hashtagId);
  if (out) args.push("--out", out);
  if (ext) args.push("--ext", ext);
  if (Number.isFinite(limit) && limit > 0) args.push("--limit", String(Math.floor(limit)));
  if (Number.isFinite(maxPages) && maxPages > 0) args.push("--max-pages", String(Math.floor(maxPages)));
  if (payload.listOnly) args.push("--list-only");
  if (curlFile) args.push("--curl-file", curlFile);
  if (selectionFile) args.push("--selection-file", selectionFile);

  return args;
}

async function startJob(payload) {
  await fs.mkdir(RUN_DIR, { recursive: true });
  const id = randomUUID().slice(0, 12);
  const curlText = String(payload.curlText || "").trim();
  const cookie = String(payload.cookie || "").trim();
  const aduid = String(payload.aduid || "").trim();
  let curlFile = "";
  let selectionFile = "";

  if (curlText) {
    curlFile = path.join(RUN_DIR, `${id}.curl`);
    await fs.writeFile(curlFile, curlText);
  }
  if (Array.isArray(payload.selectedFiles) && payload.selectedFiles.length) {
    selectionFile = path.join(RUN_DIR, `${id}.selection.json`);
    await fs.writeFile(selectionFile, `${JSON.stringify(payload.selectedFiles, null, 2)}\n`);
  }

  const args = buildArgs(payload, curlFile, selectionFile);
  const env = { ...process.env };
  if (cookie) env.ZSXQ_COOKIE = cookie;
  if (aduid) env.ZSXQ_ADUID = aduid;

  const job = {
    id,
    status: "running",
    startedAt: new Date().toISOString(),
    endedAt: "",
    code: null,
    signal: null,
    logs: [],
    clients: new Set(),
    child: null,
    publicConfig: {
      group: payload.group || "88888142214212",
      tag: payload.tag || "",
      hashtagId: payload.hashtagId || "",
      out: payload.out || "downloads/海外投行报告",
      ext: payload.ext || "pdf",
      limit: Number(payload.limit || 0),
      maxPages: Number(payload.maxPages || 0),
      listOnly: Boolean(payload.listOnly),
      selectedCount: Array.isArray(payload.selectedFiles) ? payload.selectedFiles.length : 0,
      authMode: curlText ? "curl" : cookie ? "cookie" : process.env.ZSXQ_COOKIE ? "env" : "browser",
    },
  };
  jobs.set(id, job);

  const child = spawn(process.execPath, args, {
    cwd: ROOT,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  job.child = child;
  pushLog(job, `$ ${process.execPath} ${args.map((arg) => arg.includes(" ") ? JSON.stringify(arg) : arg).join(" ")}`);
  emitJob(job, "state");

  child.stdout.on("data", (chunk) => pushLog(job, chunk));
  child.stderr.on("data", (chunk) => pushLog(job, chunk));
  child.on("close", (code, signal) => {
    job.status = code === 0 ? "completed" : signal ? "stopped" : "failed";
    job.endedAt = new Date().toISOString();
    job.code = code;
    job.signal = signal;
    emitJob(job, "state");
  });

  return publicJob(job);
}

function uuidLikeFrontend() {
  let id = "";
  for (let i = 0; i < 32; i += 1) {
    id += randomInt(16).toString(16);
    if ([8, 12, 16, 20].includes(i)) id += "-";
  }
  return id;
}

function normalizeSignatureUrl(url) {
  const [base, ...queryParts] = url.split("?");
  if (!queryParts.length) return base;
  return `${base}?${queryParts.join("?").replace(/'/g, "%27")}`;
}

function signedHeaders(url, aduid, cookie) {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const requestId = uuidLikeFrontend();
  const signature = createHash("sha1")
    .update(`${normalizeSignatureUrl(url)} ${timestamp} ${requestId}`)
    .digest("hex");
  const headers = {
    accept: "application/json, text/plain, */*",
    origin: WEB_ORIGIN,
    referer: `${WEB_ORIGIN}/`,
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "x-request-id": requestId,
    "x-version": X_VERSION,
    "x-signature": signature,
    "x-timestamp": timestamp,
    "x-aduid": aduid || uuidLikeFrontend(),
  };
  if (cookie) headers.cookie = cookie;
  return headers;
}

function normalizeCookieText(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  const withoutHeader = text.replace(/^cookie:\s*/i, "").trim();
  const lines = withoutHeader.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return lines
    .map((line) => line.replace(/^cookie:\s*/i, "").replace(/;$/, ""))
    .join("; ")
    .replace(/;\s*;/g, ";")
    .trim();
}

function extractShellQuotedValue(text, flagPattern) {
  const pattern = new RegExp(`${flagPattern}\\s+(?:'([^']*)'|"([^"]*)"|([^\\\\\\s]+))`, "i");
  const match = text.match(pattern);
  return match ? (match[1] || match[2] || match[3] || "") : "";
}

function extractCurlHeader(text, headerName) {
  const headerPattern = new RegExp(`-H\\s+(?:'([^']*)'|"([^"]*)"|([^\\\\\\n]+))`, "gi");
  let match;
  while ((match = headerPattern.exec(text))) {
    const header = match[1] || match[2] || match[3] || "";
    const colonAt = header.indexOf(":");
    if (colonAt < 0) continue;
    const name = header.slice(0, colonAt).trim().toLowerCase();
    if (name === headerName.toLowerCase()) return header.slice(colonAt + 1).trim();
  }
  return "";
}

function parseCurlAuth(text) {
  const cookie =
    extractShellQuotedValue(text, "(?:-b|--cookie)") ||
    extractCurlHeader(text, "cookie");
  return {
    cookie: normalizeCookieText(cookie),
    aduid: extractCurlHeader(text, "x-aduid"),
  };
}

function resolveAuth(payload) {
  const curlText = String(payload.curlText || "").trim();
  if (curlText) {
    const parsed = parseCurlAuth(curlText);
    return { cookie: parsed.cookie, aduid: String(payload.aduid || parsed.aduid || process.env.ZSXQ_ADUID || "") };
  }
  return {
    cookie: normalizeCookieText(payload.cookie || process.env.ZSXQ_COOKIE || ""),
    aduid: String(payload.aduid || process.env.ZSXQ_ADUID || ""),
  };
}

async function apiGet(auth, url) {
  const response = await fetch(url, { headers: signedHeaders(url, auth.aduid, auth.cookie), redirect: "follow" });
  const text = await response.text();
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error(`接口返回不是 JSON：HTTP ${response.status}`);
  }
  if (!response.ok || !payload.succeeded) {
    const error = new Error(payload.info || payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.code = payload.code;
    throw error;
  }
  return payload.resp_data;
}

function normalizeTagName(value) {
  return String(value || "").replace(/^#+|#+$/g, "").trim();
}

async function resolveHashtag(auth, group, tag, hashtagId) {
  if (hashtagId) return { hashtagId: String(hashtagId), title: tag || `#${hashtagId}#` };
  if (!tag) return null;
  const data = await apiGet(auth, `${API_BASE}/groups/${group}/hashtags/defaults`);
  const target = normalizeTagName(tag);
  const found = (data.hashtags || []).find((item) => normalizeTagName(item.title) === target);
  if (!found) {
    const available = (data.hashtags || []).map((item) => item.title).join(", ");
    throw new Error(`没有找到标签：${tag}。可用标签：${available}`);
  }
  return { hashtagId: String(found.hashtag_id), title: found.title, topicsCount: found.topics_count || 0 };
}

function getTopicFiles(topic) {
  const typed = topic?.[topic.type] || topic?.talk || topic?.task || topic?.solution || {};
  return typed.files || topic.files || [];
}

function fileExtension(name) {
  return path.extname(name || "").toLowerCase().replace(/^\./, "");
}

function normalizeSearchText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "");
}

function fuzzyMatch(text, keyword) {
  const query = normalizeSearchText(keyword);
  if (!query) return { ok: true, score: 0 };
  const haystack = normalizeSearchText(text);
  if (haystack.includes(query)) return { ok: true, score: 100 + query.length };
  if (/^[a-z0-9]{1,3}$/i.test(query)) return { ok: false, score: 0 };

  let index = 0;
  let gaps = 0;
  for (const char of query) {
    const foundAt = haystack.indexOf(char, index);
    if (foundAt < 0) return { ok: false, score: 0 };
    gaps += Math.max(0, foundAt - index);
    index = foundAt + 1;
  }
  return { ok: true, score: Math.max(1, 60 - gaps) };
}

function topicFileToSearchItem(topic, file, hashtag, score) {
  return {
    fileId: String(file.file_id),
    name: file.name || `${file.file_id}.bin`,
    size: file.size || file.file_size || 0,
    createTime: file.create_time || topic.create_time || "",
    downloadCount: file.download_count || 0,
    topicId: topic.topic_id || "",
    topicCreateTime: topic.create_time || "",
    hashtag: hashtag?.title || "",
    score,
  };
}

function plainFileToSearchItem(item, score) {
  const file = item.file || item;
  return {
    fileId: String(file.file_id || item.file_id || item.id || ""),
    name: file.name || `${file.file_id || item.file_id || item.id}.bin`,
    size: file.size || file.file_size || 0,
    createTime: file.create_time || item.create_time || "",
    downloadCount: file.download_count || 0,
    topicId: item.topic_id || item.topic?.topic_id || "",
    topicCreateTime: item.topic?.create_time || "",
    hashtag: "",
    score,
  };
}

async function searchTagFiles(auth, payload, hashtag) {
  const keyword = String(payload.keyword || "").trim();
  const ext = String(payload.ext || "").trim().toLowerCase().replace(/^\./, "");
  const pageLimit = Math.max(1, Math.min(500, Number(payload.searchPages || payload.maxPages || 20) || 20));
  const resultLimit = Math.max(1, Math.min(1000, Number(payload.resultLimit || 200) || 200));
  const count = 20;
  const seen = new Set();
  const items = [];
  let endTime = "";
  let scannedTopics = 0;

  for (let page = 1; page <= pageLimit; page += 1) {
    const url = new URL(`${API_BASE}/hashtags/${hashtag.hashtagId}/topics`);
    url.searchParams.set("count", String(count));
    if (endTime) url.searchParams.set("end_time", endTime);
    const data = await apiGet(auth, url.toString());
    const topics = data.topics || [];
    scannedTopics += topics.length;
    for (const topic of topics) {
      for (const file of getTopicFiles(topic)) {
        if (!file.file_id || seen.has(String(file.file_id))) continue;
        if (ext && fileExtension(file.name) !== ext) continue;
        const match = fuzzyMatch(`${file.name || ""} ${topic.title || ""}`, keyword);
        if (!match.ok) continue;
        seen.add(String(file.file_id));
        items.push(topicFileToSearchItem(topic, file, hashtag, match.score));
      }
    }
    const last = topics[topics.length - 1];
    endTime = last?.create_time || endTime;
    if (!topics.length || topics.length < count || items.length >= resultLimit) break;
  }
  items.sort((a, b) => b.score - a.score || String(b.createTime).localeCompare(String(a.createTime)));
  return { items: items.slice(0, resultLimit), scannedTopics, hashtag };
}

async function searchGroupFiles(auth, payload) {
  const group = String(payload.group || "88888142214212").trim();
  const keyword = String(payload.keyword || "").trim();
  const ext = String(payload.ext || "").trim().toLowerCase().replace(/^\./, "");
  const resultLimit = Math.max(1, Math.min(1000, Number(payload.resultLimit || 200) || 200));
  const pageLimit = Math.max(1, Math.min(100, Number(payload.searchPages || payload.maxPages || 10) || 10));
  const seen = new Set();
  const items = [];
  let index = "";
  let endTime = "";

  for (let page = 1; page <= pageLimit; page += 1) {
    const url = keyword
      ? new URL(`${API_BASE}/search/groups/${group}/files`)
      : new URL(`${API_BASE}/groups/${group}/files`);
    url.searchParams.set("count", "20");
    if (keyword) url.searchParams.set("keyword", keyword);
    if (index) url.searchParams.set("index", index);
    if (!keyword && endTime) url.searchParams.set("end_time", endTime);
    if (!keyword) url.searchParams.set("sort", "by_create_time");
    const data = await apiGet(auth, url.toString());
    const files = data.files || [];
    for (const item of files) {
      const candidate = plainFileToSearchItem(item, keyword ? 80 : 0);
      if (!candidate.fileId || seen.has(candidate.fileId)) continue;
      if (ext && fileExtension(candidate.name) !== ext) continue;
      const match = keyword ? fuzzyMatch(candidate.name, keyword) : { ok: true, score: 0 };
      if (!match.ok) continue;
      seen.add(candidate.fileId);
      items.push({ ...candidate, score: Math.max(candidate.score, match.score) });
    }
    index = data.index || "";
    const last = files[files.length - 1];
    endTime = last?.file?.create_time || last?.create_time || endTime;
    if (!files.length || files.length < 20 || items.length >= resultLimit) break;
  }
  items.sort((a, b) => b.score - a.score || String(b.createTime).localeCompare(String(a.createTime)));
  return { items: items.slice(0, resultLimit), scannedTopics: 0, hashtag: null };
}

async function searchFiles(payload) {
  const auth = resolveAuth(payload);
  if (!auth.cookie) throw new Error("缺少 Cookie。请选择环境凭证、粘贴 curl，或粘贴 Cookie。");
  const group = String(payload.group || "88888142214212").trim();
  const hashtag = await resolveHashtag(auth, group, String(payload.tag || "").trim(), String(payload.hashtagId || "").trim());
  const result = hashtag ? await searchTagFiles(auth, payload, hashtag) : await searchGroupFiles(auth, payload);
  return {
    ...result,
    keyword: String(payload.keyword || "").trim(),
    count: result.items.length,
  };
}

async function stopJob(id) {
  const job = jobs.get(id);
  if (!job) return null;
  if (job.child && job.status === "running") {
    job.status = "stopping";
    job.child.kill("SIGTERM");
    emitJob(job, "state");
  }
  return publicJob(job);
}

async function readDownloads(out = "downloads/海外投行报告") {
  const dir = path.resolve(ROOT, out);
  const result = {
    dir,
    exists: fssync.existsSync(dir),
    files: [],
    summary: { total: 0, downloaded: 0, failed: 0, listed: 0, sizeBytes: 0 },
  };
  if (!result.exists) return result;

  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const filePath = path.join(dir, entry.name);
    const stat = await fs.stat(filePath);
    result.summary.sizeBytes += stat.size;
    if (!["manifest.json", "files.csv"].includes(entry.name) && !entry.name.endsWith(".part")) {
      result.files.push({ name: entry.name, size: stat.size, mtime: stat.mtime.toISOString() });
    }
  }
  result.files.sort((a, b) => b.mtime.localeCompare(a.mtime));
  result.summary.downloaded = result.files.length;

  const manifestPath = path.join(dir, "manifest.json");
  if (fssync.existsSync(manifestPath)) {
    try {
      const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
      const records = Object.values(manifest.files || {});
      result.summary.total = records.length;
      result.summary.failed = records.filter((item) => item.status === "failed").length;
      result.summary.listed = records.filter((item) => item.status === "listed").length;
    } catch {
      result.summary.total = result.files.length;
    }
  } else {
    result.summary.total = result.files.length;
  }
  return result;
}

function modelDefaults() {
  return {
    baseUrl: process.env.OPENAI_BASE_URL || "https://api.openai.com/v1",
    model: process.env.OPENAI_MODEL || "",
    hasApiKey: Boolean(process.env.OPENAI_API_KEY),
    temperature: 0.2,
    maxTokens: 4096,
    imagePages: "auto",
    compat: "auto",
    thinking: "disabled",
    extraBody: "",
  };
}

function parseExtraBody(value) {
  if (!value) return {};
  if (typeof value === "object" && !Array.isArray(value)) return { ...value };
  const text = String(value || "").trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("高级参数必须是 JSON 对象");
    return parsed;
  } catch (error) {
    throw new Error(`高级参数 JSON 无效：${error.message}`);
  }
}

function parseImagePageLimit(value) {
  const text = String(value ?? "auto").trim().toLowerCase();
  if (!text || ["auto", "all", "full", "全部"].includes(text)) return 0;
  const number = Number(text);
  if (!Number.isFinite(number) || number <= 0) return 0;
  return Math.max(1, Math.floor(number));
}

function resolveModelConfig(payload = {}) {
  const defaults = modelDefaults();
  const config = payload.modelConfig || payload;
  const extraBody = parseExtraBody(config.extraBody);
  const baseUrl = String(config.baseUrl || defaults.baseUrl).trim().replace(/\/+$/, "");
  const apiKey = String(config.apiKey || process.env.OPENAI_API_KEY || "").trim();
  const model = String(config.model || defaults.model).trim();
  const temperature = Number.isFinite(Number(config.temperature)) ? Number(config.temperature) : defaults.temperature;
  const rawMaxTokens = Number.isFinite(Number(config.maxTokens)) ? Number(config.maxTokens) : defaults.maxTokens;
  const imagePages = config.imagePages ?? config.visionPages ?? extraBody.imagePages ?? extraBody.visionPages ?? defaults.imagePages;
  const compat = String(config.compat || defaults.compat || "auto").trim();
  const thinking = String(config.thinking || defaults.thinking || "default").trim();
  delete extraBody.imagePages;
  delete extraBody.visionPages;

  if (!baseUrl) throw new Error("缺少模型 Base URL");
  if (!model) throw new Error("缺少模型名称");

  return {
    baseUrl,
    apiKey,
    model,
    temperature: Math.max(0, Math.min(2, temperature)),
    maxTokens: Math.max(256, Math.min(12000, Math.floor(rawMaxTokens))),
    imagePageLimit: parseImagePageLimit(imagePages),
    compat: ["auto", "standard", "kimi", "custom"].includes(compat) ? compat : "auto",
    thinking: ["default", "disabled", "enabled"].includes(thinking) ? thinking : "default",
    extraBody,
  };
}

function chatCompletionsUrl(baseUrl) {
  if (/\/chat\/completions$/i.test(baseUrl)) return baseUrl;
  return `${baseUrl}/chat/completions`;
}

function isKimiMultimodalModel(model) {
  return /^kimi-k2\.[56](?:\b|[-_])/i.test(String(model || ""));
}

function resolveCompatMode(config) {
  if (config.compat === "auto") return isKimiMultimodalModel(config.model) ? "kimi" : "standard";
  return config.compat;
}

function extractChoiceText(choice) {
  const message = choice?.message || {};
  const parts = [];
  if (typeof message.content === "string") parts.push(message.content);
  if (Array.isArray(message.content)) {
    parts.push(...message.content.map((item) => item.text || item.content || "").filter(Boolean));
  }
  if (typeof message.reasoning_content === "string") parts.push(message.reasoning_content);
  if (typeof message.reasoning === "string") parts.push(message.reasoning);
  if (typeof choice?.text === "string") parts.push(choice.text);
  return parts.join("\n").trim();
}

function buildChatBody(config, messages) {
  const compat = resolveCompatMode(config);
  const body = {
    model: config.model,
    messages,
    max_tokens: config.maxTokens,
  };

  if (compat === "kimi") {
    if (config.thinking !== "default") body.thinking = { type: config.thinking };
  } else if (compat === "standard") {
    body.temperature = config.temperature;
  }
  Object.assign(body, config.extraBody);
  return body;
}

async function callChatCompletions(config, messages) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180_000);
  const headers = { "content-type": "application/json" };
  if (config.apiKey) headers.authorization = `Bearer ${config.apiKey}`;
  try {
    const response = await fetch(chatCompletionsUrl(config.baseUrl), {
      method: "POST",
      headers,
      signal: controller.signal,
      body: JSON.stringify(buildChatBody(config, messages)),
    });
    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`模型接口返回不是 JSON：HTTP ${response.status} ${text.slice(0, 300)}`);
    }
    if (!response.ok) {
      const detail = data.error?.message || data.message || text.slice(0, 500);
      throw new Error(`模型接口错误：HTTP ${response.status} ${detail}`);
    }
    const choice = data.choices?.[0];
    const content = extractChoiceText(choice);
    if (content) return { content, usage: data.usage || null, finishReason: choice?.finish_reason || "" };
    const finish = choice?.finish_reason ? `，finish_reason=${choice.finish_reason}` : "";
    const hint = messages.some((message) => Array.isArray(message.content))
      ? "当前文件以图片模式发送，请确认模型支持 image_url/base64 视觉输入；也可以调整模型适配模式、思考选项或高级参数后重试。"
      : "请把输出上限调高后重试。";
    throw new Error(`模型返回了空正文${finish}。${hint}`);
  } catch (error) {
    if (error.name === "AbortError") throw new Error("模型调用超时");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function probeChatRequest(config, messages) {
  const body = buildChatBody(config, messages);
  return {
    url: chatCompletionsUrl(config.baseUrl),
    body: {
      ...body,
      messages: Array.isArray(body.messages) ? body.messages.length : 0,
    }
  };
}

function sanitizeFilename(value) {
  return String(value || "analysis")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 160) || "analysis";
}

function truncateText(text, limit) {
  const value = String(text || "");
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n\n[内容过长，已截断 ${value.length - limit} 个字符]`;
}

function cleanPdfText(text) {
  return String(text || "")
    .replace(/\n\s*--\s*\d+\s+of\s+\d+\s*--\s*\n/gi, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function resolveAnalysisFile(payload) {
  if (payload.path) return safeJoin(ROOT, String(payload.path));
  const out = String(payload.out || "downloads/海外投行报告").trim();
  const name = String(payload.name || "").trim();
  if (!name) throw new Error("缺少文件名");
  return safeJoin(ROOT, path.join(out, name));
}

function analysisOutputPath(filePath) {
  const dir = path.join(path.dirname(filePath), "analyses");
  const base = sanitizeFilename(path.basename(filePath).replace(/\.[^.]+$/, ""));
  return path.join(dir, `${base}.md`);
}

async function extractPdfContent(filePath, options = {}) {
  const data = await fs.readFile(filePath);
  const parser = new PDFParse({ data });
  try {
    const textResult = await parser.getText({ first: 1, last: 25 });
    const text = cleanPdfText(textResult.text);
    if (text.length >= 500) {
      return {
        mode: "text",
        text: truncateText(text, 40_000),
        chars: Math.min(text.length, 40_000),
        totalPages: textResult.total || 0,
        pagesUsed: textResult.pages?.length || 0,
      };
    }

    const totalPages = textResult.total || 0;
    const pagesToRead = options.imagePageLimit > 0
      ? Math.min(options.imagePageLimit, Math.max(1, totalPages || options.imagePageLimit))
      : Math.max(1, totalPages || 1);
    const shots = await parser.getScreenshot({
      first: 1,
      last: pagesToRead,
      desiredWidth: 1000,
      imageDataUrl: true,
    });
    const images = (shots.pages || [])
      .filter((page) => page.dataUrl)
      .map((page) => ({
        pageNumber: page.pageNumber,
        dataUrl: page.dataUrl,
        width: page.width,
        height: page.height,
      }));
    if (!images.length) throw new Error("PDF 无法抽取文本，也无法生成页面截图");
    return {
      mode: "images",
      images,
      text: text ? truncateText(text, 2000) : "",
      chars: text.length,
      totalPages: shots.total || textResult.total || 0,
      pagesUsed: images.length,
    };
  } finally {
    await parser.destroy?.();
  }
}

async function extractFileContent(filePath, options = {}) {
  const pageLimit = options.imagePageLimit || 0;
  const stat = await fs.stat(filePath);
  if (!stat.isFile()) throw new Error("不是可解读的文件");
  const cacheKey = `${filePath}:${stat.mtimeMs}:${stat.size}:${pageLimit}`;
  if (extractionCache.has(cacheKey)) return extractionCache.get(cacheKey);
  const ext = path.extname(filePath).toLowerCase();
  let result;
  if (ext === ".pdf") {
    result = await extractPdfContent(filePath, options);
  } else if (ext === ".docx") {
    const doc = await mammoth.extractRawText({ path: filePath });
    const text = doc.value.trim();
    if (!text) throw new Error("DOCX 没有抽取到文本");
    result = { mode: "text", text: truncateText(text, 40_000), chars: Math.min(text.length, 40_000), pagesUsed: 0, totalPages: 0 };
  } else if ([".txt", ".md", ".csv", ".json", ".log", ".html", ".htm"].includes(ext)) {
    const text = await fs.readFile(filePath, "utf8");
    result = { mode: "text", text: truncateText(text, 40_000), chars: Math.min(text.length, 40_000), pagesUsed: 0, totalPages: 0 };
  } else {
    throw new Error(`暂不支持解读 ${ext || "无扩展名"} 文件。当前支持 PDF、DOCX、TXT、MD、CSV、JSON。`);
  }
  extractionCache.set(cacheKey, result);
  if (extractionCache.size > 8) extractionCache.delete(extractionCache.keys().next().value);
  return result;
}

function buildAnalysisMessages(fileName, extracted, prompt) {
  const task = String(prompt || DEFAULT_ANALYSIS_PROMPT).trim() || DEFAULT_ANALYSIS_PROMPT;
  const system = [
    "你是严谨的中文投资研究助理。",
    "只根据用户提供的文件内容解读，不编造文件中没有的信息。",
    "如果证据来自截图，请尽量说明页码；如果看不清，请明确说明不确定。",
  ].join("\n");

  if (extracted.mode === "images") {
    const content = [
      {
        type: "text",
        text: [
          `文件名：${fileName}`,
          `说明：该 PDF 文本抽取不足，下面提供前 ${extracted.pagesUsed} 页截图用于视觉解读。`,
          extracted.text ? `可抽取到的少量文本：\n${extracted.text}` : "",
          `任务：\n${task}`,
        ].filter(Boolean).join("\n\n"),
      },
    ];
    for (const image of extracted.images) {
      content.push({ type: "text", text: `第 ${image.pageNumber} 页截图：` });
      content.push({ type: "image_url", image_url: { url: image.dataUrl, detail: "high" } });
    }
    return [
      { role: "system", content: system },
      { role: "user", content },
    ];
  }

  return [
    { role: "system", content: system },
    {
      role: "user",
      content: [
        `文件名：${fileName}`,
        `任务：\n${task}`,
        `文件文本：\n${extracted.text}`,
      ].join("\n\n"),
    },
  ];
}

function normalizeChatMessages(messages) {
  if (!Array.isArray(messages)) return [];
  const normalized = [];
  for (const message of messages) {
    const role = ["user", "assistant", "system"].includes(message?.role) ? message.role : "";
    const content = String(message?.content || "").trim();
    if (!role || !content) continue;
    normalized.push({ role, content: truncateText(content, 20_000) });
  }
  return normalized.slice(-24);
}

function reportPromptFromHistory(history, fallback = DEFAULT_ANALYSIS_PROMPT) {
  const latestUser = [...history].reverse().find((message) => message.role === "user");
  return latestUser?.content?.trim() || fallback;
}

function fileContextMessage(fileName, extracted, prompt, modeLabel) {
  const task = String(prompt || DEFAULT_ANALYSIS_PROMPT).trim() || DEFAULT_ANALYSIS_PROMPT;
  if (extracted.mode === "images") {
    const content = [
      {
        type: "text",
        text: [
          `文件名：${fileName}`,
          `Skill：${modeLabel}`,
          `说明：该 PDF 文本抽取不足，下面提供 ${extracted.pagesUsed} 页截图作为文件上下文。`,
          extracted.text ? `可抽取到的少量文本：\n${extracted.text}` : "",
          `用户请求：\n${task}`,
        ].filter(Boolean).join("\n\n"),
      },
    ];
    for (const image of extracted.images) {
      content.push({ type: "text", text: `第 ${image.pageNumber} 页截图：` });
      content.push({ type: "image_url", image_url: { url: image.dataUrl, detail: "high" } });
    }
    return { role: "user", content };
  }
  return {
    role: "user",
    content: [
      `文件名：${fileName}`,
      `Skill：${modeLabel}`,
      `用户请求：\n${task}`,
      `文件文本：\n${extracted.text}`,
    ].join("\n\n"),
  };
}

async function writeAnalysisMarkdown(filePath, config, extracted, content) {
  const outputPath = analysisOutputPath(filePath);
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const markdown = [
    `# ${path.basename(filePath)}`,
    "",
    `- 模型：${config.model}`,
    `- 生成时间：${new Date().toISOString()}`,
    `- 输入模式：${extracted.mode}`,
    extracted.totalPages ? `- PDF 页数：${extracted.totalPages}` : "",
    extracted.pagesUsed ? `- 使用页数：${extracted.pagesUsed}` : "",
    "",
    content.trim(),
    "",
  ].filter((line) => line !== "").join("\n");
  await fs.writeFile(outputPath, markdown);
  return outputPath;
}

async function analyzeFile(payload) {
  const config = resolveModelConfig(payload);
  const filePath = resolveAnalysisFile(payload);
  if (!fssync.existsSync(filePath)) throw new Error("文件不存在");
  const extracted = await extractFileContent(filePath, { imagePageLimit: config.imagePageLimit });
  const messages = buildAnalysisMessages(path.basename(filePath), extracted, payload.prompt);
  const modelResult = await callChatCompletions(config, messages);
  const outputPath = await writeAnalysisMarkdown(filePath, config, extracted, modelResult.content);
  return {
    result: modelResult.content,
    analysisPath: path.relative(ROOT, outputPath),
    source: {
      file: path.relative(ROOT, filePath),
      mode: extracted.mode,
      chars: extracted.chars,
      pagesUsed: extracted.pagesUsed,
      totalPages: extracted.totalPages,
    },
    usage: modelResult.usage,
  };
}

async function chatWorkbench(payload) {
  const config = resolveModelConfig(payload);
  const skill = String(payload.skill || "chat").trim();
  const includeFile = Boolean(payload.includeFile || skill === "report_analysis");
  const history = normalizeChatMessages(payload.messages);
  const system = [
    "你是一个中文投资研究对话工作台。",
    "你可以和用户围绕研报、公司、行业和交易线索持续对话。",
    "如果使用了文件上下文，只根据文件与会话内容回答；不确定时直接说明。",
    "回答要结构清晰，优先给结论、依据、风险和可继续追问的方向。",
  ].join("\n");
  const modelMessages = [{ role: "system", content: system }];
  const prior = history.slice(0, -1);
  const latestPrompt = String(payload.prompt || "").trim() || reportPromptFromHistory(history, skill === "report_analysis" ? DEFAULT_ANALYSIS_PROMPT : "");
  let source = null;
  let sources = [];
  let analysisPath = "";

  if (includeFile) {
    const filePayloads = Array.isArray(payload.files) && payload.files.length ? payload.files : [payload.file || payload];
    modelMessages.push(...prior);
    const extractedFiles = [];
    for (const filePayload of filePayloads.slice(0, 5)) {
      const filePath = resolveAnalysisFile(filePayload);
      if (!fssync.existsSync(filePath)) throw new Error("文件不存在");
      const extracted = await extractFileContent(filePath, { imagePageLimit: config.imagePageLimit });
      extractedFiles.push({ filePath, extracted });
      sources.push({
        file: path.relative(ROOT, filePath),
        mode: extracted.mode,
        chars: extracted.chars,
        pagesUsed: extracted.pagesUsed,
        totalPages: extracted.totalPages,
      });
      const prompt = filePayloads.length === 1
        ? latestPrompt
        : "请先把这份文件作为引用上下文。稍后综合所有引用回答用户请求。";
      modelMessages.push(fileContextMessage(path.basename(filePath), extracted, prompt, skill === "report_analysis" ? "解析研报" : "引用文件"));
    }
    if (filePayloads.length > 1) {
      modelMessages.push({ role: "user", content: `用户请求：\n${latestPrompt}` });
    }
    source = sources[0] || null;
    const modelResult = await callChatCompletions(config, modelMessages);
    if (skill === "report_analysis" && extractedFiles.length === 1) {
      analysisPath = path.relative(ROOT, await writeAnalysisMarkdown(extractedFiles[0].filePath, config, extractedFiles[0].extracted, modelResult.content));
    }
    return { reply: modelResult.content, skill, source, sources, analysisPath, usage: modelResult.usage };
  }

  if (!history.length) throw new Error("请输入消息");
  modelMessages.push(...history);
  const modelResult = await callChatCompletions(config, modelMessages);
  return { reply: modelResult.content, skill, source, sources, analysisPath, usage: modelResult.usage };
}

async function route(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (req.method === "OPTIONS") {
    res.writeHead(204, corsHeaders({ "cache-control": "no-store" }));
    res.end();
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/status") {
    send(res, 200, {
      credentialsAvailable: Boolean(process.env.ZSXQ_COOKIE),
      aduidAvailable: Boolean(process.env.ZSXQ_ADUID),
      jobs: Array.from(jobs.values()).map(publicJob).reverse(),
      defaults: {
        group: "88888142214212",
        tag: "海外投行报告",
        out: "downloads/海外投行报告",
        ext: "pdf",
        limit: 20,
      },
    });
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/model-config") {
    send(res, 200, modelDefaults());
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/ai/test") {
    try {
      const payload = await parseJson(req);
      const config = resolveModelConfig(payload);
      const result = await callChatCompletions(config, [
        { role: "system", content: "你是一个接口连通性测试助手。" },
        { role: "user", content: "请只回复：模型连接正常" },
      ]);
      send(res, 200, { ok: true, result: result.content, usage: result.usage });
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/ai/analyze") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await analyzeFile(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/chat") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await chatWorkbench(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/jobs") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await startJob(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/search") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await searchFiles(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  const stopMatch = url.pathname.match(/^\/api\/jobs\/([^/]+)\/stop$/);
  if (req.method === "POST" && stopMatch) {
    const job = await stopJob(stopMatch[1]);
    send(res, job ? 200 : 404, job || { error: "job 不存在" });
    return;
  }

  const eventMatch = url.pathname.match(/^\/api\/jobs\/([^/]+)\/events$/);
  if (req.method === "GET" && eventMatch) {
    const job = jobs.get(eventMatch[1]);
    if (!job) return send(res, 404, { error: "job 不存在" });
    res.writeHead(200, corsHeaders({
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store",
      connection: "keep-alive",
    }));
    job.clients.add(res);
    res.write(`event: state\ndata: ${JSON.stringify(publicJob(job))}\n\n`);
    for (const item of job.logs) {
      res.write(`event: log\ndata: ${JSON.stringify(item)}\n\n`);
    }
    req.on("close", () => job.clients.delete(res));
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/downloads") {
    try {
      send(res, 200, await readDownloads(url.searchParams.get("out") || "downloads/海外投行报告"));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "GET" && url.pathname.startsWith("/downloads/")) {
    try {
      const filePath = safeJoin(ROOT, decodeURIComponent(url.pathname.slice(1)));
      if (!fssync.existsSync(filePath)) return send(res, 404, "Not found", "text/plain; charset=utf-8");
      const stat = await fs.stat(filePath);
      if (!stat.isFile()) return send(res, 404, "Not found", "text/plain; charset=utf-8");
      res.writeHead(200, corsHeaders({
        "content-type": "application/octet-stream",
        "content-length": stat.size,
      }));
      fssync.createReadStream(filePath).pipe(res);
    } catch {
      send(res, 404, "Not found", "text/plain; charset=utf-8");
    }
    return;
  }

  const staticPath = url.pathname === "/" ? "/index.html" : url.pathname;
  try {
    const filePath = safeJoin(PUBLIC_DIR, decodeURIComponent(staticPath.slice(1)));
    if (!fssync.existsSync(filePath)) return send(res, 404, "Not found", "text/plain; charset=utf-8");
    const ext = path.extname(filePath);
    const types = {
      ".html": "text/html; charset=utf-8",
      ".css": "text/css; charset=utf-8",
      ".js": "application/javascript; charset=utf-8",
      ".svg": "image/svg+xml",
    };
    send(res, 200, await fs.readFile(filePath, ext === ".svg" ? "utf8" : undefined), types[ext] || "application/octet-stream");
  } catch {
    send(res, 404, "Not found", "text/plain; charset=utf-8");
  }
}

async function main() {
  await fs.mkdir(PUBLIC_DIR, { recursive: true });
  const server = http.createServer((req, res) => {
    route(req, res).catch((error) => send(res, 500, { error: error.message }));
  });
  server.listen(DEFAULT_PORT, "127.0.0.1", () => {
    console.log(`知识星球下载工具已启动：http://127.0.0.1:${DEFAULT_PORT}`);
  });
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
