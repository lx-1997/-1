#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const fssync = require("node:fs");
const path = require("node:path");
const { Readable } = require("node:stream");
const { pipeline } = require("node:stream/promises");
const { chromium } = require("playwright-core");

const API_ORIGIN = "https://api.zsxq.com";
const WEB_ORIGIN = "https://wx.zsxq.com";
const API_BASE = `${API_ORIGIN}/v2`;
const X_VERSION = "2.91.0";
const V3_X_VERSION = "3.18.0";
const DEFAULT_GROUP = "88888142214212";
const DEFAULT_PROFILE_DIR = path.resolve(".zsxq-browser-profile");
const DEFAULT_OUTPUT_DIR = path.resolve("downloads");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function parseArgs(argv) {
  const args = {
    group: DEFAULT_GROUP,
    out: DEFAULT_OUTPUT_DIR,
    profile: DEFAULT_PROFILE_DIR,
    count: 20,
    sort: "by_download_count",
    tag: "",
    hashtagId: "",
    loginTimeout: 5 * 60 * 1000,
    limit: 0,
    maxPages: 0,
    ext: [],
    listOnly: false,
    headless: false,
    cookie: "",
    cookieFile: "",
    curlFile: "",
    selectionFile: "",
    aduid: "",
  };

  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i];
    if (arg === "--group") args.group = next();
    else if (arg === "--url") args.group = extractGroupId(next());
    else if (arg === "--out") args.out = path.resolve(next());
    else if (arg === "--profile") args.profile = path.resolve(next());
    else if (arg === "--count") args.count = Number(next());
    else if (arg === "--sort") args.sort = normalizeSort(next());
    else if (arg === "--tag") args.tag = next();
    else if (arg === "--hashtag-id") args.hashtagId = next();
    else if (arg === "--limit") args.limit = Number(next());
    else if (arg === "--max-pages") args.maxPages = Number(next());
    else if (arg === "--ext") args.ext = next().split(",").map((x) => x.trim().toLowerCase().replace(/^\./, "")).filter(Boolean);
    else if (arg === "--login-timeout") args.loginTimeout = Number(next()) * 1000;
    else if (arg === "--cookie") args.cookie = next();
    else if (arg === "--cookie-file") args.cookieFile = path.resolve(next());
    else if (arg === "--curl-file") args.curlFile = path.resolve(next());
    else if (arg === "--selection-file") args.selectionFile = path.resolve(next());
    else if (arg === "--aduid") args.aduid = next();
    else if (arg === "--list-only") args.listOnly = true;
    else if (arg === "--headless") args.headless = true;
    else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`未知参数：${arg}`);
    }
  }

  if (!/^\d+$/.test(String(args.group))) throw new Error(`无效 group id：${args.group}`);
  if (!Number.isFinite(args.count) || args.count < 1 || args.count > 20) args.count = 20;
  if (!Number.isFinite(args.limit) || args.limit < 0) args.limit = 0;
  if (!Number.isFinite(args.maxPages) || args.maxPages < 0) args.maxPages = 0;
  return args;
}

function printHelp() {
  console.log(`知识星球文件下载器

用法：
  node zsxq-downloader.js --group 88888142214212
  node zsxq-downloader.js --url https://wx.zsxq.com/group/88888142214212 --out ./downloads

常用参数：
  --group <id>          星球 ID，默认 ${DEFAULT_GROUP}
  --url <url>           从星球 URL 提取 ID
  --out <dir>           下载目录，默认 ./downloads
  --profile <dir>       浏览器登录档案目录，默认 ./.zsxq-browser-profile
  --sort <mode>         by_download_count 或 by_create_time
  --tag <name>          按星球标签下载主题附件，例如 海外投行报告
  --hashtag-id <id>     按标签 ID 下载主题附件
  --ext <list>          只下载指定扩展名，例如 pdf,docx,xlsx
  --limit <n>           最多下载 n 个文件，0 表示不限
  --max-pages <n>       最多抓取 n 页，0 表示不限
  --list-only           只抓取列表和写 manifest，不下载文件
  --login-timeout <sec> 等待扫码/验证码登录的秒数，默认 300
  --cookie-file <path>  从本地文件读取 Cookie，提供后不打开浏览器
  --curl-file <path>    从浏览器复制的 curl 命令中提取 Cookie 和 X-Aduid
  --selection-file <p>  只下载 JSON 文件里指定的文件列表
  --cookie <text>       直接传 Cookie 字符串，不推荐，容易进入 shell 历史
  --aduid <text>        可选，手动指定 X-Aduid；不传会自动生成

也可以用环境变量：
  ZSXQ_COOKIE='a=b; c=d' node zsxq-downloader.js --group 88888142214212
`);
}

