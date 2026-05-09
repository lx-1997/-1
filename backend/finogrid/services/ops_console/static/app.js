const state = {
  view: "overview",
  apiKey: localStorage.getItem("finogridOpsKey") || "ops_dev_key",
  lastSummary: [],
};

const els = {};

const REQUEST_NAMES = {
  exceptions: "异常",
  approvals: "审批",
  agents: "代理",
  ledger: "账本",
  corridors: "通道",
};

const VALUE_LABELS = {
  active: "生效中",
  ok: "正常",
  basic: "基础",
  enhanced: "增强",
  completed: "已完成",
  settled: "已结算",
  settled_offchain: "链下已结算",
  settled_onchain: "链上已结算",
  pending: "待处理",
  held: "暂挂",
  held_for_review: "待复核",
  draft: "草稿",
  unverified: "未验证",
  expired: "已过期",
  failed: "失败",
  blocked: "已阻塞",
  revoked: "已撤销",
  rejected: "已拒绝",
  suspended: "已暂停",
  credit: "入账",
  debit: "出账",
  refund: "退款",
  fee: "费用",
  intent_reserve: "意图冻结",
  intent_release: "意图释放",
  held_task: "暂挂任务",
  kya_blocked_agent: "KYA 阻塞代理",
  expired_intent: "过期意图",
  auto: "自动",
  manual: "人工",
  threshold: "阈值审批",
};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  wireEvents();
  els.apiKeyInput.value = state.apiKey;
  checkHealth();
  loadOverview();
});

function bindElements() {
  [
    "navList",
    "viewTitle",
    "healthPill",
    "apiKeyInput",
    "saveKeyButton",
    "refreshButton",
    "globalSearchInput",
    "globalSearchButton",
    "overviewMetrics",
    "overviewCorridors",
    "overviewNotes",
    "searchInput",
    "searchButton",
    "searchResults",
    "exceptionType",
    "loadExceptionsButton",
    "exceptionsResults",
    "loadApprovalsButton",
    "approvalsResults",
    "agentKyaStatus",
    "agentChain",
    "loadAgentsButton",
    "agentsResults",
    "ledgerAgentId",
    "ledgerEntryType",
    "ledgerLimit",
    "loadLedgerButton",
    "ledgerResults",
    "mandateStatus",
    "mandateAgentId",
    "loadMandatesButton",
    "mandatesResults",
    "corridorHours",
    "loadCorridorsButton",
    "corridorsResults",
    "toastRegion",
  ].forEach((id) => {
    els[id] = document.getElementById(id);
  });
}

function wireEvents() {
  els.navList.addEventListener("click", (event) => {
    const target = event.target.closest("[data-view]");
    if (target) showView(target.dataset.view);
  });

  document.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });

  els.saveKeyButton.addEventListener("click", () => {
    state.apiKey = els.apiKeyInput.value.trim() || "ops_dev_key";
    localStorage.setItem("finogridOpsKey", state.apiKey);
    toast("运维密钥已保存。");
    loadCurrentView();
  });

  els.refreshButton.addEventListener("click", () => {
    checkHealth();
    loadCurrentView();
  });

  els.globalSearchButton.addEventListener("click", () => runGlobalSearch());
  els.globalSearchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") runGlobalSearch();
  });

  els.searchButton.addEventListener("click", () => loadSearch());
  els.searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadSearch();
  });

  els.loadExceptionsButton.addEventListener("click", loadExceptions);
  els.loadApprovalsButton.addEventListener("click", loadApprovals);
  els.loadAgentsButton.addEventListener("click", loadAgents);
  els.loadLedgerButton.addEventListener("click", loadLedger);
  els.loadMandatesButton.addEventListener("click", loadMandates);
  els.loadCorridorsButton.addEventListener("click", loadCorridors);
}

function showView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("is-active"));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.remove("is-active"));

  const viewNode = document.getElementById(`${view}View`);
  const navNode = document.querySelector(`[data-view="${view}"]`);
  if (!viewNode || !navNode) return;

  viewNode.classList.add("is-active");
  navNode.classList.add("is-active");
  els.viewTitle.textContent = viewNode.dataset.title || "控制台";
  loadCurrentView();
}

function loadCurrentView() {
  const loaders = {
    overview: loadOverview,
    search: loadSearch,
    exceptions: loadExceptions,
    approvals: loadApprovals,
    agents: loadAgents,
    ledger: loadLedger,
    mandates: loadMandates,
    corridors: loadCorridors,
  };
  loaders[state.view]?.();
}

