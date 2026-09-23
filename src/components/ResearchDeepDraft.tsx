import React, { useMemo } from 'react';
import {
  Alert,
  Button,
  Empty,
  Spin,
  Tag,
  Tooltip,
  Typography
} from 'antd';
import {
  BookOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  LinkOutlined,
  ReadOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  WarningOutlined
} from '@ant-design/icons';
import './ResearchDeepDraft.css';

const { Text } = Typography;

/**
 * The long-form research contract.  The API may add fields over time; the
 * renderer intentionally keeps the presentation contract small and stable so
 * it can be mounted next to the existing quick AI card.
 */
export interface ResearchDeepDraftEvidence {
  id?: string;
  label?: string;
  page?: number | null;
  pages?: string | number[] | null;
  excerpt?: string;
  quote?: string;
  source?: string;
  source_id?: string;
  url?: string;
}

export interface ResearchDeepDraftTableColumn {
  key: string;
  label: string;
}

export interface ResearchDeepDraftTable {
  id?: string;
  title?: string;
  columns?: Array<string | ResearchDeepDraftTableColumn>;
  rows?: Array<Array<unknown> | Record<string, unknown>>;
  note?: string;
}

export interface ResearchDeepDraftMetric {
  id?: string;
  label: string;
  value?: unknown;
  change?: string;
  context?: string;
  period?: string;
  unit?: string;
  status?: string;
  evidence?: ResearchDeepDraftEvidence[];
}

export interface ResearchDeepDraftGlossaryEntry {
  term: string;
  meaning: string;
}

export interface ResearchDeepDraftLogicLine {
  title?: string;
  evidence?: string;
  chain?: string;
  impact?: string;
  watch?: string;
}

export interface ResearchDeepDraftSection {
  id?: string;
  title: string;
  summary?: string;
  paragraphs?: string[];
  bullets?: string[];
  evidence?: ResearchDeepDraftEvidence[];
  tables?: ResearchDeepDraftTable[];
  /** Optional compatibility field used by the previous compact report. */
  logic_lines?: ResearchDeepDraftLogicLine[];
}

export interface ResearchDeepDraftWatchItem {
  id?: string;
  title: string;
  item?: string;
  signal?: string;
  window?: string;
  time_window?: string;
  metric?: string;
  trigger?: string;
  why?: string;
  rationale?: string;
  evidence?: ResearchDeepDraftEvidence[];
}

export interface ResearchDeepDraftRisk {
  title?: string;
  detail?: string;
  trigger?: string;
  evidence?: ResearchDeepDraftEvidence[];
}

export interface ResearchDeepDraftSource {
  id?: string;
  title?: string;
  label?: string;
  provider?: string;
  url?: string;
  page?: number | null;
  pages?: string | number[] | null;
  kind?: string;
}

export interface ResearchDeepDraft {
  title: string;
  subtitle?: string;
  subject?: string;
  symbol?: string;
  generated_at?: string;
  read_time_minutes?: number;
  source_count?: number;
  confidence?: number;
  one_liner?: string;
  executive_summary?: string;
  core_conclusion?: string;
  thesis?: string;
  sections: ResearchDeepDraftSection[];
  metrics?: ResearchDeepDraftMetric[];
  plain_language_summary?: string;
  decision_implication?: string;
  glossary?: ResearchDeepDraftGlossaryEntry[];
  tables?: ResearchDeepDraftTable[];
  logic_lines?: ResearchDeepDraftLogicLine[];
  watchlist?: ResearchDeepDraftWatchItem[];
  risks?: Array<string | ResearchDeepDraftRisk>;
  sources?: ResearchDeepDraftSource[];
  disclaimer?: string;
  mode?: string;
  pages_analyzed?: number;
  provider?: string;
  source_coverage?: string;
  /** Preserve forward-compatible API fields without forcing consumers to cast. */
  [key: string]: unknown;
}

export type ResearchDeepDraftInput = Partial<ResearchDeepDraft> | Record<string, unknown> | null | undefined;

export interface ResearchDeepDraftProps {
  draft?: ResearchDeepDraftInput;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onClose?: () => void;
  onSourceClick?: (source: ResearchDeepDraftEvidence | ResearchDeepDraftSource) => void;
  className?: string;
}

type Dict = Record<string, unknown>;

const asDict = (value: unknown): Dict => (
  value && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {}
);

const firstValue = (value: Dict, keys: string[]): unknown => {
  for (const key of keys) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== '') return value[key];
  }
  return undefined;
};

const asText = (value: unknown): string => {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
};

// Evidence is a locator, not a second copy of a third-party report.  Keep the
// client-side normaliser bounded too, so legacy/alternate backends cannot make
// a citation chip echo a long block of source text.
const asExcerpt = (value: unknown): string => asText(value).slice(0, 80);

const asTextArray = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (typeof item === 'string' || typeof item === 'number') {
        const text = asText(item);
        return text ? [text] : [];
      }
      const record = asDict(item);
      const text = asText(firstValue(record, ['text', 'detail', 'content', 'value', 'label', 'title']));
      return text ? [text] : [];
    });
  }
  const text = asText(value);
  return text ? [text] : [];
};

const asNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value.replace(/[%，,]/g, ''));
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
};

