"use strict";

if (window.self !== window.top) {
  document.body.classList.add("embedded");
}

const form = document.querySelector("#jobForm");
const authPanel = document.querySelector(".auth");
const logBox = document.querySelector("#logBox");
const stopBtn = document.querySelector("#stopBtn");
const listBtn = document.querySelector("#listBtn");
const downloadBtn = document.querySelector("#downloadBtn");
const summarizeHitsBtn = document.querySelector("#summarizeHitsBtn");
const refreshBtn = document.querySelector("#refreshBtn");
const clearLogBtn = document.querySelector("#clearLogBtn");
const browserLoginBtn = document.querySelector("#browserLoginBtn");
const credentialStatus = document.querySelector("#credentialStatus");
const jobStatus = document.querySelector("#jobStatus");
const fileCount = document.querySelector("#fileCount");
const filesBody = document.querySelector("#filesBody");
const sizeText = document.querySelector("#sizeText");
const outputPath = document.querySelector("#outputPath");
const keywordInput = document.querySelector("#keywordInput");
const searchTagInput = document.querySelector("#searchTagInput");
const searchPagesInput = document.querySelector("#searchPagesInput");
const resultLimitInput = document.querySelector("#resultLimitInput");
const searchBtn = document.querySelector("#searchBtn");
const loadTagsBtn = document.querySelector("#loadTagsBtn");
const tagChips = document.querySelector("#tagChips");
const selectAllBtn = document.querySelector("#selectAllBtn");
const downloadSelectedBtn = document.querySelector("#downloadSelectedBtn");
const summarizeSearchBtn = document.querySelector("#summarizeSearchBtn");
const searchBody = document.querySelector("#searchBody");
const searchSummary = document.querySelector("#searchSummary");
const searchError = document.querySelector("#searchError");
const modelForm = document.querySelector("#modelForm");
const modelStatus = document.querySelector("#modelStatus");
const modelBaseUrl = document.querySelector("#modelBaseUrl");
const modelApiKey = document.querySelector("#modelApiKey");
const saveApiKey = document.querySelector("#saveApiKey");
const modelName = document.querySelector("#modelName");
const modelCompat = document.querySelector("#modelCompat");
const modelTemperature = document.querySelector("#modelTemperature");
const modelMaxTokens = document.querySelector("#modelMaxTokens");
const modelThinking = document.querySelector("#modelThinking");
const modelExtraBody = document.querySelector("#modelExtraBody");
const testModelBtn = document.querySelector("#testModelBtn");
const aiFileFilter = document.querySelector("#aiFileFilter");
const aiFileSelect = document.querySelector("#aiFileSelect");
const chatSkill = document.querySelector("#chatSkill");
const includeFileContext = document.querySelector("#includeFileContext");
const chatMessagesEl = document.querySelector("#chatMessages");
const chatInput = document.querySelector("#chatInput");
const chatStatus = document.querySelector("#chatStatus");
const previewPanel = document.querySelector("#previewPanel");
const previewTitle = document.querySelector("#previewTitle");
const previewStatus = document.querySelector("#previewStatus");
const previewFrame = document.querySelector("#previewFrame");
const previewOpen = document.querySelector("#previewOpen");
const previewCloseBtn = document.querySelector("#previewCloseBtn");
const refreshAiFilesBtn = document.querySelector("#refreshAiFilesBtn");
const parseReportBtn = document.querySelector("#parseReportBtn");
const sendChatBtn = document.querySelector("#sendChatBtn");
const syncToMainAiBtn = document.querySelector("#syncToMainAiBtn");
const clearChatBtn = document.querySelector("#clearChatBtn");
const addReferenceBtn = document.querySelector("#addReferenceBtn");
const chatReferencesEl = document.querySelector("#chatReferences");

const DEFAULT_ANALYSIS_PROMPT = [
  "请以华尔街 buy-side 投资经理/投委会备忘录的标准解读这份海外投行研报。",
  "目标不是复述研报，而是把研报转成可决策、可复核、可跟踪的投资摘要。",
  "严格使用 Markdown；不要写代码块；不要使用 --- 分隔线；不要输出连续长段。",
  "这是给多数用户阅读的解析版：正文和表格不要出现页码、Exhibit、章节位置，也不要单独设置“位置/页码/出处”列。",
  "不要输出任何技术元信息，例如输入模式、文件路径、Markdown 路径、导出时间、模型名称、生成时间。",
  "每个表格最多 3 列、最多 5 行；每个单元格尽量不超过 42 个中文字符，长内容拆成 bullet。",
  "",
  "## 投资判断",
  "- **投资动作**：用一句话说明评级/方向、目标价或隐含上行空间；没有披露就写“研报未披露”。",
  "- **为什么现在重要**：说明这份研报今天对股价/预期差最重要的变化。",
  "- **核心分歧**：说明多空分歧的核心，不超过 1 句。",
  "- **跟踪窗口**：说明 3-12 个月最重要的验证窗口。",
  "",
  "## 关键数字",
  "| 指标 | 研报数据 | 投资含义 |",
  "| --- | --- | --- |",
  "| 目标价/评级/上行空间 | ... | ... |",
  "| EPS/PE/收入/毛利/价格等关键变量 | ... | ... |",
  "",
  "## 预期差与情景推演",
  "| 情景 | 触发条件 | 投资含义 |",
  "| --- | --- | --- |",
  "| 乐观 | 哪个变量超预期 | 股价/估值/盈利怎么上修 |",
  "| 基准 | 研报主假设如何兑现 | 为什么维持当前判断 |",
  "| 悲观 | 哪个变量低于预期 | 什么情况下需要降权或退出 |",
  "",
  "## 投资逻辑",
  "| 论点 | 证据 | 结论 |",
  "| --- | --- | --- |",
  "| ... | 只写关键事实和数字，不写页码 | 对盈利、估值或情绪的影响 |",
  "",
  "## 催化剂与跟踪清单",
  "| 指标/事件 | 好于预期 | 差于预期 |",
  "| --- | --- | --- |",
  "| 价格/订单/渠道/政策/财报等 | 加仓、上修或继续持有的含义 | 降权、观望或复核的含义 |",
  "",
  "## 推翻条件与风险",
  "| 推翻条件 | 观察信号 | 需要动作 |",
  "| --- | --- | --- |",
  "| ... | ... | 重新估值、降权、退出或继续观察 |",
  "",
  "## 证据质量与待确认",
  "- **高可信**：列出证据充分的结论，不写具体页码。",
  "- **待确认**：列出研报没有披露、OCR 不清晰或需要外部数据复核的点。",
  "- **下一步问题**：给分析师/Agent 的 3 个追问。",
  "",
  "证据不足时明确写“不确定/研报未披露”。引用定位保留在后台复核，不要放进给用户看的解析正文。",
].join("\n");

const WORKBENCH_BASE_URL = new URL(".", window.location.href);
const DEFAULT_MAX_PAGES = 5;