async function apiFetch(path, options = {}) {
  const headers = {
    Accept: "application/json",
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(path === "/health" ? {} : { "X-OPS-API-KEY": state.apiKey }),
    ...(options.headers || {}),
  };

  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (error) {
    throw new Error(`无法访问 ${path}: ${error.message}`);
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = { detail: response.statusText };
  }

  if (!response.ok) {
    const message = data?.detail || data?.message || response.statusText;
    throw new Error(`${response.status} ${message}`);
  }
  return data;
}

async function checkHealth() {
  setHealth("检查中", "pending");
  try {
    const data = await apiFetch("/health");
    setHealth(data.status === "ok" ? "在线" : "异常", data.status === "ok" ? "ok" : "bad");
  } catch (error) {
    setHealth("离线", "bad");
  }
}

function setHealth(text, tone) {
  els.healthPill.childNodes[1].nodeValue = text;
  els.healthPill.classList.toggle("is-ok", tone === "ok");
  els.healthPill.classList.toggle("is-bad", tone === "bad");
}

async function loadOverview() {
  renderMetrics([
    metric("健康状态", "检查中", "本地 API"),
    metric("异常", "...", "人工处理队列"),
    metric("审批", "...", "待人工复核"),
    metric("代理", "...", "已注册账户"),
  ]);
  els.overviewCorridors.innerHTML = loading();
  els.overviewNotes.innerHTML = "";

  const calls = [
    ["exceptions", "/v1/ops/exceptions?limit=50"],
    ["approvals", "/v1/ops/approvals"],
    ["agents", "/v1/ops/agents?limit=50"],
    ["ledger", "/v1/ops/ledger?limit=20"],
    ["corridors", "/v1/ops/corridors?since_hours=24"],
  ];

  const settled = await Promise.allSettled(calls.map(([name, path]) => apiFetch(path).then((data) => ({ name, data }))));
  const byName = {};
  const notes = [];
  settled.forEach((result, index) => {
    const name = calls[index][0];
    if (result.status === "fulfilled") {
      byName[name] = result.value.data;
    } else {
      notes.push(`${REQUEST_NAMES[name] || name}: ${result.reason.message}`);
    }
  });

  renderMetrics([
    metric("健康状态", "在线", "本地 API"),
    metric("异常", countOf(byName.exceptions, "items"), "人工处理队列"),
    metric("审批", countOf(byName.approvals, "items"), "待人工复核"),
    metric("代理", countOf(byName.agents, "agents"), "已注册账户"),
  ]);

  renderCorridorChart(els.overviewCorridors, byName.corridors?.corridors || []);
  renderNotes(notes);
}

function renderMetrics(metrics) {
  els.overviewMetrics.innerHTML = metrics.map((item) => `
    <article class="metric-card">
      <span>${escapeHTML(item.label)}</span>
      <strong>${escapeHTML(String(item.value))}</strong>
      <small>${escapeHTML(item.caption)}</small>
    </article>
  `).join("");
}

function metric(label, value, caption) {
  return { label, value, caption };
}

function countOf(data, key) {
  if (!data) return "0";
  if (typeof data.total === "number") return data.total;
  if (Array.isArray(data[key])) return data[key].length;
  return "0";
}

function renderNotes(notes) {
  if (!notes.length) {
    els.overviewNotes.innerHTML = `<div class="note-item">所有视图已响应。数据库表当前可能仍为空。</div>`;
    return;
  }
  els.overviewNotes.innerHTML = notes.map((note) => `
    <div class="note-item">${escapeHTML(note)}</div>
  `).join("");
}

async function runGlobalSearch() {
  const q = els.globalSearchInput.value.trim();
  if (!q) return toast("请输入搜索关键词。", true);
  els.searchInput.value = q;
  showView("search");
}

async function loadSearch() {
  const q = els.searchInput.value.trim();
  if (!q) {
    els.searchResults.innerHTML = empty("请输入关键词以查询运维索引。");
    return;
  }
  els.searchResults.innerHTML = loading();
  try {
    const data = await apiFetch(`/v1/ops/search?q=${encodeURIComponent(q)}&limit=50`);
    renderTable(els.searchResults, data.results || [], [
      ["type", "类型"],
      ["id", "ID", "mono"],
      ["label", "标签"],
      ["status", "状态", "badge"],
      ["kya_status", "KYA", "badge"],
      ["amount_usdc", "金额"],
    ]);
  } catch (error) {
    renderError(els.searchResults, error);
  }
}