function extractGroupId(value) {
  const match = String(value).match(/group\/(\d+)/);
  if (!match) throw new Error(`无法从 URL 提取 group id：${value}`);
  return match[1];
}

function normalizeSort(value) {
  if (value === "counts" || value === "download" || value === "by_download_count") return "by_download_count";
  if (value === "time" || value === "create_time" || value === "by_create_time") return "by_create_time";
  throw new Error(`不支持的排序：${value}`);
}

function chromeExecutablePath() {
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ];
  const found = candidates.find((candidate) => fssync.existsSync(candidate));
  if (!found) {
    throw new Error("未找到 Chrome/Chromium。请安装 Chrome，或在脚本里补充 executablePath。");
  }
  return found;
}

function uuidLikeFrontend() {
  let id = "";
  for (let i = 0; i < 32; i += 1) {
    id += crypto.randomInt(16).toString(16);
    if ([8, 12, 16, 20].includes(i)) id += "-";
  }
  return id;
}

function signedHeaders(url, aduid) {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const requestId = uuidLikeFrontend();
  const version = url.includes("/v3/") ? V3_X_VERSION : X_VERSION;
  const normalizedUrl = normalizeSignatureUrl(url);
  const signature = crypto
    .createHash("sha1")
    .update(`${normalizedUrl} ${timestamp} ${requestId}`)
    .digest("hex");

  return {
    Accept: "application/json, text/plain, */*",
    Origin: WEB_ORIGIN,
    Referer: `${WEB_ORIGIN}/`,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "X-Request-Id": requestId,
    "X-Version": version,
    "X-Signature": signature,
    "X-Timestamp": timestamp,
    "X-Aduid": aduid,
  };
}

function normalizeSignatureUrl(url) {
  const [base, ...queryParts] = url.split("?");
  if (!queryParts.length) return base;
  return `${base}?${queryParts.join("?").replace(/'/g, "%27")}`;
}

async function getAduid(page) {
  await page.goto(WEB_ORIGIN, { waitUntil: "domcontentloaded" }).catch(() => undefined);
  return page.evaluate(() => {
    function makeId() {
      let id = "";
      for (let i = 0; i < 32; i += 1) {
        id += Math.floor(Math.random() * 16).toString(16);
        if ([8, 12, 16, 20].includes(i)) id += "-";
      }
      return id;
    }
    let aduid = localStorage.getItem("XAduid");
    if (!aduid) {
      aduid = makeId();
      localStorage.setItem("XAduid", aduid);
    }
    return aduid;
  });
}

async function cookieHeader(context) {
  if (!context) return "";
  if (typeof context === "string") return context;
  if (context.cookie) return context.cookie;
  const cookies = await context.cookies([API_ORIGIN, WEB_ORIGIN]);
  return cookies
    .filter((cookie) => cookie.domain.includes("zsxq.com"))
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
}

function normalizeCookieText(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";

  const withoutHeader = text.replace(/^cookie:\s*/i, "").trim();
  const lines = withoutHeader.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

  const netscapeCookies = [];
  for (const line of lines) {
    if (line.startsWith("#")) continue;
    const parts = line.split("\t");
    if (parts.length >= 7) {
      netscapeCookies.push(`${parts[5]}=${parts.slice(6).join("\t")}`);
    }
  }
  if (netscapeCookies.length) return netscapeCookies.join("; ");

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
  const aduid = extractCurlHeader(text, "x-aduid");
  return {
    cookie: normalizeCookieText(cookie),
    aduid,
  };
}

