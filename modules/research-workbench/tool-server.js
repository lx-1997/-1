#!/usr/bin/env node
"use strict";

const fs = require("node:fs/promises");
const fssync = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { createHash, randomInt, randomUUID } = require("node:crypto");
const { Readable } = require("node:stream");
const mammoth = require("mammoth");
const { PDFParse } = require("pdf-parse");
const { chromium } = require("playwright-core");

const ROOT = __dirname;
const REPO_ROOT = path.resolve(ROOT, "../..");
const PUBLIC_DIR = path.join(ROOT, "tool-public");
const RUN_DIR = path.join(ROOT, ".tool-runs");
const BROWSER_PROFILE_DIR = path.join(ROOT, ".zsxq-browser-profile");
const BROWSER_AUTH_PATH = path.join(RUN_DIR, "zsxq-browser-auth.json");
const SUMMARY_EXPORT_DIR = path.join(ROOT, "exports", "summaries");
const SHARED_MODEL_CONFIG_PATH = process.env.DEEPFOCUS_MODEL_CONFIG_PATH
  || path.join(REPO_ROOT, "backend", ".model_config.json");
const DEFAULT_PORT = Number(process.env.PORT || 3927);
const CORS_ORIGIN = process.env.RESEARCH_WORKBENCH_CORS_ORIGIN || "*";
const API_BASE = "https://api.zsxq.com/v2";
const WEB_ORIGIN = "https://wx.zsxq.com";
const X_VERSION = "2.91.0";
const DEFAULT_MAX_PAGES = 5;
const DEFAULT_SEARCH_PAGES = 100;
const DEFAULT_ANALYSIS_PROMPT = [
  "请以华尔街 buy-side 投资经理/投委会备忘录的标准解读这份海外投行研报。",
  "目标不是复述研报，而是把研报转成可决策、可复核、可跟踪的投资摘要。",
  "输出给前端展示，直接写可读短句；不要使用 Markdown 标题、表格语法、加粗符号、代码块或分隔线。",
  "这是给多数用户阅读的解析版：正文和表格不要出现页码、Exhibit、章节位置，也不要单独设置“位置/页码/出处”列。",
  "不要输出任何技术元信息，例如输入模式、文件路径、Markdown 路径、导出时间、模型名称、生成时间。",
  "不要输出连续长段；每个要点拆成独立短句，方便前端渲染成卡片。",
  "",
  "投资判断",
  "投资动作：用一句话说明评级/方向、目标价或隐含上行空间；没有披露就写“研报未披露”。",
  "为什么现在重要：说明这份研报今天对股价/预期差最重要的变化。",
  "核心分歧：说明多空分歧的核心，不超过 1 句。",
  "跟踪窗口：说明 3-12 个月最重要的验证窗口。",
  "",
  "关键数字",
  "列出目标价/评级/上行空间、EPS/PE/收入/毛利/价格等关键变量；每项写成“指标：研报数据；投资含义”。",
  "",
  "预期差与情景推演",
  "乐观：哪个变量超预期；股价、估值或盈利怎么上修。",
  "基准：研报主假设如何兑现；为什么维持当前判断。",
  "悲观：哪个变量低于预期；什么情况下需要降权或退出。",
  "",
  "投资逻辑",
  "按“论点：关键事实和数字；结论：对盈利、估值或情绪的影响”输出。",
  "",
  "催化剂与跟踪清单",
  "列出价格、订单、渠道、政策、财报等指标或事件；说明好于预期和差于预期时的动作。",
  "",
  "推翻条件与风险",
  "列出推翻条件、观察信号和需要动作，例如重新估值、降权、退出或继续观察。",
  "",
  "证据质量与待确认",
  "高可信：列出证据充分的结论，不写具体页码。",
  "待确认：列出研报没有披露、OCR 不清晰或需要外部数据复核的点。",
  "下一步问题：给分析师/Agent 的 3 个追问。",
  "",
  "证据不足时明确写“不确定/研报未披露”。引用定位保留在后台复核，不要放进给用户看的解析正文。",
].join("\n");
const STRUCTURED_OUTPUT_RULES = [
  "输出必须像投资经理备忘录：先给投资判断和核心分歧，再给情景推演、证据、跟踪清单、推翻条件。",
  "直接输出可给用户阅读的短句内容，不要依赖 Markdown 标题、表格语法、代码块或分隔线；前端会负责排版。",
  "不要输出 #、**、`、| 表格等格式符；如需列点，用自然短句分行。",
  "面向用户的解析版不要出现页码、Exhibit、章节位置或“位置/页码/出处”列。",
  "不要输出输入模式、文件路径、Markdown 路径、导出时间、模型名称、生成时间等技术元信息。",
  "不要输出连续超过 4 行的大段文字；每个要点拆成独立短句。",
  "证据不足时写“不确定/研报未披露”，不要补外部事实。",
].join("\n");
const jobs = new Map();
const extractionCache = new Map();
const previewLinks = new Map();
const PREVIEW_TTL_MS = 10 * 60 * 1000;

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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function inlineMarkdownHtml(value) {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(\[[A-Za-z]\d+\]|第\s*\d+\s*页|\bP\.?\s*\d+\b|\bp\.?\s*\d+\b)/g, "<span class=\"citation\">$1</span>");
}

