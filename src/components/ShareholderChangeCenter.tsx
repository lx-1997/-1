import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Segmented, Select, Space, Spin, Tag, Typography } from 'antd';
import {
  AuditOutlined,
  BulbOutlined,
  CalendarOutlined,
  FallOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  TeamOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import {
  ShareholderChangeDirection,
  ShareholderChangeMarket,
  ShareholderChangeRecord,
  ShareholderChangeRecordDirection,
  ShareholderChangeStatus,
  ShareholderRiskLevel,
  ShareholderChangeInterpretResponse,
  interpretShareholderChange,
  scanShareholderChanges
} from '../services/shareholderChangeService';

const { Text } = Typography;

interface ShareholderChangeCenterProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

type RiskFilter = 'all' | ShareholderRiskLevel;
type StatusFilter = 'all' | ShareholderChangeStatus;
type SortMode = 'time_desc' | 'ratio_desc' | 'amount_desc';
type AiInterpretationTone = 'positive' | 'watch' | 'risk' | 'neutral';
const US_FORM4_XML_DETAIL_LIMIT = 120;

const directionMeta: Record<ShareholderChangeRecordDirection, { label: string; color: string; icon?: React.ReactNode }> = {
  increase: { label: '增持', color: 'green', icon: <RiseOutlined /> },
  decrease: { label: '减持', color: 'red', icon: <FallOutlined /> },
  mixed: { label: '增减持', color: 'gold' },
  unknown: { label: '持股变动', color: 'default' }
};

const statusMeta: Record<ShareholderChangeStatus, { label: string; color: string }> = {
  plan: { label: '计划', color: 'blue' },
  progress: { label: '进展', color: 'purple' },
  completed: { label: '完成/终止', color: 'default' },
  other: { label: '其他', color: 'cyan' }
};

const riskMeta: Record<ShareholderRiskLevel, { label: string; color: string }> = {
  high: { label: '高风险', color: 'red' },
  medium: { label: '中风险', color: 'gold' },
  low: { label: '低风险', color: 'green' }
};

const statusOptionsBase: Array<{ label: string; value: StatusFilter }> = [
  { label: '全部阶段', value: 'all' },
  { label: '计划', value: 'plan' },
  { label: '进展', value: 'progress' },
  { label: '完成/终止', value: 'completed' },
  { label: '其他', value: 'other' }
];

const detailQualityLabel: Record<string, string> = {
  full: '明细完整',
  partial: '部分明细',
  title_only: '标题识别'
};

const marketMeta: Record<ShareholderChangeMarket, { label: string; eyebrow: string; source: string; subtitle: string }> = {
  A: {
    label: 'A 股',
    eyebrow: 'A SHAREHOLDER EVENTS',
    source: '巨潮 / 东方财富',
    subtitle: '扫描 A 股股东、控股股东和董监高增持/减持公告，保留原文用于核验。'
  },
  HK: {
    label: '港股',
    eyebrow: 'HK DISCLOSURE OF INTERESTS',
    source: 'HKEX DI',
    subtitle: '扫描港交所权益披露每日摘要，覆盖主要股东、董事和高管权益变动。'
  },
  US: {
    label: '美股',
    eyebrow: 'US INSIDER TRANSACTIONS',
    source: 'SEC EDGAR Form 4',
    subtitle: '扫描 SEC EDGAR Form 4 内部人交易申报，抽取买卖方向、股数、价格和交易后持仓。'
  }
};

const directionOptions: Array<{ label: string; value: ShareholderChangeDirection }> = [
  { label: '增减持', value: 'all' },
  { label: '增持', value: 'increase' },
  { label: '减持', value: 'decrease' }
];

const getRecordKey = (record: ShareholderChangeRecord): string => {
  return record.announcement_id || `${record.symbol}-${record.announcement_date}-${record.title}`;
};

const displayValue = (value?: string, fallback = '--'): string => {
  const trimmed = (value || '').trim();
  return trimmed || fallback;
};

const hasSecRateLimitWarning = (warnings?: string[]): boolean => {
  return (warnings || []).some(warning => /429|Too Many Requests|限流/.test(warning));
};

const normalizeSearchText = (value: string): string => {
  return value
    .toLowerCase()
    .replace(/[\s._\-—~～,，.。:：;；/\\()[\]【】《》"'“”‘’]+/g, '');
};

const fuzzyMatch = (value: string, keyword: string): boolean => {
  const target = normalizeSearchText(value);
  const query = normalizeSearchText(keyword);
  if (!query) {
    return true;
  }
  if (target.includes(query)) {
    return true;
  }

  let queryIndex = 0;
  for (const char of target) {
    if (char === query[queryIndex]) {
      queryIndex += 1;
      if (queryIndex === query.length) {
        return true;
      }
    }
  }
  return false;
};

const recordMatchesKeywords = (
  record: ShareholderChangeRecord,
  keywords: string[],
  stockBySymbol: Map<string, Stock>
): boolean => {
  if (keywords.length === 0) {
    return true;
  }

  const displayName = recordDisplayName(record, stockBySymbol);
  const searchFields = [
    record.symbol,
    record.name,
    displayName,
    recordDisplayTitle(record, displayName),
    holderLabel(record),
    record.shareholder_type,
    record.change_method,
    formatChangeMethodDisplay(record),
    record.change_ratio,
    record.change_amount,
    formatAmountDisplay(record),
    ...record.tags
  ];

  return keywords.every(keyword => searchFields.some(field => fuzzyMatch(field, keyword)));
};

const parseMetricValue = (value?: string): number | null => {
  const compact = (value || '').replace(/[,，\s]/g, '');
  if (!compact || compact === '--') {
    return null;
  }

  const matcher = /(-?\d+(?:\.\d+)?)(万|亿)?/g;
  const values: number[] = [];
  let match = matcher.exec(compact);
  while (match) {
    const number = Number(match[1]);
    if (Number.isFinite(number)) {
      const multiplier = match[2] === '亿' ? 100_000_000 : match[2] === '万' ? 10_000 : 1;
      values.push(Math.abs(number * multiplier));
    }
    match = matcher.exec(compact);
  }

  return values.length ? Math.max(...values) : null;
};

const parseRatioChangeScore = (value?: string): number | null => {
  const text = (value || '').replace(/[,，\s]/g, '');
  if (!text || text === '--') {
    return null;
  }

  const percentValues = Array.from(text.matchAll(/(-?\d+(?:\.\d+)?)%/g))
    .map(match => Number(match[1]))
    .filter(Number.isFinite);
  if (percentValues.length >= 2 && /(?:→|->|至|到|~|～|-)/.test(text)) {
    return Math.abs(percentValues[percentValues.length - 1] - percentValues[0]);
  }
  if (percentValues.length >= 1) {
    return Math.max(...percentValues.map(value => Math.abs(value)));
  }

  return parseMetricValue(value);
};

const announcementTimeScore = (record: ShareholderChangeRecord): number => {
  const parsed = new Date(`${record.announcement_date}T00:00:00`).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
};

const metricSortScore = (value?: string): number => {
  const parsed = parseMetricValue(value);
  return parsed == null ? Number.NEGATIVE_INFINITY : parsed;
};

const ratioSortScore = (record: ShareholderChangeRecord): number => {
  const parsed = parseRatioChangeScore(record.change_ratio);
  return parsed == null ? Number.NEGATIVE_INFINITY : parsed;
};

const isReadableHolderName = (value?: string): boolean => {
  const clean = (value || '').trim();
  if (!clean || /[□√]/.test(clean) || /是否/.test(clean)) {
    return false;
  }
  if (/本次|公告|增持计划|减持计划|年度基本薪酬|个人所得税|五险一金|公开市场|监管机构|不存在|敏感期/.test(clean)) {
    return false;
  }
  if (clean.length > 80 && /[，,。；;]/.test(clean)) {
    return false;
  }
  return true;
};

const holderNamesForDisplay = (record: ShareholderChangeRecord): string[] => {
  return record.shareholder_names.filter(isReadableHolderName);
};

const holderLabel = (record: ShareholderChangeRecord): string => {
  const cleanNames = holderNamesForDisplay(record);
  if (cleanNames.length > 0) {
    const names = cleanNames.slice(0, 4).join('、');
    return cleanNames.length > 4 ? `${names}等` : names;
  }

  return isReadableHolderName(record.shareholder_hint) ? record.shareholder_hint : record.shareholder_type || '原文核验';
};

const HK_NAME_ZH_BY_SYMBOL: Record<string, string> = {
  '00001': '长和',
  '00006': '电能实业',
  '00023': '东亚银行',
  '00168': '青岛啤酒股份'
};

const hasChineseText = (value?: string): boolean => /[\u4e00-\u9fff]/.test(value || '');

const metadataString = (record: ShareholderChangeRecord, key: string): string => {
  const value = record.metadata?.[key];
  return typeof value === 'string' ? value.trim() : '';
};

const metadataNumber = (record: ShareholderChangeRecord, key: string): number => {
  const value = record.metadata?.[key];
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
};

const disclosureCount = (record: ShareholderChangeRecord): number => {
  return Math.max(1, metadataNumber(record, 'collapsed_disclosure_count'));
};

const cleanDisclosureText = (value?: string): string => {
  return (value || '')
    .replace(/\s*\|\s*/g, ' / ')
    .replace(/(?:\s*\/\s*){2,}/g, ' / ')
    .replace(/\s*@\s*/g, ' @ ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/(?:\s*\/\s*)+$/g, '')
    .trim();
};

const hkPositionLabel = (value?: string): string => {
  const marker = (value || '').match(/\(([LSP])\)/i)?.[1]?.toUpperCase();
  return marker === 'L' ? '好仓' : marker === 'S' ? '淡仓' : marker === 'P' ? '可供借出的股份' : '';
};

const parseDisclosureNumber = (value?: string): number | null => {
  const match = (value || '').replace(/[,，]/g, '').match(/-?\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
};

const parseDisclosurePrice = (value?: string): number | null => {
  const clean = cleanDisclosureText(value);
  const afterAt = clean.includes('@') ? clean.split('@').pop() : '';
  const scoped = afterAt || clean;
  const currencyMatch = scoped.replace(/[,，]/g, '').match(/\b[A-Z]{3}\b\s*(-?\d+(?:\.\d+)?)/i);
  if (currencyMatch) {
    const parsed = Number(currencyMatch[1]);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return parseDisclosureNumber(scoped);
};

const amountCurrencyLabel = (value?: string, source?: string): string => {
  const clean = cleanDisclosureText(value);
  const currency = clean.match(/\b([A-Z]{3})\b/i)?.[1]?.toUpperCase();
  if (currency) {
    return currency;
  }
  if (/人民币|RMB|CNY|元/.test(clean)) {
    return '元';
  }
  return source === 'hkex-di' ? 'HKD' : '';
};

const formatAmountInYi = (amount: number, currency = ''): string => {
  const value = amount / 100_000_000;
  const formatted = value.toLocaleString('zh-CN', {
    minimumFractionDigits: value >= 100 ? 0 : 2,
    maximumFractionDigits: 2
  });
  if (currency === '元') {
    return `约 ${formatted} 亿元`;
  }
  return `约 ${currency ? `${currency} ` : ''}${formatted} 亿`;
};

const formatShareDisplay = (value?: string): string => {
  const clean = cleanDisclosureText(value);
  if (!clean) {
    return '';
  }
  if (/[万亿]/.test(clean) || (!/\([LSP]\)/i.test(clean) && /股/.test(clean))) {
    return clean.replace(/\([LSP]\)/gi, '').replace(/\s+/g, ' ').trim();
  }
  const shares = parseDisclosureNumber(clean);
  if (shares == null) {
    return clean.replace(/\([LSP]\)/gi, '').trim();
  }
  const position = hkPositionLabel(clean);
  return [shares.toLocaleString('en-US', { maximumFractionDigits: 0 }), '股', position ? `· ${position}` : ''].filter(Boolean).join(' ');
};

const formatPriceDisplay = (value?: string): string => cleanDisclosureText(value).replace(/\([LSP]\)/gi, '').trim();

const formatAmountDisplay = (record: ShareholderChangeRecord): string => {
  const explicit = cleanDisclosureText(record.change_amount);
  if (explicit && !/@/.test(explicit) && !/\([LSP]\)/i.test(explicit)) {
    if (explicit.length > 60) {
      return '';
    }
    if (/亿/.test(explicit)) {
      return explicit;
    }
    const explicitAmount = parseDisclosureNumber(explicit);
    const explicitCurrency = amountCurrencyLabel(explicit, record.source);
    if (explicitAmount != null && explicitAmount <= 0) {
      return '';
    }
    if (explicitAmount != null && (explicitCurrency || explicitAmount >= 100_000_000)) {
      return formatAmountInYi(explicitAmount, explicitCurrency);
    }
    return explicit;
  }

  const shares = parseDisclosureNumber(record.change_shares || explicit);
  const priceText = record.price_range || explicit;
  const price = parseDisclosurePrice(priceText);
  if (shares == null || price == null || shares <= 0 || price <= 0) {
    return explicit.replace(/\([LSP]\)/gi, '').trim();
  }

  const currency = amountCurrencyLabel(priceText, record.source);
  return formatAmountInYi(shares * price, currency);
};

const formatChangeMethodDisplay = (record: ShareholderChangeRecord): string => {
  const clean = cleanDisclosureText(record.change_method);
  if (!clean) {
    return '';
  }
  if (/代码\s*\d{4}/.test(clean) && /权益|好仓|淡仓|取得|处置|增持|减持/.test(clean)) {
    return clean;
  }

  const reasonMatch = clean.match(/HKEX\s+DI\s+reason\s+(\d{4})/i) || clean.match(/披露原因\s+(\d{4})/) || clean.match(/\b(\d{4})\b/);
  if (!reasonMatch) {
    return clean.replace(/\([LSP]\)/gi, '').trim();
  }

  const position = metadataString(record, 'share_position') || hkPositionLabel(clean);
  const action = record.direction === 'increase'
    ? '取得/增持权益'
    : record.direction === 'decrease'
      ? '处置/减持权益'
      : '权益变动';
  return [action, position, `代码 ${reasonMatch[1]}`].filter(Boolean).join(' · ');
};

const formatRatioDisplay = (value?: string): string => cleanDisclosureText(value);

const buildDetailMetrics = (record: ShareholderChangeRecord): Array<{ label: string; value: string; primary?: boolean }> => {
  return [
    { label: '比例变化', value: formatRatioDisplay(record.change_ratio) || '未披露', primary: true },
    { label: '变动股数', value: formatShareDisplay(record.change_shares) || '未披露', primary: true },
    { label: '价格/均价', value: formatPriceDisplay(record.price_range) || '未披露' },
    { label: '估算金额', value: formatAmountDisplay(record) || '未披露' },
    { label: '披露原因', value: formatChangeMethodDisplay(record) || '未披露' },
    { label: '事件日', value: displayValue(record.change_period, '未披露') }
  ];
};

const formatEvidenceExcerpt = (value?: string): string => {
  return cleanDisclosureText(value)
    .replace(/HKEX\s+DI\s+reason\s*/gi, '披露原因 ')
    .replace(/\([LSP]\)/gi, '')
    .replace(/\s+·\s+/g, ' · ')
    .trim();
};

const relationshipText = (record: ShareholderChangeRecord): string => {
  return [
    holderLabel(record),
    record.shareholder_type,
    record.title,
    record.detail_summary
  ].join(' ');
};

const isControllerRelated = (record: ShareholderChangeRecord): boolean => {
  return /控股股东|实际控制人|一致行动|controller|controlling/i.test(relationshipText(record));
};

const isManagementRelated = (record: ShareholderChangeRecord): boolean => {
  return /董监高|董事|监事|高管|director|officer|insider|management/i.test(relationshipText(record));
};

const statusAiLabel = (status: ShareholderChangeStatus): string => {
  return {
    plan: '计划阶段',
    progress: '执行中',
    completed: '结果披露',
    other: '事件披露'
  }[status];
};

const aiToneTagColor = (tone: AiInterpretationTone): string => {
  return {
    positive: 'green',
    watch: 'gold',
    risk: 'red',
    neutral: 'blue'
  }[tone];
};

const buildAiInterpretation = (
  record: ShareholderChangeRecord,
  displayName: string
): { tone: AiInterpretationTone; tag: string; title: string; summary: string; points: string[]; followUps: string[] } => {
  const holder = holderLabel(record);
  const ratio = formatRatioDisplay(record.change_ratio);
  const amount = formatAmountDisplay(record);
  const shares = formatShareDisplay(record.change_shares);
  const method = formatChangeMethodDisplay(record);
  const isPlan = record.status === 'plan';
  const isProgress = record.status === 'progress';
  const isCompleted = record.status === 'completed';
  const controller = isControllerRelated(record);
  const management = isManagementRelated(record);
  const ratioScore = ratioSortScore(record);
  const points: string[] = [];
  const followUps: string[] = [];

  let tone: AiInterpretationTone = 'neutral';
  let tag = '需核验';
  let title = `${statusAiLabel(record.status)}，先看执行而不是标题`;

  if (record.direction === 'increase') {
    tone = record.risk_level === 'medium' || isPlan ? 'watch' : 'positive';
    tag = isPlan ? '偏正面·待落地' : '偏正面';
    title = isPlan ? '增持计划偏正面，但强度取决于后续成交' : '增持披露偏正面，关注持续性';
    points.push(controller
      ? `${holder}属于控股股东/实际控制人相关主体，信号更偏向稳定预期与表态。`
      : management
        ? `${holder}属于董监高/管理层相关主体，通常代表内部人信心信号。`
        : `${holder}增持${displayName}，方向上偏正面。`);
    points.push(isPlan
      ? '当前是计划公告，不能等同于已经买入；真正含金量要看完成公告里的成交股数、均价和金额。'
      : isProgress
        ? '当前处于执行进展阶段，信号比单纯计划更实，但仍需跟踪最终完成比例。'
        : isCompleted
          ? '已有结果披露，信号强度高于单纯计划，但仍要核对是否存在终止或未足额完成。'
          : '该事件需要结合原文确认执行状态。');
  } else if (record.direction === 'decrease') {
    tone = 'risk';
    tag = '供给压力';
    title = isPlan ? '减持计划带来潜在供给压力' : '减持披露偏风险，需要看规模和原因';
    points.push(controller
      ? `${holder}为控股股东/实际控制人相关主体，减持对市场情绪影响更敏感。`
      : management
        ? `${holder}为董监高/管理层相关主体，需关注是否与基本面或解禁节奏共振。`
        : `${holder}减持${displayName}，短期更偏风险信号。`);
    points.push(isPlan
      ? '当前是计划阶段，实际冲击取决于窗口期内的减持节奏和成交价格。'
      : '已出现减持结果或进展，需要结合成交价格判断是否形成持续抛压。');
  } else {
    tone = record.risk_level === 'high' ? 'risk' : 'neutral';
    tag = '结构变化';
    title = '持股结构发生变化，方向需要原文确认';
    points.push(`${holder}披露了${displayName}持股变化，但当前方向不是单一增持或减持。`);
    points.push('这类披露可能来自仓位重分类、权益链路变化或多主体合并，建议以原文表格核验。');
  }

  if (ratio) {
    points.push(ratio.includes('→') ? `持股比例为 ${ratio}，应关注变化幅度而不是只看变动后的绝对持股。` : `公告披露比例为 ${ratio}。`);
  }
  if (shares && shares !== '未披露') {
    points.push(`涉及股数 ${shares}${amount && amount !== '未披露' ? `，估算金额 ${amount}` : ''}。`);
  }
  if (!amount || amount === '未披露') {
    followUps.push('金额或价格未披露，打开原文确认资金规模和价格区间。');
  }
  if (method) {
    followUps.push(`核验披露方式：${method}。`);
  }
  if (ratioScore >= 1) {
    followUps.push('比例变化超过 1pct，建议进一步检查是否触发控制权、举牌或持股线变化。');
  }
  if (disclosureCount(record) > 1) {
    followUps.push(`该事件合并了 ${disclosureCount(record)} 条披露，避免把同一权益链路重复计数。`);
  }
  if (record.change_reason) {
    points.push(`公告原因：${record.change_reason}。`);
  }

  return {
    tone,
    tag,
    title,
    summary: `${displayName} · ${directionMeta[record.direction].label} · ${statusAiLabel(record.status)} · ${riskMeta[record.risk_level].label}`,
    points: points.slice(0, 4),
    followUps: followUps.slice(0, 3)
  };
};

const normalizeStockKey = (value?: string): string => {
  const upper = (value || '').trim().toUpperCase();
  const base = upper.replace(/\.HK$/, '');
  if (/^\d{1,5}$/.test(base)) {
    return base.padStart(5, '0');
  }
  return upper;
};

const recordDisplayName = (
  record: ShareholderChangeRecord,
  stockBySymbol: Map<string, Stock>
): string => {
  const metadataName = metadataString(record, 'issuer_name_zh') || metadataString(record, 'name_zh') || metadataString(record, 'company_name_zh');
  if (metadataName) {
    return metadataName;
  }

  const stock = stockBySymbol.get(record.symbol.toUpperCase()) || stockBySymbol.get(normalizeStockKey(record.symbol));
  if (stock?.name && hasChineseText(stock.name)) {
    return stock.name;
  }

  if (hasChineseText(record.name)) {
    return record.name;
  }

  return HK_NAME_ZH_BY_SYMBOL[normalizeStockKey(record.symbol)] || record.name;
};

const recordDisplayTitle = (record: ShareholderChangeRecord, displayName: string): string => {
  const originalName = metadataString(record, 'issuer_name_en') || record.name;
  if (displayName && originalName && displayName !== originalName) {
    return record.title.replace(originalName, displayName);
  }
  return record.title;
};

const formatDate = (value: string): string => {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short'
  });
};

const formatFullDate = (value: string): string => {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'long'
  });
};

const formatTimestamp = (value?: string): string => {
  if (!value) {
    return '--';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

const ShareholderChangeCenter: React.FC<ShareholderChangeCenterProps> = ({ appState, onStockSelect }) => {
  const [market, setMarket] = useState<ShareholderChangeMarket>('A');
  const [direction, setDirection] = useState<ShareholderChangeDirection>('all');
  const [days, setDays] = useState(30);
  const [limit, setLimit] = useState(120);
  const [detailLimit, setDetailLimit] = useState(12);
  const [query, setQuery] = useState('');
  const [riskFilter, setRiskFilter] = useState<RiskFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [sortMode, setSortMode] = useState<SortMode>('time_desc');
  const [result, setResult] = useState<Awaited<ReturnType<typeof scanShareholderChanges>> | null>(null);
  const [selectedRecordKey, setSelectedRecordKey] = useState<string | null>(null);
  const [aiInterpretations, setAiInterpretations] = useState<Record<string, ShareholderChangeInterpretResponse>>({});
  const [aiLoadingKey, setAiLoadingKey] = useState<string | null>(null);
  const [aiError, setAiError] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const lastAutoScanSignatureRef = useRef('');

  const stockBySymbol = useMemo(() => {
    const map = new Map<string, Stock>();
    appState.stocks.forEach(stock => {
      map.set(stock.symbol.toUpperCase(), stock);
      map.set(normalizeStockKey(stock.symbol), stock);
    });
    return map;
  }, [appState.stocks]);

  const requestDetailLimit = useMemo(() => {
    if (market === 'US') {
      return Math.min(limit, US_FORM4_XML_DETAIL_LIMIT);
    }
    if (market === 'HK') {
      return 0;
    }
    return detailLimit;
  }, [detailLimit, limit, market]);

  const loadChanges = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await scanShareholderChanges({
        market,
        days,
        direction,
        limit,
        detail_limit: requestDetailLimit
      });
      setResult(prevResult => {
        if (
          market === 'US'
          && response.records.length === 0
          && hasSecRateLimitWarning(response.warnings)
          && prevResult?.market === 'US'
          && prevResult.records.length > 0
        ) {
          return {
            ...prevResult,
            warnings: [
              'SEC EDGAR 当前触发限流，已保留最近一次成功结果；请稍后再刷新。',
              ...response.warnings,
              ...prevResult.warnings
            ].filter((warning, index, list) => warning && list.indexOf(warning) === index).slice(0, 8)
          };
        }
        return response;
      });
      setSelectedRecordKey(prev => {
        if (market === 'US' && response.records.length === 0 && hasSecRateLimitWarning(response.warnings)) {
          return prev;
        }
        const stillExists = prev && response.records.some(record => getRecordKey(record) === prev);
        return stillExists ? prev : response.records[0] ? getRecordKey(response.records[0]) : null;
      });
    } catch (scanError) {
      console.warn('Shareholder change scan failed:', scanError);
      setError(`${marketMeta[market].label}股东变动扫描服务暂不可用，请稍后重试或检查后端连接。`);
    } finally {
      setLoading(false);
    }
  }, [days, direction, limit, market, requestDetailLimit]);

  useEffect(() => {
    const signature = `${market}:${direction}:${days}:${limit}:${requestDetailLimit}`;
    if (lastAutoScanSignatureRef.current === signature) {
      return;
    }
    lastAutoScanSignatureRef.current = signature;
    void loadChanges();
  }, [days, direction, limit, loadChanges, market, requestDetailLimit]);

  const records = result?.records || [];
  const queryKeywords = useMemo(() => query.trim().split(/\s+/).filter(Boolean), [query]);

  const statusCounts = useMemo(() => {
    const counts: Record<ShareholderChangeStatus, number> = {
      plan: 0,
      progress: 0,
      completed: 0,
      other: 0
    };

    records.forEach(record => {
      if (!recordMatchesKeywords(record, queryKeywords, stockBySymbol)) {
        return;
      }
      if (riskFilter !== 'all' && record.risk_level !== riskFilter) {
        return;
      }
      counts[record.status] += 1;
    });

    return counts;
  }, [queryKeywords, records, riskFilter, stockBySymbol]);

  const statusFilterOptions = useMemo(() => {
    const total = Object.values(statusCounts).reduce((sum, count) => sum + count, 0);
    return statusOptionsBase.map(option => {
      const count = option.value === 'all' ? total : statusCounts[option.value];
      return {
        label: `${option.label} ${count}`,
        value: option.value,
        disabled: option.value !== 'all' && count === 0
      };
    });
  }, [statusCounts]);

  useEffect(() => {
    if (statusFilter !== 'all' && statusCounts[statusFilter] === 0) {
      setStatusFilter('all');
    }
  }, [statusCounts, statusFilter]);

  const filteredRecords = useMemo(() => {
    return records.filter(record => {
      const matchesQuery = recordMatchesKeywords(record, queryKeywords, stockBySymbol);
      const matchesRisk = riskFilter === 'all' || record.risk_level === riskFilter;
      const matchesStatus = statusFilter === 'all' || record.status === statusFilter;
      return matchesQuery && matchesRisk && matchesStatus;
    });
  }, [queryKeywords, records, riskFilter, statusFilter, stockBySymbol]);

  const rankedRecords = useMemo(() => {
    return [...filteredRecords].sort((left, right) => {
      const timeDelta = announcementTimeScore(right) - announcementTimeScore(left);
      if (sortMode === 'ratio_desc') {
        const ratioDelta = ratioSortScore(right) - ratioSortScore(left);
        return ratioDelta || timeDelta || left.symbol.localeCompare(right.symbol);
      }
      if (sortMode === 'amount_desc') {
        const amountDelta = metricSortScore(formatAmountDisplay(right)) - metricSortScore(formatAmountDisplay(left));
        return amountDelta || timeDelta || left.symbol.localeCompare(right.symbol);
      }
      return timeDelta || left.symbol.localeCompare(right.symbol);
    });
  }, [filteredRecords, sortMode]);

  useEffect(() => {
    if (rankedRecords.length === 0) {
      setSelectedRecordKey(null);
      return;
    }

    if (!selectedRecordKey || !rankedRecords.some(record => getRecordKey(record) === selectedRecordKey)) {
      setSelectedRecordKey(getRecordKey(rankedRecords[0]));
    }
  }, [rankedRecords, selectedRecordKey]);

  useEffect(() => {
    setAiError('');
  }, [selectedRecordKey]);

  const selectedRecord = rankedRecords.find(record => getRecordKey(record) === selectedRecordKey) || rankedRecords[0];
  const selectedStock = selectedRecord
    ? stockBySymbol.get(selectedRecord.symbol.toUpperCase()) || stockBySymbol.get(normalizeStockKey(selectedRecord.symbol))
    : undefined;
  const selectedDisplayName = selectedRecord ? recordDisplayName(selectedRecord, stockBySymbol) : '';
  const selectedDisplayTitle = selectedRecord ? recordDisplayTitle(selectedRecord, selectedDisplayName) : '';
  const selectedDetailMetrics = selectedRecord ? buildDetailMetrics(selectedRecord) : [];
  const selectedEvidenceExcerpt = selectedRecord ? formatEvidenceExcerpt(selectedRecord.evidence_excerpt) : '';
  const selectedAiInterpretation = selectedRecord ? buildAiInterpretation(selectedRecord, selectedDisplayName) : null;
  const selectedAiModelInterpretation = selectedRecord ? aiInterpretations[getRecordKey(selectedRecord)] : undefined;
  const highRiskCount = records.filter(record => record.risk_level === 'high').length;
  const decreaseCount = records.filter(record => record.direction === 'decrease').length;
  const increaseCount = records.filter(record => record.direction === 'increase').length;
  const detailRate = result
    ? `${result.detail_success_count}/${result.detail_attempted_count || result.detail_success_count || 0}`
    : '--';
  const activeMarket = marketMeta[market];

  const openAnnouncement = (url: string) => {
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  };

  const handleAiInterpret = useCallback(async (record: ShareholderChangeRecord) => {
    const key = getRecordKey(record);
    setAiLoadingKey(key);
    setAiError('');
    try {
      const interpretation = await interpretShareholderChange({ record, style: 'brief' });
      setAiInterpretations(current => ({
        ...current,
        [key]: interpretation
      }));
    } catch (interpretError) {
      console.warn('Shareholder AI interpretation failed:', interpretError);
      setAiError('AI 解读暂不可用，请检查模型配置或稍后重试。');
    } finally {
      setAiLoadingKey(null);
    }
  }, []);

  const handleSortModeChange = (value: SortMode) => {
    setSortMode(value);
    if (value !== 'time_desc') {
      setDetailLimit(current => Math.max(current, Math.min(limit, 50)));
    }
  };

  return (
    <div className="shareholder-change-shell">
      <div className="shareholder-change-header">
        <div>
          <div className="dashboard-eyebrow">{activeMarket.eyebrow}</div>
          <h2 className="dashboard-title">股东增减持</h2>
          <div className="dashboard-subtitle">{activeMarket.subtitle}</div>
        </div>
        <Space size={8} wrap>
          <Segmented
            value={market}
            onChange={value => {
              setMarket(value as ShareholderChangeMarket);
              setQuery('');
              setRiskFilter('all');
              setStatusFilter('all');
              setResult(null);
              setSelectedRecordKey(null);
              setAiError('');
            }}
            options={[
              { label: 'A 股', value: 'A' },
              { label: '港股', value: 'HK' },
              { label: '美股', value: 'US' }
            ]}
          />
          <Tag color={market === 'A' ? 'red' : market === 'HK' ? 'blue' : 'purple'}>{activeMarket.label}</Tag>
          {result && <Tag color="cyan">{result.provider}</Tag>}
          <Select
            value={days}
            onChange={setDays}
            style={{ width: 104 }}
            options={[
              { label: '近 7 天', value: 7 },
              { label: '近 30 天', value: 30 },
              { label: '近 90 天', value: 90 },
              { label: '近 180 天', value: 180 }
            ]}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadChanges}>
            刷新
          </Button>
        </Space>
      </div>

      <div className="shareholder-change-scanbar">
        <Space size={8} wrap>
          <Select
            value={limit}
            onChange={setLimit}
            style={{ width: 112 }}
            options={[
              { label: '前 40 条', value: 40 },
              { label: '前 80 条', value: 80 },
              { label: '前 120 条', value: 120 },
              { label: '前 200 条', value: 200 }
            ]}
          />
          {market === 'A' ? (
            <Select
              value={detailLimit}
              onChange={setDetailLimit}
              style={{ width: 132 }}
              options={[
                { label: '不解析 PDF', value: 0 },
                { label: '解析 8 条', value: 8 },
                { label: '解析 12 条', value: 12 },
                { label: '解析 24 条', value: 24 },
                { label: '解析 50 条', value: 50 }
              ]}
            />
          ) : (
            <Tag color="blue">
              {market === 'HK' ? '自动解析 HKEX 摘要' : `自动解析 Form 4 XML · 最多 ${requestDetailLimit} 条`}
            </Tag>
          )}
        </Space>
      </div>

      {error && (
        <Alert className="shareholder-change-alert" type="warning" showIcon message={error} />
      )}

      {result?.warnings.length ? (
        <Alert
          className="shareholder-change-alert"
          type="info"
          showIcon
          message={result.warnings[0]}
        />
      ) : null}

      <div className="shareholder-change-summary-grid">
        <div className="metric-tile">
          <div className="metric-label"><FileSearchOutlined /><span>命中公告</span></div>
          <div className="metric-value">{result?.returned_count ?? '--'}</div>
          <div className="metric-note">总命中 {result?.total_found ?? '--'} 条</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><SafetyCertificateOutlined /><span>高风险</span></div>
          <div className="metric-value">{highRiskCount}</div>
          <div className="metric-note">控股股东/董监高/减持计划</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><FallOutlined /><span>减持/增持</span></div>
          <div className="metric-value">{decreaseCount}/{increaseCount}</div>
          <div className="metric-note">方向结构</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><AuditOutlined /><span>{market === 'A' ? 'PDF 明细' : '披露明细'}</span></div>
          <div className="metric-value">{detailRate}</div>
          <div className="metric-note">{activeMarket.source}</div>
        </div>
      </div>

      {result && (
        <Alert
          className="shareholder-change-alert"
          type="info"
          showIcon
          message={result.summary}
          description={result.coverage_note}
        />
      )}

      <div className="shareholder-change-toolbar">
        <Space size={8} wrap>
          <Select
            value={direction}
            onChange={setDirection}
            style={{ width: 112 }}
            options={directionOptions}
          />
          <Select
            value={riskFilter}
            onChange={setRiskFilter}
            style={{ width: 120 }}
            options={[
              { label: '全部风险', value: 'all' },
              { label: '高风险', value: 'high' },
              { label: '中风险', value: 'medium' },
              { label: '低风险', value: 'low' }
            ]}
          />
          <Select
            value={statusFilter}
            onChange={setStatusFilter}
            style={{ width: 148 }}
            options={statusFilterOptions}
          />
          <Select
            value={sortMode}
            onChange={handleSortModeChange}
            style={{ width: 132 }}
            options={[
              { label: '公告时间', value: 'time_desc' },
              { label: '幅度排名', value: 'ratio_desc' },
              { label: '金额排名', value: 'amount_desc' }
            ]}
          />
        </Space>
        <Input.Search
          allowClear
          placeholder={`搜索${activeMarket.label}代码、公司、股东或披露`}
          value={query}
          onChange={event => setQuery(event.target.value)}
          style={{ width: 280 }}
        />
      </div>

      <Spin spinning={loading}>
        <div className="shareholder-change-layout">
          <section className="shareholder-change-list-panel">
            {rankedRecords.length === 0 ? (
              <Empty description={`没有匹配的${activeMarket.label}股东变动披露`} />
            ) : (
              <>
                <div className="shareholder-change-list-head">
                  <span>标的</span>
                  <span>主体/公告</span>
                  <span>变动</span>
                  <span>风险/日期</span>
                </div>
                {rankedRecords.map((record, index) => {
                  const key = getRecordKey(record);
                  const isSelected = selectedRecordKey === key;
                  const displayName = recordDisplayName(record, stockBySymbol);
                  const displayTitle = recordDisplayTitle(record, displayName);
                  const holder = holderLabel(record);
                  const mergedCount = disclosureCount(record);
                  const rowTitle = mergedCount > 1
                    ? `${displayName} ${directionMeta[record.direction].label}：${holder}`
                    : displayTitle;
                  return (
                    <button
                      type="button"
                      className={`shareholder-change-row ${isSelected ? 'selected' : ''}`}
                      key={key}
                      onClick={() => setSelectedRecordKey(key)}
                    >
                      <div className="shareholder-change-issuer">
                        <span className="shareholder-change-rank">#{index + 1}</span>
                        <div>
                          <div className="shareholder-change-symbol">{record.symbol}</div>
                          <div className="shareholder-change-name">{displayName}</div>
                        </div>
                      </div>
                      <div>
                        <div className="shareholder-change-row-title">{rowTitle}</div>
                        <div className="shareholder-change-event-meta">
                          <Tag color={directionMeta[record.direction].color} icon={directionMeta[record.direction].icon}>
                            {directionMeta[record.direction].label}
                          </Tag>
                          <Tag color={statusMeta[record.status].color}>{statusMeta[record.status].label}</Tag>
                          {mergedCount > 1 && <Tag color="blue">合并 {mergedCount} 条</Tag>}
                          <span>{holder}</span>
                          {record.shareholder_type && <span>{record.shareholder_type}</span>}
                        </div>
                      </div>
                      <div>
                        <div className="shareholder-change-main-value">
                          {displayValue(formatRatioDisplay(record.change_ratio) || formatShareDisplay(record.change_shares), directionMeta[record.direction].label)}
                        </div>
                        <div className="shareholder-change-meta">
                          {displayValue(formatChangeMethodDisplay(record) || record.change_period, record.detail_summary || statusMeta[record.status].label)}
                        </div>
                      </div>
                      <div className="shareholder-change-risk-cell">
                        <Tag color={riskMeta[record.risk_level].color}>{riskMeta[record.risk_level].label}</Tag>
                        <span>{formatDate(record.announcement_date)}</span>
                      </div>
                    </button>
                  );
                })}
              </>
            )}
          </section>

          <aside className="shareholder-change-detail-panel">
            {selectedRecord ? (
              <>
                <div className="shareholder-change-detail-head">
                  <div>
                    <div className="shareholder-change-symbol">{selectedRecord.symbol}</div>
                    <h3>{selectedDisplayName}</h3>
                    <Text type="secondary">{selectedRecord.source_name} · {formatTimestamp(result?.generated_at)}</Text>
                  </div>
                  <Space size={6} wrap>
                    <Tag color={directionMeta[selectedRecord.direction].color}>{directionMeta[selectedRecord.direction].label}</Tag>
                    <Tag color={riskMeta[selectedRecord.risk_level].color}>{riskMeta[selectedRecord.risk_level].label}</Tag>
                    {disclosureCount(selectedRecord) > 1 && <Tag color="blue">合并 {disclosureCount(selectedRecord)} 条</Tag>}
                  </Space>
                </div>

                <div className="shareholder-change-detail-date">
                  <CalendarOutlined />
                  <span>{formatFullDate(selectedRecord.announcement_date)}</span>
                  <Tag color={statusMeta[selectedRecord.status].color}>{statusMeta[selectedRecord.status].label}</Tag>
                  <Tag color={selectedRecord.detail_quality === 'full' ? 'green' : selectedRecord.detail_quality === 'partial' ? 'blue' : 'default'}>
                    {detailQualityLabel[selectedRecord.detail_quality] || selectedRecord.detail_source}
                  </Tag>
                </div>

                {selectedAiInterpretation && (
                  <div className={`shareholder-change-ai-brief shareholder-change-ai-brief--${selectedAiInterpretation.tone}`}>
                    <div className="shareholder-change-ai-head">
                      <div>
                        <span><BulbOutlined />规则解读</span>
                        <strong>{selectedAiInterpretation.title}</strong>
                      </div>
                      <Space size={6} wrap>
                        <Tag color={aiToneTagColor(selectedAiInterpretation.tone)}>{selectedAiInterpretation.tag}</Tag>
                        <Button
                          size="small"
                          icon={<BulbOutlined />}
                          loading={aiLoadingKey === getRecordKey(selectedRecord)}
                          onClick={() => handleAiInterpret(selectedRecord)}
                        >
                          {selectedAiModelInterpretation ? '重新解读' : 'AI 解读'}
                        </Button>
                      </Space>
                    </div>
                    <p>{selectedAiInterpretation.summary}</p>
                    <ul>
                      {selectedAiInterpretation.points.map(point => <li key={point}>{point}</li>)}
                    </ul>
                    {selectedAiInterpretation.followUps.length > 0 && (
                      <div className="shareholder-change-ai-follow">
                        {selectedAiInterpretation.followUps.map(item => <span key={item}>{item}</span>)}
                      </div>
                    )}
                  </div>
                )}

                {aiError && (
                  <Alert
                    className="shareholder-change-alert"
                    type="warning"
                    showIcon
                    message={aiError}
                  />
                )}

                {selectedAiModelInterpretation && (
                  <div className={`shareholder-change-ai-brief shareholder-change-ai-brief--${selectedAiModelInterpretation.tone}`}>
                    <div className="shareholder-change-ai-head">
                      <div>
                        <span><BulbOutlined />AI 解读</span>
                        <strong>{selectedAiModelInterpretation.verdict}</strong>
                      </div>
                      <Tag color={aiToneTagColor(selectedAiModelInterpretation.tone)}>
                        {selectedAiModelInterpretation.provider}
                      </Tag>
                    </div>
                    <p>{selectedAiModelInterpretation.summary}</p>
                    {selectedAiModelInterpretation.points.length > 0 && (
                      <ul>
                        {selectedAiModelInterpretation.points.map(point => <li key={point}>{point}</li>)}
                      </ul>
                    )}
                    <div className="shareholder-change-ai-follow">
                      {[
                        ...selectedAiModelInterpretation.risks.map(item => `风险：${item}`),
                        ...selectedAiModelInterpretation.questions.map(item => `问题：${item}`),
                        ...selectedAiModelInterpretation.actions.map(item => `动作：${item}`)
                      ].slice(0, 8).map(item => <span key={item}>{item}</span>)}
                    </div>
                    <div className="shareholder-change-ai-foot">
                      <span>{selectedAiModelInterpretation.model}</span>
                      <span>置信度 {Math.round(selectedAiModelInterpretation.confidence * 100)}%</span>
                    </div>
                  </div>
                )}

                <div className="shareholder-change-metric-grid">
                  {selectedDetailMetrics.map(item => (
                    <div
                      key={item.label}
                      className={`shareholder-change-metric-box ${item.primary ? 'shareholder-change-metric-box--primary' : ''}`}
                    >
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>

                <div className="shareholder-change-detail-section">
                  <div className="shareholder-change-box-title"><TeamOutlined />股东/人员</div>
                  <div className="shareholder-change-chip-grid">
                    {(holderNamesForDisplay(selectedRecord).length
                      ? holderNamesForDisplay(selectedRecord)
                      : [holderLabel(selectedRecord)])
                      .filter(Boolean)
                      .map(item => <span key={item}>{item}</span>)}
                  </div>
                </div>

                <div className="shareholder-change-detail-section">
                  <div className="shareholder-change-box-title"><AuditOutlined />持股变化</div>
                  <div className="shareholder-change-holding-strip">
                    <div>
                      <span>变动前</span>
                      <strong>{displayValue(selectedRecord.holding_before)}</strong>
                    </div>
                    <div>
                      <span>变动后</span>
                      <strong>{displayValue(selectedRecord.holding_after)}</strong>
                    </div>
                  </div>
                </div>

                {selectedRecord.change_reason && (
                  <div className="shareholder-change-detail-section">
                    <div className="shareholder-change-box-title">原因</div>
                    <Text>{selectedRecord.change_reason}</Text>
                  </div>
                )}

                <div className="shareholder-change-detail-section">
                  <div className="shareholder-change-box-title"><FileSearchOutlined />公告</div>
                  <div className="shareholder-change-title">{selectedDisplayTitle}</div>
                  {selectedEvidenceExcerpt && (
                    <div className="shareholder-change-excerpt">{selectedEvidenceExcerpt}</div>
                  )}
                </div>

                <div className="shareholder-change-actions">
                  <Button icon={<LinkOutlined />} onClick={() => openAnnouncement(selectedRecord.url)}>
                    原文披露
                  </Button>
                  {selectedStock && (
                    <Button type="primary" onClick={() => onStockSelect(selectedStock)}>
                      个股专区
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <Empty description="选择一条公告查看明细" />
            )}
          </aside>
        </div>
      </Spin>
    </div>
  );
};

export default ShareholderChangeCenter;
