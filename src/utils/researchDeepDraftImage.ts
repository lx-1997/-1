/**
 * Export helpers for the publication-style research draft.
 *
 * The terminal keeps two representations of a report: `aiDeepDraft` is the
 * article users are reading, while `aiResult` is a deliberately small legacy
 * projection used by the old quick card.  Image/text sharing must use the
 * former, otherwise a deep article silently turns back into a four-bullet
 * card.  This module is intentionally React-free so it can be tested as a
 * pure contract and reused by desktop/mobile hosts.
 */

type Dict = Record<string, unknown>;

export interface ResearchDeepDraftImageEvidence {
  label?: string;
  page?: string;
  excerpt?: string;
}

export interface ResearchDeepDraftImageLogicLine {
  title?: string;
  evidence?: string;
  chain?: string;
  impact?: string;
  watch?: string;
}

export interface ResearchDeepDraftImageTable {
  title?: string;
  columns: string[];
  rows: string[][];
  note?: string;
}

export interface ResearchDeepDraftImageSection {
  title: string;
  summary?: string;
  paragraphs: string[];
  bullets: string[];
  logicLines: ResearchDeepDraftImageLogicLine[];
  tables: ResearchDeepDraftImageTable[];
  evidence: ResearchDeepDraftImageEvidence[];
}

export interface ResearchDeepDraftImageWatchItem {
  title: string;
  signal?: string;
  window?: string;
  metric?: string;
  trigger?: string;
  why?: string;
  evidence: ResearchDeepDraftImageEvidence[];
}

export interface ResearchDeepDraftImageRisk {
  title: string;
  detail?: string;
  trigger?: string;
  evidence: ResearchDeepDraftImageEvidence[];
}

export interface ResearchDeepDraftImageSource {
  title: string;
  provider?: string;
  page?: string;
  kind?: string;
}

export interface ResearchDeepDraftImageMetric {
  label: string;
  value?: string;
  change?: string;
  context?: string;
}

export interface ResearchDeepDraftImageModel {
  title: string;
  subtitle?: string;
  metadata: string[];
  oneLiner?: string;
  plainLanguageSummary?: string;
  coreConclusion?: string;
  executiveSummary?: string;
  thesis?: string;
  decisionImplication?: string;
  metrics: ResearchDeepDraftImageMetric[];
  logicLines: ResearchDeepDraftImageLogicLine[];
  sections: ResearchDeepDraftImageSection[];
  tables: ResearchDeepDraftImageTable[];
  watchlist: ResearchDeepDraftImageWatchItem[];
  risks: ResearchDeepDraftImageRisk[];
  sources: ResearchDeepDraftImageSource[];
  disclaimer: string;
}

export interface ResearchDeepDraftQr {
  size: number;
  matrix: number[][];
}

export interface ResearchDeepDraftImageOptions {
  /** Override the report title shown on the card. */
  title?: string;
  date?: string;
  site?: string;
  qr?: ResearchDeepDraftQr | null;
  /** Logical CSS pixels before the 2x export scale. */
  width?: number;
  scale?: number;
}

const MAX_SECTIONS = 16;
const MAX_PARAGRAPHS = 14;
const MAX_BULLETS = 16;
const MAX_LOGIC = 16;
const MAX_TABLES = 8;
const MAX_ROWS = 28;
const MAX_COLUMNS = 10;
const MAX_WATCH = 16;
const MAX_RISKS = 16;
const MAX_SOURCES = 20;
const MAX_TEXT = 1_800;
const MAX_EXCERPT = 120;

const asDict = (value: unknown): Dict => (
  value && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {}
);

const text = (value: unknown, limit = MAX_TEXT): string => {
  if (value === undefined || value === null) return '';
  const out = typeof value === 'string' ? value : (typeof value === 'number' || typeof value === 'boolean' ? String(value) : '');
  return out.trim().slice(0, limit);
};

const first = (record: Dict, keys: string[]): unknown => {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
};