function localizeReaderEnglish(value) {
  return String(value || "")
    .replace(/["“”']?\bBuy\b["“”']?/gi, "买入")
    .replace(/["“”']?\bSell\b["“”']?/gi, "卖出")
    .replace(/["“”']?\bHold\b["“”']?/gi, "持有")
    .replace(/["“”']?\bNeutral\b["“”']?/gi, "中性")
    .replace(/\bThesis\s*break\b/gi, "反证条件")
    .replace(/\bKey\s*debate\b/gi, "核心分歧")
    .replace(/\bAction\b/gi, "投资动作")
    .replace(/\bGS\s*Forecast\b/gi, "高盛预测")
    .replace(/\bDTC\b/g, "直销渠道")
    .replace(/\bstaples\b/gi, "必选消费品")
    .replace(/\bvs\./gi, "相较");
}

function stripReaderReferenceText(value) {
  return localizeReaderEnglish(String(value || ""))
    .replace(/（\s*[^（）]*(第\s*\d+\s*页|P\.?\s*\d+|p\.?\s*\d+|Exhibit\s*\d+)[^（）]*\s*）/gi, "")
    .replace(/\(\s*[^()]*(第\s*\d+\s*页|P\.?\s*\d+|p\.?\s*\d+|Exhibit\s*\d+)[^()]*\s*\)/gi, "")
    .replace(/第\s*\d+\s*页\s*(正文|顶部价格卡片|图表|表格)?/g, "")
    .replace(/\b[Pp]\.?\s*\d+\b/g, "")
    .replace(/\bExhibit\s*\d+\b/gi, "")
    .replace(/位置未标明/g, "")
    .replace(/[；;，,、]\s*([）\)])/g, "$1")
    .replace(/（\s*）|\(\s*\)/g, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([，。；：、])/g, "$1")
    .replace(/([（(])\s+/g, "$1")
    .trim();
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

function isReaderReferenceColumn(header) {
  return /^(位置|页码|出处|来源|引用|原文位置|证据位置|章节|已确认依据|原文依据|证据来源)$/i.test(String(header || "").trim());
}

function markdownTableDivider(columnCount) {
  return `| ${Array.from({ length: columnCount }, () => "---").join(" | ")} |`;
}

function simplifyMarkdownForReader(markdown) {
  const rawLines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const firstContentIndex = rawLines.findIndex((line) => line.trim());
  const hasSavedAnalysisMeta = rawLines
    .slice(Math.max(0, firstContentIndex), Math.max(0, firstContentIndex) + 8)
    .some((line) => /^\s*[-*]\s*(模型|生成时间|输入模式|PDF\s*页数|使用页数|Markdown|导出时间|输入)\s*[:：]/i.test(line));
  let startIndex = 0;
  if (firstContentIndex >= 0 && (/^#\s+/.test(rawLines[firstContentIndex].trim()) || hasSavedAnalysisMeta)) {
    const firstSectionIndex = rawLines.findIndex((line, index) => index > firstContentIndex && /^#{2,4}\s+/.test(line.trim()));
    if (firstSectionIndex > firstContentIndex) startIndex = firstSectionIndex;
  }
  const lines = rawLines.slice(startIndex);
  const output = [];
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const line = raw.trim();
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) continue;
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading && /原文依据|引用定位|来源定位|出处/.test(heading[2]) && /未确认|待确认|需补充/.test(heading[2])) {
      output.push(`${heading[1]} 待确认项`);
      continue;
    }
    if (isMarkdownTableLine(line) && isMarkdownTableDivider(lines[index + 1])) {
      const header = splitMarkdownTableRow(line);
      const keepIndexes = header
        .map((cell, cellIndex) => ({ cell, cellIndex }))
        .filter(({ cell }) => !isReaderReferenceColumn(cell))
        .map(({ cellIndex }) => cellIndex);
      const finalIndexes = keepIndexes.length ? keepIndexes : header.map((_, cellIndex) => cellIndex);
      const finalHeader = finalIndexes.map((cellIndex) => stripReaderReferenceText(header[cellIndex]) || header[cellIndex]);
      if (finalHeader.length === 1) {
        index += 2;
        while (index < lines.length && isMarkdownTableLine(lines[index])) {
          const row = splitMarkdownTableRow(lines[index]);
          const cell = stripReaderReferenceText(row[finalIndexes[0]] || "");
          if (cell) output.push(`- ${cell}`);
          index += 1;
        }
        index -= 1;
        continue;
      }
      output.push(`| ${finalHeader.join(" | ")} |`);
      output.push(markdownTableDivider(finalHeader.length));
      index += 2;
      while (index < lines.length && isMarkdownTableLine(lines[index])) {
        const row = splitMarkdownTableRow(lines[index]);
        const cells = finalIndexes.map((cellIndex) => stripReaderReferenceText(row[cellIndex] || ""));
        output.push(`| ${cells.join(" | ")} |`);
        index += 1;
      }
      index -= 1;
      continue;
    }
    output.push(stripReaderReferenceText(raw));
  }
  return output.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function cleanPdfTitle(value) {
  return path.basename(String(value || "研报总结").trim() || "研报总结")
    .replace(/\.pdf-\d{8}-\d{6}\.pdf$/i, "")
    .replace(/\.(pdf|md)$/i, "")
    .replace(/\s+/g, " ")
    .trim() || "研报总结";
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let listType = "";
  let inCode = false;
  let codeLines = [];

  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = "";
  };
  const ensureList = (type) => {
    if (listType === type) return;
    closeList();
    listType = type;
    html.push(`<${type}>`);
  };
  const closeCode = () => {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    codeLines = [];
    inCode = false;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const line = raw.trim();
    if (/^```/.test(line)) {
      closeList();
      if (inCode) {
        closeCode();
      } else {
        inCode = true;
        codeLines = [];
      }
      continue;
    }
    if (inCode) {
      codeLines.push(raw);
      continue;
    }
    if (!line) {
      closeList();
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      closeList();
      continue;
    }
    if (isMarkdownTableLine(line) && isMarkdownTableDivider(lines[index + 1])) {
      closeList();
      const header = splitMarkdownTableRow(line);
      const colCount = Math.min(Math.max(header.length, 1), 4);
      html.push(`<table class="md-table cols-${colCount}"><thead><tr>`);
      for (const cell of header) html.push(`<th>${inlineMarkdownHtml(cell)}</th>`);
      html.push("</tr></thead><tbody>");
      index += 2;
      while (index < lines.length && isMarkdownTableLine(lines[index])) {
        html.push("<tr>");
        for (const cell of splitMarkdownTableRow(lines[index])) html.push(`<td>${inlineMarkdownHtml(cell)}</td>`);
        html.push("</tr>");
        index += 1;
      }
      index -= 1;
      html.push("</tbody></table>");
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(3, Math.max(1, heading[1].length - 1));
      html.push(`<h${level}>${inlineMarkdownHtml(heading[2])}</h${level}>`);
      continue;
    }
    const ordered = line.match(/^(\d+)[.、)]\s+(.+)$/);
    if (ordered) {
      ensureList("ol");
      html.push(`<li>${inlineMarkdownHtml(ordered[2])}</li>`);
      continue;
    }
    const unordered = line.match(/^[-*•]\s+(.+)$/);
    if (unordered) {
      ensureList("ul");
      html.push(`<li>${inlineMarkdownHtml(unordered[1])}</li>`);
      continue;
    }
    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      closeList();
      html.push(`<blockquote>${inlineMarkdownHtml(quote[1])}</blockquote>`);
      continue;
    }
    closeList();
    html.push(`<p>${inlineMarkdownHtml(line)}</p>`);
  }
  if (inCode) closeCode();
  closeList();
  return html.join("\n");
}

function plainReaderText(value) {
  return stripReaderReferenceText(value)
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compactText(value, maxLength = 62) {
  const text = plainReaderText(value);
  if (text.length <= maxLength) return text;
  const head = text.slice(0, maxLength);
  const sentenceBreak = Math.max(
    head.lastIndexOf("。"),
    head.lastIndexOf("！"),
    head.lastIndexOf("？"),
    head.lastIndexOf("；"),
    head.lastIndexOf(";")
  );
  if (sentenceBreak >= Math.min(36, Math.floor(maxLength * 0.55))) {
    return head.slice(0, sentenceBreak + 1);
  }
  const clauseBreak = Math.max(head.lastIndexOf("，"), head.lastIndexOf(","));
  if (clauseBreak >= Math.min(32, Math.floor(maxLength * 0.5))) {
    return `${head.slice(0, clauseBreak)}。`;
  }
  return head;
}

function compactMetricText(value, maxLength = 62) {
  return compactText(String(value || "")
    .replace(/（[^（）]{8,}）/g, "")
    .replace(/\([^()]{8,}\)/g, "")
    .replace(/\s+/g, " ")
    .trim(), maxLength);
}

function sectionText(markdown, patterns) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const matchers = patterns.map((pattern) => new RegExp(pattern, "i"));
  let start = -1;
  for (let index = 0; index < lines.length; index += 1) {
    const heading = lines[index].match(/^#{1,4}\s+(.+)$/);
    if (heading && matchers.some((matcher) => matcher.test(heading[1]))) {
      start = index + 1;
      break;
    }
  }
  if (start < 0) return "";
  const collected = [];
  for (let index = start; index < lines.length; index += 1) {
    if (/^#{1,4}\s+/.test(lines[index])) break;
    collected.push(lines[index]);
  }
  return collected.join("\n");
}

function firstBulletText(markdown) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  for (const line of lines) {
    const bullet = line.trim().match(/^[-*•]\s+(.+)$/);
    if (bullet) return bullet[1];
  }
  return "";
}

function labeledBulletText(markdown, labels = []) {
  const matchers = labels.map((label) => new RegExp(label, "i"));
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  for (const line of lines) {
    const match = line.trim().match(/^[-*•]\s+(?:\*\*)?([^：:*]+)(?:\*\*)?\s*[:：]\s*(.+)$/);
    if (!match) continue;
    const label = plainReaderText(match[1]);
    if (matchers.some((matcher) => matcher.test(label))) return match[2];
  }
  return "";
}

function firstTableRowSummary(markdown, preferredColumns = []) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  for (let index = 0; index < lines.length - 1; index += 1) {
    if (!isMarkdownTableLine(lines[index]) || !isMarkdownTableDivider(lines[index + 1])) continue;
    const header = splitMarkdownTableRow(lines[index]);
    let rowIndex = index + 2;
    while (rowIndex < lines.length && isMarkdownTableLine(lines[rowIndex])) {
      const row = splitMarkdownTableRow(lines[rowIndex]);
      const cells = preferredColumns.length
        ? preferredColumns
          .map((name) => header.findIndex((cell) => new RegExp(name, "i").test(cell)))
          .filter((cellIndex) => cellIndex >= 0)
          .map((cellIndex) => row[cellIndex])
        : row;
      const summary = cells.map((cell) => plainReaderText(cell)).filter(Boolean).join("：");
      if (summary) return summary;
      rowIndex += 1;
    }
  }
  return "";
}

function matchMetric(text, patterns) {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match?.[1]) return match[1].replace(/\s+/g, " ").trim();
  }
  return "";
}

function normalizeRatingText(value) {
  const text = String(value || "").trim();
  const lower = text.toLowerCase();
  if (lower === "buy") return "买入";
  if (lower === "sell") return "卖出";
  if (lower === "hold") return "持有";
  if (lower === "neutral") return "中性";
  return localizeReaderEnglish(text);
}

const PRICE_EXPR = "(?:[$¥￥]\\s*)?[0-9,]+(?:\\.\\d+)?\\s*(?:万亿|亿|万)?\\s*(?:人民币|港元|港币|美元|美金|韩元|日元|欧元|英镑|台币|新元|元|KRW|USD|HKD|JPY|EUR|GBP)?";

function buildReaderSnapshot(markdown) {
  const text = plainReaderText(markdown);
  const conclusionSection = sectionText(markdown, ["投资判断", "投资结论", "一句话结论", "结论"]);
  const logicSection = sectionText(markdown, ["投资逻辑", "核心判断", "关键证据"]);
  const catalystSection = sectionText(markdown, ["催化", "监控", "跟踪"]);
  const riskSection = sectionText(markdown, ["推翻", "风险", "反证", "Thesis"]);
  const action = labeledBulletText(conclusionSection, ["投资动作", "投资判断", "投资结论"])
    || firstBulletText(conclusionSection)
    || firstTableRowSummary(logicSection, ["论点", "判断"]);
  const whyNow = labeledBulletText(conclusionSection, ["为什么现在", "现在重要", "预期差"])
    || firstTableRowSummary(logicSection, ["证据", "结论"]);
  const debate = labeledBulletText(conclusionSection, ["核心分歧", "多空分歧", "分歧"])
    || firstTableRowSummary(markdown, ["情景", "触发", "含义"]);
  const trackingWindow = labeledBulletText(conclusionSection, ["跟踪窗口", "验证窗口", "时间窗口"])
    || firstTableRowSummary(catalystSection, ["指标", "监控项", "事件"]);
  const rating = normalizeRatingText(matchMetric(text, [
    /(?:维持|给予|评级[为是]?|评级["“]?)\s*(买入|Buy|增持|中性|持有|卖出|Sell)/i,
    /(买入|Buy|增持|中性|持有|卖出|Sell)\s*评级/i,
    /\b(Buy|Sell|Hold|Neutral)\b/i,
  ]));
  const targetPrice = matchMetric(text, [
    new RegExp(`(?:目标价|12\\s*个月目标价)[^。\\n；;]{0,48}?(?:上调|下调|调整|提升|提高|降低|升至|降至|raise|cut)?\\s*(?:至|到|为)\\s*(${PRICE_EXPR})`, "i"),
    new RegExp(`(?:目标价|12\\s*个月目标价)\\s*[=:：]?\\s*(${PRICE_EXPR})`, "i"),
  ]);
  const upside = matchMetric(text, [
    /(?:上行空间|上涨空间|上行)\s*(?:为|约|[:：])?\s*([0-9]+(?:\.\d+)?%)/i,
    /([0-9]+(?:\.\d+)?%)\s*(?:的)?\s*(?:上行空间|上涨空间|上行)/i,
    /(?:隐含|对应|较当前|较现价|较收盘价)[^。；;，,]{0,42}?([0-9]+(?:\.\d+)?%)/i,
  ]);
  const catalyst = firstTableRowSummary(catalystSection, ["监控项", "触发", "验证", "催化"])
    || firstBulletText(catalystSection);
  const risk = firstTableRowSummary(riskSection, ["风险", "观察", "Thesis"])
    || firstBulletText(riskSection);
  const actionValue = [
    rating,
  ].filter(Boolean).join("，") || compactText(action, 80) || "研报未披露";
  const upsideValue = upside || (targetPrice ? `目标价 ${targetPrice}` : "研报未披露");
  return {
    metrics: [
      { label: "投资动作", value: compactText(actionValue, 72) },
      { label: "目标价", value: compactText(targetPrice || "研报未披露", 52) },
      { label: "上行空间", value: compactText(upsideValue, 48) },
      { label: "验证窗口", value: compactMetricText(trackingWindow || "研报未披露", 48) },
    ],
    briefs: [
      { label: "为什么现在重要", value: compactText(whyNow, 180) || compactText(action, 180) || "研报未披露" },
      { label: "核心分歧", value: compactText(debate, 180) || "研报未披露" },
      { label: "关键催化", value: compactText(catalyst, 180) || "研报未披露" },
      { label: "推翻条件", value: compactText(risk, 180) || "研报未披露" },
    ],
  };
}

function safePdfFilename(value) {
  const cleaned = cleanPdfTitle(value)
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return `${cleaned || "研报总结"}.pdf`;
}

async function saveExportedSummaryPdf(pdf, filename) {
  await fs.mkdir(SUMMARY_EXPORT_DIR, { recursive: true });
  const parsed = path.parse(filename);
  let candidate = `${parsed.name}${parsed.ext || ".pdf"}`;
  let target = path.join(SUMMARY_EXPORT_DIR, candidate);
  if (fssync.existsSync(target)) {
    const stamp = new Date().toISOString()
      .replace(/[-:]/g, "")
      .replace(/\..+$/, "")
      .replace("T", "-");
    candidate = `${parsed.name}-${stamp}${parsed.ext || ".pdf"}`;
    target = path.join(SUMMARY_EXPORT_DIR, candidate);
  }
  await fs.writeFile(target, pdf);
  return path.relative(ROOT, target);
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

function openCommandForPath(targetPath) {
  if (process.platform === "darwin") return { command: "open", args: [targetPath] };
  if (process.platform === "win32") return { command: "cmd", args: ["/c", "start", "", targetPath] };
  return { command: "xdg-open", args: [targetPath] };
}

async function openWorkbenchPath(payload = {}) {
  const rawPath = String(payload.path || payload.analysisPath || "").trim();
  if (!rawPath) throw new Error("缺少要打开的路径");
  const target = safeJoin(ROOT, rawPath);
  if (!fssync.existsSync(target)) throw new Error(`文件不存在：${rawPath}`);
  const stat = await fs.stat(target);
  const mode = String(payload.mode || "file").toLowerCase();
  const openTarget = mode === "folder"
    ? (stat.isDirectory() ? target : path.dirname(target))
    : target;
  const { command, args } = openCommandForPath(openTarget);
  const child = spawn(command, args, {
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  return {
    ok: true,
    opened: path.relative(ROOT, openTarget) || ".",
    mode: mode === "folder" ? "folder" : "file",
  };
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
  const maxPages = Number(payload.maxPages || DEFAULT_MAX_PAGES);

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
  let cookie = String(payload.cookie || "").trim();
  let aduid = String(payload.aduid || "").trim();
  let curlFile = "";
  let selectionFile = "";

  if (!curlText && !cookie && !process.env.ZSXQ_COOKIE) {
    const browserAuth = await readBrowserAuth();
    cookie = browserAuth.cookie || "";
    aduid ||= browserAuth.aduid || "";
  }

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
      maxPages: Number(payload.maxPages || DEFAULT_MAX_PAGES),
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
  if (!found) throw new Error("未找到 Chrome/Chromium。请先安装 Chrome 后再使用微信扫码登录。");
  return found;
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

async function cookieHeaderFromContext(context) {
  const cookies = await context.cookies(["https://api.zsxq.com", WEB_ORIGIN]);
  return cookies
    .filter((cookie) => cookie.domain.includes("zsxq.com"))
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
}

async function readBrowserAuth() {
  try {
    const saved = JSON.parse(await fs.readFile(BROWSER_AUTH_PATH, "utf8"));
    return {
      cookie: normalizeCookieText(saved.cookie || ""),
      aduid: String(saved.aduid || ""),
      savedAt: saved.savedAt || "",
    };
  } catch {
    return { cookie: "", aduid: "", savedAt: "" };
  }
}

async function writeBrowserAuth(auth) {
  await fs.mkdir(RUN_DIR, { recursive: true });
  const cookie = normalizeCookieText(auth.cookie || "");
  const payload = {
    cookie,
    aduid: String(auth.aduid || ""),
    savedAt: new Date().toISOString(),
  };
  await fs.writeFile(BROWSER_AUTH_PATH, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return payload;
}

function authPreview(auth) {
  const cookie = normalizeCookieText(auth.cookie || "");
  return {
    configured: Boolean(cookie),
    cookieLength: cookie.length,
    aduidAvailable: Boolean(auth.aduid),
    savedAt: auth.savedAt || "",
  };
}

async function validateAuth(auth, group) {
  if (!auth.cookie) return false;
  try {
    await apiGet(auth, `${API_BASE}/groups/${group}/files?count=1`);
    return true;
  } catch (error) {
    if (error.status === 401 || error.code === 401) return false;
    throw error;
  }
}

async function browserLoginAuth(payload = {}) {
  const group = String(payload.group || "88888142214212").trim();
  const loginTimeout = Math.max(30, Math.min(600, Number(payload.loginTimeout || 300))) * 1000;
  let context = null;
  try {
    context = await chromium.launchPersistentContext(BROWSER_PROFILE_DIR, {
      headless: false,
      executablePath: chromeExecutablePath(),
      acceptDownloads: true,
      viewport: { width: 1280, height: 900 },
    });
    const page = context.pages()[0] || await context.newPage();
    const aduid = await getAduid(page);
    const probe = async () => {
      const cookie = normalizeCookieText(await cookieHeaderFromContext(context));
      if (!cookie) return null;
      const auth = { cookie, aduid };
      if (await validateAuth(auth, group)) return auth;
      return null;
    };

    const existing = await probe();
    if (existing) return writeBrowserAuth(existing);

    await page.goto(`${WEB_ORIGIN}/group/${group}/files`, { waitUntil: "domcontentloaded" }).catch(() => undefined);
    await page.bringToFront().catch(() => undefined);
    const started = Date.now();
    while (Date.now() - started < loginTimeout) {
      const auth = await probe();
      if (auth) return writeBrowserAuth(auth);
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    throw new Error("等待微信扫码登录超时。请确认已在打开的 Chrome 页面完成登录，并且账号有该星球权限。");
  } finally {
    if (context) await context.close().catch(() => undefined);
  }
}

async function resolveAuth(payload) {
  const curlText = String(payload.curlText || "").trim();
  if (curlText) {
    const parsed = parseCurlAuth(curlText);
    return { cookie: parsed.cookie, aduid: String(payload.aduid || parsed.aduid || process.env.ZSXQ_ADUID || "") };
  }
  const explicitCookie = normalizeCookieText(payload.cookie || "");
  if (explicitCookie) return { cookie: explicitCookie, aduid: String(payload.aduid || process.env.ZSXQ_ADUID || "") };
  const envCookie = normalizeCookieText(process.env.ZSXQ_COOKIE || "");
  if (envCookie) return { cookie: envCookie, aduid: String(payload.aduid || process.env.ZSXQ_ADUID || "") };
  const cached = await readBrowserAuth();
  if (cached.cookie) return { cookie: cached.cookie, aduid: String(payload.aduid || process.env.ZSXQ_ADUID || cached.aduid || "") };
  if (payload.authMode === "browser") return browserLoginAuth(payload);
  return { cookie: "", aduid: String(payload.aduid || process.env.ZSXQ_ADUID || "") };
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

async function downloadUrlForFile(auth, fileId) {
  const data = await apiGet(auth, `${API_BASE}/files/${encodeURIComponent(fileId)}/download_url`);
  if (!data.download_url) throw new Error(`文件 ${fileId} 没有返回在线预览地址`);
  return data.download_url;
}

function cleanupPreviewLinks() {
  const now = Date.now();
  for (const [id, entry] of previewLinks.entries()) {
    if (entry.expiresAt <= now) previewLinks.delete(id);
  }
}

async function createPreviewLink(payload) {
  const fileId = String(payload.fileId || payload.file_id || "").trim();
  if (!fileId) throw new Error("缺少文件 ID，无法在线预览");
  const auth = await resolveAuth(payload);
  if (!auth.cookie) throw new Error("缺少 Cookie。请选择环境凭证、粘贴 curl、粘贴 Cookie，或先微信扫码登录。");
  const downloadUrl = await downloadUrlForFile(auth, fileId);
  cleanupPreviewLinks();
  const id = randomUUID().slice(0, 12);
  const name = String(payload.name || `${fileId}.pdf`).trim();
  previewLinks.set(id, {
    url: downloadUrl,
    name,
    expiresAt: Date.now() + PREVIEW_TTL_MS,
  });
  return {
    id,
    name,
    previewUrl: `/api/previews/${id}`,
    expiresAt: new Date(Date.now() + PREVIEW_TTL_MS).toISOString(),
  };
}

function contentTypeForName(name) {
  const ext = path.extname(String(name || "")).toLowerCase();
  if (ext === ".pdf") return "application/pdf";
  if (ext === ".txt" || ext === ".md" || ext === ".csv") return "text/plain; charset=utf-8";
  if (ext === ".html" || ext === ".htm") return "text/html; charset=utf-8";
  return "application/octet-stream";
}

async function streamPreview(id, res, download = false) {
  cleanupPreviewLinks();
  const entry = previewLinks.get(id);
  if (!entry) return send(res, 410, "Preview expired", "text/plain; charset=utf-8");
  const upstream = await fetch(entry.url, { redirect: "follow" });
  if (!upstream.ok) {
    return send(res, upstream.status, "Preview source unavailable", "text/plain; charset=utf-8");
  }
  const contentType = upstream.headers.get("content-type") || contentTypeForName(entry.name);
  const contentLength = upstream.headers.get("content-length");
  const disposition = download ? "attachment" : "inline";
  const headers = {
    "content-type": contentType,
    "content-disposition": `${disposition}; filename*=UTF-8''${encodeURIComponent(entry.name)}`,
    "cache-control": "no-store",
  };
  if (contentLength) headers["content-length"] = contentLength;
  res.writeHead(200, corsHeaders(headers));
  if (upstream.body) {
    Readable.fromWeb(upstream.body).pipe(res);
    return undefined;
  }
  res.end(Buffer.from(await upstream.arrayBuffer()));
  return undefined;
}

function summaryPdfHtml(payload) {
  const title = cleanPdfTitle(payload.title || "研报总结");
  const content = simplifyMarkdownForReader(String(payload.content || "").trim());
  const snapshot = buildReaderSnapshot(content);
  const watermarkTitle = "关注公众号：赚DAO";
  const watermarkSubtitle = "每天拆解机构研报，抓预期差、催化剂与风险信号";
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    @page { size: A4; margin: 16mm 15mm 15mm; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: #172026;
      background: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif;
      font-size: 11.4px;
      line-height: 1.52;
    }
    .cover {
      margin-bottom: 12px;
      padding-bottom: 10px;
      border-bottom: 1.5px solid #235c67;
    }
    .eyebrow {
      margin-bottom: 6px;
      color: #235c67;
      font-size: 9.5px;
      font-weight: 800;
      letter-spacing: 0;
    }
    h1 {
      margin: 0;
      color: #111820;
      font-size: 18.5px;
      line-height: 1.28;
    }
    h2, h3 {
      break-after: avoid;
      color: #111820;
      line-height: 1.35;
    }
    h2 {
      margin: 15px 0 7px;
      padding: 5px 8px;
      border-left: 3px solid #235c67;
      background: #f4f8f8;
      font-size: 14.5px;
    }
    h3 {
      margin: 12px 0 6px;
      font-size: 12.8px;
    }
    p {
      margin: 5px 0;
      orphans: 3;
      widows: 3;
    }
    ul, ol {
      margin: 6px 0 8px 17px;
      padding: 0;
    }
    li {
      margin: 3px 0;
      padding-left: 2px;
      break-inside: avoid;
    }
    main > h2:first-child + ul {
      margin: 7px 0 11px;
      padding: 8px 11px 8px 24px;
      border: 1px solid #cfdfe3;
      border-left: 4px solid #235c67;
      border-radius: 6px;
      background: #f8fbfb;
      list-style-position: outside;
    }
    main > h2:first-child + ul li {
      font-weight: 650;
    }
    table {
      width: 100%;
      margin: 8px 0 10px;
      border-collapse: collapse;
      table-layout: fixed;
      break-inside: auto;
      page-break-inside: auto;
      font-size: 9.8px;
      line-height: 1.38;
    }
    thead { display: table-header-group; }
    tr {
      break-inside: avoid;
      page-break-inside: avoid;
    }
    th, td {
      padding: 5px 6px;
      border: 1px solid #dbe5e9;
      vertical-align: top;
      text-align: left;
      overflow-wrap: anywhere;
    }
    th {
      color: #2f414b;
      background: #edf4f5;
      font-weight: 800;
    }
    tbody tr:nth-child(even) td {
      background: #fbfdfd;
    }
    .cols-2 th:first-child,
    .cols-2 td:first-child { width: 32%; }
    .cols-3 th:first-child,
    .cols-3 td:first-child { width: 25%; }
    .cols-3 th:nth-child(2),
    .cols-3 td:nth-child(2) { width: 43%; }
    .cols-4 th:first-child,
    .cols-4 td:first-child { width: 20%; }
    .cols-4 th:nth-child(2),
    .cols-4 td:nth-child(2) { width: 39%; }
    .cols-4 th:nth-child(3),
    .cols-4 td:nth-child(3) { width: 17%; }
    blockquote {
      margin: 10px 0;
      padding: 8px 10px;
      border-left: 3px solid #235c67;
      color: #40515c;
      background: #f5f9fc;
    }
    code {
      padding: 1px 4px;
      border-radius: 4px;
      background: #eef2f5;
      font-family: Menlo, Consolas, monospace;
      font-size: 10px;
    }
    pre {
      white-space: pre-wrap;
      padding: 10px;
      border: 1px solid #d9e1e7;
      border-radius: 6px;
      background: #111a20;
      color: #d8e1e7;
    }
	    .citation { color: inherit; font-weight: inherit; }
    .watermark {
      position: fixed;
      top: 49%;
      left: 50%;
      width: 180mm;
      transform: translate(-50%, -50%) rotate(-27deg);
      color: rgba(35, 92, 103, 0.075);
      text-align: center;
      font-size: 44px;
      font-weight: 900;
      letter-spacing: 0;
      line-height: 1.15;
      white-space: nowrap;
      pointer-events: none;
      z-index: 0;
    }
    .watermark span {
      display: block;
      margin-top: 8px;
      font-size: 14px;
      font-weight: 800;
      letter-spacing: 0;
    }
    .memo-snapshot {
      position: relative;
      z-index: 1;
      margin: 12px 0 14px;
      break-inside: avoid;
    }
    .memo-label {
      margin-bottom: 8px;
      color: #235c67;
      font-size: 11px;
      font-weight: 850;
      letter-spacing: 0;
    }
    .metric-row {
      display: grid;
      grid-template-columns: 1.2fr 0.95fr 0.9fr 1.25fr;
      gap: 0;
      overflow: hidden;
      border: 1px solid #d6e2e6;
      border-top: 3px solid #235c67;
      border-radius: 7px 7px 0 0;
      background: rgba(248, 251, 251, 0.98);
    }
    .metric-cell {
      min-height: 48px;
      padding: 8px 10px;
      border-right: 1px solid #d6e2e6;
    }
    .metric-cell:last-child { border-right: 0; }
    .metric-label {
      margin-bottom: 3px;
      color: #697782;
      font-size: 9.2px;
      font-weight: 850;
      letter-spacing: 0;
    }
    .metric-value {
      color: #111820;
      font-size: 12px;
      font-weight: 850;
      line-height: 1.36;
      overflow-wrap: anywhere;
    }
    .brief-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0;
      overflow: hidden;
      border: 1px solid #d6e2e6;
      border-top: 0;
      border-radius: 0 0 7px 7px;
      background: rgba(255, 255, 255, 0.97);
    }
    .brief-cell {
      min-height: 64px;
      padding: 8px 10px;
      border-right: 1px solid #d6e2e6;
      border-bottom: 1px solid #d6e2e6;
      break-inside: avoid;
    }
    .brief-cell:nth-child(2n) { border-right: 0; }
    .brief-cell:nth-last-child(-n + 2) { border-bottom: 0; }
    .brief-label {
      margin-bottom: 4px;
      color: #235c67;
      font-size: 9.4px;
      font-weight: 850;
    }
    .brief-value {
      color: #52616b;
      font-size: 9.3px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .cover,
    .memo-snapshot,
    main,
    .footer-note {
      position: relative;
      z-index: 1;
    }
    .footer-note {
      margin-top: 16px;
      padding-top: 8px;
      border-top: 1px solid #e8eef2;
      color: #8a97a1;
      font-size: 9px;
    }
  </style>
</head>
<body>
  <div class="watermark">${escapeHtml(watermarkTitle)}<span>${escapeHtml(watermarkSubtitle)}</span></div>
  <section class="cover">
    <div class="eyebrow">DeepFocus 投资研究</div>
    <h1>${escapeHtml(title)}</h1>
  </section>
  <section class="memo-snapshot">
    <div class="memo-label">投资判断</div>
    <div class="metric-row">
      ${snapshot.metrics.map((item) => `
        <div class="metric-cell">
          <div class="metric-label">${escapeHtml(item.label)}</div>
          <div class="metric-value">${escapeHtml(item.value)}</div>
        </div>
      `).join("")}
    </div>
    <div class="brief-grid">
      ${snapshot.briefs.map((item) => `
        <div class="brief-cell">
          <div class="brief-label">${escapeHtml(item.label)}</div>
          <div class="brief-value">${escapeHtml(item.value)}</div>
        </div>
      `).join("")}
    </div>
  </section>
  <main>${markdownToHtml(content)}</main>
  <div class="footer-note">仅供研究讨论，不构成投资建议。关注公众号「赚DAO」获取更多机构研报拆解。</div>
</body>
</html>`;
}

async function exportSummaryPdf(payload) {
  const content = String(payload.content || "").trim();
  if (!content) throw new Error("缺少可导出的研报总结内容");
  const title = String(payload.title || "研报总结").trim() || "研报总结";
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromeExecutablePath(),
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1240, height: 1754 } });
    await page.setContent(summaryPdfHtml({ ...payload, title, content }), { waitUntil: "load" });
    const pdf = await page.pdf({
      format: "A4",
      printBackground: true,
      preferCSSPageSize: true,
      displayHeaderFooter: true,
      headerTemplate: "<div></div>",
      footerTemplate: "<div style=\"width:100%;font-size:8px;color:#8a97a1;padding:0 15mm;text-align:right;\"><span class=\"pageNumber\"></span> / <span class=\"totalPages\"></span></div>",
      margin: { top: "16mm", right: "15mm", bottom: "15mm", left: "15mm" },
    });
    const filename = safePdfFilename(title);
    const savedPath = await saveExportedSummaryPdf(pdf, filename);
    return {
      pdf,
      filename,
      savedPath,
    };
  } finally {
    await browser.close().catch(() => undefined);
  }
}

function normalizeTagName(value) {
  return String(value || "").replace(/^#+|#+$/g, "").trim();
}

function splitSearchValues(value) {
  if (Array.isArray(value)) {
    return value
      .flatMap((item) => splitSearchValues(item))
      .filter(Boolean);
  }
  return String(value || "")
    .split(/[,，;；、\n\r]+/)
    .map((item) => item.trim())
    .filter(Boolean);
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

async function listDefaultHashtags(auth, group) {
  const data = await apiGet(auth, `${API_BASE}/groups/${group}/hashtags/defaults`);
  return (data.hashtags || []).map((item) => ({
    hashtagId: String(item.hashtag_id),
    title: item.title || `#${item.hashtag_id}#`,
    topicsCount: item.topics_count || 0,
  }));
}

async function listSearchTags(payload) {
  const auth = await resolveAuth(payload);
  if (!auth.cookie) throw new Error("缺少 Cookie。请选择环境凭证、粘贴 curl、粘贴 Cookie，或先微信扫码登录。");
  const group = String(payload.group || "88888142214212").trim();
  return {
    group,
    tags: await listDefaultHashtags(auth, group),
  };
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

function normalizeSearchResultLimit(value, fallback = 200) {
  if (value === 0 || value === "0" || value === null) return 0;
  if (value === undefined || value === "") return fallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return fallback;
  if (numeric === 0) return 0;
  return Math.max(1, Math.min(1000, numeric));
}

function normalizeSearchPageLimit(value, fallback) {
  if (value === 0 || value === "0" || value === null) return Number.POSITIVE_INFINITY;
  if (value === undefined || value === "") return fallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return fallback;
  if (numeric === 0) return Number.POSITIVE_INFINITY;
  return Math.max(1, Math.floor(numeric));
}

function hasReachedResultLimit(items, resultLimit) {
  return resultLimit > 0 && items.length >= resultLimit;
}

function searchItemTime(item) {
  const value = item.createTime || item.topicCreateTime || "";
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function sortSearchItemsByTime(items) {
  items.sort((a, b) => (
    searchItemTime(b) - searchItemTime(a) ||
    String(b.createTime || b.topicCreateTime || "").localeCompare(String(a.createTime || a.topicCreateTime || "")) ||
    Number(b.score || 0) - Number(a.score || 0)
  ));
  return items;
}

async function searchTagFiles(auth, payload, hashtag) {
  const keyword = String(payload.keyword || "").trim();
  const ext = String(payload.ext || "").trim().toLowerCase().replace(/^\./, "");
  const pageLimit = normalizeSearchPageLimit(payload.searchPages ?? payload.maxPages, DEFAULT_SEARCH_PAGES);
  const resultLimit = normalizeSearchResultLimit(payload.resultLimit);
  const count = 20;
  const seen = new Set();
  const items = [];
  let endTime = "";
  let scannedTopics = 0;

  for (let page = 1; page <= pageLimit; page += 1) {
    const previousEndTime = endTime;
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
    if (!topics.length || topics.length < count || hasReachedResultLimit(items, resultLimit)) break;
    if (endTime && endTime === previousEndTime) break;
  }
  sortSearchItemsByTime(items);
  return { items: resultLimit > 0 ? items.slice(0, resultLimit) : items, scannedTopics, hashtag };
}

async function searchGroupFiles(auth, payload) {
  const group = String(payload.group || "88888142214212").trim();
  const keyword = String(payload.keyword || "").trim();
  const ext = String(payload.ext || "").trim().toLowerCase().replace(/^\./, "");
  const resultLimit = normalizeSearchResultLimit(payload.resultLimit);
  const pageLimit = normalizeSearchPageLimit(payload.searchPages ?? payload.maxPages, DEFAULT_SEARCH_PAGES);
  const seen = new Set();
  const items = [];
  let index = "";
  let endTime = "";

  for (let page = 1; page <= pageLimit; page += 1) {
    const previousIndex = index;
    const previousEndTime = endTime;
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
    if (!files.length || files.length < 20 || hasReachedResultLimit(items, resultLimit)) break;
    if (keyword && (!index || index === previousIndex)) break;
    if (!keyword && endTime && endTime === previousEndTime) break;
  }
  sortSearchItemsByTime(items);
  return { items: resultLimit > 0 ? items.slice(0, resultLimit) : items, scannedTopics: 0, hashtag: null };
}

async function searchFiles(payload) {
  const auth = await resolveAuth(payload);
  if (!auth.cookie) throw new Error("缺少 Cookie。请选择环境凭证、粘贴 curl、粘贴 Cookie，或先微信扫码登录。");
  const group = String(payload.group || "88888142214212").trim();
  const tags = splitSearchValues(payload.tags ?? payload.tag);
  const hashtagIds = splitSearchValues(payload.hashtagIds ?? payload.hashtagId);
  const hashtags = [];
  const maxLength = Math.max(tags.length, hashtagIds.length);
  for (let index = 0; index < maxLength; index += 1) {
    const hashtag = await resolveHashtag(auth, group, tags[index] || "", hashtagIds[index] || "");
    if (hashtag && !hashtags.some((item) => item.hashtagId === hashtag.hashtagId)) {
      hashtags.push(hashtag);
    }
  }
  const result = hashtags.length
    ? mergeSearchResults(await Promise.all(hashtags.map((hashtag) => searchTagFiles(auth, payload, hashtag))))
    : await searchGroupFiles(auth, payload);
  return {
    ...result,
    hashtags: hashtags.length ? hashtags : result.hashtag ? [result.hashtag] : [],
    keyword: String(payload.keyword || "").trim(),
    count: result.items.length,
  };
}

function mergeSearchResults(results) {
  const merged = new Map();
  let scannedTopics = 0;
  for (const result of results) {
    scannedTopics += Number(result.scannedTopics || 0);
    for (const item of result.items || []) {
      const key = item.fileId || `${item.name}-${item.topicId}`;
      const previous = merged.get(key);
      if (!previous) {
        merged.set(key, { ...item });
        continue;
      }
      const tags = splitSearchValues([previous.hashtag, item.hashtag]);
      merged.set(key, {
        ...previous,
        ...item,
        score: Math.max(Number(previous.score || 0), Number(item.score || 0)),
        hashtag: Array.from(new Set(tags)).join("、"),
      });
    }
  }
  const items = Array.from(merged.values());
  sortSearchItemsByTime(items);
  return {
    items,
    scannedTopics,
    hashtag: results.length === 1 ? results[0].hashtag : null,
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

function defaultProviderConfig() {
  const provider = String(
    process.env.DEEPFOCUS_LLM_PROVIDER
    || process.env.FINGPT_LLM_PROVIDER
    || "openai"
  ).toLowerCase();
  if (provider === "minimax") {
    return {
      provider,
      model: process.env.MINIMAX_MODEL || "MiniMax-M2.7",
      base_url: process.env.MINIMAX_BASE_URL || "https://api.minimax.io/v1",
      api_key: process.env.MINIMAX_API_KEY || "",
      temperature: Number(process.env.DEEPFOCUS_LLM_TEMPERATURE || 0.2),
    };
  }
  if (["openai", "openai-compatible", "cloud"].includes(provider)) {
    return {
      provider,
      model: process.env.OPENAI_MODEL || "",
      base_url: process.env.OPENAI_BASE_URL || "https://api.openai.com/v1",
      api_key: process.env.OPENAI_API_KEY || "",
      temperature: Number(process.env.DEEPFOCUS_LLM_TEMPERATURE || 0.2),
    };
  }
  return {
    provider: "mock",
    model: "mock-research-analyst",
    base_url: "",
    api_key: "",
    temperature: 0.2,
  };
}

function readSharedModelConfigSync() {
  const config = defaultProviderConfig();
  if (!fssync.existsSync(SHARED_MODEL_CONFIG_PATH)) return normalizeSharedModelConfig(config);
  try {
    const saved = JSON.parse(fssync.readFileSync(SHARED_MODEL_CONFIG_PATH, "utf8"));
    return normalizeSharedModelConfig({ ...config, ...Object.fromEntries(
      Object.entries(saved || {}).filter(([, value]) => value !== null && value !== undefined)
    ) });
  } catch {
    return normalizeSharedModelConfig(config);
  }
}

function normalizeSharedModelConfig(config) {
  const provider = ["mock", "openai", "minimax", "openai-compatible", "cloud"].includes(String(config.provider || "").toLowerCase())
    ? String(config.provider).toLowerCase()
    : "mock";
  const baseUrl = config.base_url || defaultBaseUrlForProvider(provider);
  const model = config.model || defaultModelForProvider(provider);
  const temperature = Number.isFinite(Number(config.temperature)) ? Number(config.temperature) : 0.2;
  return {
    provider,
    model,
    base_url: baseUrl,
    api_key: config.api_key || "",
    temperature: Math.max(0, Math.min(1, temperature)),
  };
}

function defaultModelForProvider(provider) {
  if (provider === "minimax") return "MiniMax-M2.7";
  if (["openai", "openai-compatible", "cloud"].includes(provider)) return "gpt-4o-mini";
  return "mock-research-analyst";
}

function defaultBaseUrlForProvider(provider) {
  if (provider === "minimax") return "https://api.minimax.io/v1";
  if (provider === "mock") return "";
  return process.env.OPENAI_BASE_URL || "https://api.openai.com/v1";
}

function inferProviderFromBaseUrl(baseUrl) {
  const normalized = String(baseUrl || "").toLowerCase();
  if (normalized.includes("minimax")) return "minimax";
  if (!normalized || normalized.includes("api.openai.com")) return "openai";
  return "openai-compatible";
}

function maskKey(apiKey) {
  if (!apiKey) return null;
  if (apiKey.length <= 8) return "*".repeat(apiKey.length);
  return `${apiKey.slice(0, 4)}...${apiKey.slice(-4)}`;
}

function modelDefaults() {
  const shared = readSharedModelConfigSync();
  const baseUrl = shared.base_url || defaultBaseUrlForProvider(shared.provider);
  return {
    provider: shared.provider,
    baseUrl,
    model: shared.model,
    hasApiKey: Boolean(shared.api_key),
    apiKeyPreview: maskKey(shared.api_key),
    temperature: shared.temperature,
    maxTokens: 4096,
    imagePages: "auto",
    compat: "auto",
    thinking: "disabled",
    extraBody: "",
    configSource: SHARED_MODEL_CONFIG_PATH,
  };
}

async function saveSharedModelConfig(payload = {}) {
  const current = readSharedModelConfigSync();
  const raw = payload.modelConfig || payload;
  const baseUrl = String(raw.baseUrl ?? raw.base_url ?? current.base_url ?? "").trim().replace(/\/+$/, "");
  const provider = String(raw.provider || inferProviderFromBaseUrl(baseUrl)).toLowerCase();
  const model = String(raw.model || current.model || defaultModelForProvider(provider)).trim();
  const apiKeyProvided = raw.apiKey !== undefined || raw.api_key !== undefined;
  const apiKeyInput = raw.apiKey ?? raw.api_key;
  const apiKey = apiKeyProvided && String(apiKeyInput || "").trim()
    ? String(apiKeyInput).trim()
    : current.api_key;
  const temperature = Number.isFinite(Number(raw.temperature)) ? Number(raw.temperature) : current.temperature;
  const nextConfig = normalizeSharedModelConfig({
    provider,
    model,
    base_url: baseUrl || defaultBaseUrlForProvider(provider),
    api_key: apiKey,
    temperature,
  });
  await fs.mkdir(path.dirname(SHARED_MODEL_CONFIG_PATH), { recursive: true });
  await fs.writeFile(SHARED_MODEL_CONFIG_PATH, `${JSON.stringify({
    provider: nextConfig.provider,
    model: nextConfig.model,
    base_url: nextConfig.base_url || null,
    api_key: nextConfig.api_key || null,
    temperature: nextConfig.temperature,
  }, null, 2)}\n`, "utf8");
  return modelDefaults();
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

function parsePositiveInteger(value, fallback, { min = 1, max = 100_000 } = {}) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return fallback;
  return Math.max(min, Math.min(max, Math.floor(number)));
}

function resolveModelConfig(payload = {}) {
  const defaults = modelDefaults();
  const shared = readSharedModelConfigSync();
  const config = payload.modelConfig || payload;
  const extraBody = parseExtraBody(config.extraBody);
  const baseUrl = String(config.baseUrl || defaults.baseUrl).trim().replace(/\/+$/, "");
  const apiKey = String(config.apiKey || shared.api_key || process.env.OPENAI_API_KEY || "").trim();
  const model = String(config.model || defaults.model).trim();
  const temperature = Number.isFinite(Number(config.temperature)) ? Number(config.temperature) : defaults.temperature;
  const rawMaxTokens = Number.isFinite(Number(config.maxTokens)) ? Number(config.maxTokens) : defaults.maxTokens;
  const imagePages = config.imagePages ?? config.visionPages ?? extraBody.imagePages ?? extraBody.visionPages ?? defaults.imagePages;
  const textPages = config.textPages ?? extraBody.textPages ?? 12;
  const textChars = config.textChars ?? extraBody.textChars ?? 18_000;
  const compat = String(config.compat || defaults.compat || "auto").trim();
  const thinking = String(config.thinking || defaults.thinking || "default").trim();
  delete extraBody.imagePages;
  delete extraBody.visionPages;
  delete extraBody.textPages;
  delete extraBody.textChars;

  if (!baseUrl) throw new Error("缺少模型 Base URL");
  if (!model) throw new Error("缺少模型名称");

  return {
    baseUrl,
    apiKey,
    model,
    temperature: Math.max(0, Math.min(2, temperature)),
    maxTokens: Math.max(256, Math.min(12000, Math.floor(rawMaxTokens))),
    imagePageLimit: parseImagePageLimit(imagePages),
    textPageLimit: parsePositiveInteger(textPages, 12, { min: 1, max: 60 }),
    textCharLimit: parsePositiveInteger(textChars, 18_000, { min: 4_000, max: 80_000 }),
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
    const textPageLimit = options.textPageLimit || 12;
    const textCharLimit = options.textCharLimit || 18_000;
    const textResult = await parser.getText({ first: 1, last: textPageLimit });
    const text = cleanPdfText(textResult.text);
    if (text.length >= 500) {
      return {
        mode: "text",
        text: truncateText(text, textCharLimit),
        chars: Math.min(text.length, textCharLimit),
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
  const textPageLimit = options.textPageLimit || 12;
  const textCharLimit = options.textCharLimit || 18_000;
  const stat = await fs.stat(filePath);
  if (!stat.isFile()) throw new Error("不是可解读的文件");
  const cacheKey = `${filePath}:${stat.mtimeMs}:${stat.size}:${pageLimit}:${textPageLimit}:${textCharLimit}`;
  if (extractionCache.has(cacheKey)) return extractionCache.get(cacheKey);
  const ext = path.extname(filePath).toLowerCase();
  let result;
  if (ext === ".pdf") {
    result = await extractPdfContent(filePath, options);
  } else if (ext === ".docx") {
    const doc = await mammoth.extractRawText({ path: filePath });
    const text = doc.value.trim();
    if (!text) throw new Error("DOCX 没有抽取到文本");
    result = { mode: "text", text: truncateText(text, textCharLimit), chars: Math.min(text.length, textCharLimit), pagesUsed: 0, totalPages: 0 };
  } else if ([".txt", ".md", ".csv", ".json", ".log", ".html", ".htm"].includes(ext)) {
    const text = await fs.readFile(filePath, "utf8");
    result = { mode: "text", text: truncateText(text, textCharLimit), chars: Math.min(text.length, textCharLimit), pagesUsed: 0, totalPages: 0 };
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
    "先为用户生成可读的解析版，不要把页码、Exhibit 或章节位置塞进正文；如果看不清，请明确说明不确定。",
    STRUCTURED_OUTPUT_RULES,
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
  const extracted = await extractFileContent(filePath, {
    imagePageLimit: config.imagePageLimit,
    textPageLimit: config.textPageLimit,
    textCharLimit: config.textCharLimit,
  });
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
    STRUCTURED_OUTPUT_RULES,
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
      const extracted = await extractFileContent(filePath, {
        imagePageLimit: config.imagePageLimit,
        textPageLimit: config.textPageLimit,
        textCharLimit: config.textCharLimit,
      });
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
    const browserAuth = await readBrowserAuth();
    const credentialsAvailable = Boolean(process.env.ZSXQ_COOKIE || browserAuth.cookie);
    send(res, 200, {
      credentialsAvailable,
      envCredentialsAvailable: Boolean(process.env.ZSXQ_COOKIE),
      browserAuthAvailable: Boolean(browserAuth.cookie),
      browserAuth: authPreview(browserAuth),
      aduidAvailable: Boolean(process.env.ZSXQ_ADUID || browserAuth.aduid),
      jobs: Array.from(jobs.values()).map(publicJob).reverse(),
      defaults: {
        group: "88888142214212",
        tag: "海外投行报告",
        out: "downloads/海外投行报告",
        ext: "pdf",
        limit: 20,
        maxPages: DEFAULT_MAX_PAGES,
      },
    });
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/model-config") {
    send(res, 200, modelDefaults());
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/model-config") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await saveSharedModelConfig(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
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

  if (req.method === "POST" && url.pathname === "/api/tags") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await listSearchTags(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/preview") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await createPreviewLink(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/export-summary-pdf") {
    try {
      const payload = await parseJson(req);
      const result = await exportSummaryPdf(payload);
      res.writeHead(200, corsHeaders({
        "content-type": "application/pdf",
        "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(result.filename)}`,
        "x-saved-path": encodeURIComponent(result.savedPath),
        "content-length": result.pdf.length,
        "cache-control": "no-store",
      }));
      res.end(result.pdf);
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/open-path") {
    try {
      const payload = await parseJson(req);
      send(res, 200, await openWorkbenchPath(payload));
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/browser-login") {
    try {
      const payload = await parseJson(req);
      const auth = await browserLoginAuth(payload);
      send(res, 200, {
        ok: true,
        message: "微信扫码登录态已保存到本机。",
        auth: authPreview(auth),
      });
    } catch (error) {
      send(res, 400, { error: error.message });
    }
    return;
  }

  const previewMatch = url.pathname.match(/^\/api\/previews\/([^/]+)$/);
  if (req.method === "GET" && previewMatch) {
    try {
      await streamPreview(previewMatch[1], res, url.searchParams.get("download") === "1");
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