function workbenchUrl(path) {
  return new URL(String(path).replace(/^\/+/, ""), WORKBENCH_BASE_URL).toString();
}

let currentJobId = "";
let events = null;
let searchResults = [];
let searchResultsQuery = "";
let downloadedFiles = [];
let chatHistory = [];
let chatReferences = [];
let pendingReferenceNames = new Set();
let pendingAnalysisNames = new Set();

function storageGet(key, fallback = "") {
  try {
    return window.localStorage?.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage?.setItem(key, value);
  } catch {
    // Some embedded browser contexts disable storage; the workbench should still run.
  }
}

function storageRemove(key) {
  try {
    window.localStorage?.removeItem(key);
  } catch {
    // Ignore unavailable storage.
  }
}

function formData(listOnlyOverride) {
  const data = new FormData(form);
  const mode = data.get("authMode");
  return {
    authMode: mode,
    group: data.get("group"),
    keyword: data.get("keyword"),
    tag: data.get("tag"),
    out: data.get("out"),
    ext: data.get("ext"),
    limit: Number(data.get("limit") || 0),
    maxPages: Number(data.get("maxPages") || DEFAULT_MAX_PAGES),
    listOnly: typeof listOnlyOverride === "boolean" ? listOnlyOverride : Boolean(data.get("listOnly")),
    curlText: mode === "curl" ? data.get("curlText") : "",
    cookie: mode === "cookie" ? data.get("cookie") : "",
    aduid: mode === "cookie" ? data.get("aduid") : "",
  };
}

function currentOut() {
  return new FormData(form).get("out") || "downloads/海外投行报告";
}

function appendLog(line) {
  logBox.textContent += `${line}\n`;
  logBox.scrollTop = logBox.scrollHeight;
}

function setRunning(running) {
  stopBtn.disabled = !running;
  downloadBtn.disabled = running;
  listBtn.disabled = running;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleDateString("zh-CN");
}

function updateJob(job) {
  if (!job) return;
  currentJobId = job.id;
  const label = {
    running: "运行中",
    stopping: "停止中",
    stopped: "已停止",
    completed: "完成",
    failed: "失败",
  }[job.status] || job.status;
  jobStatus.textContent = label;
  setRunning(job.status === "running" || job.status === "stopping");
  if (job.status !== "running" && job.status !== "stopping") {
    loadDownloads().then(() => {
      if (job.status === "completed") consumePendingReferences();
    }).catch((error) => appendLog(`文件刷新失败：${error.message}`));
  }
}

async function loadStatus() {
  const res = await fetch(workbenchUrl("api/status"));
  const status = await res.json();
  credentialStatus.textContent = status.browserAuthAvailable
    ? "扫码已登录"
    : status.credentialsAvailable ? "已配置" : "未配置";
  const latest = status.jobs[0];
  if (latest) updateJob(latest);
  loadDownloads();
}

async function browserLogin() {
  browserLoginBtn.disabled = true;
  browserLoginBtn.textContent = "等待扫码";
  appendLog("已打开知识星球登录页，请用微信扫码或验证码登录。");
  try {
    const res = await fetch(workbenchUrl("api/browser-login"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...formData(false), loginTimeout: 300 }),
    });
    const data = await res.json();
    if (!res.ok) {
      appendLog(`扫码登录失败：${data.error || "未知错误"}`);
      return;
    }
    credentialStatus.textContent = "扫码已登录";
    appendLog(data.message || "微信扫码登录态已保存。");
  } finally {
    browserLoginBtn.disabled = false;
    browserLoginBtn.textContent = "微信扫码登录";
  }
}

async function loadDownloads() {
  const outValue = currentOut();
  const out = encodeURIComponent(outValue);
  const res = await fetch(workbenchUrl(`api/downloads?out=${out}`));
  const data = await res.json();
  outputPath.textContent = outValue;
  outputPath.title = data.dir || outValue;
  fileCount.textContent = String(data.summary?.downloaded || 0);
  sizeText.textContent = formatBytes(data.summary?.sizeBytes || 0);
  downloadedFiles = data.files || [];
  filesBody.innerHTML = "";
  for (const file of downloadedFiles) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    const link = document.createElement("a");
    link.textContent = file.name;
    link.href = workbenchUrl(`${encodeURI(outValue.replace(/^\/+/, ""))}/${encodeURIComponent(file.name)}`);
    link.target = "_blank";
    name.append(link);
    const size = document.createElement("td");
    size.textContent = formatBytes(file.size);
    const time = document.createElement("td");
    time.textContent = formatTime(file.mtime);
    const action = document.createElement("td");
    const analyze = document.createElement("button");
    analyze.type = "button";
    analyze.className = "table-action";
    analyze.textContent = "解读";
    analyze.addEventListener("click", () => {
      activateView("ai");
      selectAiFile(file.name);
    });
    action.append(analyze);
    tr.append(name, size, time, action);
    filesBody.append(tr);
  }
  renderAiFileOptions();
  if (searchResults.length) renderSearchResults(searchResults);
}

function connectJob(id) {
  if (events) events.close();
  events = new EventSource(workbenchUrl(`api/jobs/${id}/events`));
  events.addEventListener("state", (event) => updateJob(JSON.parse(event.data)));
  events.addEventListener("log", (event) => {
    const item = JSON.parse(event.data);
    appendLog(item.line);
  });
}

async function startJob(listOnly) {
  const payload = formData(listOnly);
  if (String(payload.keyword || "").trim()) {
    await startKeywordJob(payload, listOnly);
    return;
  }
  await startJobWithPayload(payload);
}