async function resolveManualAuth(args) {
  if (args.curlFile) {
    const parsed = parseCurlAuth(await fs.readFile(args.curlFile, "utf8"));
    return {
      cookie: parsed.cookie,
      aduid: args.aduid || process.env.ZSXQ_ADUID || parsed.aduid || "",
    };
  }
  if (args.cookieFile) {
    return {
      cookie: normalizeCookieText(await fs.readFile(args.cookieFile, "utf8")),
      aduid: args.aduid || process.env.ZSXQ_ADUID || "",
    };
  }
  return {
    cookie: normalizeCookieText(args.cookie || process.env.ZSXQ_COOKIE || ""),
    aduid: args.aduid || process.env.ZSXQ_ADUID || "",
  };
}

async function apiGet(context, aduid, url) {
  const headers = signedHeaders(url, aduid);
  const cookie = await cookieHeader(context);
  if (cookie) headers.Cookie = cookie;

  const response = await fetch(url, { headers, redirect: "follow" });
  const text = await response.text();
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error(`接口返回不是 JSON：HTTP ${response.status} ${text.slice(0, 200)}`);
  }

  if (!response.ok || !payload.succeeded) {
    const err = new Error(payload.info || payload.error || `HTTP ${response.status}`);
    err.status = response.status;
    err.code = payload.code;
    err.payload = payload;
    throw err;
  }
  return payload.resp_data;
}

async function waitForLogin(context, page, aduid, args) {
  const probe = () => listFilesPage(context, aduid, args, "", "");
  try {
    await probe();
    return;
  } catch (error) {
    if (error.status !== 401 && error.code !== 401) throw error;
  }

  console.log("需要登录：已打开 Chrome，请在页面里完成微信扫码或手机号验证码登录。");
  console.log(`等待登录最多 ${Math.round(args.loginTimeout / 1000)} 秒...`);
  await page.goto(`${WEB_ORIGIN}/group/${args.group}/files`, { waitUntil: "domcontentloaded" }).catch(() => undefined);
  await page.bringToFront().catch(() => undefined);

  const started = Date.now();
  let lastMessageAt = 0;
  while (Date.now() - started < args.loginTimeout) {
    try {
      await probe();
      console.log("登录态验证成功。");
      return;
    } catch (error) {
      if (error.status !== 401 && error.code !== 401) throw error;
      if (Date.now() - lastMessageAt > 30_000) {
        const left = Math.max(0, Math.round((args.loginTimeout - (Date.now() - started)) / 1000));
        console.log(`仍在等待登录，剩余约 ${left} 秒...`);
        lastMessageAt = Date.now();
      }
      await sleep(3000);
    }
  }
  throw new Error("等待登录超时。重新运行脚本会继续使用同一个浏览器档案。");
}

function listFilesUrl(args, endTime, index) {
  const url = new URL(`${API_BASE}/groups/${args.group}/files`);
  url.searchParams.set("count", String(args.count));
  if (endTime) url.searchParams.set("end_time", endTime);
  if (index) url.searchParams.set("index", index);
  if (args.sort) url.searchParams.set("sort", args.sort);
  return url.toString();
}

async function listFilesPage(context, aduid, args, endTime, index) {
  return apiGet(context, aduid, listFilesUrl(args, endTime, index));
}

async function listDefaultHashtags(context, aduid, groupId) {
  const data = await apiGet(context, aduid, `${API_BASE}/groups/${groupId}/hashtags/defaults`);
  return data.hashtags || [];
}