const list = (value: unknown, limit: number, itemLimit = MAX_TEXT): string[] => {
  const values = Array.isArray(value) ? value : (value === undefined || value === null ? [] : [value]);
  return values.slice(0, limit).flatMap(item => {
    if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') {
      const v = text(item, itemLimit);
      return v ? [v] : [];
    }
    const record = asDict(item);
    const v = text(first(record, ['text', 'content', 'body', 'detail', 'value', 'label', 'title']), itemLimit);
    return v ? [v] : [];
  });
};

const paragraphs = (value: unknown): string[] => {
  if (Array.isArray(value)) return list(value, MAX_PARAGRAPHS, MAX_TEXT);
  const v = text(value, MAX_PARAGRAPHS * MAX_TEXT);
  return v ? v.split(/\n\s*\n|\r?\n/).map(item => item.trim()).filter(Boolean).slice(0, MAX_PARAGRAPHS).map(item => item.slice(0, MAX_TEXT)) : [];
};

const pageLabel = (record: Dict): string => {
  const raw = first(record, ['page', 'pages', 'page_range', 'source_page', 'source_pages']);
  if (Array.isArray(raw)) {
    const pages = raw.map(item => text(item, 20)).filter(Boolean);
    return pages.length ? `第 ${pages.join('、')} 页` : '';
  }
  const value = text(raw, 40);
  if (!value) return '';
  return /^第/.test(value) || !/^\d/.test(value) ? value : `第 ${value} 页`;
};

const evidence = (value: unknown): ResearchDeepDraftImageEvidence[] => {
  const values = Array.isArray(value) ? value : (value ? [value] : []);
  return values.slice(0, 12).flatMap(item => {
    const record = typeof item === 'string' ? { excerpt: item } : asDict(item);
    const label = text(first(record, ['label', 'title', 'source', 'report_title']), 160);
    const excerpt = text(first(record, ['excerpt', 'quote', 'text', 'source_excerpt', 'evidence']), MAX_EXCERPT);
    const page = pageLabel(record);
    return label || excerpt || page ? [{ label: label || undefined, page: page || undefined, excerpt: excerpt || undefined }] : [];
  });
};

const logicLines = (value: unknown): ResearchDeepDraftImageLogicLine[] => {
  const values = Array.isArray(value) ? value : (value ? [value] : []);
  return values.slice(0, MAX_LOGIC).flatMap(item => {
    const record = typeof item === 'string' ? { title: item } : asDict(item);
    const line = {
      title: text(first(record, ['title', 'name', 'point'])),
      evidence: text(first(record, ['evidence', 'fact', 'facts'])),
      chain: text(first(record, ['chain', 'transmission', 'mechanism'])),
      impact: text(first(record, ['impact', 'conclusion', 'implication'])),
      watch: text(first(record, ['watch', 'validation', 'verify', 'trigger']))
    };
    return Object.values(line).some(Boolean) ? [line] : [];
  });
};

const tables = (value: unknown): ResearchDeepDraftImageTable[] => {
  const values = Array.isArray(value) ? value : (value ? [value] : []);
  return values.slice(0, MAX_TABLES).flatMap(item => {
    const record = asDict(item);
    const rawColumns = first(record, ['columns', 'headers', 'fields']);
    const columns = (Array.isArray(rawColumns) ? rawColumns : []).slice(0, MAX_COLUMNS).map(column => {
      if (typeof column === 'string' || typeof column === 'number') return String(column).trim().slice(0, 120);
      const c = asDict(column);
      return text(first(c, ['label', 'title', 'name', 'key']), 120);
    }).filter(Boolean);
    const rawRows = Array.isArray(record.rows) ? record.rows : (Array.isArray(record.data) ? record.data : []);
    const rows = rawRows.slice(0, MAX_ROWS).flatMap(row => {
      if (Array.isArray(row)) return [row.slice(0, MAX_COLUMNS).map(cell => text(cell, 240))];
      const r = asDict(row);
      if (!columns.length) return [Object.values(r).slice(0, MAX_COLUMNS).map(cell => text(cell, 240))];
      return [columns.map(column => text(r[column], 240))];
    }).filter(row => row.some(Boolean));
    if (!columns.length && !rows.length) return [];
    return [{ title: text(first(record, ['title', 'name', 'caption']), 180) || undefined, columns, rows, note: text(first(record, ['note', 'footnote', 'source_note']), 400) || undefined }];
  });
};

