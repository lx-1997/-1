"use strict";

const form = document.querySelector("#jobForm");
const authPanel = document.querySelector(".auth");
const logBox = document.querySelector("#logBox");
const stopBtn = document.querySelector("#stopBtn");
const listBtn = document.querySelector("#listBtn");
const downloadBtn = document.querySelector("#downloadBtn");
const refreshBtn = document.querySelector("#refreshBtn");
const clearLogBtn = document.querySelector("#clearLogBtn");
const credentialStatus = document.querySelector("#credentialStatus");
const jobStatus = document.querySelector("#jobStatus");
const fileCount = document.querySelector("#fileCount");
const filesBody = document.querySelector("#filesBody");
const sizeText = document.querySelector("#sizeText");
const outputPath = document.querySelector("#outputPath");
const keywordInput = document.querySelector("#keywordInput");
const searchPagesInput = document.querySelector("#searchPagesInput");
const resultLimitInput = document.querySelector("#resultLimitInput");
const searchBtn = document.querySelector("#searchBtn");
const selectAllBtn = document.querySelector("#selectAllBtn");
const downloadSelectedBtn = document.querySelector("#downloadSelectedBtn");
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
const refreshAiFilesBtn = document.querySelector("#refreshAiFilesBtn");
const parseReportBtn = document.querySelector("#parseReportBtn");
const sendChatBtn = document.querySelector("#sendChatBtn");
const clearChatBtn = document.querySelector("#clearChatBtn");
const addReferenceBtn = document.querySelector("#addReferenceBtn");
const chatReferencesEl = document.querySelector("#chatReferences");

const DEFAULT_ANALYSIS_PROMPT = [
  "请用中文解读这份海外投行研报，面向投资研究场景输出：",
  "1. 一句话结论",
  "2. 核心观点",
  "3. 关键数据、催化剂或时间点",
  "4. 风险点",
  "5. 可跟踪标的、行业或主题",
  "6. 原文证据，尽量标注页码、标题或表格位置",
].join("\n");

let currentJobId = "";
let events = null;
let searchResults = [];
let downloadedFiles = [];
let chatHistory = [];
let chatReferences = [];

function formData(listOnlyOverride) {
  const data = new FormData(form);
  const mode = data.get("authMode");
  return {
    group: data.get("group"),
    tag: data.get("tag"),
    out: data.get("out"),
    ext: data.get("ext"),
    limit: Number(data.get("limit") || 0),
    maxPages: Number(data.get("maxPages") || 0),
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
    loadDownloads();
  }
}

async function loadStatus() {
  const res = await fetch("/api/status");
  const status = await res.json();
  credentialStatus.textContent = status.credentialsAvailable ? "已配置" : "未配置";
  const latest = status.jobs[0];
  if (latest) updateJob(latest);
  loadDownloads();
}

async function loadDownloads() {
  const outValue = currentOut();
  const out = encodeURIComponent(outValue);
  const res = await fetch(`/api/downloads?out=${out}`);
  const data = await res.json();
  outputPath.textContent = data.dir || "";
  fileCount.textContent = String(data.summary?.downloaded || 0);
  sizeText.textContent = formatBytes(data.summary?.sizeBytes || 0);
  downloadedFiles = data.files || [];
  filesBody.innerHTML = "";
  for (const file of downloadedFiles) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    const link = document.createElement("a");
    link.textContent = file.name;
    link.href = `/${encodeURI(outValue.replace(/^\/+/, ""))}/${encodeURIComponent(file.name)}`;
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
}

function connectJob(id) {
  if (events) events.close();
  events = new EventSource(`/api/jobs/${id}/events`);
  events.addEventListener("state", (event) => updateJob(JSON.parse(event.data)));
  events.addEventListener("log", (event) => {
    const item = JSON.parse(event.data);
    appendLog(item.line);
  });
}

async function startJob(listOnly) {
  const payload = formData(listOnly);
  await startJobWithPayload(payload);
}