async function startKeywordJob(payload, listOnly) {
  const keyword = String(payload.keyword || "").trim();
  const tags = splitList(payload.tag);
  const downloadLimit = Number(payload.limit || 0);
  let jobStarted = false;
  appendLog(`关键词搜索：${keyword}${tags.length ? ` · 标签 ${tags.join(", ")}` : ""}`);
  setRunning(true);
  try {
    const res = await fetch(workbenchUrl("api/search"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...payload,
        keyword,
        tags,
        tag: tags.join(","),
        searchPages: Number(payload.maxPages || 0) || DEFAULT_MAX_PAGES,
        resultLimit: downloadLimit || 200,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      appendLog(`关键词搜索失败：${data.error || "未知错误"}`);
      return;
    }

    const items = (data.items || []).slice(0, downloadLimit || undefined);
    renderSearchResults(data.items || [], keyword);
    activateView("ai");
    chatStatus.textContent = "命中已同步，可在线看或交给 AI 研读";
    appendLog(`关键词命中：${data.count || items.length} 项，准备${listOnly ? "列出" : "下载"} ${items.length} 项`);
    if (!items.length) {
      appendLog("没有匹配研报。可以放宽关键词，或清空标签后再试。");
      return;
    }
    if (listOnly || payload.listOnly) {
      items.slice(0, 20).forEach((item, index) => {
        appendLog(`[${index + 1}] ${item.name}`);
      });
      if (items.length > 20) appendLog(`...还有 ${items.length - 20} 项未显示`);
      return;
    }

    jobStarted = true;
    await startJobWithPayload({
      ...payload,
      selectedFiles: items,
      limit: 0,
      listOnly: false,
    });
  } finally {
    if (!jobStarted) setRunning(false);
  }
}

async function startJobWithPayload(payload, options = {}) {
  const res = await fetch(workbenchUrl("api/jobs"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    appendLog(`启动失败：${data.error || "未知错误"}`);
    return;
  }
  logBox.textContent = "";
  for (const name of options.referenceNames || []) {
    pendingReferenceNames.add(name);
  }
  updateJob(data);
  connectJob(data.id);
}

function searchPayload() {
  const tags = activeSearchTags();
  return {
    ...formData(false),
    keyword: keywordInput.value.trim(),
    tags,
    tag: tags.join(","),
    searchPages: Number(searchPagesInput.value || 10),
    resultLimit: Number(resultLimitInput.value || 200),
  };
}

function splitList(value) {
  return String(value || "")
    .split(/[,，;；、\n\r]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function activeSearchTags() {
  const typed = searchTagInput.value.trim();
  if (typed) return splitList(typed);
  return splitList(new FormData(form).get("tag"));
}

function setActiveSearchTags(tags) {
  searchTagInput.value = Array.from(new Set(tags.map((tag) => tag.trim()).filter(Boolean))).join(", ");
}

function toggleSearchTag(tag) {
  const target = String(tag || "").trim();
  if (!target) return;
  const current = activeSearchTags();
  const exists = current.some((item) => item === target);
  setActiveSearchTags(exists ? current.filter((item) => item !== target) : [...current, target]);
}

function renderTagChips(tags) {
  tagChips.innerHTML = "";
  if (!tags.length) {
    const empty = document.createElement("span");
    empty.className = "tag-empty";
    empty.textContent = "暂无标签";
    tagChips.append(empty);
    return;
  }
  for (const tag of tags) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tag-chip";
    button.textContent = tag.title;
    button.title = tag.topicsCount ? `${tag.topicsCount} 个主题` : "点击加入/移除搜索标签";
    button.addEventListener("click", () => toggleSearchTag(tag.title));
    tagChips.append(button);
  }
}

async function loadTags() {
  loadTagsBtn.disabled = true;
  loadTagsBtn.textContent = "读取中";
  try {
    const res = await fetch(workbenchUrl("api/tags"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(formData(false)),
    });
    const data = await res.json();
    if (!res.ok) {
      appendLog(`标签读取失败：${data.error || "未知错误"}`);
      renderTagChips([]);
      return;
    }
    renderTagChips(data.tags || []);
    appendLog(`已读取标签：${(data.tags || []).length} 个`);
  } finally {
    loadTagsBtn.disabled = false;
    loadTagsBtn.textContent = "载入标签";
  }
}

function selectedSearchFiles() {
  const selected = [];
  for (const box of searchBody.querySelectorAll("input[type='checkbox']:checked")) {
    const index = Number(box.dataset.index);
    if (searchResults[index]) selected.push(searchResults[index]);
  }
  return selected;
}

function updateSelectionLabel() {
  const selected = selectedSearchFiles().length;
  const total = searchResults.length;
  searchSummary.textContent = selected ? `${selected}/${total} 项` : `${total} 项`;
}

function filenameStem(name) {
  return String(name || "").replace(/\.[^.]+$/, "").trim();
}

function findDownloadedFile(name) {
  const exact = downloadedFiles.find((file) => file.name === name);
  if (exact) return exact;
  const stem = filenameStem(name);
  if (!stem) return null;
  return downloadedFiles.find((file) => {
    const candidate = filenameStem(file.name);
    return candidate === stem || candidate.startsWith(`${stem} `) || candidate.startsWith(`${stem} [`);
  }) || null;
}

function localFileUrl(file) {
  const outValue = currentOut();
  return workbenchUrl(`${encodeURI(outValue.replace(/^\/+/, ""))}/${encodeURIComponent(file.name)}`);
}

function showPreview(title, url, status = "在线预览") {
  activateView("ai");
  previewPanel.hidden = false;
  previewTitle.textContent = title || "在线预览";
  previewStatus.textContent = status;
  previewFrame.src = url;
  previewOpen.href = url;
}

function closePreview() {
  previewPanel.hidden = true;
  previewFrame.src = "about:blank";
  previewOpen.removeAttribute("href");
}

async function previewSearchItem(index) {
  const item = searchResults[index];
  if (!item) return;
  const downloaded = findDownloadedFile(item.name);
  if (downloaded) {
    showPreview(downloaded.name, localFileUrl(downloaded), "本地文件预览");
    return;
  }
  if (!item.fileId) {
    chatStatus.textContent = "该命中缺少文件 ID，无法在线预览";
    return;
  }
  chatStatus.textContent = "正在生成在线预览";
  try {
    const res = await fetch(workbenchUrl("api/preview"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...formData(false), fileId: item.fileId, name: item.name }),
    });
    const data = await res.json();
    if (!res.ok) {
      chatStatus.textContent = "预览失败";
      appendLog(`在线预览失败：${data.error || "未知错误"}`);
      return;
    }
    showPreview(item.name, workbenchUrl(data.previewUrl), "临时预览链接 · 约 10 分钟有效");
    chatStatus.textContent = "在线预览已打开";
  } catch (error) {
    chatStatus.textContent = "预览失败";
    appendLog(`在线预览失败：${error.message}`);
  }
}

async function analyzeReferencedReport(name) {
  if (!name) return;
  activateView("ai");
  chatSkill.value = "report_analysis";
  includeFileContext.checked = true;
  addReference(name);
  await sendWorkbenchMessage("report_analysis");
}

function consumePendingReferences() {
  if (!pendingReferenceNames.size) return;
  for (const name of Array.from(pendingReferenceNames)) {
    const file = findDownloadedFile(name);
    if (!file) continue;
    addReference(file.name);
    pendingReferenceNames.delete(name);
    const shouldAnalyze = pendingAnalysisNames.has(name);
    pendingAnalysisNames.delete(name);
    chatStatus.textContent = shouldAnalyze ? "已下载，准备交给 AI 研读" : "已下载并加入引用";
    if (shouldAnalyze) {
      analyzeReferencedReport(file.name).catch((error) => {
        chatStatus.textContent = "AI研读失败";
        appendLog(`AI研读失败：${error.message}`);
      });
    }
  }
}

function referenceSearchItem(index) {
  const item = searchResults[index];
  if (!item) return;
  const file = findDownloadedFile(item.name);
  if (file) {
    addReference(file.name);
    chatStatus.textContent = "已加入引用";
    return;
  }
  pendingReferenceNames.add(item.name);
  chatStatus.textContent = "下载中，完成后会自动加入引用";
  const payload = {
    ...formData(false),
    selectedFiles: [item],
    limit: 0,
    listOnly: false,
  };
  startJobWithPayload(payload, { referenceNames: [item.name] });
}

function aiReadSearchItem(index) {
  const item = searchResults[index];
  if (!item) return;
  const file = findDownloadedFile(item.name);
  if (file) {
    analyzeReferencedReport(file.name).catch((error) => {
      chatStatus.textContent = "AI研读失败";
      appendLog(`AI研读失败：${error.message}`);
    });
    return;
  }
  pendingReferenceNames.add(item.name);
  pendingAnalysisNames.add(item.name);
  chatStatus.textContent = "下载后自动交给 AI 研读";
  appendLog(`AI研读排队：${item.name}`);
  const payload = {
    ...formData(false),
    selectedFiles: [item],
    limit: 0,
    listOnly: false,
  };
  startJobWithPayload(payload, { referenceNames: [item.name] });
}

function renderSearchResults(items, query = searchResultsQuery) {
  searchResults = items || [];
  searchResultsQuery = String(query || "").trim();
  searchBody.innerHTML = "";
  searchSummary.textContent = `${searchResults.length} 项`;
  for (const [index, item] of searchResults.entries()) {
    const tr = document.createElement("tr");
    const pick = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.index = String(index);
    checkbox.addEventListener("change", updateSelectionLabel);
    pick.append(checkbox);

    const name = document.createElement("td");
    const title = document.createElement("div");
    title.className = "source-file-title";
    title.textContent = item.name;
    const meta = document.createElement("div");
    meta.className = "source-file-meta";
    meta.textContent = [
      item.hashtag ? `#${item.hashtag}` : "",
      formatBytes(item.size),
      shortTime(item.topicCreateTime || item.createTime),
      item.downloadCount ? `${item.downloadCount} 次下载` : ""
    ].filter(Boolean).join(" · ");
    name.append(title, meta);

    const action = document.createElement("td");
    const downloaded = findDownloadedFile(item.name);
    const actions = document.createElement("div");
    actions.className = "search-result-actions";
    const previewButton = document.createElement("button");
    previewButton.type = "button";
    previewButton.className = "table-action";
    previewButton.textContent = "在线看";
    previewButton.title = downloaded ? "预览本地 PDF" : "生成临时预览链接，不先下载入库";
    previewButton.addEventListener("click", () => previewSearchItem(index));

    const aiButton = document.createElement("button");
    aiButton.type = "button";
    aiButton.className = "table-action primary-action";
    aiButton.textContent = "AI研读";
    aiButton.title = downloaded ? "把本地 PDF 交给 AI 解析" : "先下载 PDF，再自动交给 AI 解析";
    aiButton.addEventListener("click", () => aiReadSearchItem(index));

    const quoteButton = document.createElement("button");
    quoteButton.type = "button";
    quoteButton.className = downloaded ? "table-action" : "table-action quiet-action";
    quoteButton.textContent = downloaded ? "引用全文" : "下载引用";
    quoteButton.title = downloaded
      ? "把本地 PDF 正文加入 Agent 上下文"
      : "先下载 PDF，再把正文加入 Agent 上下文";
    quoteButton.addEventListener("click", () => referenceSearchItem(index));
    actions.append(previewButton, aiButton, quoteButton);
    action.append(actions);
    tr.append(pick, name, action);
    searchBody.append(tr);
  }
}

async function runSearch() {
  searchBtn.disabled = true;
  searchSummary.textContent = "搜索中";
  searchError.hidden = true;
  searchError.textContent = "";
  try {
    const res = await fetch(workbenchUrl("api/search"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(searchPayload()),
    });
    const data = await res.json();
    if (!res.ok) {
      const errorMessage = data.error || "未知错误";
      appendLog(`搜索失败：${errorMessage}`);
      searchSummary.textContent = "失败";
      searchError.textContent = `搜索失败：${errorMessage}`;
      searchError.hidden = false;
      return;
    }
    renderSearchResults(data.items || [], keywordInput.value.trim());
    const tagText = (data.hashtags || []).map((item) => item.title).join(", ");
    appendLog(`搜索完成：${data.count} 项${tagText ? `，标签 ${tagText}` : ""}，扫描主题 ${data.scannedTopics || 0} 个`);
  } finally {
    searchBtn.disabled = false;
  }
}

function activateView(viewName) {
  document.body.dataset.view = viewName;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelector(`#${viewName}View`)?.classList.add("active");
  if (viewName === "data" || viewName === "ai") loadDownloads();
}

function savedModelConfig() {
  try {
    const config = JSON.parse(storageGet("zsxq.modelConfig", "{}") || "{}");
    const savedKey = storageGet("zsxq.modelApiKey", "");
    if (savedKey) config.apiKey = savedKey;
    return config;
  } catch {
    return {};
  }
}

function modelConfig() {
  return {
    baseUrl: modelBaseUrl.value.trim(),
    apiKey: modelApiKey.value.trim(),
    model: modelName.value.trim(),
    compat: modelCompat.value || "auto",
    temperature: Number(modelTemperature.value || 0.2),
    maxTokens: Number(modelMaxTokens.value || 4096),
    thinking: modelThinking.value || "disabled",
    extraBody: modelExtraBody.value.trim(),
  };
}

async function saveModelConfig() {
  const config = modelConfig();
  storageSet("zsxq.modelConfig", JSON.stringify({
    baseUrl: config.baseUrl,
    saveApiKey: saveApiKey.checked,
    model: config.model,
    compat: config.compat,
    temperature: config.temperature,
    maxTokens: config.maxTokens,
    thinking: config.thinking,
    extraBody: config.extraBody,
  }));
  if (saveApiKey.checked && config.apiKey) {
    storageSet("zsxq.modelApiKey", config.apiKey);
  } else if (saveApiKey.checked) {
    storageRemove("zsxq.modelApiKey");
  } else if (!saveApiKey.checked) {
    storageRemove("zsxq.modelApiKey");
  }
  modelStatus.textContent = "保存中";
  try {
    const res = await fetch(workbenchUrl("api/model-config"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ modelConfig: config }),
    });
    const data = await res.json();
    if (!res.ok) {
      modelStatus.textContent = "本地已保存";
      chatStatus.textContent = data.error || "全局配置保存失败";
      return;
    }
    if (data.hasApiKey) modelApiKey.placeholder = `已保存 ${data.apiKeyPreview || "API Key"}，可留空`;
    modelStatus.textContent = "已同步到全局设置";
  } catch (error) {
    modelStatus.textContent = "本地已保存";
    chatStatus.textContent = error.message || "全局配置保存失败";
  }
}