const normalizeSection = (value: unknown, index: number): ResearchDeepDraftImageSection => {
  const record = asDict(value);
  const rawContent = first(record, ['paragraphs', 'content', 'body', 'text']);
  return {
    title: text(first(record, ['title', 'heading', 'name']), 180) || `第 ${index + 1} 节`,
    summary: text(first(record, ['summary', 'abstract', 'dek']), 900) || undefined,
    paragraphs: paragraphs(rawContent),
    bullets: list(first(record, ['bullets', 'key_points', 'points']), MAX_BULLETS, 700),
    logicLines: logicLines(first(record, ['logic_lines', 'logicLines', 'logic'])),
    tables: tables(first(record, ['tables', 'table'])),
    evidence: evidence(first(record, ['evidence', 'citations', 'sources']))
  };
};

const normalizeWatch = (value: unknown, index: number): ResearchDeepDraftImageWatchItem => {
  const record = typeof value === 'string' ? { title: value } : asDict(value);
  return {
    title: text(first(record, ['title', 'name', 'item', 'point']), 180) || `跟踪事项 ${index + 1}`,
    signal: text(first(record, ['signal', 'direction', 'stance']), 240) || undefined,
    window: text(first(record, ['window', 'time_window', 'period', 'horizon']), 180) || undefined,
    metric: text(first(record, ['metric', 'indicator', 'measure']), 240) || undefined,
    trigger: text(first(record, ['trigger', 'condition', 'invalidation']), 700) || undefined,
    why: text(first(record, ['why', 'rationale', 'reason']), 700) || undefined,
    evidence: evidence(first(record, ['evidence', 'citations', 'sources']))
  };
};

const normalizeRisk = (value: unknown): ResearchDeepDraftImageRisk => {
  const record = typeof value === 'string' || typeof value === 'number' ? { title: String(value) } : asDict(value);
  return {
    title: text(first(record, ['title', 'name', 'risk']), 180) || '风险提示',
    detail: text(first(record, ['detail', 'description', 'text', 'content']), 900) || undefined,
    trigger: text(first(record, ['trigger', 'condition', 'invalidation']), 700) || undefined,
    evidence: evidence(first(record, ['evidence', 'citations', 'sources']))
  };
};

const normalizeSource = (value: unknown, index: number): ResearchDeepDraftImageSource => {
  const record = typeof value === 'string' ? { title: value } : asDict(value);
  return {
    title: text(first(record, ['title', 'report_title', 'name', 'label']), 220) || `来源 ${index + 1}`,
    provider: text(first(record, ['provider', 'institution', 'author']), 160) || undefined,
    page: pageLabel(record) || undefined,
    kind: text(first(record, ['kind', 'type']), 80) || undefined
  };
};

const normalizeMetrics = (value: unknown): ResearchDeepDraftImageMetric[] => {
  const values = Array.isArray(value) ? value : (value ? [value] : []);
  const seen = new Set<string>();
  return values.slice(0, 6).flatMap((item, index) => {
    const record = typeof item === 'string' || typeof item === 'number' ? { label: String(item) } : asDict(item);
    const label = text(first(record, ['label', 'title', 'name', 'metric_label', 'metric_key']), 160) || `关键指标 ${index + 1}`;
    const key = label.replace(/\s+/g, '').toLowerCase();
    if (seen.has(key)) return [];
    seen.add(key);
    return [{
      label,
      value: text(first(record, ['value', 'raw_value', 'normalized_value', 'number']), 120) || undefined,
      change: text(first(record, ['change', 'delta', 'comparison', 'change_text']), 120) || undefined,
      context: text(first(record, ['context', 'description', 'note', 'meaning']), 260) || undefined
    }];
  });
};