const splitParagraphs = (value: unknown): string[] => {
  if (Array.isArray(value)) return asTextArray(value);
  const text = asText(value);
  if (!text) return [];
  return text
    .split(/\n\s*\n|\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
};

const pageLabel = (value: unknown): string => {
  if (Array.isArray(value)) {
    const pages = value.map(asText).filter(Boolean);
    return pages.length ? `第 ${pages.join('、')} 页` : '';
  }
  const text = asText(value);
  if (!text) return '';
  // Numeric values/ranges get the friendly Chinese page prefix.  Labels such
  // as “全文” or “封面” are already human-readable and should not become the
  // nonsensical “第 全文 页”。
  return /^第/.test(text) || /\d/.test(text) ? (/^第/.test(text) ? text : `第 ${text} 页`) : text;
};

const normalizeEvidence = (value: unknown, index: number): ResearchDeepDraftEvidence => {
  const record = asDict(value);
  const rawPage = firstValue(record, ['page', 'source_page', 'page_number']);
  const page = typeof rawPage === 'number' ? rawPage : (rawPage && /^\d+$/.test(String(rawPage)) ? Number(rawPage) : null);
  const pages = firstValue(record, ['pages', 'page_range', 'source_pages']);
  return {
    id: asText(firstValue(record, ['id', 'citation_id', 'source_id'])) || `evidence-${index + 1}`,
    label: asText(firstValue(record, ['label', 'title', 'source', 'report_title'])) || undefined,
    page,
    pages: Array.isArray(pages) ? pages.map((item) => Number(item)).filter((item) => Number.isFinite(item)) : asText(pages) || null,
    excerpt: asExcerpt(firstValue(record, ['excerpt', 'quote', 'text', 'source_excerpt', 'evidence'])),
    quote: asExcerpt(firstValue(record, ['quote', 'excerpt', 'text'])),
    source: asText(firstValue(record, ['source', 'report_title', 'label'])),
    source_id: asText(firstValue(record, ['source_id', 'report_id', 'citation_id'])) || undefined,
    url: asText(firstValue(record, ['url', 'source_url'])) || undefined
  };
};

const normalizeLogicLine = (value: unknown): ResearchDeepDraftLogicLine => {
  if (typeof value === 'string') return { title: value };
  const record = asDict(value);
  return {
    title: asText(firstValue(record, ['title', 'name', 'point'])),
    evidence: asText(firstValue(record, ['evidence', 'fact', 'facts'])),
    chain: asText(firstValue(record, ['chain', 'transmission', 'mechanism'])),
    impact: asText(firstValue(record, ['impact', 'conclusion', 'implication'])),
    watch: asText(firstValue(record, ['watch', 'validation', 'verify', 'trigger']))
  };
};

const normalizeTable = (value: unknown, index: number): ResearchDeepDraftTable => {
  const record = asDict(value);
  const rawColumns = firstValue(record, ['columns', 'headers', 'fields']);
  let columns: Array<string | ResearchDeepDraftTableColumn> = [];
  if (Array.isArray(rawColumns)) {
    rawColumns.forEach((column) => {
      if (typeof column === 'string' || typeof column === 'number') {
        columns.push(String(column));
        return;
      }
      const item = asDict(column);
      const label = asText(firstValue(item, ['label', 'title', 'name', 'key']));
      if (label) columns.push({ key: asText(firstValue(item, ['key', 'id'])) || label, label });
    });
  }
  const rawRows = firstValue(record, ['rows', 'data', 'items']);
  const rows: Array<Array<unknown> | Record<string, unknown>> = Array.isArray(rawRows)
    ? rawRows.filter((row) => Array.isArray(row) || (row && typeof row === 'object')) as Array<Array<unknown> | Record<string, unknown>>
    : [];
  if (!columns.length && rows.length && !Array.isArray(rows[0])) {
    columns = Object.keys(rows[0] as Record<string, unknown>).slice(0, 10).map((key) => ({ key, label: key }));
  }
  return {
    id: asText(firstValue(record, ['id', 'key'])) || `table-${index + 1}`,
    title: asText(firstValue(record, ['title', 'name', 'caption'])) || undefined,
    columns,
    rows,
    note: asText(firstValue(record, ['note', 'footnote', 'source_note'])) || undefined
  };
};

const normalizeMetric = (value: unknown, index: number): ResearchDeepDraftMetric => {
  if (typeof value === 'string' || typeof value === 'number') {
    return { id: `metric-${index + 1}`, label: String(value) };
  }
  const record = asDict(value);
  return {
    id: asText(firstValue(record, ['id', 'key'])) || `metric-${index + 1}`,
    label: asText(firstValue(record, ['label', 'title', 'name', 'metric_label', 'metric_key'])) || `关键指标 ${index + 1}`,
    value: firstValue(record, ['value', 'raw_value', 'normalized_value', 'number']),
    change: asText(firstValue(record, ['change', 'delta', 'comparison', 'change_text'])),
    context: asText(firstValue(record, ['context', 'description', 'note', 'meaning'])),
    period: asText(firstValue(record, ['period', 'window', 'date'])),
    unit: asText(firstValue(record, ['unit'])),
    status: asText(firstValue(record, ['status', 'signal', 'direction'])),
    evidence: Array.isArray(firstValue(record, ['evidence', 'citations', 'sources']))
      ? (firstValue(record, ['evidence', 'citations', 'sources']) as unknown[]).map(normalizeEvidence)
      : []
  };
};

const normalizeGlossaryEntry = (value: unknown, index: number): ResearchDeepDraftGlossaryEntry => {
  if (typeof value === 'string') return { term: `术语 ${index + 1}`, meaning: value };
  const record = asDict(value);
  return {
    term: asText(firstValue(record, ['term', 'label', 'name', 'key'])) || `术语 ${index + 1}`,
    meaning: asText(firstValue(record, ['meaning', 'definition', 'description', 'explanation', 'value']))
  };
};

const normalizeSection = (value: unknown, index: number): ResearchDeepDraftSection => {
  const record = asDict(value);
  const title = asText(firstValue(record, ['title', 'heading', 'name'])) || `第 ${index + 1} 节`;
  const rawContent = firstValue(record, ['paragraphs', 'content', 'body', 'text']);
  const rawTables = firstValue(record, ['tables', 'table']);
  const rawEvidence = firstValue(record, ['evidence', 'citations', 'sources']);
  const rawLogic = firstValue(record, ['logic_lines', 'logicLines', 'logic']);
  return {
    id: asText(firstValue(record, ['id', 'slug', 'key'])) || `section-${index + 1}`,
    title,
    summary: asText(firstValue(record, ['summary', 'abstract', 'dek'])) || undefined,
    paragraphs: splitParagraphs(rawContent),
    bullets: asTextArray(firstValue(record, ['bullets', 'key_points', 'points'])),
    evidence: Array.isArray(rawEvidence) ? rawEvidence.map(normalizeEvidence) : [],
    tables: Array.isArray(rawTables) ? rawTables.map(normalizeTable) : [],
    logic_lines: Array.isArray(rawLogic) ? rawLogic.map(normalizeLogicLine) : []
  };
};

const normalizeWatchItem = (value: unknown, index: number): ResearchDeepDraftWatchItem => {
  if (typeof value === 'string') {
    return { id: `watch-${index + 1}`, title: value, window: '持续跟踪' };
  }
  const record = asDict(value);
  const title = asText(firstValue(record, ['title', 'name', 'item', 'point'])) || `跟踪事项 ${index + 1}`;
  const evidence = firstValue(record, ['evidence', 'citations', 'sources']);
  return {
    id: asText(firstValue(record, ['id', 'key'])) || `watch-${index + 1}`,
    title,
    item: asText(firstValue(record, ['item', 'name', 'title'])) || undefined,
    signal: asText(firstValue(record, ['signal', 'direction', 'stance'])) || undefined,
    window: asText(firstValue(record, ['window', 'time_window', 'period', 'horizon'])) || '持续跟踪',
    time_window: asText(firstValue(record, ['time_window', 'window', 'period'])) || undefined,
    metric: asText(firstValue(record, ['metric', 'indicator', 'measure'])) || undefined,
    trigger: asText(firstValue(record, ['trigger', 'condition', 'invalidation'])) || undefined,
    why: asText(firstValue(record, ['why', 'rationale', 'reason'])) || undefined,
    rationale: asText(firstValue(record, ['rationale', 'why', 'reason'])) || undefined,
    evidence: Array.isArray(evidence) ? evidence.map(normalizeEvidence) : []
  };
};

const normalizeRisk = (value: unknown): string | ResearchDeepDraftRisk => {
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  const record = asDict(value);
  const evidence = firstValue(record, ['evidence', 'citations', 'sources']);
  return {
    title: asText(firstValue(record, ['title', 'name', 'risk'])) || '风险提示',
    detail: asText(firstValue(record, ['detail', 'description', 'text', 'content'])),
    trigger: asText(firstValue(record, ['trigger', 'condition', 'invalidation'])),
    evidence: Array.isArray(evidence) ? evidence.map(normalizeEvidence) : []
  };
};

const normalizeSource = (value: unknown, index: number): ResearchDeepDraftSource => {
  if (typeof value === 'string') return { id: `source-${index + 1}`, title: value, label: value };
  const record = asDict(value);
  const title = asText(firstValue(record, ['title', 'report_title', 'name', 'label'])) || `来源 ${index + 1}`;
  const pages = firstValue(record, ['pages', 'page_range', 'source_pages']);
  return {
    id: asText(firstValue(record, ['id', 'source_id', 'report_id', 'citation_id'])) || `source-${index + 1}`,
    title,
    label: asText(firstValue(record, ['label', 'provider'])) || title,
    provider: asText(firstValue(record, ['provider', 'institution', 'author'])) || undefined,
    url: asText(firstValue(record, ['url', 'source_url'])) || undefined,
    page: typeof record.page === 'number' ? record.page : null,
    pages: Array.isArray(pages) ? pages.map((item) => Number(item)).filter((item) => Number.isFinite(item)) : asText(pages) || null,
    kind: asText(firstValue(record, ['kind', 'type'])) || undefined
  };
};

/**
 * Convert both the new deep-draft response and the old compact report into a
 * predictable shape. This is exported as a pure helper so callers/tests can
 * validate API compatibility without rendering React.
 */
export function normalizeResearchDeepDraft(input: ResearchDeepDraftInput): ResearchDeepDraft | null {
  if (!input) return null;
  const record = asDict(input);
  const nestedReport = asDict(record.report);
  const pick = (keys: string[]): unknown => firstValue(record, keys) ?? firstValue(nestedReport, keys);
  const title = asText(firstValue(record, ['title', 'headline', 'name']))
    || asText(firstValue(nestedReport, ['title', 'headline', 'name']))
    || '研报深度解读';
  const rawSections = pick(['sections', 'chapters', 'outline']);
  let sections = Array.isArray(rawSections) ? rawSections.map(normalizeSection) : [];
  const topContent = pick(['body', 'content', 'article']);
  if (!sections.length && topContent) {
    sections = [{
      id: 'section-1',
      title: '核心分析',
      paragraphs: splitParagraphs(topContent),
      summary: undefined,
      bullets: [],
      evidence: [],
      tables: [],
      logic_lines: []
    }];
  }
  const rawTables = pick(['tables', 'table']);
  const rawMetrics = pick(['metrics', 'metric_cards', 'key_metrics']);
  const rawGlossary = pick(['glossary', 'terms', 'definitions']);
  const rawWatchlist = pick(['watchlist', 'watch_items', 'tracking', 'follow_up']);
  const rawRisks = pick(['risks', 'risk_flags']);
  const rawSources = pick(['sources', 'source_refs', 'citations']) || record.citations || nestedReport.citations;
  const rawLogic = pick(['logic_lines', 'logicLines']);
  const normalizedTables = Array.isArray(rawTables) ? rawTables.map(normalizeTable) : [];
  const normalizedMetrics = Array.isArray(rawMetrics) ? rawMetrics.map(normalizeMetric) : [];
  const glossaryItems = Array.isArray(rawGlossary)
    ? rawGlossary
    : Object.entries(asDict(rawGlossary)).map(([term, meaning]) => ({ term, meaning }));
  const normalizedGlossary = glossaryItems.map(normalizeGlossaryEntry).filter((item) => item.meaning);
  const legacyMetrics = Array.isArray(record.key_metrics)
    ? record.key_metrics
    : (Array.isArray(nestedReport.key_metrics) ? nestedReport.key_metrics : []);
  if (!normalizedTables.length && legacyMetrics.length) {
    normalizedTables.push(normalizeTable({
      id: 'legacy-key-metrics',
      title: '关键指标（来自原始研报抽取）',
      columns: ['指标', '数值', '单位', '期间', '来源'],
      rows: legacyMetrics.map((metric) => {
        const item = asDict(metric);
        return [
          firstValue(item, ['metric_label', 'label', 'metric_key']),
          firstValue(item, ['raw_value', 'value', 'normalized_value']),
          firstValue(item, ['unit']),
          firstValue(item, ['period']),
          firstValue(item, ['source_excerpt', 'source_page'])
        ];
      })
    }, 0));
  }
  const normalizedWatchlist = Array.isArray(rawWatchlist) ? rawWatchlist.map(normalizeWatchItem) : [];
  if (!normalizedWatchlist.length && (Array.isArray(record.follow_up_questions) || Array.isArray(nestedReport.follow_up_questions))) {
    const followUps = Array.isArray(record.follow_up_questions) ? record.follow_up_questions : nestedReport.follow_up_questions;
    normalizedWatchlist.push(...(followUps as unknown[]).map(normalizeWatchItem));
  }
  const normalizedRisks = Array.isArray(rawRisks) ? rawRisks.map(normalizeRisk) : [];
  if (!normalizedRisks.length && (Array.isArray(record.quality_flags) || Array.isArray(nestedReport.quality_flags))) {
    const qualityFlags = Array.isArray(record.quality_flags) ? record.quality_flags : nestedReport.quality_flags;
    normalizedRisks.push(...(qualityFlags as unknown[]).map(normalizeRisk));
  }
  const summary = asText(pick(['executive_summary', 'summary', 'abstract']));
  const core = asText(pick(['core_conclusion', 'core_logic', 'thesis', 'takeaway', 'df_take']));
  const oneLiner = asText(pick(['one_liner', 'oneLiner', 'headline']));
  // Legacy compact reports expose key_points but no sections. Keep these points
  // visible instead of silently dropping them while a deep draft is rolling out.
  if (!sections.length) {
    const legacyPoints = asTextArray(pick(['key_points', 'points', 'bullish']));
    if (summary || core || legacyPoints.length) {
      sections = [{
        id: 'section-1',
        title: '核心分析',
        summary: summary || undefined,
        paragraphs: core ? [core] : [],
        bullets: legacyPoints,
        evidence: [],
        tables: [],
        logic_lines: []
      }];
    }
  }
  return {
    ...record,
    title,
    subtitle: asText(pick(['subtitle', 'dek', 'sub_title'])) || undefined,
    subject: asText(record.subject) || asText(nestedReport.subject) || undefined,
    symbol: asText(record.symbol) || asText(nestedReport.symbol) || undefined,
    generated_at: asText(pick(['generated_at', 'generatedAt', 'created_at'])) || undefined,
    read_time_minutes: asNumber(pick(['read_time_minutes', 'read_time', 'reading_time'])),
    source_count: asNumber(pick(['source_count', 'sources_count'])),
    confidence: asNumber(pick(['confidence'])),
    one_liner: oneLiner || undefined,
    executive_summary: summary || undefined,
    core_conclusion: core || undefined,
    thesis: asText(pick(['thesis'])) || undefined,
    sections,
    metrics: normalizedMetrics,
    plain_language_summary: asText(pick(['plain_language_summary', 'reader_summary', 'simple_summary', 'beginner_summary'])) || undefined,
    decision_implication: asText(pick(['decision_implication', 'research_implication', 'so_what'])) || undefined,
    glossary: normalizedGlossary,
    tables: normalizedTables,
    logic_lines: Array.isArray(rawLogic) ? rawLogic.map(normalizeLogicLine) : [],
    watchlist: normalizedWatchlist,
    risks: normalizedRisks,
    sources: Array.isArray(rawSources) ? rawSources.map(normalizeSource) : [],
    disclaimer: asText(pick(['disclaimer', 'notice'])) || 'AI 生成内容仅供研究参考，不构成投资建议。',
    mode: asText(pick(['mode'])) || undefined,
    pages_analyzed: asNumber(pick(['pages_analyzed'])),
    provider: asText(pick(['provider'])) || undefined,
    source_coverage: normalizeCoverage(pick(['source_coverage']))
  };
}

/** API versions have returned source coverage as either a sentence or a small
 * object (for example `{covered: 8, total: 10}`). Keep rendering text-only. */
function normalizeCoverage(value: unknown): string | undefined {
  const text = asText(value);
  if (text) return text;
  const record = asDict(value);
  if (!Object.keys(record).length) return undefined;
  // The backend contract uses pages_read/total_pages; keep aliases for older
  // drafts, otherwise the coverage badge silently disappears on every new
  // response.
  const covered = firstValue(record, ['covered', 'included', 'pages_analyzed', 'pages_read']);
  const total = firstValue(record, ['total', 'available', 'pages_total', 'total_pages']);
  if (covered !== undefined && total !== undefined) return `证据覆盖 ${asText(covered)}/${asText(total)}`;
  const label = asText(firstValue(record, ['label', 'summary', 'detail', 'text']));
  return label || undefined;
}

const slugify = (value: string, fallback: string): string => {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
  return slug || fallback;
};

const formatGeneratedAt = (value?: string) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const confidenceLabel = (value?: number) => {
  if (value === undefined || Number.isNaN(value)) return '';
  const percent = value <= 1 ? Math.round(value * 100) : Math.round(value);
  return `证据置信度 ${Math.max(0, Math.min(100, percent))}%`;
};

const modeLabel = (value?: string) => {
  if (!value) return '';
  const labels: Record<string, string> = {
    deep_draft: '深度稿',
    synthesis: '多篇综合',
    vision: '视觉兼容',
    compact: '兼容稿'
  };
  return labels[value] || value;
};

const isSafeExternalUrl = (value?: string): value is string => {
  if (!value) return false;
  try {
    const parsed = new URL(value, 'https://daocaijing.local');
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
};

const renderParagraphs = (paragraphs: string[], className = 'research-deep-draft__paragraphs') => (
  paragraphs.length ? (
    <div className={className}>
      {paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 18)}`}>{paragraph}</p>)}
    </div>
  ) : null
);

const DEFAULT_GLOSSARY: ResearchDeepDraftGlossaryEntry[] = [
  { term: 'NET', meaning: '净值 = 预期增加比例 − 预期减少比例；正数越高，代表增加意愿越强。' },
  { term: '证据覆盖', meaning: 'AI 实际读取并用于整理的页数 / 原文总页数，不等于结论正确率。' },
  { term: '证据置信度', meaning: '材料可核验程度的提示，不代表投资收益概率，也不替代人工判断。' }
];

/** Turn a table-shaped response into a compact first-screen signal strip. */
const deriveMetricCards = (draft: ResearchDeepDraft): ResearchDeepDraftMetric[] => {
  const explicit = (draft.metrics || []).filter((metric) => metric.label || asText(metric.value));
  if (explicit.length) return explicit.slice(0, 6);

  const table = (draft.tables || []).find((item) => (item.rows || []).length > 0);
  if (!table) return [];
  return (table.rows || []).slice(0, 4).flatMap((row, index) => {
    const cells = Array.isArray(row)
      ? row
      : (table.columns || []).map((column) => {
        const key = typeof column === 'string' ? column : column.key;
        return (row as Record<string, unknown>)[key];
      });
    const label = asText(cells[0]);
    const value = asText(cells[1]);
    if (!label && !value) return [];
    return [{
      id: `derived-metric-${index + 1}`,
      label: label || `关键指标 ${index + 1}`,
      value: value || '—',
      change: asText(cells[2]),
      context: asText(cells[3])
    }];
  });
};

const MetricCards: React.FC<{ metrics: ResearchDeepDraftMetric[] }> = ({ metrics }) => {
  const visible = metrics.filter((metric) => metric.label || asText(metric.value));
  if (!visible.length) return null;
  return (
    <div className="research-deep-draft__metric-grid" aria-label="关键数字">
      {visible.map((metric, index) => (
        <article className="research-deep-draft__metric-card" key={metric.id || `${metric.label}-${index}`}>
          <div className="research-deep-draft__metric-label">{metric.label}</div>
          <div className="research-deep-draft__metric-value">
            {asText(metric.value) || '—'}
            {metric.unit && <small>{metric.unit}</small>}
          </div>
          {(metric.change || metric.period) && (
            <div className="research-deep-draft__metric-change">{metric.change || metric.period}</div>
          )}
          {metric.context && <p>{metric.context}</p>}
        </article>
      ))}
    </div>
  );
};

const GlossaryHints: React.FC<{ entries?: ResearchDeepDraftGlossaryEntry[] }> = ({ entries }) => {
  const visible = (entries && entries.length ? entries : DEFAULT_GLOSSARY).slice(0, 5);
  return (
    <div className="research-deep-draft__glossary" aria-label="术语说明">
      <span className="research-deep-draft__glossary-label"><InfoCircleOutlined /> 看不懂术语？</span>
      {visible.map((entry, index) => (
        <Tooltip title={entry.meaning} key={`${entry.term}-${index}`}>
          <span className="research-deep-draft__glossary-term" tabIndex={0}>
            {entry.term}<InfoCircleOutlined />
          </span>
        </Tooltip>
      ))}
    </div>
  );
};

interface EvidenceChipsProps {
  evidence: ResearchDeepDraftEvidence[];
  onSourceClick?: ResearchDeepDraftProps['onSourceClick'];
}

const EvidenceChips: React.FC<EvidenceChipsProps> = ({ evidence, onSourceClick }) => {
  if (!evidence.length) return null;
  return (
    <div className="research-deep-draft__evidence" aria-label="证据引用">
      <span className="research-deep-draft__evidence-label"><FileSearchOutlined /> 证据</span>
      {evidence.map((item, index) => {
        const page = item.page !== null && item.page !== undefined ? pageLabel(item.page) : pageLabel(item.pages);
        const label = item.label || item.source || `来源 ${index + 1}`;
        const text = item.excerpt || item.quote || '';
        const content = text ? `${label}${page ? ` · ${page}` : ''}\n${text}` : `${label}${page ? ` · ${page}` : ''}`;
        const chip = (
          <span className="research-deep-draft__evidence-chip" key={item.id || `${label}-${index}`}>
            <LinkOutlined />
            <span>{label}</span>
            {page && <b>{page}</b>}
            {text && <small className="research-deep-draft__evidence-excerpt">{text}</small>}
          </span>
        );
        return onSourceClick || text ? (
          <Tooltip title={content} key={item.id || `${label}-${index}`}>
            {onSourceClick ? (
              <button
                type="button"
                className="research-deep-draft__evidence-button"
                onClick={() => onSourceClick(item)}
                aria-label={`打开证据：${label}${page ? `，${page}` : ''}`}
              >{chip}</button>
            ) : chip}
          </Tooltip>
        ) : chip;
      })}
    </div>
  );
};

interface TableBlockProps {
  table: ResearchDeepDraftTable;
  index: number;
}

const TableBlock: React.FC<TableBlockProps> = ({ table, index }) => {
  const columns = (table.columns || []).map((column, columnIndex) => (
    typeof column === 'string' ? { key: `column-${columnIndex}`, label: column } : column
  ));
  if (!columns.length && !table.rows?.length) return null;
  return (
    <figure className="research-deep-draft__table-wrap" key={table.id || `table-${index}`}>
      {table.title && <figcaption>{table.title}</figcaption>}
      <div className="research-deep-draft__table-scroll">
        <table className="research-deep-draft__table">
          {columns.length > 0 && (
            <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
          )}
          <tbody>
            {(table.rows || []).map((row, rowIndex) => {
              const cells = Array.isArray(row)
                ? row
                : columns.map((column) => (row as Record<string, unknown>)[column.key]);
              return (
                <tr key={`${table.id || index}-row-${rowIndex}`}>
                  {cells.map((cell, cellIndex) => <td key={`${rowIndex}-${cellIndex}`}>{asText(cell) || '—'}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {table.note && <div className="research-deep-draft__table-note">{table.note}</div>}
    </figure>
  );
};

const LogicLines: React.FC<{ lines: ResearchDeepDraftLogicLine[] }> = ({ lines }) => {
  const visible = lines.filter((line) => line.title || line.evidence || line.chain || line.impact || line.watch);
  if (!visible.length) return null;
  return (
    <div className="research-deep-draft__logic-lines" aria-label="逻辑线">
      {visible.map((line, index) => (
        <article className="research-deep-draft__logic-line" key={`${line.title || 'logic'}-${index}`}>
          <div className="research-deep-draft__logic-index">{String(index + 1).padStart(2, '0')}</div>
          <div className="research-deep-draft__logic-main">
            {line.title && <h3>{line.title}</h3>}
            <div className="research-deep-draft__logic-grid">
              {line.evidence && <div><small>事实</small><p>{line.evidence}</p></div>}
              {line.chain && <div><small>传导</small><p>{line.chain}</p></div>}
              {line.impact && <div><small>影响</small><p>{line.impact}</p></div>}
              {line.watch && <div><small>验证</small><p>{line.watch}</p></div>}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
};

const ResearchDeepDraft: React.FC<ResearchDeepDraftProps> = ({
  draft: input,
  loading = false,
  error = null,
  onRetry,
  onClose,
  onSourceClick,
  className = ''
}) => {
  const draft = useMemo(() => normalizeResearchDeepDraft(input), [input]);

  if (loading && !draft) {
    return (
      <section className={`research-deep-draft research-deep-draft--state ${className}`.trim()} data-testid="research-deep-draft" aria-busy="true">
        <div className="research-deep-draft__state-card">
          <Spin size="large" />
          <h2>正在生成深度解读</h2>
          <p>正在整理全文证据、章节结构和后续验证项，通常需要几十秒。</p>
        </div>
      </section>
    );
  }

  if (error && !draft) {
    return (
      <section className={`research-deep-draft research-deep-draft--state ${className}`.trim()} data-testid="research-deep-draft">
        <div className="research-deep-draft__state-card">
          <Alert type="error" showIcon message="深度解读生成失败" description={error} />
          <div className="research-deep-draft__state-actions">
            {onRetry && <Button type="primary" icon={<ReloadOutlined />} onClick={onRetry}>重试</Button>}
            {onClose && <Button onClick={onClose}>返回研报</Button>}
          </div>
        </div>
      </section>
    );
  }

  if (!draft) {
    return (
      <section className={`research-deep-draft research-deep-draft--state ${className}`.trim()} data-testid="research-deep-draft">
        <div className="research-deep-draft__state-card"><Empty description="暂无深度解读内容" /></div>
      </section>
    );
  }

  const allSections = draft.sections || [];
  const sectionIds = new Map<string, number>();
  const sectionAnchor = (section: ResearchDeepDraftSection, index: number) => {
    const base = slugify(section.title, `section-${index + 1}`);
    const seen = sectionIds.get(base) || 0;
    sectionIds.set(base, seen + 1);
    return seen ? `${base}-${seen + 1}` : base;
  };
  const anchoredSections = allSections.map((section, index) => ({ section, index, anchor: sectionAnchor(section, index) }));
  const topLogic = draft.logic_lines || [];
  const globalTables = draft.tables || [];
  const metricCards = deriveMetricCards(draft);
  const sourceCount = draft.source_count || (draft.sources || []).length;
  const metadata = [
    draft.subject,
    draft.symbol,
    sourceCount ? `${sourceCount} 个来源` : '',
    draft.pages_analyzed ? `分析 ${draft.pages_analyzed} 页` : '',
    draft.read_time_minutes ? `约 ${draft.read_time_minutes} 分钟阅读` : '',
    formatGeneratedAt(draft.generated_at)
  ].filter(Boolean);
  const coreConclusion = draft.core_conclusion || draft.thesis || draft.executive_summary || draft.one_liner;
  const risks = draft.risks || [];
  const watchlist = draft.watchlist || [];
  const sources = draft.sources || [];

  return (
    <article className={`research-deep-draft research-deep-draft--deep ${className}`.trim()} data-testid="research-deep-draft">
      <header className="research-deep-draft__header">
        <div className="research-deep-draft__header-topline">
          <span className="research-deep-draft__eyebrow"><BookOutlined /> 深度研究稿</span>
          <div className="research-deep-draft__header-actions">
            {draft.mode && <Tag color="blue">{modeLabel(draft.mode)}</Tag>}
            <span className="research-deep-draft__reader-mode" aria-label="深读模式">
              <ReadOutlined /> 深读 <small>{draft.read_time_minutes || '全文'}</small>
            </span>
            {onClose && <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} aria-label="关闭深度解读" />}
          </div>
        </div>
        <h1>{draft.title}</h1>
        {draft.subtitle && <p className="research-deep-draft__subtitle">{draft.subtitle}</p>}
        {metadata.length > 0 && (
          <div className="research-deep-draft__metadata">
            {metadata.map((item, index) => <span key={`${item}-${index}`}>{item}</span>)}
          </div>
        )}
        <GlossaryHints entries={draft.glossary} />
      </header>

      <div className="research-deep-draft__layout">
        <nav className="research-deep-draft__toc" aria-label="文章目录">
          <div className="research-deep-draft__toc-title"><BookOutlined /> 目录</div>
          <a href="#research-deep-draft-conclusion">核心结论</a>
          {metricCards.length > 0 && <a href="#research-deep-draft-metrics">关键数字</a>}
          {anchoredSections.map(({ section, index, anchor }) => <a href={`#${anchor}`} key={anchor}>{String(index + 1).padStart(2, '0')} {section.title}</a>)}
          {topLogic.length > 0 && <a href="#research-deep-draft-logic">逻辑线</a>}
          {globalTables.length > 0 && <a href="#research-deep-draft-tables">数据与对比</a>}
          {watchlist.length > 0 && <a href="#research-deep-draft-watchlist">跟踪清单</a>}
          {risks.length > 0 && <a href="#research-deep-draft-risks">风险与反方</a>}
          {sources.length > 0 && <a href="#research-deep-draft-sources">来源</a>}
        </nav>

        <main className="research-deep-draft__body">
          <section id="research-deep-draft-conclusion" className="research-deep-draft__conclusion" aria-labelledby="research-deep-draft-conclusion-title">
            <div className="research-deep-draft__section-kicker" id="research-deep-draft-conclusion-title"><SafetyCertificateOutlined /> 先看结论</div>
          {draft.one_liner && <p className="research-deep-draft__one-liner">{draft.one_liner}</p>}
          {coreConclusion && coreConclusion !== draft.one_liner && <p className="research-deep-draft__core-conclusion">{coreConclusion}</p>}
            {draft.thesis && draft.thesis !== coreConclusion && draft.thesis !== draft.one_liner && (
              <div className="research-deep-draft__thesis"><b>主线</b><span>{draft.thesis}</span></div>
            )}
            {draft.executive_summary && draft.executive_summary !== coreConclusion && draft.executive_summary !== draft.one_liner && (
              <details className="research-deep-draft__summary-details" open>
                <summary><span>编辑摘要</span><small>关键数字、时间点与主体</small></summary>
                {renderParagraphs(splitParagraphs(draft.executive_summary), 'research-deep-draft__summary')}
              </details>
            )}
            <div className="research-deep-draft__conclusion-meta">
              {confidenceLabel(draft.confidence) && <Tag icon={<CheckCircleOutlined />} color="green">{confidenceLabel(draft.confidence)}</Tag>}
              {draft.source_coverage && <Tag icon={<FileSearchOutlined />}>{draft.source_coverage}</Tag>}
              {loading && <Tag icon={<Spin size="small" />}>正在补全全文</Tag>}
            </div>
          </section>

          {metricCards.length > 0 && (
            <section id="research-deep-draft-metrics" className="research-deep-draft__section research-deep-draft__section--metrics">
              <div className="research-deep-draft__section-heading"><span>数</span><h2>关键数字</h2></div>
              <p className="research-deep-draft__section-lead">先看变化幅度，再回到下方章节理解原因和限制。</p>
              <MetricCards metrics={metricCards} />
            </section>
          )}

          {topLogic.length > 0 && (
            <section id="research-deep-draft-logic" className="research-deep-draft__section research-deep-draft__section--logic">
              <div className="research-deep-draft__section-heading"><span>◎</span><h2>关键逻辑线</h2></div>
              <LogicLines lines={topLogic} />
            </section>
          )}

          {anchoredSections.map(({ section, index, anchor }) => {
            const sectionTables = section.tables || [];
            return (
              <section id={anchor} className="research-deep-draft__section" key={anchor}>
                <div className="research-deep-draft__section-heading">
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <h2>{section.title}</h2>
                </div>
                {section.summary && <p className="research-deep-draft__section-summary">{section.summary}</p>}
                {renderParagraphs(section.paragraphs || [])}
                {(section.bullets || []).length > 0 && (
                  <ul className="research-deep-draft__bullets">{(section.bullets || []).map((bullet, bulletIndex) => <li key={`${bulletIndex}-${bullet.slice(0, 18)}`}>{bullet}</li>)}</ul>
                )}
                <LogicLines lines={section.logic_lines || []} />
                {sectionTables.map((table, tableIndex) => <TableBlock table={table} index={tableIndex} key={table.id || `${anchor}-table-${tableIndex}`} />)}
                <EvidenceChips evidence={section.evidence || []} onSourceClick={onSourceClick} />
              </section>
            );
          })}

          {globalTables.length > 0 && (
            <section id="research-deep-draft-tables" className="research-deep-draft__section research-deep-draft__section--tables">
              <div className="research-deep-draft__section-heading"><span>表</span><h2>数据与对比</h2></div>
              {globalTables.map((table, index) => <TableBlock table={table} index={index} key={table.id || `global-table-${index}`} />)}
            </section>
          )}

          {watchlist.length > 0 && (
            <section id="research-deep-draft-watchlist" className="research-deep-draft__section research-deep-draft__section--watchlist">
              <div className="research-deep-draft__section-heading"><span><CalendarOutlined /></span><h2>未来跟踪清单</h2></div>
              <p className="research-deep-draft__section-lead">把观点变成可验证的事件：每一项都标注观察窗口、指标和失效条件。</p>
              <div className="research-deep-draft__watch-grid">
                {watchlist.map((item, index) => {
                  const windowText = item.window || item.time_window || '持续跟踪';
                  const rationale = item.why || item.rationale;
                  return (
                    <article className="research-deep-draft__watch-card" key={item.id || `watch-${index}`}>
                      <div className="research-deep-draft__watch-topline"><span>{String(index + 1).padStart(2, '0')}</span><Tag color="blue" icon={<ClockCircleOutlined />}>{windowText}</Tag></div>
                      <h3>{item.title}</h3>
                      {item.signal && <div className="research-deep-draft__watch-signal">信号：{item.signal}</div>}
                      {item.metric && <p><b>看指标</b>{item.metric}</p>}
                      {item.trigger && <p><b>触发/失效</b>{item.trigger}</p>}
                      {rationale && <p><b>为什么</b>{rationale}</p>}
                      <EvidenceChips evidence={item.evidence || []} onSourceClick={onSourceClick} />
                    </article>
                  );
                })}
              </div>
            </section>
          )}

          {risks.length > 0 && (
            <section id="research-deep-draft-risks" className="research-deep-draft__section research-deep-draft__section--risks">
              <div className="research-deep-draft__section-heading"><span><WarningOutlined /></span><h2>风险与反方</h2></div>
              <div className="research-deep-draft__risk-list">
                {risks.map((risk, index) => {
                  const item = typeof risk === 'string' ? { title: risk, detail: '' } : risk;
                  return (
                    <article className="research-deep-draft__risk-card" key={`${item.title || 'risk'}-${index}`}>
                      <h3>{item.title || '风险提示'}</h3>
                      {item.detail && <p>{item.detail}</p>}
                      {item.trigger && <p><b>反证条件：</b>{item.trigger}</p>}
                      <EvidenceChips evidence={item.evidence || []} onSourceClick={onSourceClick} />
                    </article>
                  );
                })}
              </div>
            </section>
          )}

          {sources.length > 0 && (
            <section id="research-deep-draft-sources" className="research-deep-draft__section research-deep-draft__section--sources">
              <div className="research-deep-draft__section-heading"><span><FileSearchOutlined /></span><h2>来源与证据范围</h2></div>
              <div className="research-deep-draft__source-list">
                {sources.map((source, index) => {
                  const page = source.page !== null && source.page !== undefined ? pageLabel(source.page) : pageLabel(source.pages);
                  const content = <><span>{source.title || source.label || `来源 ${index + 1}`}</span>{source.provider && <small>{source.provider}</small>}{page && <b>{page}</b>}{source.kind && <em>{source.kind}</em>}</>;
                  return isSafeExternalUrl(source.url) ? (
                    <a className="research-deep-draft__source" href={source.url} target="_blank" rel="noreferrer" key={source.id || `source-${index}`}>
                      <LinkOutlined />{content}
                    </a>
                  ) : (
                    <button type="button" className="research-deep-draft__source" onClick={() => onSourceClick?.(source)} key={source.id || `source-${index}`}>
                      <FileSearchOutlined />{content}
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          <footer className="research-deep-draft__footer">
            <InfoCircleOutlined />
            <span>{draft.disclaimer || 'AI 生成内容仅供研究参考，不构成投资建议。请回到原始材料核验关键数字与引用。'}</span>
            {draft.provider && <Text type="secondary">生成引擎：{draft.provider}</Text>}
          </footer>
        </main>
      </div>
    </article>
  );
};

export default ResearchDeepDraft;