async function loadModelDefaults() {
  const saved = savedModelConfig();
  try {
    const res = await fetch(workbenchUrl("api/model-config"));
    const defaults = await res.json();
    modelBaseUrl.value = defaults.baseUrl || saved.baseUrl || modelBaseUrl.value;
    modelApiKey.value = defaults.hasApiKey ? "" : saved.apiKey || "";
    saveApiKey.checked = saved.saveApiKey !== false;
    modelName.value = defaults.model || saved.model || modelName.value;
    modelCompat.value = saved.compat || defaults.compat || modelCompat.value;
    modelTemperature.value = defaults.temperature ?? saved.temperature ?? modelTemperature.value;
    modelMaxTokens.value = saved.maxTokens ?? defaults.maxTokens ?? modelMaxTokens.value;
    modelThinking.value = saved.thinking || defaults.thinking || modelThinking.value;
    modelExtraBody.value = saved.extraBody || defaults.extraBody || "";
    if (defaults.hasApiKey) modelApiKey.placeholder = `已从全局设置读取 ${defaults.apiKeyPreview || "API Key"}，可留空`;
    if (!defaults.hasApiKey && saved.apiKey && (saved.model || defaults.model)) {
      try {
        const syncRes = await fetch(workbenchUrl("api/model-config"), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ modelConfig: modelConfig() }),
        });
        if (syncRes.ok) modelStatus.textContent = "已同步到全局设置";
      } catch {
        modelStatus.textContent = "默认值已读取，全局同步失败";
      }
    }
  } catch {
    modelStatus.textContent = "默认值读取失败";
  }
}