async function loadExceptions() {
  els.exceptionsResults.innerHTML = loading();
  const type = els.exceptionType.value;
  try {
    const data = await apiFetch(`/v1/ops/exceptions?exception_type=${encodeURIComponent(type)}&limit=100`);
    renderTable(els.exceptionsResults, data.items || [], [
      ["exception_type", "异常类型", "badge"],
      ["id", "ID", "mono"],
      ["name", "名称"],
      ["status", "状态", "badge"],
      ["kya_status", "KYA", "badge"],
      ["corridor", "通道"],
      ["amount_usd", "金额"],
      ["created_at", "创建时间"],
    ]);
  } catch (error) {
    renderError(els.exceptionsResults, error);
  }
}

async function loadApprovals() {
  els.approvalsResults.innerHTML = loading();
  try {
    const data = await apiFetch("/v1/ops/approvals");
    renderTable(els.approvalsResults, data.items || [], [
      ["approval_id", "审批 ID", "mono"],
      ["entity_type", "实体", "badge"],
      ["entity_id", "实体 ID", "mono"],
      ["details", "详情", "json"],
      ["created_at", "创建时间"],
      ["approval_id", "操作", "approvalActions"],
    ]);
  } catch (error) {
    renderError(els.approvalsResults, error);
  }
}