/** Convert the API response to a bounded, deterministic export model. */
export function buildResearchDeepDraftImageModel(input: unknown, options: ResearchDeepDraftImageOptions = {}): ResearchDeepDraftImageModel {
  const record = asDict(input);
  const nested = asDict(record.report);
  const pick = (keys: string[]) => first(record, keys) ?? first(nested, keys);
  const rawSections = pick(['sections', 'chapters', 'outline']);
  const sections = (Array.isArray(rawSections) ? rawSections : []).slice(0, MAX_SECTIONS).map(normalizeSection);
  const rawTables = pick(['tables', 'table']);
  const rawWatch = pick(['watchlist', 'watch_items', 'tracking', 'follow_up']);
  const rawRisks = pick(['risks', 'risk_flags']);
  const rawSources = pick(['sources', 'source_refs', 'citations']);
  const rawLogic = pick(['logic_lines', 'logicLines', 'logic']);
  const rawMetrics = pick(['metrics', 'metric_cards', 'key_metrics']);
  const subject = text(pick(['subject']), 120);
  const symbol = text(pick(['symbol']), 80);
  const sourceCount = text(pick(['source_count', 'sources_count']), 30);
  const pages = text(pick(['pages_analyzed']), 30);
  const readTime = text(pick(['read_time_minutes', 'read_time', 'reading_time']), 30);
  const coverage = asDict(pick(['source_coverage', 'coverage']));
  const coveredPages = text(first(coverage, ['pages_read', 'covered', 'included']), 30);
  const totalPages = text(first(coverage, ['total_pages', 'total', 'available']), 30);
  const coverageLabel = coveredPages && totalPages ? `证据覆盖 ${coveredPages}/${totalPages}` : '';
  const metadata = [subject, symbol, sourceCount ? `${sourceCount} 个来源` : '', pages ? `分析 ${pages} 页` : '', coverageLabel, readTime ? `约 ${readTime} 分钟阅读` : '', text(options.date || pick(['generated_at', 'generatedAt', 'created_at']), 80)].filter(Boolean);
  const model: ResearchDeepDraftImageModel = {
    title: text(options.title || pick(['title', 'headline', 'name']), 260) || '研报深度解读',
    subtitle: text(pick(['subtitle', 'dek', 'sub_title']), 600) || undefined,
    metadata,
    oneLiner: text(pick(['one_liner', 'oneLiner', 'headline']), 900) || undefined,
    plainLanguageSummary: text(pick(['plain_language_summary', 'reader_summary', 'simple_summary', 'beginner_summary']), 1_200) || undefined,
    coreConclusion: text(pick(['core_conclusion', 'core_logic', 'takeaway']), 1_600) || undefined,
    executiveSummary: text(pick(['executive_summary', 'summary', 'abstract']), 2_400) || undefined,
    thesis: text(pick(['thesis']), 1_600) || undefined,
    decisionImplication: text(pick(['decision_implication', 'research_implication', 'so_what']), 1_200) || undefined,
    metrics: normalizeMetrics(rawMetrics),
    logicLines: logicLines(rawLogic),
    sections,
    tables: tables(rawTables),
    watchlist: (Array.isArray(rawWatch) ? rawWatch : []).slice(0, MAX_WATCH).map(normalizeWatch),
    risks: (Array.isArray(rawRisks) ? rawRisks : []).slice(0, MAX_RISKS).map(normalizeRisk),
    sources: (Array.isArray(rawSources) ? rawSources : []).slice(0, MAX_SOURCES).map(normalizeSource),
    disclaimer: text(pick(['disclaimer', 'notice']), 500) || 'AI 生成内容仅供研究参考，不构成投资建议。请回到原始材料核验关键数字与引用。'
  };

  // Some compatible responses put all prose in `body`/`content` instead of
  // sections. Keep that content in the export rather than producing a title-
  // only image.
  if (!model.sections.length) {
    const body = paragraphs(pick(['body', 'content', 'article']));
    if (body.length) model.sections.push({ title: '核心分析', paragraphs: body, summary: undefined, bullets: [], logicLines: [], tables: [], evidence: [] });
  }
  return model;
}