async function testModel() {
  testModelBtn.disabled = true;
  modelStatus.textContent = "测试中";
  try {
    const res = await fetch(workbenchUrl("api/ai/test"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ modelConfig: modelConfig() }),
    });
    const data = await res.json();
    if (!res.ok) {
      modelStatus.textContent = "测试失败";
      chatStatus.textContent = data.error || "模型测试失败";
      return;
    }
    modelStatus.textContent = "连接正常";
    chatStatus.textContent = data.result || "模型连接正常";
  } finally {
    testModelBtn.disabled = false;
  }
}

function renderAiFileOptions() {
  const previous = aiFileSelect.value;
  const keyword = aiFileFilter.value.trim().toLowerCase();
  const files = downloadedFiles.filter((file) => !keyword || file.name.toLowerCase().includes(keyword));
  aiFileSelect.innerHTML = "";
  if (!files.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有匹配文件";
    aiFileSelect.append(option);
    return;
  }
  for (const file of files) {
    const option = document.createElement("option");
    option.value = file.name;
    option.textContent = `${file.name} · ${formatBytes(file.size)}`;
    aiFileSelect.append(option);
  }
  if (files.some((file) => file.name === previous)) aiFileSelect.value = previous;
}

function selectAiFile(name) {
  aiFileFilter.value = "";
  renderAiFileOptions();
  aiFileSelect.value = name;
  chatSkill.value = "report_analysis";
  includeFileContext.checked = true;
  addReference(name);
  chatStatus.textContent = "已选择文件";
  chatInput.focus();
}

function renderReferences() {
  chatReferencesEl.innerHTML = "";
  if (!chatReferences.length) {
    const empty = document.createElement("span");
    empty.className = "reference-empty";
    empty.textContent = "未添加引用";
    chatReferencesEl.append(empty);
    return;
  }
  for (const ref of chatReferences) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "reference-chip";
    chip.title = "点击移除引用";
    chip.textContent = ref.name;
    chip.addEventListener("click", () => {
      chatReferences = chatReferences.filter((item) => item.name !== ref.name);
      renderReferences();
    });
    chatReferencesEl.append(chip);
  }
}

function addReference(name = aiFileSelect.value) {
  if (!name) {
    chatStatus.textContent = "请选择文件";
    return;
  }
  if (!chatReferences.some((ref) => ref.name === name)) {
    chatReferences.push({ out: currentOut(), name });
  }
  includeFileContext.checked = true;
  renderReferences();
}

function loadChatHistory() {
  try {
    const saved = JSON.parse(storageGet("zsxq.chatHistory", "[]") || "[]");
    return Array.isArray(saved) ? saved.filter((item) => item?.role && item?.content) : [];
  } catch {
    return [];
  }
}

function persistChatHistory() {
  storageSet("zsxq.chatHistory", JSON.stringify(chatHistory.slice(-80)));
}

function appendInlineMarkdown(parent, text) {
  const source = String(text || "");
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[A-Za-z]\d+\]|第\s*\d+\s*页|\bP\.?\s*\d+\b|\bp\.?\s*\d+\b)/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) parent.append(document.createTextNode(source.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else {
      const marker = document.createElement("span");
      marker.className = "chat-citation";
      marker.textContent = token;
      parent.append(marker);
    }
    cursor = match.index + token.length;
  }
  if (cursor < source.length) parent.append(document.createTextNode(source.slice(cursor)));
}

function splitMarkdownTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isMarkdownTableLine(line) {
  return /^\s*\|.+\|\s*$/.test(line || "");
}

function isMarkdownTableDivider(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line || "");
}

function renderMarkdownTable(lines, startIndex, target) {
  const header = splitMarkdownTableRow(lines[startIndex]);
  const tableWrap = document.createElement("div");
  tableWrap.className = "chat-table-wrap";
  const table = document.createElement("table");
  table.className = "chat-table";
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const cellText of header) {
    const th = document.createElement("th");
    appendInlineMarkdown(th, cellText);
    headerRow.append(th);
  }
  thead.append(headerRow);
  table.append(thead);

  const tbody = document.createElement("tbody");
  let index = startIndex + 2;
  while (index < lines.length && isMarkdownTableLine(lines[index])) {
    const row = document.createElement("tr");
    for (const cellText of splitMarkdownTableRow(lines[index])) {
      const td = document.createElement("td");
      appendInlineMarkdown(td, cellText);
      row.append(td);
    }
    tbody.append(row);
    index += 1;
  }
  table.append(tbody);
  tableWrap.append(table);
  target.append(tableWrap);
  return index;
}

function looksLikeNumberedHeading(text) {
  return /(结论|核心|观点|证据|数据|催化|时间|风险|反证|标的|行业|主题|依据|跟踪|指标|总览|地图|清单|优先|摘要|估值|建议|未确认)/.test(text);
}