async function decideApproval(approvalId, decision) {
  const opsAgentId = window.prompt("运维人员 ID", "ops-console");
  if (!opsAgentId) return;
  const opsNote = window.prompt("处理说明");
  if (!opsNote) return;

  try {
    await apiFetch(`/v1/ops/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST",
      body: JSON.stringify({ decision, ops_agent_id: opsAgentId, ops_note: opsNote }),
    });
    toast(decision === "approve" ? "审批已通过。" : "审批已拒绝。");
    loadApprovals();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadAgents() {
  els.agentsResults.innerHTML = loading();
  const params = new URLSearchParams({ limit: "100" });
  if (els.agentKyaStatus.value) params.set("kya_status", els.agentKyaStatus.value);
  if (els.agentChain.value.trim()) params.set("chain", els.agentChain.value.trim());
  try {
    const data = await apiFetch(`/v1/ops/agents?${params.toString()}`);
    renderTable(els.agentsResults, data.agents || [], [
      ["agent_account_id", "代理 ID", "mono"],
      ["name", "名称"],
      ["kya_status", "KYA", "badge"],
      ["chain", "链"],
      ["available_usdc", "可用 USDC"],
      ["reserved_balance_usdc", "冻结余额"],
      ["status", "状态", "badge"],
      ["created_at", "创建时间"],
    ]);
  } catch (error) {
    renderError(els.agentsResults, error);
  }
}

async function loadLedger() {
  els.ledgerResults.innerHTML = loading();
  const params = new URLSearchParams({
    limit: String(Math.min(Math.max(Number(els.ledgerLimit.value) || 100, 1), 500)),
  });
  if (els.ledgerAgentId.value.trim()) params.set("agent_account_id", els.ledgerAgentId.value.trim());
  if (els.ledgerEntryType.value) params.set("entry_type", els.ledgerEntryType.value);

  try {
    const data = await apiFetch(`/v1/ops/ledger?${params.toString()}`);
    renderTable(els.ledgerResults, data.entries || [], [
      ["id", "分录 ID", "mono"],
      ["agent_account_id", "代理 ID", "mono"],
      ["entry_type", "类型", "badge"],
      ["amount_usdc", "金额"],
      ["balance_after", "余额"],
      ["reserved_balance_after", "冻结后余额"],
      ["description", "描述"],
      ["created_at", "创建时间"],
    ]);
  } catch (error) {
    renderError(els.ledgerResults, error);
  }
}

async function loadMandates() {
  els.mandatesResults.innerHTML = loading();
  const params = new URLSearchParams();
  if (els.mandateStatus.value) params.set("status", els.mandateStatus.value);
  if (els.mandateAgentId.value.trim()) params.set("agent_account_id", els.mandateAgentId.value.trim());

  try {
    const query = params.toString() ? `?${params.toString()}` : "";
    const data = await apiFetch(`/v1/ops/mandates${query}`);
    renderTable(els.mandatesResults, data.mandates || [], [
      ["mandate_id", "授权 ID", "mono"],
      ["agent_account_id", "代理 ID", "mono"],
      ["status", "状态", "badge"],
      ["scope", "范围"],
      ["approval_mode", "审批模式"],
      ["max_amount_per_tx_usdc", "单笔上限"],
      ["max_daily_usdc", "每日上限"],
      ["created_at", "创建时间"],
    ]);
  } catch (error) {
    renderError(els.mandatesResults, error);
  }
}

async function loadCorridors() {
  els.corridorsResults.innerHTML = loading();
  const hours = Math.min(Math.max(Number(els.corridorHours.value) || 24, 1), 720);
  try {
    const data = await apiFetch(`/v1/ops/corridors?since_hours=${hours}`);
    renderCorridorChart(els.corridorsResults, data.corridors || []);
  } catch (error) {
    renderError(els.corridorsResults, error);
  }
}

function renderCorridorChart(target, corridors) {
  if (!corridors.length) {
    target.innerHTML = empty("所选时间窗口内暂无通道活动。");
    return;
  }
  const max = Math.max(...corridors.map((item) => Number(item.total) || 0), 1);
  target.innerHTML = corridors.map((item) => {
    const width = Math.max(4, Math.round(((Number(item.total) || 0) / max) * 100));
    return `
      <div class="chart-row">
        <div class="chart-label">${escapeHTML(item.corridor || "未知")}</div>
        <div class="chart-track"><div class="chart-fill" style="width:${width}%"></div></div>
        <div class="chart-value">${escapeHTML(String(item.total ?? 0))}</div>
      </div>
      <div class="note-item">
        成功率 ${escapeHTML(String(item.success_rate_pct ?? 0))}% · 失败 ${escapeHTML(String(item.failed ?? 0))} · 暂挂 ${escapeHTML(String(item.held ?? 0))}
      </div>
    `;
  }).join("");
}

function renderTable(target, rows, columns) {
  if (!rows.length) {
    target.innerHTML = empty("暂无记录。");
    return;
  }

  target.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>${columns.map(([, label]) => `<th>${escapeHTML(label)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              ${columns.map(([key, , type]) => `<td>${renderCell(row, key, type)}</td>`).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCell(row, key, type) {
  const value = row[key];
  if (type === "badge") return badge(value);
  if (type === "json") return `<span class="cell-mono">${escapeHTML(JSON.stringify(value ?? {}, null, 0))}</span>`;
  if (type === "mono") return `<span class="cell-mono">${escapeHTML(formatValue(value))}</span>`;
  if (type === "approvalActions") {
    const id = formatValue(value);
    return `
      <div class="row-actions">
        <button class="mini-button good" type="button" onclick="decideApproval('${escapeAttribute(id)}','approve')">通过</button>
        <button class="mini-button bad" type="button" onclick="decideApproval('${escapeAttribute(id)}','reject')">拒绝</button>
      </div>
    `;
  }
  return escapeHTML(formatValue(value));
}

function badge(value) {
  const text = formatValue(value);
  const normalized = text.toLowerCase();
  let tone = "";
  if (["active", "ok", "basic", "enhanced", "completed", "settled"].some((word) => normalized.includes(word))) tone = "good";
  if (["pending", "held", "draft", "unverified", "expired"].some((word) => normalized.includes(word))) tone = "warn";
  if (["failed", "blocked", "revoked", "reject", "error"].some((word) => normalized.includes(word))) tone = "bad";
  return `<span class="badge ${tone}">${escapeHTML(localizeValue(text))}</span>`;
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function localizeValue(value) {
  const text = String(value);
  return VALUE_LABELS[text.toLowerCase()] || text;
}

function loading() {
  return `<div class="empty-state">加载中...</div>`;
}

function empty(message) {
  return `<div class="empty-state">${escapeHTML(message)}</div>`;
}

function renderError(target, error) {
  target.innerHTML = `<div class="error-state">${escapeHTML(error.message)}</div>`;
}

function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast ${isError ? "is-error" : ""}`;
  node.textContent = message;
  els.toastRegion.appendChild(node);
  window.setTimeout(() => node.remove(), 3200);
}

function escapeHTML(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHTML(value).replaceAll("`", "&#096;");
}