const cleanForText = (value: string): string => value.replace(/\s+$/g, '').trim();

const evidenceText = (items: ResearchDeepDraftImageEvidence[]): string[] => items.map(item => {
  const head = [item.label, item.page].filter(Boolean).join(' · ');
  return [head, item.excerpt].filter(Boolean).join('：');
}).filter(Boolean);

/** Plain-text counterpart used by the “复制为文字” action. */
export function researchDeepDraftToText(input: unknown, options: ResearchDeepDraftImageOptions = {}): string {
  const model = buildResearchDeepDraftImageModel(input, options);
  const lines: string[] = [`📑 DeepFocus AI 深度研究稿｜${model.title}`];
  if (model.subtitle) lines.push(model.subtitle);
  if (model.metadata.length) lines.push(model.metadata.join(' · '));
  if (model.oneLiner) lines.push('', `💡 ${model.oneLiner}`);
  if (model.plainLanguageSummary) lines.push('', '【白话解释】', model.plainLanguageSummary);
  if (model.coreConclusion) lines.push('', '【核心结论】', model.coreConclusion);
  if (model.thesis) lines.push('', '【主线】', model.thesis);
  if (model.decisionImplication) lines.push('', '【研究含义】', model.decisionImplication);
  if (model.metrics.length) {
    lines.push('', '【关键数字】');
    model.metrics.forEach((metric) => {
      lines.push(`${metric.label}：${metric.value || '—'}${metric.change ? `（${metric.change}）` : ''}${metric.context ? `；${metric.context}` : ''}`);
    });
  }
  if (model.executiveSummary && model.executiveSummary !== model.coreConclusion) lines.push('', '【摘要】', model.executiveSummary);
  const appendLogic = (items: ResearchDeepDraftImageLogicLine[]) => {
    if (!items.length) return;
    lines.push('', '【关键逻辑线】');
    items.forEach((item, index) => {
      lines.push(`${index + 1}. ${item.title || `逻辑线 ${index + 1}`}`);
      if (item.evidence) lines.push(`事实：${item.evidence}`);
      if (item.chain) lines.push(`传导：${item.chain}`);
      if (item.impact) lines.push(`影响：${item.impact}`);
      if (item.watch) lines.push(`验证：${item.watch}`);
    });
  };
  appendLogic(model.logicLines);
  model.sections.forEach((section, index) => {
    lines.push('', `【${String(index + 1).padStart(2, '0')} ${section.title}】`);
    if (section.summary) lines.push(section.summary);
    section.paragraphs.forEach(paragraph => lines.push(paragraph));
    if (section.bullets.length) lines.push(...section.bullets.map(item => `· ${item}`));
    appendLogic(section.logicLines);
    section.tables.forEach(table => {
      if (table.title) lines.push('', `表：${table.title}`);
      if (table.columns.length) lines.push(table.columns.join(' | '));
      table.rows.forEach(row => lines.push(row.join(' | ')));
      if (table.note) lines.push(`注：${table.note}`);
    });
    const refs = evidenceText(section.evidence);
    if (refs.length) lines.push('证据：', ...refs.map(item => `- ${item}`));
  });
  if (model.tables.length) {
    lines.push('', '【数据与对比】');
    model.tables.forEach(table => {
      if (table.title) lines.push(`表：${table.title}`);
      if (table.columns.length) lines.push(table.columns.join(' | '));
      table.rows.forEach(row => lines.push(row.join(' | ')));
      if (table.note) lines.push(`注：${table.note}`);
    });
  }
  if (model.watchlist.length) {
    lines.push('', '【未来跟踪清单】');
    model.watchlist.forEach((item, index) => {
      lines.push(`${index + 1}. ${item.title}`);
      [item.signal && `信号：${item.signal}`, item.window && `窗口：${item.window}`, item.metric && `指标：${item.metric}`, item.trigger && `触发/失效：${item.trigger}`, item.why && `为什么：${item.why}`].filter(Boolean).forEach(value => lines.push(value as string));
      lines.push(...evidenceText(item.evidence).map(value => `证据：${value}`));
    });
  }
  if (model.risks.length) {
    lines.push('', '【风险与反方】');
    model.risks.forEach(item => {
      lines.push(`· ${item.title}`);
      if (item.detail) lines.push(item.detail);
      if (item.trigger) lines.push(`反证条件：${item.trigger}`);
      lines.push(...evidenceText(item.evidence).map(value => `证据：${value}`));
    });
  }
  if (model.sources.length) {
    lines.push('', '【来源与证据范围】');
    model.sources.forEach(item => lines.push(`- ${[item.title, item.provider, item.page, item.kind].filter(Boolean).join(' · ')}`));
  }
  lines.push('', '——————————', model.disclaimer, `—— DeepFocus 金融终端 · ${options.site || 'daocaijing.com'}`);
  return lines.map(cleanForText).filter(Boolean).join('\n');
}