function renderStructuredMessage(content) {
  const root = document.createElement("div");
  root.className = "chat-structured";
  const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  let listType = "";

  function closeList() {
    list = null;
    listType = "";
  }

  function flushParagraph() {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    appendInlineMarkdown(p, paragraph.join(" "));
    root.append(p);
    paragraph = [];
  }

  function ensureList(type, start = 1) {
    if (list && listType === type) return list;
    closeList();
    list = document.createElement(type);
    list.className = "chat-list";
    if (type === "ol" && start > 1) list.start = start;
    listType = type;
    root.append(list);
    return list;
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) {
      flushParagraph();
      closeList();
      continue;
    }

    if (isMarkdownTableLine(line) && isMarkdownTableDivider(lines[index + 1])) {
      flushParagraph();
      closeList();
      index = renderMarkdownTable(lines, index, root) - 1;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flushParagraph();
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length <= 2 ? "h3" : "h4";
      const node = document.createElement(level);
      node.className = "chat-heading";
      appendInlineMarkdown(node, heading[2].trim());
      root.append(node);
      continue;
    }

    const numberedHeading = line.match(/^(\d+)[.、)]\s+(.{2,36})[:：]?\s*$/);
    if (numberedHeading && looksLikeNumberedHeading(numberedHeading[2])) {
      flushParagraph();
      closeList();
      const node = document.createElement("h3");
      node.className = "chat-heading";
      appendInlineMarkdown(node, `${numberedHeading[1]}. ${numberedHeading[2].trim()}`);
      root.append(node);
      continue;
    }

    const ordered = line.match(/^(\d+)[.、)]\s+(.+)$/);
    const unordered = line.match(/^[-*•]\s+(.+)$/);
    if (ordered || unordered) {
      flushParagraph();
      const target = ordered ? ensureList("ol", Number(ordered[1]) || 1) : ensureList("ul");
      const item = document.createElement("li");
      appendInlineMarkdown(item, ordered ? ordered[2] : unordered[1]);
      target.append(item);
      continue;
    }

    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      closeList();
      const blockquote = document.createElement("blockquote");
      appendInlineMarkdown(blockquote, quote[1]);
      root.append(blockquote);
      continue;
    }

    closeList();
    paragraph.push(line);
  }

  flushParagraph();
  if (!root.childNodes.length) root.textContent = content;
  return root;
}

function summaryPdfTitle(message, index) {
  const previousUser = chatHistory
    .slice(0, index)
    .reverse()
    .find((item) => item.role === "user");
  const previousText = String(previousUser?.content || "").split("\n")[0] || "";
  const heading = String(message.content || "").split("\n").find((line) => /^#{1,3}\s+/.test(line.trim()));
  const title = previousText.replace(/^解析研报[:：]\s*/, "").trim()
    || heading?.replace(/^#{1,3}\s+/, "").trim()
    || "研报总结";
  return title
    .replace(/\.pdf-\d{8}-\d{6}\.pdf$/i, "")
    .replace(/\.(pdf|md)$/i, "")
    .trim()
    .slice(0, 90);
}

function filenameFromDisposition(value, fallback) {
  const header = String(value || "");
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) return decodeURIComponent(encoded[1]);
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : fallback;
}

function decodeResponseHeader(value) {
  if (!value) return "";
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportMessagePdf(message, index) {
  if (!message?.content) return;
  const title = summaryPdfTitle(message, index);
  chatStatus.textContent = "正在生成 PDF";
  try {
    const res = await fetch(workbenchUrl("api/export-summary-pdf"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title,
        content: message.content,
        sourceSummary: message.meta?.sourceSummary || "",
        analysisPath: message.meta?.analysisPath || "",
        generatedAt: message.at || "",
      }),
    });
    if (!res.ok) {
      let detail = "导出失败";
      try {
        const data = await res.json();
        detail = data.error || detail;
      } catch {
        // Keep the generic failure if the server did not return JSON.
      }
      chatStatus.textContent = detail;
      appendLog(`PDF 导出失败：${detail}`);
      return;
    }
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("content-disposition"), `${title || "研报总结"}.pdf`);
    const savedPath = decodeResponseHeader(res.headers.get("x-saved-path"));
    downloadBlob(blob, filename);
    if (savedPath && chatHistory[index]) {
      chatHistory[index].meta = {
        ...(chatHistory[index].meta || {}),
        pdfPath: savedPath,
      };
      persistChatHistory();
      renderChatMessages();
      appendLog(`PDF 已保存：${savedPath}`);
    }
    chatStatus.textContent = savedPath ? `PDF 已生成：${savedPath}` : "PDF 已生成";
  } catch (error) {
    chatStatus.textContent = "PDF 导出失败";
    appendLog(`PDF 导出失败：${error.message}`);
  }
}