async function startJobWithPayload(payload) {
  const res = await fetch("/api/jobs", {
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
  updateJob(data);
  connectJob(data.id);
}

function searchPayload() {
  return {
    ...formData(false),
    keyword: keywordInput.value.trim(),
    searchPages: Number(searchPagesInput.value || 10),
    resultLimit: Number(resultLimitInput.value || 200),
  };
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

function renderSearchResults(items) {
  searchResults = items || [];
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
    name.textContent = item.name;
    const size = document.createElement("td");
    size.textContent = formatBytes(item.size);
    const time = document.createElement("td");
    time.textContent = shortTime(item.topicCreateTime || item.createTime);
    const count = document.createElement("td");
    count.textContent = String(item.downloadCount || 0);
    tr.append(pick, name, size, time, count);
    searchBody.append(tr);
  }
}

async function runSearch() {
  searchBtn.disabled = true;
  searchSummary.textContent = "搜索中";
  searchError.hidden = true;
  searchError.textContent = "";
  try {
    const res = await fetch("/api/search", {
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
    renderSearchResults(data.items || []);
    appendLog(`搜索完成：${data.count} 项，扫描主题 ${data.scannedTopics || 0} 个`);
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
    const config = JSON.parse(localStorage.getItem("zsxq.modelConfig") || "{}");
    const savedKey = localStorage.getItem("zsxq.modelApiKey") || "";
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

function saveModelConfig() {
  const config = modelConfig();
  localStorage.setItem("zsxq.modelConfig", JSON.stringify({
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
    localStorage.setItem("zsxq.modelApiKey", config.apiKey);
  } else if (saveApiKey.checked) {
    localStorage.removeItem("zsxq.modelApiKey");
  } else if (!saveApiKey.checked) {
    localStorage.removeItem("zsxq.modelApiKey");
  }
  modelStatus.textContent = "已保存";
}

async function loadModelDefaults() {
  const saved = savedModelConfig();
  try {
    const res = await fetch("/api/model-config");
    const defaults = await res.json();
    modelBaseUrl.value = saved.baseUrl || defaults.baseUrl || modelBaseUrl.value;
    modelApiKey.value = saved.apiKey || "";
    saveApiKey.checked = saved.saveApiKey !== false;
    modelName.value = saved.model || defaults.model || modelName.value;
    modelCompat.value = saved.compat || defaults.compat || modelCompat.value;
    modelTemperature.value = saved.temperature ?? defaults.temperature ?? modelTemperature.value;
    modelMaxTokens.value = saved.maxTokens ?? defaults.maxTokens ?? modelMaxTokens.value;
    modelThinking.value = saved.thinking || defaults.thinking || modelThinking.value;
    modelExtraBody.value = saved.extraBody || defaults.extraBody || "";
    if (defaults.hasApiKey) modelApiKey.placeholder = "已从环境变量读取，可留空";
  } catch {
    modelStatus.textContent = "默认值读取失败";
  }
}

async function testModel() {
  testModelBtn.disabled = true;
  modelStatus.textContent = "测试中";
  try {
    const res = await fetch("/api/ai/test", {
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
    const saved = JSON.parse(localStorage.getItem("zsxq.chatHistory") || "[]");
    return Array.isArray(saved) ? saved.filter((item) => item?.role && item?.content) : [];
  } catch {
    return [];
  }
}

function persistChatHistory() {
  localStorage.setItem("zsxq.chatHistory", JSON.stringify(chatHistory.slice(-80)));
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
  for (const message of chatHistory) {
    const item = document.createElement("article");
    item.className = `chat-message ${message.role}`;
    const role = document.createElement("div");
    role.className = "chat-role";
    role.textContent = message.role === "user" ? "你" : "助手";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = message.content;
    if (message.meta?.analysisPath) {
      const meta = document.createElement("div");
      meta.className = "chat-meta";
      meta.textContent = `已保存：${message.meta.analysisPath}`;
      bubble.append(meta);
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

function setChatBusy(busy) {
  parseReportBtn.disabled = busy;
  sendChatBtn.disabled = busy;
  clearChatBtn.disabled = busy;
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
  chatStatus.textContent = skill === "report_analysis" ? "解析中" : "思考中";
  try {
    const res = await fetch("/api/chat", {
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
    chatStatus.textContent = source.mode ? `完成 · ${source.mode}` : "完成";
    appendChat("assistant", data.reply || "", { analysisPath: data.analysisPath || "" });
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
searchBtn.addEventListener("click", runSearch);
keywordInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runSearch();
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
  await fetch(`/api/jobs/${currentJobId}/stop`, { method: "POST" });
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
loadModelDefaults();
document.body.dataset.view = "ai";
loadStatus().catch((error) => appendLog(`状态读取失败：${error.message}`));