type DrawBlock = { lines: string[]; font: string; lineHeight: number; color: string; marginTop: number; bullet?: string };

/**
 * Render a complete deep draft as a shareable PNG.  The function is guarded for
 * non-browser callers and returns null when Canvas is unavailable; callers can
 * then fall back to the existing text/share path.
 */
export async function drawResearchDeepDraftImage(input: unknown, options: ResearchDeepDraftImageOptions = {}): Promise<Blob | null> {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  let context: CanvasRenderingContext2D | null = null;
  try { context = canvas.getContext('2d'); } catch { return null; }
  if (!context) return null;
  const ctx = context;
  const model = buildResearchDeepDraftImageModel(input, options);
  const scale = Math.max(1, Math.min(3, options.scale || 2));
  const width = Math.max(640, Math.min(1_400, options.width || 960));
  const pad = width >= 900 ? 56 : 38;
  const maxWidth = width - pad * 2;
  const font = (size: number, weight = '400') => `${weight} ${size}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
  const wrap = (value: string, face: string, max: number): string[] => {
    ctx.font = face;
    const out: string[] = [];
    let line = '';
    for (const char of String(value || '')) {
      if (char === '\n') { out.push(line); line = ''; continue; }
      if (line && ctx.measureText(line + char).width > max) { out.push(line); line = char; } else line += char;
    }
    if (line || !out.length) out.push(line);
    return out;
  };
  const blocks: DrawBlock[] = [];
  let logicalHeight = pad;
  const add = (value: string | string[], face: string, lineHeight: number, color: string, marginTop = 0, bullet?: string, max = maxWidth) => {
    const values = Array.isArray(value) ? value : [value];
    const lines = values.flatMap(item => wrap(item, face, bullet ? max - 24 : max));
    if (!lines.length || !lines.some(Boolean)) return;
    blocks.push({ lines, font: face, lineHeight, color, marginTop, bullet });
    logicalHeight += marginTop + lines.length * lineHeight;
  };
  const addEvidence = (items: ResearchDeepDraftImageEvidence[]) => {
    const refs = evidenceText(items);
    if (refs.length) add(refs.map(ref => `证据 · ${ref}`), font(11), 18, '#8793a3', 10, '·');
  };

  add('DEEPFOCUS 金融终端 · AI 深度研究稿', font(17, '800'), 26, '#ffb000');
  add(model.title, font(29, '800'), 38, '#f4ecd6', 10);
  if (model.subtitle) add(model.subtitle, font(15), 24, '#b0b9c6', 4);
  if (model.metadata.length) add(model.metadata.join('　·　'), font(12), 20, '#8a8463', 12);
  if (model.oneLiner) add(`💡 ${model.oneLiner}`, font(20, '800'), 30, '#ffd980', 22);
  if (model.plainLanguageSummary) add(['白话解释', model.plainLanguageSummary], font(14, '600'), 23, '#d9e7d8', 12);
  if (model.coreConclusion) add(['核心结论', model.coreConclusion], font(15, '700'), 25, '#e4ebf4', 12);
  if (model.thesis) add(`主线：${model.thesis}`, font(14), 23, '#b9c7d7', 10);
  if (model.decisionImplication) add(`研究含义：${model.decisionImplication}`, font(14), 23, '#b9c7d7', 10);
  if (model.metrics.length) {
    add('关键数字', font(15, '800'), 24, '#ffd980', 18);
    model.metrics.forEach(metric => add(`${metric.label}：${metric.value || '—'}${metric.change ? `（${metric.change}）` : ''}${metric.context ? `；${metric.context}` : ''}`, font(12.5), 20, '#d0d9e2', 3));
  }
  if (model.executiveSummary && model.executiveSummary !== model.coreConclusion) add(['摘要', model.executiveSummary], font(14), 23, '#c5cfda', 13);
  const addLogic = (items: ResearchDeepDraftImageLogicLine[]) => {
    if (!items.length) return;
    add(`🧭 关键逻辑线（${items.length} 条）`, font(15, '800'), 24, '#9fc9f0', 20);
    items.forEach((item, index) => {
      add(`${index + 1}. ${item.title || `逻辑线 ${index + 1}`}`, font(14, '700'), 22, '#e1ebf6', 5);
      [item.evidence && `事实：${item.evidence}`, item.chain && `传导：${item.chain}`, item.impact && `影响：${item.impact}`, item.watch && `验证：${item.watch}`].filter(Boolean).forEach(value => add(value as string, font(12.5), 20, '#b9c8d8', 2, undefined, maxWidth - 18));
    });
  };
  addLogic(model.logicLines);
  const addTable = (table: ResearchDeepDraftImageTable) => {
    if (table.title) add(`表｜${table.title}`, font(13, '700'), 21, '#c4b5fd', 12);
    if (table.columns.length) add(table.columns.join('　|　'), font(11.5, '700'), 19, '#9fb0c2', 4);
    table.rows.forEach(row => add(row.join('　|　'), font(11.5), 19, '#d0d9e2', 2));
    if (table.note) add(`注：${table.note}`, font(10.5), 17, '#8793a3', 4);
  };
  model.sections.forEach((section, index) => {
    add(`${String(index + 1).padStart(2, '0')}  ${section.title}`, font(19, '800'), 28, '#5fe39a', 25);
    if (section.summary) add(section.summary, font(14, '600'), 23, '#aeb9c5', 4);
    section.paragraphs.forEach(paragraph => add(paragraph, font(14), 25, '#e0e6ec', 8));
    section.bullets.forEach(bullet => add(bullet, font(13.5), 23, '#d4e9dc', 5, '◆'));
    addLogic(section.logicLines);
    section.tables.forEach(addTable);
    addEvidence(section.evidence);
  });
  if (model.tables.length) {
    add('数据与对比', font(19, '800'), 28, '#c4b5fd', 26);
    model.tables.forEach(addTable);
  }
  if (model.watchlist.length) {
    add('未来跟踪清单', font(19, '800'), 28, '#7ec8ff', 26);
    model.watchlist.forEach((item, index) => {
      add(`${index + 1}. ${item.title}`, font(14, '700'), 22, '#e2ebf4', 7);
      [item.signal && `信号：${item.signal}`, item.window && `窗口：${item.window}`, item.metric && `指标：${item.metric}`, item.trigger && `触发/失效：${item.trigger}`, item.why && `为什么：${item.why}`].filter(Boolean).forEach(value => add(value as string, font(12.5), 20, '#b9c6d3', 2, undefined, maxWidth - 12));
      addEvidence(item.evidence);
    });
  }
  if (model.risks.length) {
    add('风险与反方', font(19, '800'), 28, '#ff8a8a', 26);
    model.risks.forEach(item => {
      add(`⚠ ${item.title}`, font(14, '700'), 22, '#ffc0b8', 7);
      if (item.detail) add(item.detail, font(12.5), 20, '#e5c9c8', 2);
      if (item.trigger) add(`反证条件：${item.trigger}`, font(12.5), 20, '#e5c9c8', 2);
      addEvidence(item.evidence);
    });
  }
  if (model.sources.length) {
    add('来源与证据范围', font(19, '800'), 28, '#9fb9d9', 26);
    model.sources.forEach((item, index) => add(`${index + 1}. ${[item.title, item.provider, item.page, item.kind].filter(Boolean).join(' · ')}`, font(12), 19, '#b2becb', 3));
  }
  const footerTop = logicalHeight + 22;
  const logicalMax = 14_000;
  const finalHeight = Math.min(Math.max(footerTop + 104 + pad, 420), logicalMax);
  canvas.width = Math.ceil(width * scale);
  canvas.height = Math.ceil(finalHeight * scale);
  ctx.scale(scale, scale);
  ctx.fillStyle = '#0a0d12';
  ctx.fillRect(0, 0, width, finalHeight);
  ctx.fillStyle = '#ffb000';
  ctx.fillRect(0, 0, width, 5);
  // Low-contrast watermark makes it clear that this is our interpretation,
  // while remaining behind readable text.
  ctx.save();
  ctx.globalAlpha = 0.035;
  ctx.fillStyle = '#ffb000';
  ctx.font = font(38, '800');
  ctx.translate(width / 2, finalHeight / 2);
  ctx.rotate(-Math.PI / 7);
  for (let y = -finalHeight; y < finalHeight; y += 130) for (let x = -width; x < width; x += 390) ctx.fillText('DEEPFOCUS', x, y);
  ctx.restore();
  ctx.textBaseline = 'top';
  let y = pad;
  for (const block of blocks) {
    if (y >= finalHeight - 150) break;
    y += block.marginTop;
    ctx.font = block.font;
    ctx.fillStyle = block.color;
    for (const line of block.lines) {
      if (y >= finalHeight - 120) break;
      if (block.bullet) {
        ctx.font = font(10, '700');
        ctx.fillStyle = block.bullet === '◆' ? '#5fe39a' : '#8fa8c0';
        ctx.fillText(block.bullet, pad, y + 4);
        ctx.font = block.font;
        ctx.fillStyle = block.color;
        ctx.fillText(line, pad + 20, y);
      } else ctx.fillText(line, pad, y);
      y += block.lineHeight;
    }
  }
  const dividerY = Math.min(Math.max(y + 12, footerTop), finalHeight - 100);
  ctx.strokeStyle = '#1c2530';
  ctx.beginPath(); ctx.moveTo(pad, dividerY); ctx.lineTo(width - pad, dividerY); ctx.stroke();
  const qr = options.qr && options.qr.size > 0 ? options.qr : null;
  const qrSize = qr ? 104 : 0;
  if (qr) {
    const cell = qrSize / qr.size;
    const qx = width - pad - qrSize;
    const qy = dividerY + 14;
    ctx.fillStyle = '#fff'; ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12);
    ctx.fillStyle = '#000';
    for (let row = 0; row < qr.size; row++) for (let col = 0; col < qr.size; col++) if (qr.matrix[row]?.[col]) ctx.fillRect(qx + col * cell, qy + row * cell, cell + 0.6, cell + 0.6);
  }
  ctx.font = font(17, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('DEEPFOCUS 金融终端', pad, dividerY + 14);
  ctx.font = font(12); ctx.fillStyle = '#8a93a0'; ctx.fillText(`扫码访问 · ${(options.site || 'daocaijing.com').replace(/^https?:\/\//, '')}`, pad, dividerY + 42);
  ctx.font = font(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText('AI 深度研究稿 · 仅供参考，非投资建议', pad, dividerY + 64);
  return await new Promise<Blob | null>(resolve => {
    try { canvas.toBlob(resolve, 'image/png'); } catch { resolve(null); }
  });
}