async function openSavedAnalysis(path, mode) {
  const targetPath = String(path || "").trim();
  if (!targetPath) return;
  chatStatus.textContent = mode === "folder" ? "正在打开文件夹" : "正在打开文件";
  try {
    const res = await fetch(workbenchUrl("api/open-path"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path: targetPath, mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      chatStatus.textContent = "打开失败";
      appendLog(`打开路径失败：${data.error || "未知错误"}`);
      return;
    }
    chatStatus.textContent = mode === "folder" ? "文件夹已打开" : "文件已打开";
  } catch (error) {
    chatStatus.textContent = "打开失败";
    appendLog(`打开路径失败：${error.message}`);
  }
}

function renderChatMessages() {
  chatMessagesEl.innerHTML = "";
  if (!chatHistory.length) {
    const empty = document.createElement("div");
    empty.className = "chat-empty";
    empty.textContent = "选择一份研报，点“解析研报”，然后就可以像聊天一样继续追问。";
    chatMessagesEl.append(empty);
    return;
  }
  for (const [index, message] of chatHistory.entries()) {
    const item = document.createElement("article");
    item.className = `chat-message ${message.role}`;
    const role = document.createElement("div");
    role.className = "chat-role";
    role.textContent = message.role === "user" ? "你" : "助手";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    if (message.role === "assistant") {
      bubble.append(renderStructuredMessage(message.content));
    } else {
      bubble.textContent = message.content;
    }
    if (message.meta?.analysisPath) {
      const meta = document.createElement("div");
      meta.className = "chat-meta";
      const label = document.createElement("span");
      label.className = "chat-meta-path";
      label.textContent = `已保存：${message.meta.analysisPath}`;
      const actions = document.createElement("span");
      actions.className = "chat-meta-actions";
      const openFile = document.createElement("button");
      openFile.type = "button";
      openFile.className = "table-action quiet-action";
      openFile.textContent = "打开文件";
      openFile.addEventListener("click", () => openSavedAnalysis(message.meta.analysisPath, "file"));
      const openFolder = document.createElement("button");
      openFolder.type = "button";
      openFolder.className = "table-action quiet-action";
      openFolder.textContent = "打开文件夹";
      openFolder.addEventListener("click", () => openSavedAnalysis(message.meta.analysisPath, "folder"));
      actions.append(openFile, openFolder);
      meta.append(label, actions);
      bubble.append(meta);
    }
    if (message.meta?.pdfPath) {
      const meta = document.createElement("div");
      meta.className = "chat-meta";
      const label = document.createElement("span");
      label.className = "chat-meta-path";
      label.textContent = `PDF 已保存：${message.meta.pdfPath}`;
      const actions = document.createElement("span");
      actions.className = "chat-meta-actions";
      const openFile = document.createElement("button");
      openFile.type = "button";
      openFile.className = "table-action quiet-action";
      openFile.textContent = "打开PDF";
      openFile.addEventListener("click", () => openSavedAnalysis(message.meta.pdfPath, "file"));
      const openFolder = document.createElement("button");
      openFolder.type = "button";
      openFolder.className = "table-action quiet-action";
      openFolder.textContent = "打开文件夹";
      openFolder.addEventListener("click", () => openSavedAnalysis(message.meta.pdfPath, "folder"));
      actions.append(openFile, openFolder);
      meta.append(label, actions);
      bubble.append(meta);
    }
    if (message.meta?.sourceSummary) {
      const meta = document.createElement("div");
      meta.className = "chat-meta";
      meta.textContent = message.meta.sourceSummary;
      bubble.append(meta);
    }
    if (message.role === "assistant" && message.content.trim()) {
      const actions = document.createElement("div");
      actions.className = "chat-message-actions";
      const exportButton = document.createElement("button");
      exportButton.type = "button";
      exportButton.className = "table-action";
      exportButton.textContent = "导出PDF";
      exportButton.title = "把这条研报总结导出为 PDF";
      exportButton.addEventListener("click", () => exportMessagePdf(message, index));
      actions.append(exportButton);
      bubble.append(actions);
    }
    item.append(role, bubble);
    chatMessagesEl.append(item);
  }
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function appendChat(role, content, meta = {}) {
  chatHistory.push({ role, content: String(content || "").trim(), meta, at: new Date().toISOString() });
  chatHistory = chatHistory.filter((message) => message.content).slice(-80);
  persistChatHistory();
  renderChatMessages();
}

function selectedFilePayload() {
  const name = aiFileSelect.value;
  if (!name) {
    throw new Error("请选择文件");
  }
  return { out: currentOut(), name };
}

function referencedFilesPayload() {
  if (chatReferences.length) return chatReferences.map((ref) => ({ out: ref.out || currentOut(), name: ref.name }));
  if (includeFileContext.checked) return [selectedFilePayload()];
  return [];
}

function parentAppOrigin() {
  try {
    return document.referrer ? new URL(document.referrer).origin : "*";
  } catch {
    return "*";
  }
}

function sendToMainAi() {
  const skill = chatSkill.value || "chat";
  let files = [];
  try {
    files = referencedFilesPayload();
  } catch {
    files = [];
  }
  const typed = chatInput.value.trim();
  const prompt = typed || (skill === "report_analysis"
    ? DEFAULT_ANALYSIS_PROMPT
    : "请基于研报工作台当前引用资料，提炼投资结论、关键证据、反证风险和下一步跟踪指标。");
  const references = files.map((file) => file.name).filter(Boolean);
  window.parent?.postMessage({
    type: "deepfocus:send-to-ai",
    prompt,
    skill,
    references,
    source: "research-workbench",
  }, parentAppOrigin());
  chatStatus.textContent = "已发送到主 AI";
}

function setChatBusy(busy) {
  parseReportBtn.disabled = busy;
  sendChatBtn.disabled = busy;
  syncToMainAiBtn.disabled = busy;
  clearChatBtn.disabled = busy;
  summarizeHitsBtn.disabled = busy;
  summarizeSearchBtn.disabled = busy;
}

function buildSearchSummaryPrompt(items, keyword) {
  const limited = items.slice(0, 120);
  const lines = limited.map((item, index) => [
    `[${index + 1}] ${item.name}`,
    item.hashtag ? `标签：${item.hashtag}` : "",
    item.size ? `大小：${formatBytes(item.size)}` : "",
    item.topicCreateTime || item.createTime ? `日期：${shortTime(item.topicCreateTime || item.createTime)}` : "",
    item.downloadCount ? `下载：${item.downloadCount}` : "",
  ].filter(Boolean).join("；"));
  return [
    `请基于下面的知识星球研报搜索命中列表，做一页中文投研情报总结。关键词：${keyword || keywordInput.value.trim() || "未指定"}`,
    "",
    "注意：你现在只能看到标题、标签、大小、日期和下载次数，不能假装读过 PDF 正文。请明确说明结论来自标题级线索。",
    "",
    "请严格使用 Markdown 输出，按以下结构：",
    "## 1. 一句话总览",
    "- 这批研报主要讨论什么。",
    "## 2. 主题地图",
    "- 按主题聚类，例如业绩前瞻、GTC/Rubin/Blackwell、HBM/存储、CPO/光模块、供应链、汽车/机器人、估值与风险。",
    "## 3. 优先下载深读 Top 10",
    "| 排名 | 报告 | 理由 | 需要验证的问题 |",
    "| --- | --- | --- | --- |",
    "## 4. 重复/同主题合并建议",
    "- 合并相似标题，并说明保留哪类报告。",
    "## 5. 投资跟踪清单",
    "- 接下来应该追哪些数据、公司、事件和风险。",
    "",
    `命中数量：${items.length}，本次用于总结：${limited.length}`,
    lines.join("\n"),
  ].join("\n");
}

async function summarizeSearchResults() {
  const selected = selectedSearchFiles();
  const items = selected.length ? selected : searchResults;
  if (!items.length) {
    chatStatus.textContent = "先搜索或列出研报命中";
    appendLog("先搜索或列出研报命中，再做 AI 总结。");
    return;
  }

  activateView("ai");
  const prompt = buildSearchSummaryPrompt(items, searchResultsQuery || keywordInput.value.trim() || new FormData(form).get("keyword"));
  appendChat("user", selected.length ? `AI总结选中 ${selected.length} 条研报命中` : `AI总结全部 ${items.length} 条研报命中`);
  setChatBusy(true);
  chatStatus.textContent = "总结命中列表中 · 只读标题级线索，不下载 PDF";
  try {
    const res = await fetch(workbenchUrl("api/chat"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: prompt }],
        skill: "chat",
        includeFile: false,
        prompt,
        modelConfig: modelConfig(),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      chatStatus.textContent = "总结失败";
      appendChat("assistant", data.error || "AI 总结失败");
      appendLog(`AI 总结失败：${data.error || "未知错误"}`);
      return;
    }
    chatStatus.textContent = "命中总结完成";
    appendChat("assistant", data.reply || "没有生成有效总结");
  } finally {
    setChatBusy(false);
  }
}

async function summarizeCurrentKeywordHits() {
  const payload = formData(false);
  const keyword = String(payload.keyword || "").trim();
  if (searchResults.length && (!keyword || searchResultsQuery === keyword)) {
    await summarizeSearchResults();
    return;
  }

  if (!keyword) {
    chatStatus.textContent = "先输入关键词";
    appendLog("先在资料与抓取页输入关键词，再做 AI 总结。");
    return;
  }

  const tags = splitList(payload.tag);
  const summaryLimit = Math.max(Number(payload.limit || 0) || 0, 200);
  appendLog(`AI 总结准备：正在为“${keyword}”拉取命中列表...`);
  summarizeHitsBtn.disabled = true;
  try {
    const res = await fetch(workbenchUrl("api/search"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...payload,
        keyword,
        tags,
        tag: tags.join(","),
        searchPages: Number(payload.maxPages || 0) || DEFAULT_MAX_PAGES,
        resultLimit: summaryLimit,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      chatStatus.textContent = "搜索失败";
      appendLog(`AI 总结前搜索失败：${data.error || "未知错误"}`);
      return;
    }

    renderSearchResults(data.items || [], keyword);
    appendLog(`AI 总结准备完成：命中 ${data.count || searchResults.length} 项`);
    await summarizeSearchResults();
  } finally {
    summarizeHitsBtn.disabled = false;
  }
}

async function sendWorkbenchMessage(skillOverride = "") {
  const skill = skillOverride || chatSkill.value || "chat";
  const needsFile = skill === "report_analysis" || skill === "file_qa" || includeFileContext.checked || chatReferences.length > 0;
  let files = [];
  try {
    if (needsFile) {
      files = referencedFilesPayload();
      if (!files.length) files = [selectedFilePayload()];
    }
  } catch (error) {
    chatStatus.textContent = error.message;
    return;
  }

  const typed = chatInput.value.trim();
  const prompt = typed || (skill === "report_analysis" ? DEFAULT_ANALYSIS_PROMPT : "");
  if (!prompt) {
    chatStatus.textContent = "请输入消息";
    return;
  }

  const fileName = files.map((file) => file.name).join(", ");
  const display = skill === "report_analysis" && !typed
    ? `解析研报：${fileName}`
    : [prompt, fileName ? `引用：${fileName}` : ""].filter(Boolean).join("\n\n");
  appendChat("user", display);
  chatInput.value = "";
  setChatBusy(true);
  chatStatus.textContent = skill === "report_analysis"
    ? "解析中 · 抽取 PDF 并调用模型，较大研报约需 30-120 秒"
    : "思考中";
  try {
    const res = await fetch(workbenchUrl("api/chat"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        messages: chatHistory.map((message) => ({ role: message.role, content: message.content })),
        skill,
        includeFile: needsFile,
        file: files[0] || null,
        files,
        prompt,
        modelConfig: modelConfig(),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      chatStatus.textContent = "失败";
      appendChat("assistant", data.error || "调用失败");
      appendLog(`工作台调用失败：${data.error || "未知错误"}`);
      return;
    }
    const source = data.source || {};
    const sourceSummary = source.mode
      ? `输入：${source.mode}${source.pagesUsed ? ` · ${source.pagesUsed}/${source.totalPages || "?"} 页` : ""}${source.chars ? ` · ${source.chars.toLocaleString("zh-CN")} 字符` : ""}`
      : "";
    chatStatus.textContent = source.mode ? `完成 · ${source.mode}` : "完成";
    appendChat("assistant", data.reply || "", { analysisPath: data.analysisPath || "", sourceSummary });
  } finally {
    setChatBusy(false);
  }
}

for (const radio of form.querySelectorAll("input[name='authMode']")) {
  radio.addEventListener("change", () => {
    authPanel.dataset.mode = radio.checked ? radio.value : authPanel.dataset.mode;
  });
}
authPanel.dataset.mode = "env";

for (const item of document.querySelectorAll(".nav-item")) {
  item.addEventListener("click", () => activateView(item.dataset.view));
}

for (const tab of document.querySelectorAll(".module-tab")) {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".module-tab").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".data-pane").forEach((pane) => pane.classList.toggle("active", pane.id === `${tab.dataset.pane}Pane`));
    if (tab.dataset.pane === "files") loadDownloads();
  });
}