function normalizeTagName(value) {
  return String(value || "").replace(/^#+|#+$/g, "").trim();
}

async function resolveHashtag(context, aduid, args) {
  if (args.hashtagId) {
    return { hashtagId: args.hashtagId, title: args.tag || `#${args.hashtagId}#` };
  }
  if (!args.tag) return null;

  const target = normalizeTagName(args.tag);
  const hashtags = await listDefaultHashtags(context, aduid, args.group);
  const found = hashtags.find((hashtag) => normalizeTagName(hashtag.title) === target);
  if (!found) {
    const available = hashtags.map((hashtag) => hashtag.title).join(", ");
    throw new Error(`没有找到标签：${args.tag}。可用标签：${available}`);
  }
  return {
    hashtagId: String(found.hashtag_id),
    title: found.title,
    topicsCount: found.topics_count || 0,
  };
}

function listHashtagTopicsUrl(hashtagId, count, endTime) {
  const url = new URL(`${API_BASE}/hashtags/${hashtagId}/topics`);
  url.searchParams.set("count", String(count));
  if (endTime) url.searchParams.set("end_time", endTime);
  return url.toString();
}

async function listHashtagTopicsPage(context, aduid, hashtagId, count, endTime) {
  return apiGet(context, aduid, listHashtagTopicsUrl(hashtagId, count, endTime));
}

function getFileId(item) {
  return item?.file?.file_id || item?.file_id || item?.id;
}

function getFileMeta(item) {
  const file = item.file || item;
  return {
    fileId: getFileId(item),
    name: file.name || `${getFileId(item)}.bin`,
    size: file.size || file.file_size || 0,
    createTime: file.create_time || item.create_time || "",
    downloadCount: file.download_count || 0,
    raw: item,
  };
}

function getTopicFiles(topic) {
  const typed = topic?.[topic.type] || topic?.talk || topic?.task || topic?.solution || {};
  return typed.files || topic.files || [];
}

function getTopicFileMeta(topic, file, hashtag) {
  return {
    fileId: file.file_id,
    name: file.name || `${file.file_id}.bin`,
    size: file.size || file.file_size || 0,
    createTime: file.create_time || topic.create_time || "",
    downloadCount: file.download_count || 0,
    topicId: topic.topic_id,
    topicCreateTime: topic.create_time || "",
    hashtag: hashtag?.title || "",
    raw: { topic, file },
  };
}

function fileExtension(name) {
  const ext = path.extname(name || "").toLowerCase().replace(/^\./, "");
  return ext;
}

function sanitizeFilename(name) {
  return String(name || "unnamed")
    .replace(/[\u0000-\u001f]/g, "")
    .replace(/[\\/:"*?<>|]+/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180) || "unnamed";
}

async function uniquePath(dir, preferredName, fileId) {
  const parsed = path.parse(sanitizeFilename(preferredName));
  const stem = parsed.name || String(fileId);
  const ext = parsed.ext || "";
  let candidate = path.join(dir, `${stem}${ext}`);
  if (!fssync.existsSync(candidate)) return candidate;
  candidate = path.join(dir, `${stem} [${fileId}]${ext}`);
  if (!fssync.existsSync(candidate)) return candidate;
  for (let i = 2; i < 1000; i += 1) {
    candidate = path.join(dir, `${stem} [${fileId}-${i}]${ext}`);
    if (!fssync.existsSync(candidate)) return candidate;
  }
  throw new Error(`无法生成不冲突文件名：${preferredName}`);
}

async function readManifest(outDir, groupId) {
  const manifestPath = path.join(outDir, "manifest.json");
  try {
    const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
    manifest.groupId ||= groupId;
    manifest.files ||= {};
    return manifest;
  } catch {
    return { groupId, updatedAt: "", files: {} };
  }
}

async function writeManifest(outDir, manifest) {
  manifest.updatedAt = new Date().toISOString();
  const manifestPath = path.join(outDir, "manifest.json");
  await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  await writeCsv(outDir, manifest);
}

async function writeCsv(outDir, manifest) {
  const rows = [["file_id", "name", "path", "size", "create_time", "download_count", "topic_id", "topic_create_time", "hashtag", "status", "downloaded_at"]];
  for (const [fileId, info] of Object.entries(manifest.files)) {
    rows.push([
      fileId,
      info.name || "",
      info.path || "",
      info.size || "",
      info.createTime || "",
      info.downloadCount || "",
      info.topicId || "",
      info.topicCreateTime || "",
      info.hashtag || "",
      info.status || "",
      info.downloadedAt || "",
    ]);
  }
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\n") + "\n";
  await fs.writeFile(path.join(outDir, "files.csv"), csv);
}

function csvCell(value) {
  const s = String(value ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

async function downloadUrlForFile(context, aduid, fileId) {
  const data = await apiGet(context, aduid, `${API_BASE}/files/${fileId}/download_url`);
  if (!data.download_url) throw new Error(`文件 ${fileId} 没有返回 download_url`);
  return data.download_url;
}

async function downloadFile(downloadUrl, targetPath) {
  const response = await fetch(downloadUrl, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    },
    redirect: "follow",
  });
  if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`);

  const tmpPath = `${targetPath}.part`;
  if (!response.body) throw new Error("下载响应没有 body");
  await pipeline(Readable.fromWeb(response.body), fssync.createWriteStream(tmpPath));
  await fs.rename(tmpPath, targetPath);
}

async function collectFiles(context, aduid, args) {
  if (args.selectionFile) return readSelectionFiles(args.selectionFile);

  const hashtag = await resolveHashtag(context, aduid, args);
  if (hashtag) return collectHashtagFiles(context, aduid, args, hashtag);

  const seen = new Set();
  const files = [];
  let endTime = "";
  let index = "";
  let pageNo = 0;

  while (true) {
    pageNo += 1;
    if (args.maxPages && pageNo > args.maxPages) break;
    const data = await listFilesPage(context, aduid, args, endTime, index);
    const pageFiles = data.files || [];
    console.log(`第 ${pageNo} 页：${pageFiles.length} 个文件`);

    let added = 0;
    for (const item of pageFiles) {
      const meta = getFileMeta(item);
      if (!meta.fileId || seen.has(meta.fileId)) continue;
      seen.add(meta.fileId);
      if (args.ext.length && !args.ext.includes(fileExtension(meta.name))) continue;
      files.push(meta);
      added += 1;
      if (args.limit && files.length >= args.limit) return files;
    }

    index = data.index || "";
    const last = pageFiles[pageFiles.length - 1];
    endTime = last?.file?.create_time || last?.create_time || endTime;
    if (!pageFiles.length || pageFiles.length < args.count || !added && !index) break;
  }
  return files;
}

async function readSelectionFiles(selectionFile) {
  const raw = JSON.parse(await fs.readFile(selectionFile, "utf8"));
  const items = Array.isArray(raw) ? raw : raw.files;
  if (!Array.isArray(items) || !items.length) throw new Error("选中文件列表为空");
  const seen = new Set();
  const files = [];
  for (const item of items) {
    const fileId = String(item.fileId || item.file_id || "").trim();
    if (!fileId || seen.has(fileId)) continue;
    seen.add(fileId);
    files.push({
      fileId,
      name: item.name || `${fileId}.bin`,
      size: item.size || 0,
      createTime: item.createTime || item.create_time || "",
      downloadCount: item.downloadCount || item.download_count || 0,
      topicId: item.topicId || item.topic_id || "",
      topicCreateTime: item.topicCreateTime || item.topic_create_time || "",
      hashtag: item.hashtag || "",
      raw: item,
    });
  }
  console.log(`读取选中文件：${files.length} 个`);
  return files;
}

async function collectHashtagFiles(context, aduid, args, hashtag) {
  console.log(`按标签抓取：${hashtag.title} (${hashtag.hashtagId})${hashtag.topicsCount ? `，主题数约 ${hashtag.topicsCount}` : ""}`);
  const seen = new Set();
  const files = [];
  let endTime = "";
  let pageNo = 0;

  while (true) {
    pageNo += 1;
    if (args.maxPages && pageNo > args.maxPages) break;

    const data = await listHashtagTopicsPage(context, aduid, hashtag.hashtagId, args.count, endTime);
    const topics = data.topics || [];
    console.log(`标签第 ${pageNo} 页：${topics.length} 个主题`);

    let added = 0;
    for (const topic of topics) {
      for (const file of getTopicFiles(topic)) {
        const meta = getTopicFileMeta(topic, file, hashtag);
        if (!meta.fileId || seen.has(meta.fileId)) continue;
        seen.add(meta.fileId);
        if (args.ext.length && !args.ext.includes(fileExtension(meta.name))) continue;
        files.push(meta);
        added += 1;
        if (args.limit && files.length >= args.limit) return files;
      }
    }

    const last = topics[topics.length - 1];
    endTime = last?.create_time || endTime;
    if (!topics.length || topics.length < args.count || !added) break;
  }
  return files;
}

async function main() {
  const args = parseArgs(process.argv);
  await fs.mkdir(args.out, { recursive: true });

  const manualAuth = await resolveManualAuth(args);
  const manualCookie = manualAuth.cookie;
  let context = null;
  let auth = null;
  let aduid = manualAuth.aduid;

  if (manualCookie) {
    auth = { cookie: manualCookie };
    aduid ||= uuidLikeFrontend();
    console.log("已使用手动 Cookie 模式，不打开浏览器。");
  } else {
    context = await chromium.launchPersistentContext(args.profile, {
      headless: args.headless,
      executablePath: chromeExecutablePath(),
      acceptDownloads: true,
      viewport: { width: 1280, height: 900 },
    });
    const page = context.pages()[0] || await context.newPage();
    aduid = await getAduid(page);
    auth = context;
    await waitForLogin(auth, page, aduid, args);
  }

  try {
    if (manualCookie) {
      try {
        await listFilesPage(auth, aduid, args, "", "");
      } catch (error) {
        if (error.status === 401 || error.code === 401) {
          throw new Error("Cookie 验证失败：接口返回 401。请重新从已登录的 wx.zsxq.com / api.zsxq.com 请求里复制 Cookie。");
        }
        throw error;
      }
      console.log("Cookie 验证成功。");
    }

    const manifest = await readManifest(args.out, args.group);
    const files = await collectFiles(auth, aduid, args);
    console.log(`共抓到 ${files.length} 个待处理文件。`);

    for (let i = 0; i < files.length; i += 1) {
      const meta = files[i];
      const existing = manifest.files[meta.fileId];
      if (existing?.status === "downloaded" && existing.path && fssync.existsSync(existing.path)) {
        console.log(`[${i + 1}/${files.length}] 跳过已下载：${meta.name}`);
        continue;
      }

      manifest.files[meta.fileId] = {
        ...(existing || {}),
        name: meta.name,
        size: meta.size,
        createTime: meta.createTime,
        downloadCount: meta.downloadCount,
        topicId: meta.topicId || "",
        topicCreateTime: meta.topicCreateTime || "",
        hashtag: meta.hashtag || "",
        status: args.listOnly ? "listed" : "pending",
      };

      if (args.listOnly) {
        console.log(`[${i + 1}/${files.length}] 已列入：${meta.name}`);
        continue;
      }

      console.log(`[${i + 1}/${files.length}] 下载：${meta.name}`);
      try {
        const downloadUrl = await downloadUrlForFile(auth, aduid, meta.fileId);
        const targetPath = await uniquePath(args.out, meta.name, meta.fileId);
        await downloadFile(downloadUrl, targetPath);
        manifest.files[meta.fileId] = {
          ...manifest.files[meta.fileId],
          path: targetPath,
          status: "downloaded",
          downloadedAt: new Date().toISOString(),
        };
      } catch (error) {
        manifest.files[meta.fileId] = {
          ...manifest.files[meta.fileId],
          status: "failed",
          error: error.message,
        };
        console.error(`  失败：${error.message}`);
      }

      if ((i + 1) % 5 === 0) await writeManifest(args.out, manifest);
      await sleep(700);
    }

    await writeManifest(args.out, manifest);
    console.log(`完成。manifest：${path.join(args.out, "manifest.json")}`);
    console.log(`索引 CSV：${path.join(args.out, "files.csv")}`);
  } finally {
    if (context) await context.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