for (const action of document.querySelectorAll(".skill-action")) {
  action.addEventListener("click", () => {
    if (action.dataset.targetView) {
      activateView(action.dataset.targetView);
      return;
    }
    activateView("ai");
    chatSkill.value = action.dataset.skill || "chat";
    includeFileContext.checked = chatSkill.value !== "chat";
    chatInput.focus();
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  startJob(false);
});

listBtn.addEventListener("click", () => startJob(true));
summarizeHitsBtn.addEventListener("click", () => {
  summarizeCurrentKeywordHits().catch((error) => {
    chatStatus.textContent = "总结失败";
    appendLog(`AI 总结失败：${error.message}`);
  });
});
browserLoginBtn.addEventListener("click", browserLogin);
searchBtn.addEventListener("click", runSearch);
keywordInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runSearch();
});
searchTagInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runSearch();
});
loadTagsBtn.addEventListener("click", loadTags);
summarizeSearchBtn.addEventListener("click", () => {
  summarizeSearchResults().catch((error) => {
    chatStatus.textContent = "总结失败";
    appendLog(`AI 总结失败：${error.message}`);
  });
});
selectAllBtn.addEventListener("click", () => {
  const boxes = Array.from(searchBody.querySelectorAll("input[type='checkbox']"));
  const allChecked = boxes.length > 0 && boxes.every((box) => box.checked);
  for (const box of boxes) box.checked = !allChecked;
  updateSelectionLabel();
});
downloadSelectedBtn.addEventListener("click", () => {
  const selectedFiles = selectedSearchFiles();
  if (!selectedFiles.length) {
    appendLog("请先勾选文件");
    return;
  }
  const payload = {
    ...formData(false),
    selectedFiles,
    limit: 0,
    listOnly: false,
  };
  startJobWithPayload(payload);
});

stopBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  await fetch(workbenchUrl(`api/jobs/${currentJobId}/stop`), { method: "POST" });
});

refreshBtn.addEventListener("click", loadStatus);
clearLogBtn.addEventListener("click", () => {
  logBox.textContent = "";
});
form.out.addEventListener("change", loadDownloads);
modelForm.addEventListener("submit", (event) => {
  event.preventDefault();
  saveModelConfig();
});
testModelBtn.addEventListener("click", testModel);
aiFileFilter.addEventListener("input", renderAiFileOptions);
refreshAiFilesBtn.addEventListener("click", loadDownloads);
addReferenceBtn.addEventListener("click", () => addReference());
parseReportBtn.addEventListener("click", () => sendWorkbenchMessage("report_analysis"));
sendChatBtn.addEventListener("click", () => sendWorkbenchMessage());
previewCloseBtn.addEventListener("click", closePreview);
syncToMainAiBtn.addEventListener("click", sendToMainAi);
clearChatBtn.addEventListener("click", () => {
  chatHistory = [];
  persistChatHistory();
  renderChatMessages();
  chatStatus.textContent = "已清空";
});
chatSkill.addEventListener("change", () => {
  includeFileContext.checked = chatSkill.value === "file_qa" || chatSkill.value === "report_analysis";
});
chatInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    sendWorkbenchMessage();
  }
});

chatHistory = loadChatHistory();
renderChatMessages();
renderReferences();
document.querySelector("#modelView")?.append(modelForm);
searchTagInput.value = new FormData(form).get("tag") || "";
loadModelDefaults();
document.body.dataset.view = "ai";
loadStatus().catch((error) => appendLog(`状态读取失败：${error.message}`));
