import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Checkbox,
  Empty,
  Input,
  Pagination,
  Progress,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload
} from 'antd';
import {
  ApiOutlined,
  AuditOutlined,
  BarChartOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  ExportOutlined,
  FileDoneOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  LinkOutlined,
  MessageOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  ThunderboltOutlined,
  UploadOutlined,
  WarningOutlined,
  SearchOutlined,
  DownloadOutlined,
  RobotOutlined,
  GlobalOutlined,
  BankOutlined,
  ReadOutlined
} from '@ant-design/icons';
import { AppState, ViewType } from '../types';
import {
  analyzeProfessionalReport,
  ingestProfessionalReportUrl,
  ingestWorkbenchReportFile,
  listProfessionalMetrics,
  listProfessionalReports,
  listWorkbenchDownloads,
  ProfessionalCitation,
  ProfessionalEvalRunResult,
  ProfessionalMetricRecord,
  ProfessionalRagQueryResult,
  ProfessionalReportAnalysis,
  ProfessionalReportRecord,
  ProfessionalReportType,
  queryProfessionalRag,
  runProfessionalEval,
  uploadProfessionalReport,
  WorkbenchDownloadFile,
  WorkbenchDownloadsResponse
} from '../services/researchService';
import {
  ResearchWorkbenchJob,
  ResearchWorkbenchSearchItem,
  searchResearchWorkbenchReports,
  startResearchWorkbenchDownload,
  ResearchMarket,
  ResearchReportItem,
  ResearchVisionAnalysis,
  searchResearchReports,
  createWorkbenchPreview,
  visionAnalyzeReport
} from '../services/researchService';
import type { DataQuality } from '../types';
import './centers/researchAgent.css';
import ShareButton from './common/ShareButton';
import DataQualityBanner from './common/DataQualityBanner';

const { Text, Title } = Typography;
const { TextArea, Search } = Input;

interface WorkbenchJob {
  id: string;
  status: string;
}

interface WorkbenchStatus {
  credentialsAvailable: boolean;
  jobs: WorkbenchJob[];
}

interface ResearchWorkbenchProps {
  appState: AppState;
  onViewChange: (view: ViewType) => void;
}

interface AiDraftPayload {
  prompt: string;
  source?: string;
  references?: string[];
  skill?: string;
  createdAt?: string;
}

const START_COMMAND = 'npm run backend';
const INSTALL_COMMAND = 'npm run research-workbench:install';
const AI_DRAFT_STORAGE_KEY = 'deepfocus.aiDraft.v1';
const WORKBENCH_DOWNLOAD_OUT = 'downloads/海外投行报告';
const DEFAULT_CRAWL_SEARCH_PAGES = 100;

const reportTypeLabels: Record<string, string> = {
  annual: '年报',
  semiannual: '半年报',
  quarterly: '季报',
  research: '研报',
  transcript: '电话会',
  other: '资料'
};

const jobStatusLabel: Record<string, string> = {
  running: '运行中',
  stopping: '停止中',
  stopped: '已停止',
  completed: '完成',
  failed: '失败'
};

const defaultQuestions = [
  '这份报告里最能改变估值的三条证据是什么？',
  '收入、毛利率、现金流和资本开支有什么红旗？',
  '哪些结论必须回到原文引用核验？'
];

const normalizeBaseUrl = (value: string) => value.replace(/\/+$/, '');

const defaultWorkbenchUrl = () => {
  const hostname = window.location.hostname || '127.0.0.1';
  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
  return `${protocol}//${hostname}:8300/research-workbench`;
};

const writeAiDraft = (payload: AiDraftPayload) => {
  const normalized: AiDraftPayload = {
    ...payload,
    prompt: payload.prompt.trim(),
    source: payload.source || 'research-workbench',
    createdAt: new Date().toISOString()
  };
  window.localStorage.setItem(AI_DRAFT_STORAGE_KEY, JSON.stringify(normalized));
};

const formatDate = (value?: string | null) => {
  if (!value) return '未记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

const compactNumber = (value: number) => {
  if (value >= 10000) return `${(value / 10000).toFixed(1)}万`;
  return value.toLocaleString('zh-CN');
};

const formatFileSize = (value: number) => {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
};

const cleanReportTitle = (value: string) => (
  value
    .replace(/\.(pdf|docx?|txt|md|markdown)$/i, '')
    .replace(/\s+/g, ' ')
    .trim()
);

const cleanUserFacingText = (value?: string | null) => (
  String(value || '')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\s*>+\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/^\s*[-–—]{3,}\s*$/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
    .replace(/~~(.*?)~~/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/\|/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
);

const isUserFacingSectionLabel = (value: string) => /^(投资判断|核心观点|关键数字|关键指标|预期差|情景推演|投资逻辑|催化剂|跟踪清单|推翻条件|风险|风险提示|证据质量|待确认|下一步问题)$/i
  .test(value.replace(/[:：]\s*$/, '').trim());

const splitReadableLines = (value?: string | null, limit = 5) => (
  cleanUserFacingText(value)
    .split(/\n+|；|;|(?<=。)/)
    .map(item => item.trim())
    .filter(item => item && !isUserFacingSectionLabel(item))
    .slice(0, limit)
);

const metricInsightText = (metric: ProfessionalMetricRecord) => (
  `${metric.metric_label}：${metricDisplayValue(metric)}${metric.period ? `（${metric.period}）` : ''}`
);

const evidenceBaseName = (value: string) => {
  const trimmed = value.trim();
  const parts = trimmed.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || trimmed;
};

const isUsefulEvidenceName = (value?: string | null) => {
  const baseName = evidenceBaseName(String(value || ''));
  if (!baseName) return false;
  if (baseName.startsWith('.') || baseName.startsWith('._')) return false;
  return !/^(?:\.DS_Store|Thumbs\.db|desktop\.ini|__MACOSX)$/i.test(baseName);
};

const isUsefulWorkbenchDownload = (file: WorkbenchDownloadFile) => (
  isUsefulEvidenceName(file.name) && /\.(pdf|docx?|txt|md|markdown)$/i.test(evidenceBaseName(file.name))
);

const isUsefulProfessionalReport = (report: ProfessionalReportRecord) => (
  isUsefulEvidenceName(report.metadata?.filename || report.title)
);

const normalizeSymbolInput = (value: string) => {
  const trimmed = value.trim();
  if (!trimmed) return '';
  return /^[a-z0-9._:-]+$/i.test(trimmed) ? trimmed.toUpperCase() : trimmed;
};

const symbolParamFromInput = (value?: string | null) => {
  const normalized = normalizeSymbolInput(value || '');
  return /^[A-Z0-9._:-]+$/.test(normalized) ? normalized : undefined;
};

const reportTypeFromName = (value: string): ProfessionalReportType => {
  if (/10-k|annual|年报/i.test(value)) return 'annual';
  if (/10-q|quarter|季报|一季|三季/i.test(value)) return 'quarterly';
  if (/half|semi|半年/i.test(value)) return 'semiannual';
  if (/transcript|call|电话会/i.test(value)) return 'transcript';
  if (/research|研报|深度|覆盖/i.test(value)) return 'research';
  return 'other';
};

const metricDisplayValue = (metric: ProfessionalMetricRecord) => {
  if (metric.raw_value) return metric.raw_value;
  if (metric.value == null) return '未识别';
  const suffix = metric.unit ? ` ${metric.unit}` : '';
  return `${metric.value.toLocaleString('zh-CN')}${suffix}`;
};

const confidenceColor = (value: number) => {
  if (value >= 0.75) return '#13a36b';
  if (value >= 0.5) return '#c98213';
  return '#d1495b';
};

const citationReference = (citation: ProfessionalCitation) => (
  `${citation.report_title}${citation.page ? ` p.${citation.page}` : ''} · ${citation.citation_id}`
);

const crawlItemKey = (item: ResearchWorkbenchSearchItem) => (
  item.fileId || item.topicId || item.name
);

// 海外投行报告多为图片型 PDF（投行 PPT 截图，无文字层），RAG 入库需要可抽取文本——
// 把后端「报告文本为空」明确翻译给用户，区别于真正的失败（预览/下载仍可用）。
const analyzeErrorMessage = (error: any, fallback: string) => {
  const detail = String(error?.response?.data?.detail || error?.message || '');
  return `AI 分析失败：${detail || fallback}`;
};

// 图片型 PDF（无文字层）入库时后端返回「报告文本为空」——据此自动改走视觉解读。
const isEmptyTextError = (error: any) => (
  /文本为空|无法入库/.test(String(error?.response?.data?.detail || error?.message || ''))
);

const ResearchWorkbench: React.FC<ResearchWorkbenchProps> = ({ appState, onViewChange }) => {
  const { message } = AntdApp.useApp();
  const selectedStock = appState.selectedStock;
  const initialSymbol = selectedStock?.symbol || '';
  const workbenchUrl = useMemo(
    () => normalizeBaseUrl(process.env.REACT_APP_RESEARCH_WORKBENCH_URL || defaultWorkbenchUrl()),
    []
  );
  const workbenchOrigin = useMemo(() => {
    try {
      return new URL(workbenchUrl).origin;
    } catch {
      return '';
    }
  }, [workbenchUrl]);
  const frameUrl = `${workbenchUrl}/`;
  const apiOrigin = useMemo(() => workbenchUrl.replace(/\/research-workbench\/?$/, ''), [workbenchUrl]);

  const [mode, setMode] = useState<'quick' | 'pro'>('quick');

  // 快速研读（搜索 · 在线预览 · 一键AI分析）
  const [quickKeyword, setQuickKeyword] = useState(selectedStock?.name || initialSymbol || '');
  const [quickMarket, setQuickMarket] = useState<'auto' | ResearchMarket>('auto');
  const [quickLoading, setQuickLoading] = useState(false);
  const [overseaLoading, setOverseaLoading] = useState(false);
  const [eastmoneyResults, setEastmoneyResults] = useState<ResearchReportItem[]>([]);
  const [overseaResults, setOverseaResults] = useState<ResearchWorkbenchSearchItem[]>([]);
  const [quickWarnings, setQuickWarnings] = useState<string[]>([]);
  const [quickDq, setQuickDq] = useState<DataQuality | null>(null);
  const [quickSearched, setQuickSearched] = useState(false);
  const [selectedQuickKey, setSelectedQuickKey] = useState<string>('');
  const [previewSrc, setPreviewSrc] = useState<string>('');
  const [previewTitle, setPreviewTitle] = useState<string>('');
  const [previewMeta, setPreviewMeta] = useState<string>('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string>('');
  const [currentPdfUrl, setCurrentPdfUrl] = useState<string>('');
  const [quickAnalysis, setQuickAnalysis] = useState<ProfessionalReportAnalysis | null>(null);
  const [quickVision, setQuickVision] = useState<ResearchVisionAnalysis | null>(null);
  const [quickAnalyzing, setQuickAnalyzing] = useState(false);
  const [quickAnalyzeStep, setQuickAnalyzeStep] = useState<string>('');
  const quickSearchSeq = useRef(0);

  const [status, setStatus] = useState<WorkbenchStatus | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [checking, setChecking] = useState(true);
  const [showLegacyWorkbench, setShowLegacyWorkbench] = useState(false);
  const [downloads, setDownloads] = useState<WorkbenchDownloadFile[]>([]);
  const [downloadSummary, setDownloadSummary] = useState<WorkbenchDownloadsResponse['summary'] | null>(null);
  const [loadingDownloads, setLoadingDownloads] = useState(false);
  const [ingestingFile, setIngestingFile] = useState<string | null>(null);
  const [crawlKeyword, setCrawlKeyword] = useState(selectedStock?.name || initialSymbol);
  const [crawlTag, setCrawlTag] = useState('海外投行报告');
  const [crawlPageSize, setCrawlPageSize] = useState(10);
  const [crawlPage, setCrawlPage] = useState(1);
  const [crawlResults, setCrawlResults] = useState<ResearchWorkbenchSearchItem[]>([]);
  const [selectedCrawlKeys, setSelectedCrawlKeys] = useState<string[]>([]);
  const [searchingCrawl, setSearchingCrawl] = useState(false);
  const [downloadingCrawl, setDownloadingCrawl] = useState(false);
  const [crawlJob, setCrawlJob] = useState<ResearchWorkbenchJob | null>(null);
  const crawlSearchSeq = useRef(0);

  const [symbolDraft, setSymbolDraft] = useState(initialSymbol);
  const [activeSymbol, setActiveSymbol] = useState(initialSymbol);
  const [reportTypeFilter, setReportTypeFilter] = useState<string>('all');
  const [reports, setReports] = useState<ProfessionalReportRecord[]>([]);
  const [selectedReportId, setSelectedReportId] = useState<string>('');
  const [metrics, setMetrics] = useState<ProfessionalMetricRecord[]>([]);
  const [analysis, setAnalysis] = useState<ProfessionalReportAnalysis | null>(null);
  const [ragResult, setRagResult] = useState<ProfessionalRagQueryResult | null>(null);
  const [evalResult, setEvalResult] = useState<ProfessionalEvalRunResult | null>(null);
  const [ragQuestion, setRagQuestion] = useState(defaultQuestions[0]);
  const [aiBridgeDraft, setAiBridgeDraft] = useState('请基于当前研报、指标、引用和反证风险，生成一版可交给投研任务中心的执行 Brief。');

  const [loadingReports, setLoadingReports] = useState(false);
  const [loadingMetrics, setLoadingMetrics] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [urlDraft, setUrlDraft] = useState('');
  const [ingestingUrl, setIngestingUrl] = useState(false);

  const visibleReports = useMemo(() => (
    reports.filter(isUsefulProfessionalReport)
  ), [reports]);

  const filteredReports = useMemo(() => (
    reportTypeFilter === 'all'
      ? visibleReports
      : visibleReports.filter(report => report.report_type === reportTypeFilter)
  ), [reportTypeFilter, visibleReports]);

  const selectedReport = useMemo(() => (
    visibleReports.find(report => report.id === selectedReportId) || filteredReports[0] || visibleReports[0] || null
  ), [filteredReports, selectedReportId, visibleReports]);

  const reportStats = useMemo(() => {
    const metricCount = visibleReports.reduce((sum, report) => sum + report.metrics_count, 0);
    const chunkCount = visibleReports.reduce((sum, report) => sum + report.chunks_count, 0);
    const symbols = new Set(visibleReports.map(report => report.symbol).filter(Boolean));
    return {
      reports: visibleReports.length,
      metrics: metricCount,
      chunks: chunkCount,
      symbols: symbols.size
    };
  }, [visibleReports]);

  const indexedFilenames = useMemo(() => new Set(
    visibleReports
      .map(report => String(report.metadata?.filename || report.title || '').trim())
      .filter(Boolean)
  ), [visibleReports]);

  const pendingDownloads = useMemo(() => (
    downloads.filter(file => (
      isUsefulWorkbenchDownload(file) &&
      !indexedFilenames.has(file.name) &&
      !indexedFilenames.has(cleanReportTitle(file.name))
    ))
  ), [downloads, indexedFilenames]);

  const selectedCrawlItems = useMemo(() => (
    crawlResults.filter(item => selectedCrawlKeys.includes(crawlItemKey(item)))
  ), [crawlResults, selectedCrawlKeys]);

  const crawlTotalPages = useMemo(() => (
    Math.max(1, Math.ceil(crawlResults.length / crawlPageSize))
  ), [crawlPageSize, crawlResults.length]);

  const pagedCrawlResults = useMemo(() => {
    const safePage = Math.min(crawlPage, crawlTotalPages);
    const start = (safePage - 1) * crawlPageSize;
    return crawlResults.slice(start, start + crawlPageSize);
  }, [crawlPage, crawlPageSize, crawlResults, crawlTotalPages]);

  const currentCrawlPageKeys = useMemo(() => (
    pagedCrawlResults.map(crawlItemKey)
  ), [pagedCrawlResults]);

  const currentCrawlPageSelected = currentCrawlPageKeys.length > 0 && currentCrawlPageKeys.every(key => selectedCrawlKeys.includes(key));

  const selectedReportCoverage = selectedReport
    ? Math.min(100, Math.round((selectedReport.chunks_count / 18) * 100))
    : 0;

  const icReadinessScore = useMemo(() => {
    let score = 0;
    if (reportStats.reports > 0) score += 24;
    if (reportStats.chunks > 0) score += 22;
    if (reportStats.metrics > 0) score += 14;
    if (selectedReport) score += 10;
    if (analysis) score += 16;
    if (ragResult?.citations.length) score += 8;
    if (evalResult?.total) score += 6;
    if (!reportStats.reports && pendingDownloads.length) score = 32;
    return Math.min(100, score);
  }, [analysis, evalResult, pendingDownloads.length, ragResult, reportStats, selectedReport]);

  const nextAction = useMemo(() => {
    if (!visibleReports.length && pendingDownloads.length) return '入库最近研报';
    if (!visibleReports.length) return '上传第一份研报';
    if (!selectedReport) return '选择报告';
    if (!analysis) return '生成投委会摘要';
    if (!ragResult?.citations.length) return '核验证据引用';
    if (!evalResult?.total) return '运行引用评测';
    return '发送投研任务';
  }, [analysis, evalResult, pendingDownloads.length, ragResult, selectedReport, visibleReports.length]);

  const missionHint = useMemo(() => {
    if (!visibleReports.length && pendingDownloads.length) return `${pendingDownloads.length} 份抓取文件等待入库，先把资料转成可引用报告。`;
    if (!visibleReports.length) return '先抓取或上传一份报告，建立可复核的证据链。';
    if (!selectedReport) return '从左侧证据流选择一份报告进入研读。';
    if (!analysis) return '把当前报告压缩成摘要、风险和追问。';
    if (!ragResult?.citations.length) return '对最关键问题做引用问答，确认结论不是空转。';
    if (!evalResult?.total) return '跑一次引用评测，给投委会留出质量闸门。';
    return '证据、引用和评测已经闭环，可以交给投研任务中心。';
  }, [analysis, evalResult, pendingDownloads.length, ragResult, selectedReport, visibleReports.length]);

  const pipelineSteps = useMemo(() => ([
    { key: 'ingest', label: '入库', done: reportStats.reports > 0 },
    { key: 'index', label: '索引', done: reportStats.chunks > 0 },
    { key: 'review', label: '摘要', done: Boolean(analysis) },
    { key: 'cite', label: '引用', done: Boolean(ragResult?.citations.length) },
    { key: 'gate', label: '闸门', done: Boolean(evalResult?.total) }
  ]), [analysis, evalResult, ragResult, reportStats.chunks, reportStats.reports]);

  const latestJob = status?.jobs?.[0];

  const latestCrawlStatus = crawlJob?.status || latestJob?.status;

  const checkStatus = useCallback(async () => {
    setChecking(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 2500);

    try {
      const response = await fetch(`${workbenchUrl}/api/status`, {
        cache: 'no-store',
        signal: controller.signal
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setStatus(await response.json());
      setOnline(true);
    } catch (error) {
      setStatus(null);
      setOnline(false);
    } finally {
      window.clearTimeout(timer);
      setChecking(false);
    }
  }, [workbenchUrl]);

  const loadReports = useCallback(async (symbol = activeSymbol) => {
    setLoadingReports(true);
    try {
      const result = await listProfessionalReports({
        symbol: symbol.trim() || undefined,
        limit: 60
      });
      const firstVisibleReport = result.find(isUsefulProfessionalReport);
      setReports(result);
      setSelectedReportId(previous => (
        result.some(report => report.id === previous && isUsefulProfessionalReport(report)) ? previous : firstVisibleReport?.id || ''
      ));
      if (!firstVisibleReport) {
        setAnalysis(null);
        setMetrics([]);
        setRagResult(null);
      }
    } catch (error) {
      message.error('专业研报库读取失败');
    } finally {
      setLoadingReports(false);
    }
  }, [activeSymbol, message]);

  const loadMetrics = useCallback(async (report: ProfessionalReportRecord | null) => {
    if (!report) return;
    setLoadingMetrics(true);
    try {
      const result = await listProfessionalMetrics({ report_id: report.id, limit: 40 });
      setMetrics(result);
    } catch (error) {
      setMetrics([]);
      message.warning('指标抽取结果暂不可用');
    } finally {
      setLoadingMetrics(false);
    }
  }, [message]);

  const loadDownloads = useCallback(async () => {
    setLoadingDownloads(true);
    try {
      const result = await listWorkbenchDownloads(WORKBENCH_DOWNLOAD_OUT);
      setDownloads((result.files || []).filter(isUsefulWorkbenchDownload));
      setDownloadSummary(result.summary || null);
    } catch (error) {
      // 抓取舱服务未启动时静默降级——状态已通过 online / 服务未启动提示呈现，无需打扰。
      setDownloads([]);
      setDownloadSummary(null);
    } finally {
      setLoadingDownloads(false);
    }
  }, []);

  useEffect(() => {
    void checkStatus();
    void loadDownloads();
    const timer = window.setInterval(() => {
      void checkStatus();
      void loadDownloads();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [checkStatus, loadDownloads]);

  useEffect(() => {
    void loadReports(initialSymbol);
  }, [initialSymbol, loadReports]);

  useEffect(() => {
    if (selectedReport) {
      void loadMetrics(selectedReport);
      setAnalysis(null);
      setRagResult(null);
      setEvalResult(null);
    }
  }, [loadMetrics, selectedReport?.id]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (workbenchOrigin && event.origin !== workbenchOrigin) {
        return;
      }
      const data = event.data;
      if (!data || typeof data !== 'object' || data.type !== 'deepfocus:send-to-ai') {
        return;
      }
      const prompt = typeof data.prompt === 'string' ? data.prompt.trim() : '';
      if (!prompt) {
        message.warning('研报工作台没有传入可发送的问题');
        return;
      }
      const references = Array.isArray(data.references)
        ? data.references.map((item: unknown) => String(item || '').trim()).filter(Boolean)
        : [];
      writeAiDraft({
        prompt,
        references,
        skill: typeof data.skill === 'string' ? data.skill : undefined
      });
      message.success('已同步到主 AI 对话');
      onViewChange('home');
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [message, onViewChange, workbenchOrigin]);

  const applySymbolFilter = (value = symbolDraft) => {
    const normalized = normalizeSymbolInput(value);
    setActiveSymbol(normalized);
    setSymbolDraft(normalized);
    void loadReports(symbolParamFromInput(normalized) || '');
    if (!crawlKeyword.trim()) {
      setCrawlKeyword(normalized);
    }
  };

  const runCrawlSearch = useCallback(async (options: { silent?: boolean } = {}) => {
    const searchSeq = crawlSearchSeq.current + 1;
    crawlSearchSeq.current = searchSeq;
    const keyword = crawlKeyword.trim();
    setSearchingCrawl(true);
    try {
      const result = await searchResearchWorkbenchReports({
        keyword,
        tag: crawlTag,
        out: WORKBENCH_DOWNLOAD_OUT,
        searchPages: DEFAULT_CRAWL_SEARCH_PAGES,
        resultLimit: 0
      });
      if (searchSeq !== crawlSearchSeq.current) return;
      setCrawlResults(result.items || []);
      setCrawlPage(1);
      setSelectedCrawlKeys((result.items || []).slice(0, 5).map(crawlItemKey));
      if (options.silent) return;
      if (result.items?.length) {
        message.success(`找到 ${result.items.length} 条资料，已分页展示`);
      } else {
        message.info(keyword ? '没有找到匹配资料，可以换关键词或放宽标签' : '该标签下暂未找到资料');
      }
    } catch (error: any) {
      if (searchSeq !== crawlSearchSeq.current || options.silent) return;
      const detail = error?.response?.data?.detail || '请确认资料抓取服务和凭证正常';
      message.error(`搜索失败：${detail}`);
    } finally {
      if (searchSeq === crawlSearchSeq.current) {
        setSearchingCrawl(false);
      }
    }
  }, [crawlKeyword, crawlTag, message]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void runCrawlSearch({ silent: true });
    }, 650);
    return () => window.clearTimeout(timer);
  }, [runCrawlSearch]);

  const toggleCrawlResult = (item: ResearchWorkbenchSearchItem, checked: boolean) => {
    const key = crawlItemKey(item);
    setSelectedCrawlKeys(previous => (
      checked
        ? Array.from(new Set([...previous, key]))
        : previous.filter(itemKey => itemKey !== key)
    ));
  };

  const toggleCurrentCrawlPage = () => {
    setSelectedCrawlKeys(previous => {
      if (currentCrawlPageSelected) {
        return previous.filter(key => !currentCrawlPageKeys.includes(key));
      }
      return Array.from(new Set([...previous, ...currentCrawlPageKeys]));
    });
  };

  const downloadSelectedCrawlResults = async () => {
    if (!selectedCrawlItems.length) {
      message.warning('先勾选要下载的资料');
      return;
    }
    setDownloadingCrawl(true);
    try {
      const job = await startResearchWorkbenchDownload(selectedCrawlItems, {
        tag: crawlTag,
        out: WORKBENCH_DOWNLOAD_OUT
      });
      setCrawlJob(job);
      message.success(`已提交 ${selectedCrawlItems.length} 份资料下载`);
      void checkStatus();
      window.setTimeout(() => {
        void loadDownloads();
        void checkStatus();
      }, 3500);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认资料抓取服务和登录凭证正常';
      message.error(`下载任务提交失败：${detail}`);
    } finally {
      setDownloadingCrawl(false);
    }
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const report = await uploadProfessionalReport({
        file,
        symbol: symbolParamFromInput(activeSymbol || symbolDraft),
        title: file.name,
        report_type: reportTypeFromName(file.name)
      });
      message.success('已入库并完成索引');
      setReports(previous => [report, ...previous.filter(item => item.id !== report.id)]);
      setSelectedReportId(report.id);
      setActiveSymbol(report.symbol || activeSymbol);
      setSymbolDraft(report.symbol || activeSymbol);
    } catch (error) {
      message.error('上传或解析失败');
    } finally {
      setUploading(false);
    }
  };

  const ingestUrl = async (value = urlDraft) => {
    const url = value.trim();
    if (!url) {
      message.info('粘贴研报 URL 后再入库');
      return;
    }
    setIngestingUrl(true);
    try {
      const report = await ingestProfessionalReportUrl({
        url,
        symbol: symbolParamFromInput(activeSymbol || symbolDraft),
        report_type: reportTypeFromName(url),
        tags: ['URL入库']
      });
      message.success('URL 研报已入库并建立引用索引');
      setReports(previous => [report, ...previous.filter(item => item.id !== report.id)]);
      setSelectedReportId(report.id);
      setActiveSymbol(report.symbol || activeSymbol);
      setSymbolDraft(report.symbol || activeSymbol);
      setUrlDraft('');
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认链接可公开访问，且是 PDF、HTML 或文本资料';
      message.error(`URL 入库失败：${detail}`);
    } finally {
      setIngestingUrl(false);
    }
  };

  const ingestDownload = async (file: WorkbenchDownloadFile) => {
    setIngestingFile(file.name);
    try {
      const report = await ingestWorkbenchReportFile({
        filename: file.name,
        out: WORKBENCH_DOWNLOAD_OUT,
        symbol: symbolParamFromInput(activeSymbol || symbolDraft),
        title: cleanReportTitle(file.name),
        report_type: reportTypeFromName(file.name),
        tags: ['海外投行报告']
      });
      message.success('抓取文件已入库并建立引用索引');
      setReports(previous => [report, ...previous.filter(item => item.id !== report.id)]);
      setSelectedReportId(report.id);
      setActiveSymbol(report.symbol || activeSymbol);
      setSymbolDraft(report.symbol || activeSymbol);
      void loadDownloads();
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认文件格式、大小和后端服务';
      message.error(`入库失败：${detail}`);
    } finally {
      setIngestingFile(null);
    }
  };

  const ingestRecentDownloads = async () => {
    const candidates = pendingDownloads.slice(0, 3);
    if (!candidates.length) {
      message.info('没有待入库的抓取文件');
      return;
    }
    for (const file of candidates) {
      await ingestDownload(file);
    }
  };

  const runAnalysis = async () => {
    if (!selectedReport) {
      message.warning('先选择一份研报');
      return;
    }
    setAnalyzing(true);
    try {
      const result = await analyzeProfessionalReport(selectedReport.id, {
        focus: ragQuestion || aiBridgeDraft,
        use_cloud_model: false
      });
      setAnalysis(result);
      setMetrics(result.key_metrics.length ? result.key_metrics : metrics);
    } catch (error) {
      message.error('研报复核失败');
    } finally {
      setAnalyzing(false);
    }
  };

  const runRagQuery = async () => {
    const question = ragQuestion.trim();
    if (!question) {
      message.warning('输入要核验的问题');
      return;
    }
    setQuerying(true);
    try {
      const result = await queryProfessionalRag({
        question,
        symbol: symbolParamFromInput(activeSymbol) || selectedReport?.symbol || undefined,
        report_id: selectedReport?.id,
        top_k: 6,
        use_cloud_model: false
      });
      setRagResult(result);
    } catch (error) {
      message.error('引用问答失败');
    } finally {
      setQuerying(false);
    }
  };

  const runEval = async () => {
    if (!selectedReport) {
      message.warning('先选择一份研报');
      return;
    }
    setEvaluating(true);
    try {
      const result = await runProfessionalEval({ report_id: selectedReport.id, top_k: 5 });
      setEvalResult(result);
    } catch (error) {
      message.error('引用评测失败');
    } finally {
      setEvaluating(false);
    }
  };

  const sendBridgeDraft = () => {
    const prompt = aiBridgeDraft.trim();
    if (!prompt) {
      message.warning('先输入要交给主 AI 的问题');
      return;
    }
    const references = [
      selectedReport ? `${selectedReport.title} · ${selectedReport.id}` : '',
      ...(ragResult?.citations || []).slice(0, 4).map(citationReference)
    ].filter(Boolean);
    const evidenceContext = [
      selectedReport ? `当前研报：${selectedReport.title}` : '',
      analysis?.summary ? `研报复核摘要：${analysis.summary}` : '',
      ragResult?.answer ? `引用问答结论：${ragResult.answer}` : ''
    ].filter(Boolean).join('\n');
    writeAiDraft({
      prompt: `${prompt}${evidenceContext ? `\n\n${evidenceContext}` : ''}`,
      references,
      skill: 'ProfessionalResearch'
    });
    message.success('已发送到主 AI 对话');
    onViewChange('home');
  };

  const handleNextAction = () => {
    if (!visibleReports.length && pendingDownloads.length) {
      void ingestRecentDownloads();
      return;
    }
    if (!visibleReports.length) {
      message.info('请上传研报，或在左侧资料抓取区搜索并下载文件');
      return;
    }
    if (!selectedReport) {
      setSelectedReportId(filteredReports[0]?.id || visibleReports[0]?.id || '');
      return;
    }
    if (!analysis) {
      void runAnalysis();
      return;
    }
    if (!ragResult?.citations.length) {
      void runRagQuery();
      return;
    }
    if (!evalResult?.total) {
      void runEval();
      return;
    }
    sendBridgeDraft();
  };

  const uploadProps = {
    accept: '.pdf,.txt,.md,.doc,.docx',
    showUploadList: false,
    beforeUpload: (file: File) => {
      void handleUpload(file);
      return Upload.LIST_IGNORE;
    }
  };

  // ===== 快速研读（搜索 · 在线预览 · 一键AI分析）=====

  const selectedEastmoney = useMemo(() => (
    selectedQuickKey.startsWith('em:')
      ? eastmoneyResults.find(item => `em:${item.id}` === selectedQuickKey) || null
      : null
  ), [selectedQuickKey, eastmoneyResults]);

  const selectedOversea = useMemo(() => (
    selectedQuickKey.startsWith('ov:')
      ? overseaResults.find(item => `ov:${crawlItemKey(item)}` === selectedQuickKey) || null
      : null
  ), [selectedQuickKey, overseaResults]);

  const selectEastmoney = (item: ResearchReportItem) => {
    setSelectedQuickKey(`em:${item.id}`);
    setQuickAnalysis(null); setQuickVision(null);
    setPreviewTitle(item.title);
    setPreviewMeta([item.org, item.date, item.rating].filter(Boolean).join(' · '));
    setCurrentPdfUrl(item.pdf_url);
    setPreviewError('');
    setPreviewLoading(false);
    setPreviewSrc(`${apiOrigin}${item.preview_url}`);
  };

  const selectOversea = async (item: ResearchWorkbenchSearchItem) => {
    const fileId = item.fileId || '';
    setSelectedQuickKey(`ov:${crawlItemKey(item)}`);
    setQuickAnalysis(null); setQuickVision(null);
    setPreviewTitle(cleanReportTitle(item.name));
    setPreviewMeta([item.hashtag, formatDate(item.createTime || item.topicCreateTime)].filter(Boolean).join(' · '));
    setCurrentPdfUrl('');
    setPreviewError('');
    setPreviewSrc('');
    if (!fileId) {
      setPreviewError('该条目没有可在线预览的文件，请在专业模式按主题下载。');
      return;
    }
    setPreviewLoading(true);
    try {
      const link = await createWorkbenchPreview({ fileId, name: item.name });
      setPreviewSrc(`${workbenchUrl}${link.previewUrl}`);
    } catch (error: any) {
      setPreviewError(error?.message || '在线预览失败：请先在高级抓取舱登录知识星球');
    } finally {
      setPreviewLoading(false);
    }
  };

  const runQuickSearch = useCallback(async (kw?: string) => {
    const keyword = (kw ?? quickKeyword).trim();
    if (!keyword) {
      message.info('输入公司、代码或主题，如 特斯拉、贵州茅台');
      return;
    }
    const seq = quickSearchSeq.current + 1;
    quickSearchSeq.current = seq;
    setQuickLoading(true);
    setQuickSearched(true);
    setQuickAnalysis(null); setQuickVision(null);
    setPreviewSrc('');
    setPreviewError('');
    setPreviewTitle('');
    setPreviewMeta('');
    setSelectedQuickKey('');
    setEastmoneyResults([]);
    setOverseaResults([]);
    let hadEastmoney = false;
    try {
      const resp = await searchResearchReports(keyword, quickMarket).catch(() => null);
      if (seq !== quickSearchSeq.current) return;
      const items = resp?.items || [];
      hadEastmoney = items.length > 0;
      setEastmoneyResults(items);
      setQuickWarnings(resp?.warnings || []);
      setQuickDq(resp?.data_quality || null);
      if (items.length) {
        selectEastmoney(items[0]);
      }
    } finally {
      if (seq === quickSearchSeq.current) setQuickLoading(false);
    }
    if (online !== false) {
      setOverseaLoading(true);
      searchResearchWorkbenchReports({
        keyword,
        tag: crawlTag,
        out: WORKBENCH_DOWNLOAD_OUT,
        searchPages: 30,
        resultLimit: 0
      }).then(result => {
        if (seq !== quickSearchSeq.current) return;
        const items = result.items || [];
        setOverseaResults(items);
        if (!hadEastmoney && items[0]) {
          void selectOversea(items[0]);
        }
      }).catch(() => {
        if (seq === quickSearchSeq.current) setOverseaResults([]);
      }).finally(() => {
        if (seq === quickSearchSeq.current) setOverseaLoading(false);
      });
    }
  }, [quickKeyword, quickMarket, online, crawlTag, message]);

  // 图片型研报（无文字层）走多模态视觉解读：渲染页面图像→视觉模型读图出观点（无逐句溯源）。
  const runVisionFallback = async (payload: { pdf_url?: string; workbench_filename?: string; title?: string; symbol?: string }) => {
    setQuickAnalyzeStep('图片型研报，改用 AI 视觉解读…');
    try {
      const vision = await visionAnalyzeReport({
        ...payload,
        workbench_out: WORKBENCH_DOWNLOAD_OUT,
        max_pages: 6
      });
      setQuickVision(vision);
      message.success('AI 视觉解读完成');
    } catch (visionError: any) {
      const detail = visionError?.response?.data?.detail || visionError?.message || '请稍后重试';
      message.error(`视觉解读失败：${detail}`);
    }
  };

  const analyzeOverseaSelected = async (item: ResearchWorkbenchSearchItem) => {
    if (online === false) {
      message.warning('海外投行抓取服务未启动，无法分析该来源');
      return;
    }
    setQuickAnalyzing(true);
    setQuickAnalysis(null); setQuickVision(null);
    try {
      setQuickAnalyzeStep('拉取研报中…');
      await startResearchWorkbenchDownload([item], { tag: crawlTag, out: WORKBENCH_DOWNLOAD_OUT });
      const target = cleanReportTitle(item.name);
      let file: WorkbenchDownloadFile | undefined;
      for (let attempt = 0; attempt < 12 && !file; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 2000));
        const downloads = await listWorkbenchDownloads(WORKBENCH_DOWNLOAD_OUT).catch(() => null);
        file = downloads?.files?.find(candidate => candidate.name === item.name || cleanReportTitle(candidate.name) === target);
      }
      if (!file) throw new Error('下载尚未完成，请稍后或在专业模式重试');
      setQuickAnalyzeStep('入库与解析中…');
      const report = await ingestWorkbenchReportFile({
        filename: file.name,
        out: WORKBENCH_DOWNLOAD_OUT,
        title: target,
        report_type: 'research',
        tags: ['研报快速研读', '海外投行报告']
      });
      setQuickAnalyzeStep('AI 分析中…');
      const result = await analyzeProfessionalReport(report.id, { use_cloud_model: true });
      setQuickAnalysis(result);
      setReports(previous => [report, ...previous.filter(existing => existing.id !== report.id)]);
      setSelectedReportId(report.id);
      message.success('AI 分析完成');
    } catch (error: any) {
      if (isEmptyTextError(error)) {
        await runVisionFallback({ workbench_filename: item.name, title: cleanReportTitle(item.name) });
      } else {
        message.error(analyzeErrorMessage(error, '请稍后重试'));
      }
    } finally {
      setQuickAnalyzing(false);
      setQuickAnalyzeStep('');
    }
  };

  const runQuickAnalyze = async () => {
    if (selectedEastmoney) {
      setQuickAnalyzing(true);
      setQuickAnalysis(null); setQuickVision(null);
      try {
        setQuickAnalyzeStep('入库与解析中…');
        const report = await ingestProfessionalReportUrl({
          url: selectedEastmoney.pdf_url,
          symbol: symbolParamFromInput(selectedEastmoney.symbol || ''),
          report_type: 'research',
          tags: ['研报快速研读', '东方财富']
        });
        setQuickAnalyzeStep('AI 分析中…');
        const result = await analyzeProfessionalReport(report.id, {
          focus: '请基于研报正文给出投资判断、关键证据、红旗与风险，以及需回原文核验的追问。',
          use_cloud_model: true
        });
        setQuickAnalysis(result);
        setReports(previous => [report, ...previous.filter(existing => existing.id !== report.id)]);
        setSelectedReportId(report.id);
        message.success('AI 分析完成');
      } catch (error: any) {
        if (isEmptyTextError(error)) {
          await runVisionFallback({
            pdf_url: selectedEastmoney.pdf_url,
            title: selectedEastmoney.title,
            symbol: selectedEastmoney.symbol || undefined
          });
        } else {
          message.error(analyzeErrorMessage(error, '请确认链接可访问后重试'));
        }
      } finally {
        setQuickAnalyzing(false);
        setQuickAnalyzeStep('');
      }
      return;
    }
    if (selectedOversea) {
      await analyzeOverseaSelected(selectedOversea);
      return;
    }
    message.info('先选择一篇研报');
  };

  const downloadCurrent = () => {
    if (currentPdfUrl) {
      window.open(currentPdfUrl, '_blank', 'noopener,noreferrer');
      return;
    }
    if (previewSrc) {
      window.open(previewSrc, '_blank', 'noopener,noreferrer');
      return;
    }
    message.info('先选择一篇研报');
  };

  const didAutoQuickSearch = useRef(false);
  useEffect(() => {
    if (!didAutoQuickSearch.current && mode === 'quick' && quickKeyword.trim()) {
      didAutoQuickSearch.current = true;
      void runQuickSearch(quickKeyword);
    }
  }, [mode, quickKeyword, runQuickSearch]);

  const renderQuickView = () => (
    <div className="research-quick">
      <div className="research-quick-topbar">
        <div className="research-quick-brand">
          <span className="research-desk-mark"><ReadOutlined /></span>
          <div>
            <Text className="dashboard-eyebrow">RESEARCH QUICK READ</Text>
            <Title level={3}>研报快速研读</Title>
          </div>
        </div>
        <div className="research-quick-searchbar">
          <Input
            size="large"
            allowClear
            prefix={<SearchOutlined />}
            value={quickKeyword}
            onChange={event => setQuickKeyword(event.target.value)}
            onPressEnter={() => void runQuickSearch()}
            placeholder="搜索公司 / 代码 / 主题，如 特斯拉、贵州茅台、宁德时代"
          />
          <Segmented
            value={quickMarket}
            onChange={value => setQuickMarket(value as 'auto' | ResearchMarket)}
            options={[
              { label: '自动', value: 'auto' },
              { label: 'A股', value: 'CN' },
              { label: '港股', value: 'HK' },
              { label: '美股', value: 'US' }
            ]}
          />
          <Button type="primary" size="large" icon={<SearchOutlined />} loading={quickLoading} onClick={() => void runQuickSearch()}>
            搜索
          </Button>
          <Tooltip title="切换到投委会级专业模式（入库 / 引用核验 / 评测 / 抓取舱）">
            <Button size="large" icon={<AuditOutlined />} onClick={() => setMode('pro')}>专业模式</Button>
          </Tooltip>
        </div>
      </div>

      <div className="research-quick-body">
        <aside className="research-quick-results">
          <DataQualityBanner quality={quickDq} />
          {quickWarnings.length ? (
            <Alert type="info" showIcon style={{ marginBottom: 10 }} message={quickWarnings[0]} />
          ) : null}

          <div className="research-quick-group-title">
            <span><ThunderboltOutlined /> 东方财富直连</span>
            <em>{quickLoading ? '…' : eastmoneyResults.length}</em>
          </div>
          {quickLoading ? (
            <div className="research-desk-loading compact"><Spin /></div>
          ) : eastmoneyResults.length ? (
            eastmoneyResults.map(item => (
              <button
                key={`em:${item.id}`}
                type="button"
                className={`research-quick-row${selectedQuickKey === `em:${item.id}` ? ' is-active' : ''}`}
                onClick={() => selectEastmoney(item)}
              >
                <span className="research-quick-row-title">{item.title}</span>
                <span className="research-quick-row-meta">
                  <Tag color="cyan"><BankOutlined /> {item.org || '机构'}</Tag>
                  {item.rating ? <Tag color="green">{item.rating}</Tag> : null}
                  <span>{item.date}</span>
                </span>
              </button>
            ))
          ) : quickSearched ? (
            <div className="research-empty-inline research-empty-inline--compact">
              <FileTextOutlined /><span>东财暂无该标的研报</span>
            </div>
          ) : null}

          <div className="research-quick-group-title">
            <span><GlobalOutlined /> 海外投行报告</span>
            <em>{overseaLoading ? '…' : overseaResults.length}</em>
          </div>
          {overseaLoading ? (
            <div className="research-desk-loading compact"><Spin /></div>
          ) : overseaResults.length ? (
            overseaResults.slice(0, 40).map(item => {
              const key = `ov:${crawlItemKey(item)}`;
              return (
                <button
                  key={key}
                  type="button"
                  className={`research-quick-row${selectedQuickKey === key ? ' is-active' : ''}`}
                  onClick={() => void selectOversea(item)}
                >
                  <span className="research-quick-row-title">{cleanReportTitle(item.name)}</span>
                  <span className="research-quick-row-meta">
                    <Tag color="purple"><GlobalOutlined /> 海外投行</Tag>
                    {item.hashtag ? <Tag>{item.hashtag}</Tag> : null}
                    <span>{formatDate(item.createTime || item.topicCreateTime)}</span>
                  </span>
                </button>
              );
            })
          ) : quickSearched ? (
            <div className="research-empty-inline research-empty-inline--compact">
              <GlobalOutlined /><span>{online === false ? '海外投行抓取服务未启动' : '无匹配海外投行报告'}</span>
            </div>
          ) : null}

          {!quickSearched ? (
            <div className="research-empty-inline">
              <SearchOutlined /><span>输入公司或代码，开始检索研报</span>
            </div>
          ) : null}
        </aside>

        <section className="research-quick-preview">
          <div className="research-quick-preview-head">
            <div className="research-quick-preview-meta">
              <strong>{previewTitle || '在线预览'}</strong>
              {previewMeta ? <small>{previewMeta}</small> : null}
            </div>
            <Button icon={<DownloadOutlined />} disabled={!currentPdfUrl && !previewSrc} onClick={downloadCurrent}>
              下载原文
            </Button>
          </div>
          <div className="research-quick-preview-frame">
            {previewLoading ? (
              <div className="research-quick-preview-empty"><Spin /><span>正在拉取在线预览…</span></div>
            ) : previewError ? (
              <div className="research-quick-preview-empty"><WarningOutlined /><span>{previewError}</span></div>
            ) : previewSrc ? (
              <iframe title="研报在线预览" src={previewSrc} />
            ) : (
              <div className="research-quick-preview-empty">
                <ReadOutlined /><span>从左侧选择一篇研报，在此在线预览原文</span>
              </div>
            )}
          </div>
        </section>

        <aside className="research-quick-ai">
          <div className="research-quick-ai-head">
            <div>
              <Text className="dashboard-eyebrow">ONE-CLICK AI</Text>
              <strong>一键 AI 分析</strong>
            </div>
            {quickVision ? (
              <Tag color="purple">视觉解读 {(quickVision.confidence * 100).toFixed(0)}%</Tag>
            ) : quickAnalysis ? (
              <Tag color={quickAnalysis.confidence >= 0.7 ? 'green' : quickAnalysis.confidence >= 0.45 ? 'orange' : 'red'}>
                置信度 {(quickAnalysis.confidence * 100).toFixed(0)}%
              </Tag>
            ) : null}
          </div>
          <Button
            type="primary"
            size="large"
            block
            icon={<RobotOutlined />}
            loading={quickAnalyzing}
            disabled={!selectedQuickKey}
            onClick={() => void runQuickAnalyze()}
          >
            {quickAnalyzing ? (quickAnalyzeStep || '分析中…') : '一键 AI 分析'}
          </Button>

          {quickVision ? (
            <div className="research-quick-ai-body">
              <Tag color="purple" style={{ marginBottom: 2 }}>AI 视觉解读 · 读图生成 · 无逐句溯源</Tag>
              <div className="research-analysis-brief">
                <strong>{quickVision.summary || '已生成视觉解读'}</strong>
                <div className="research-analysis-points">
                  {quickVision.key_points.slice(0, 6).map((point, index) => (
                    <span key={`${index}-${point}`}>{point}</span>
                  ))}
                </div>
              </div>
              <div className="research-metric-grid">
                {quickVision.rating ? (
                  <div className="research-metric-tile"><span>评级</span><strong>{quickVision.rating}</strong><small>视觉读取</small></div>
                ) : null}
                {quickVision.target_price ? (
                  <div className="research-metric-tile"><span>目标价</span><strong>{quickVision.target_price}</strong><small>视觉读取</small></div>
                ) : null}
                <div className="research-metric-tile"><span>已读页数</span><strong>{quickVision.pages_analyzed}</strong><small>{quickVision.provider}</small></div>
              </div>
              {quickVision.risks.length ? (
                <div className="research-flag-grid">
                  <div>
                    <span>风险</span>
                    {quickVision.risks.slice(0, 4).map(risk => (
                      <p key={risk}>{cleanUserFacingText(risk)}</p>
                    ))}
                  </div>
                </div>
              ) : null}
              <Alert type="warning" showIcon style={{ marginTop: 4 }} message={quickVision.disclaimer} />
              <Space wrap>
                <ShareButton target={() => ({ title: previewTitle || '研报视觉解读', summary: quickVision.summary, byline: '由 DeepFocus 研报视觉解读生成' })} />
                <Button
                  icon={<SendOutlined />}
                  onClick={() => {
                    writeAiDraft({ prompt: `${previewTitle}（视觉解读）\n\n${quickVision.summary}`, references: [previewTitle].filter(Boolean), skill: 'ProfessionalResearch' });
                    message.success('已发送到主 AI 对话');
                    onViewChange('home');
                  }}
                >
                  发送给 Agent
                </Button>
              </Space>
            </div>
          ) : quickAnalysis ? (
            <div className="research-quick-ai-body">
              <div className="research-analysis-brief">
                <strong>{splitReadableLines(quickAnalysis.summary, 1)[0] || '已生成研报分析'}</strong>
                <div className="research-analysis-points">
                  {splitReadableLines(quickAnalysis.summary, 5).slice(1).map((point, index) => (
                    <span key={`${index}-${point}`}>{point}</span>
                  ))}
                </div>
              </div>
              <div className="research-flag-grid">
                <div>
                  <span>红旗</span>
                  {(quickAnalysis.quality_flags.length ? quickAnalysis.quality_flags : ['未发现明显解析红旗']).slice(0, 4).map(flag => (
                    <p key={flag}>{cleanUserFacingText(flag)}</p>
                  ))}
                </div>
                <div>
                  <span>风险</span>
                  {(quickAnalysis.risks.length ? quickAnalysis.risks : ['等待进一步分析']).slice(0, 4).map(risk => (
                    <p key={risk}>{cleanUserFacingText(risk)}</p>
                  ))}
                </div>
              </div>
              {quickAnalysis.key_metrics.length ? (
                <div className="research-metric-grid">
                  {quickAnalysis.key_metrics.slice(0, 6).map(metric => (
                    <div key={metric.id} className="research-metric-tile">
                      <span>{metric.metric_label}</span>
                      <strong>{metricDisplayValue(metric)}</strong>
                      <small>{metric.period || ''}{metric.source_page ? ` · p.${metric.source_page}` : ''}</small>
                    </div>
                  ))}
                </div>
              ) : null}
              <Space wrap>
                <ShareButton target={() => ({ title: previewTitle || '研报分析', summary: quickAnalysis.summary, byline: '由 DeepFocus 研报快速研读生成' })} />
                <Button icon={<AuditOutlined />} onClick={() => setMode('pro')}>转专业模式深入</Button>
                <Button
                  icon={<SendOutlined />}
                  onClick={() => {
                    writeAiDraft({ prompt: `${previewTitle}\n\n${quickAnalysis.summary}`, references: [previewTitle].filter(Boolean), skill: 'ProfessionalResearch' });
                    message.success('已发送到主 AI 对话');
                    onViewChange('home');
                  }}
                >
                  发送给 Agent
                </Button>
              </Space>
            </div>
          ) : (
            <div className="research-quick-ai-empty">
              <RobotOutlined />
              <span>
                {selectedQuickKey
                  ? '点击「一键 AI 分析」，自动入库→解析→生成投资判断/风险/指标，无需手动下载。'
                  : '先从左侧选择一篇研报'}
              </span>
            </div>
          )}
        </aside>
      </div>
    </div>
  );

  return (
    <div className="research-desk-page">
      {mode === 'quick' ? renderQuickView() : (
      <>
      <div className="research-desk-topbar">
        <div className="research-desk-title">
          <span className="research-desk-mark">
            <AuditOutlined />
          </span>
          <div>
            <Text className="dashboard-eyebrow">RESEARCH DILIGENCE DESK</Text>
            <Title level={3}>投委会级研报工作台</Title>
            <Text className="research-desk-subtitle">证据先入库，再摘要、核引和评测。</Text>
          </div>
        </div>
        <div className="research-desk-actions">
          <Search
            className="research-symbol-search"
            value={symbolDraft}
            onChange={event => setSymbolDraft(event.target.value)}
            onSearch={applySymbolFilter}
            allowClear
            placeholder="代码 / 公司 / 主题"
            enterButton="筛选"
          />
          <Select
            value={reportTypeFilter}
            onChange={setReportTypeFilter}
            className="research-type-select"
            options={[
              { value: 'all', label: '全部类型' },
              ...Object.entries(reportTypeLabels).map(([value, label]) => ({ value, label }))
            ]}
          />
          <Search
            className="research-url-search"
            value={urlDraft}
            onChange={event => setUrlDraft(event.target.value)}
            onSearch={value => void ingestUrl(value)}
            allowClear
            loading={ingestingUrl}
            placeholder="粘贴 PDF/HTML 研报 URL"
            enterButton="URL入库"
          />
          <Upload {...uploadProps}>
            <Button icon={<UploadOutlined />} loading={uploading}>入库文件</Button>
          </Upload>
          <Tooltip title="刷新研报库与抓取队列">
            <Button
              icon={<ReloadOutlined spin={loadingReports || loadingDownloads} />}
              onClick={() => {
                void loadReports();
                void loadDownloads();
              }}
            />
          </Tooltip>
          <Tooltip title="返回研报快速研读（搜索 · 在线预览 · 一键AI分析）">
            <Button icon={<ReadOutlined />} onClick={() => setMode('quick')}>快速研读</Button>
          </Tooltip>
          <Button icon={<MessageOutlined />} onClick={() => onViewChange('home')}>
            Agent
          </Button>
        </div>
      </div>

      <div className="research-mission-strip">
        <div className="research-readiness-card">
          <span>IC READINESS</span>
          <strong>{icReadinessScore}</strong>
          <Progress percent={icReadinessScore} size="small" showInfo={false} strokeColor={confidenceColor(icReadinessScore / 100)} />
          <small>{selectedReport ? `${selectedReportCoverage}% 引用覆盖` : pendingDownloads.length ? '资料待入库' : '等待证据'}</small>
        </div>
        <div className="research-mission-main">
          <div className="research-mission-copy">
            <Text className="dashboard-eyebrow">NEXT BEST ACTION</Text>
            <strong>{nextAction}</strong>
            <span>{missionHint}</span>
          </div>
          <div className="research-pipeline-rail" aria-label="研报工作流">
            {pipelineSteps.map((step, index) => (
              <div key={step.key} className={`research-pipeline-step${step.done ? ' is-done' : ''}`}>
                <span>{index + 1}</span>
                <strong>{step.label}</strong>
              </div>
            ))}
          </div>
        </div>
        <div className="research-mission-stats" aria-label="证据链统计">
          <div>
            <DatabaseOutlined />
            <span>研报</span>
            <strong>{reportStats.reports}</strong>
          </div>
          <div>
            <FileTextOutlined />
            <span>引用块</span>
            <strong>{compactNumber(reportStats.chunks)}</strong>
          </div>
          <div>
            <BarChartOutlined />
            <span>指标</span>
            <strong>{compactNumber(reportStats.metrics)}</strong>
          </div>
          <div>
            <SafetyCertificateOutlined />
            <span>待入库</span>
            <strong>{pendingDownloads.length}</strong>
          </div>
        </div>
        <Button type="primary" size="large" icon={<ThunderboltOutlined />} loading={Boolean(ingestingFile)} onClick={handleNextAction}>
          {nextAction}
        </Button>
      </div>

      <div className="research-desk-layout">
        <section className="research-desk-panel research-report-library">
          <div className="research-panel-head">
            <div>
              <Text className="dashboard-eyebrow">EVIDENCE FLOW</Text>
              <strong>证据流</strong>
            </div>
            <Tag color={activeSymbol ? 'blue' : 'default'}>{activeSymbol || '全部标的'}</Tag>
          </div>
          <div className="research-report-list">
            <div className="research-crawl-card">
              <div className="research-library-section-title">
                <span>资料抓取</span>
                <em>{latestCrawlStatus ? (jobStatusLabel[latestCrawlStatus] || latestCrawlStatus) : online === false ? '服务未启动' : online ? '已连接' : '待连接'}</em>
              </div>
              {online === false && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="抓取服务未启动"
                  description="研报抓取依赖的工作台服务当前不可用，立即搜索 / 下载选中暂时停用。上传、索引、摘要、RAG 检索与评测不受影响，仍可正常使用。"
                />
              )}
              <div className="research-crawl-fields">
                <Input
                  value={crawlKeyword}
                  onChange={event => setCrawlKeyword(event.target.value)}
                  onPressEnter={() => void runCrawlSearch()}
                  placeholder="公司、行业、英文标题"
                />
                <Input
                  value={crawlTag}
                  onChange={event => setCrawlTag(event.target.value)}
                  placeholder="标签，如 海外投行报告"
                />
                <div className="research-crawl-meta">
                  <Input
                    type="number"
                    min={5}
                    max={50}
                    value={crawlPageSize}
                    onChange={event => {
                      setCrawlPageSize(Math.min(50, Math.max(5, Number(event.target.value) || 10)));
                      setCrawlPage(1);
                    }}
                    prefix="每页"
                  />
                  <div className="research-crawl-mode">
                    自动搜100页 · 时间倒序
                  </div>
                </div>
                <div className="research-crawl-actions">
                  <Button type="primary" icon={<FileSearchOutlined />} loading={searchingCrawl} disabled={online === false} onClick={() => void runCrawlSearch()}>
                    立即搜索
                  </Button>
                  <Button
                    icon={<DatabaseOutlined />}
                    loading={downloadingCrawl}
                    disabled={online === false || !selectedCrawlItems.length}
                    onClick={() => void downloadSelectedCrawlResults()}
                  >
                    下载选中
                  </Button>
                  <Tooltip title="打开旧抓取舱处理扫码、curl 和高级参数">
                    <Button icon={<ApiOutlined />} onClick={() => setShowLegacyWorkbench(value => !value)}>
                      高级
                    </Button>
                  </Tooltip>
                </div>
              </div>
              {crawlResults.length ? (
                <div className="research-crawl-results">
                  <div className="research-crawl-result-head">
                    <span>{crawlResults.length} 条命中 · 第 {Math.min(crawlPage, crawlTotalPages)} / {crawlTotalPages} 页</span>
                    <Space size={6}>
                      <Button size="small" onClick={toggleCurrentCrawlPage}>
                        {currentCrawlPageSelected ? '取消本页' : '选本页'}
                      </Button>
                      {selectedCrawlKeys.length ? (
                        <Button size="small" onClick={() => setSelectedCrawlKeys([])}>
                          清空
                        </Button>
                      ) : null}
                    </Space>
                  </div>
                  {pagedCrawlResults.map(item => {
                    const key = crawlItemKey(item);
                    return (
                      <label className="research-crawl-result" key={key}>
                        <Checkbox
                          checked={selectedCrawlKeys.includes(key)}
                          onChange={event => toggleCrawlResult(item, event.target.checked)}
                        />
                        <span>
                          <strong>{cleanReportTitle(item.name)}</strong>
                          <small>
                            {[
                              item.hashtag,
                              item.size ? formatFileSize(item.size) : '',
                              formatDate(item.createTime || item.topicCreateTime)
                            ].filter(Boolean).join(' · ')}
                          </small>
                        </span>
                      </label>
                    );
                  })}
                  {crawlResults.length > crawlPageSize ? (
                    <Pagination
                      className="research-crawl-pagination"
                      size="small"
                      current={Math.min(crawlPage, crawlTotalPages)}
                      pageSize={crawlPageSize}
                      total={crawlResults.length}
                      showSizeChanger={false}
                      onChange={page => setCrawlPage(page)}
                    />
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="research-library-divider" />
            <div className="research-library-section-title">
              <span>待入库下载</span>
              <em>{loadingDownloads ? '...' : pendingDownloads.length}</em>
            </div>
            {loadingDownloads ? (
              <div className="research-desk-loading compact"><Spin /></div>
            ) : pendingDownloads.length ? (
              pendingDownloads.slice(0, 6).map(file => (
                <div className="research-download-row" key={file.name}>
                  <button type="button" onClick={() => window.open(`${workbenchUrl}/${WORKBENCH_DOWNLOAD_OUT}/${encodeURIComponent(file.name)}`, '_blank', 'noopener,noreferrer')}>
                    <span>{cleanReportTitle(file.name)}</span>
                    <small>{formatFileSize(file.size)} · {formatDate(file.mtime)}</small>
                  </button>
                  <Button
                    size="small"
                    type="primary"
                    loading={ingestingFile === file.name}
                    onClick={() => void ingestDownload(file)}
                  >
                    入库
                  </Button>
                </div>
              ))
            ) : (
              <div className="research-empty-inline research-empty-inline--compact">
                <CheckCircleOutlined />
                <span>{downloads.length ? '下载文件已全部入库' : '暂无待入库下载'}</span>
              </div>
            )}

            <div className="research-library-divider" />
            <div className="research-library-section-title">
              <span>已索引研报</span>
              <em>{filteredReports.length}</em>
            </div>
            {loadingReports ? (
              <div className="research-desk-loading"><Spin /></div>
            ) : filteredReports.length ? (
              filteredReports.map(report => {
                const active = report.id === selectedReport?.id;
                return (
                  <button
                    key={report.id}
                    type="button"
                    className={`research-report-row${active ? ' is-active' : ''}`}
                    onClick={() => setSelectedReportId(report.id)}
                  >
                    <span className="research-report-row-title">{report.title}</span>
                    <span className="research-report-row-meta">
                      <Tag color="cyan">{reportTypeLabels[report.report_type] || report.report_type}</Tag>
                      {report.symbol && <Tag>{report.symbol}</Tag>}
                      {report.period && <Tag>{report.period}</Tag>}
                    </span>
                    <span className="research-report-row-foot">
                      <span>{report.metrics_count} 指标</span>
                      <span>{report.chunks_count} 引用块</span>
                      <span>{formatDate(report.updated_at)}</span>
                    </span>
                  </button>
                );
              })
            ) : (
              <div className="research-empty-inline">
                <FileTextOutlined />
                <span>{activeSymbol ? '该标的暂无入库研报' : '研报库暂无资料'}</span>
              </div>
            )}
          </div>
        </section>

        <section className="research-desk-panel research-review-stage">
          {selectedReport ? (
            <>
              <div className="research-selected-head">
                <div>
                  <Text className="dashboard-eyebrow">SELECTED REPORT</Text>
                  <Title level={4}>{selectedReport.title}</Title>
                  <Space size={6} wrap>
                    <Tag color="cyan">{reportTypeLabels[selectedReport.report_type] || selectedReport.report_type}</Tag>
                    {selectedReport.symbol && <Tag>{selectedReport.symbol}</Tag>}
                    {selectedReport.period && <Tag>{selectedReport.period}</Tag>}
                    <Tag>{compactNumber(selectedReport.char_count)} 字符</Tag>
                  </Space>
                </div>
                <Space wrap>
                  <Button type="primary" icon={<FileDoneOutlined />} loading={analyzing} onClick={runAnalysis}>
                    生成投委会摘要
                  </Button>
                  <Button icon={<ExperimentOutlined />} loading={evaluating} onClick={runEval}>
                    引用评测
                  </Button>
                </Space>
              </div>

              <div className="research-review-grid">
                <div className="research-analysis-block">
                  <div className="research-block-head">
                    <strong>投资委员会摘要</strong>
                    {analysis && (
                      <Tag color={analysis.confidence >= 0.7 ? 'green' : analysis.confidence >= 0.45 ? 'orange' : 'red'}>
                        置信度 {(analysis.confidence * 100).toFixed(0)}%
                      </Tag>
                    )}
                  </div>
                  {analyzing ? (
                    <div className="research-desk-loading"><Spin /></div>
                  ) : analysis ? (
                    <>
                      <div className="research-analysis-brief">
                        <strong>{splitReadableLines(analysis.summary, 1)[0] || '已生成研报摘要'}</strong>
                        <div className="research-analysis-points">
                          {[
                            ...splitReadableLines(analysis.summary, 4).slice(1),
                            ...analysis.key_metrics.slice(0, 2).map(metricInsightText)
                          ].slice(0, 4).map((point, index) => (
                            <span key={`${index}-${point}`}>{point}</span>
                          ))}
                        </div>
                      </div>
                      <div style={{ marginTop: 8 }}>
                        <ShareButton target={() => ({ title: selectedReport?.title || '研报复核', summary: analysis.summary, byline: '由 DeepFocus 投研工作台生成' })} />
                      </div>
                      <div className="research-ic-thesis-row">
                        <div>
                          <span>Base Case</span>
                          <strong>{analysis.confidence >= 0.7 ? '可进入投委会讨论' : '需要补证据'}</strong>
                        </div>
                        <div>
                          <span>Evidence</span>
                          <strong>{analysis.citations.length} 条引用</strong>
                        </div>
                        <div>
                          <span>Discipline</span>
                          <strong>{analysis.quality_flags.length || analysis.risks.length} 个红旗/风险</strong>
                        </div>
                      </div>
                      <div className="research-flag-grid">
                        <div>
                          <span>红旗</span>
                          {(analysis.quality_flags.length ? analysis.quality_flags : ['未发现明显解析红旗']).slice(0, 4).map(flag => (
                            <p key={flag}>{cleanUserFacingText(flag)}</p>
                          ))}
                        </div>
                        <div>
                          <span>风险</span>
                          {(analysis.risks.length ? analysis.risks : ['等待进一步分析']).slice(0, 4).map(risk => (
                            <p key={risk}>{cleanUserFacingText(risk)}</p>
                          ))}
                        </div>
                        <div>
                          <span>追问</span>
                          {(analysis.follow_up_questions.length ? analysis.follow_up_questions : defaultQuestions).slice(0, 4).map(question => (
                            <button key={question} type="button" onClick={() => setRagQuestion(question)}>
                              {cleanUserFacingText(question)}
                            </button>
                          ))}
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="research-review-placeholder">
                      <FileDoneOutlined />
                      <strong>等待生成投委会摘要</strong>
                      <span>摘要会把结论、关键证据、红旗、风险和追问压缩到可复核的 IC Memo 结构。</span>
                      <Button type="primary" icon={<FileDoneOutlined />} loading={analyzing} onClick={runAnalysis}>
                        生成摘要
                      </Button>
                    </div>
                  )}
                </div>

                <div className="research-metrics-block">
                  <div className="research-block-head">
                    <strong>关键指标</strong>
                    <Tag>{loadingMetrics ? '读取中' : `${metrics.length} 项`}</Tag>
                  </div>
                  {loadingMetrics ? (
                    <div className="research-desk-loading"><Spin /></div>
                  ) : metrics.length ? (
                    <div className="research-metric-grid">
                      {metrics.slice(0, 12).map(metric => (
                        <div key={metric.id} className="research-metric-tile">
                          <span>{metric.metric_label}</span>
                          <strong>{metricDisplayValue(metric)}</strong>
                          <small>
                            {metric.period || selectedReport.period || 'period n/a'}
                            {metric.source_page ? ` · p.${metric.source_page}` : ''}
                          </small>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无结构化指标" />
                  )}
                </div>
              </div>

              {(analysis?.citations.length || ragResult?.citations.length) ? (
                <div className="research-citation-strip">
                  <div className="research-block-head">
                    <strong>证据账本</strong>
                    <Tag color="blue">{(ragResult?.citations.length || analysis?.citations.length || 0)} 条引用</Tag>
                  </div>
                  <div className="research-citation-list">
                    {(ragResult?.citations.length ? ragResult.citations : analysis?.citations || []).slice(0, 6).map(citation => (
                      <a
                        key={citation.citation_id}
                        className="research-citation-row"
                        href="#top"
                        onClick={event => event.preventDefault()}
                      >
                        <span>{citation.title}</span>
                        <small>{citationReference(citation)} · score {citation.score.toFixed(2)}</small>
                      </a>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="research-onboarding-stage">
              <div className="research-onboarding-copy">
                <Text className="dashboard-eyebrow">FIRST ACTION</Text>
                <Title level={3}>{pendingDownloads.length ? '把抓取文件转成可引用证据' : '先建立第一条研报证据链'}</Title>
                <p>
                  {pendingDownloads.length
                    ? `抓取舱里有 ${pendingDownloads.length} 份文件尚未入库。入库后才会生成指标、引用块和投委会摘要。`
                    : '上传 PDF、Word 或文本报告，系统会抽取正文、建立引用块并准备后续问答。'}
                </p>
              </div>
              <div className="research-onboarding-actions">
                {pendingDownloads.slice(0, 3).map(file => (
                  <div className="research-import-card" key={file.name}>
                    <FileTextOutlined />
                    <div>
                      <strong>{cleanReportTitle(file.name)}</strong>
                      <span>{formatFileSize(file.size)} · {formatDate(file.mtime)}</span>
                    </div>
                    <Button type="primary" loading={ingestingFile === file.name} onClick={() => void ingestDownload(file)}>
                      入库
                    </Button>
                  </div>
                ))}
                {!pendingDownloads.length && (
                  <Search
                    className="research-url-search onboarding"
                    value={urlDraft}
                    onChange={event => setUrlDraft(event.target.value)}
                    onSearch={value => void ingestUrl(value)}
                    allowClear
                    loading={ingestingUrl}
                    placeholder="粘贴 PDF/HTML 研报 URL"
                    enterButton="URL入库"
                  />
                )}
                {!pendingDownloads.length && (
                  <Upload {...uploadProps}>
                    <Button type="primary" icon={<UploadOutlined />} loading={uploading}>上传研报入库</Button>
                  </Upload>
                )}
                {pendingDownloads.length > 1 && (
                  <Button icon={<DatabaseOutlined />} loading={Boolean(ingestingFile)} onClick={() => void ingestRecentDownloads()}>
                    入库最近 3 份
                  </Button>
                )}
                <Button icon={<LinkOutlined />} onClick={() => setShowLegacyWorkbench(true)}>
                  高级抓取舱
                </Button>
              </div>
            </div>
          )}
        </section>

        <aside className="research-desk-panel research-rag-panel">
          <div className="research-panel-head">
            <div>
              <Text className="dashboard-eyebrow">IC CONTROL</Text>
              <strong>投委会控制台</strong>
            </div>
            <Tag color={ragResult?.confidence ? 'green' : 'default'}>
              {ragResult ? `${(ragResult.confidence * 100).toFixed(0)}% 引用置信` : selectedReport ? '待核验' : '等待报告'}
            </Tag>
          </div>

          {selectedReport ? (
            <>
              <div className="research-question-stack">
                <TextArea
                  value={ragQuestion}
                  onChange={event => setRagQuestion(event.target.value)}
                  autoSize={{ minRows: 4, maxRows: 7 }}
                  placeholder="输入需要逐条引用核验的问题"
                />
                <Space wrap>
                  <Button type="primary" icon={<FileSearchOutlined />} loading={querying} onClick={runRagQuery}>
                    查询引用
                  </Button>
                  {defaultQuestions.map(question => (
                    <Button key={question} size="small" onClick={() => setRagQuestion(question)}>
                      {question.slice(0, 9)}
                    </Button>
                  ))}
                </Space>
              </div>

              <div className="research-rag-answer">
                {querying ? (
                  <div className="research-desk-loading"><Spin /></div>
                ) : ragResult ? (
                  <>
                    <p>{ragResult.answer}</p>
                    <ShareButton target={() => ({ title: ragQuestion || '引用问答结论', summary: ragResult.answer, byline: '由 DeepFocus 投研工作台引用核验生成' })} />
                    {ragResult.missing.length > 0 && (
                      <Alert
                        type="warning"
                        showIcon
                        message="缺口"
                        description={ragResult.missing.slice(0, 3).join('；')}
                      />
                    )}
                  </>
                ) : (
                  <div className="research-rag-placeholder">
                    <FileSearchOutlined />
                    <span>答案必须带引用才进入证据链</span>
                  </div>
                )}
              </div>

              <div className="research-eval-block">
                <div className="research-block-head">
                  <strong>评测闸门</strong>
                  {evalResult?.total ? (
                    <Tag color={evalResult.pass_rate >= 0.75 ? 'green' : 'orange'}>
                      {(evalResult.pass_rate * 100).toFixed(0)}%
                    </Tag>
                  ) : null}
                </div>
                {evaluating ? (
                  <div className="research-desk-loading compact"><Spin /></div>
                ) : evalResult ? (
                  <div className="research-eval-grid">
                    <div>
                      <Progress percent={Math.round(evalResult.pass_rate * 100)} size="small" strokeColor={confidenceColor(evalResult.pass_rate)} />
                      <span>通过率</span>
                    </div>
                    <div>
                      <Progress percent={Math.round(evalResult.citation_rate * 100)} size="small" strokeColor={confidenceColor(evalResult.citation_rate)} />
                      <span>引用率</span>
                    </div>
                    <div>
                      <Progress percent={Math.round(evalResult.refusal_guard_rate * 100)} size="small" strokeColor={confidenceColor(evalResult.refusal_guard_rate)} />
                      <span>拒答护栏</span>
                    </div>
                  </div>
                ) : (
                  <Text type="secondary">未运行评测</Text>
                )}
              </div>

              <div className="research-agent-bridge">
                <div className="research-block-head">
                  <strong>发送给 Agent</strong>
                  <ThunderboltOutlined />
                </div>
                <TextArea
                  value={aiBridgeDraft}
                  onChange={event => setAiBridgeDraft(event.target.value)}
                  autoSize={{ minRows: 3, maxRows: 6 }}
                />
                <Button type="primary" icon={<SendOutlined />} onClick={sendBridgeDraft}>
                  转入投研工作台
                </Button>
              </div>
            </>
          ) : (
            <div className="research-control-empty">
              <AuditOutlined />
              <strong>先让证据进入主账本</strong>
              <span>引用问答、评测闸门和投研任务都依赖已索引报告。抓取舱文件不会直接进入投委会摘要，必须先入库。</span>
              {pendingDownloads.length ? (
                <Button type="primary" loading={Boolean(ingestingFile)} onClick={() => void ingestRecentDownloads()}>
                  入库最近 3 份
                </Button>
              ) : (
                <Button onClick={() => setShowLegacyWorkbench(true)}>打开高级抓取舱</Button>
              )}
            </div>
          )}
        </aside>
      </div>

      {showLegacyWorkbench && (
        <div className="research-legacy-dock">
          <div className="research-legacy-head">
            <div className="research-pipeline-dock-title">
              <strong>高级抓取舱</strong>
              <Space wrap>
                <Tag color={online ? 'green' : frameLoaded ? 'blue' : online === false ? 'red' : 'default'}>
                  {online ? '已连接' : frameLoaded ? '已打开' : online === false ? '服务未启动' : '检测中'}
                </Tag>
                <Tag color={pendingDownloads.length ? 'orange' : 'default'}>{pendingDownloads.length} 份待入库</Tag>
                {downloadSummary && <Tag>{formatFileSize(downloadSummary.sizeBytes)}</Tag>}
                {online && (
                  <Tag color={status?.credentialsAvailable ? 'green' : 'orange'}>
                    {status?.credentialsAvailable ? '凭证已配置' : '凭证未配置'}
                  </Tag>
                )}
                {latestJob && (
                  <Tag color={latestJob.status === 'failed' ? 'red' : latestJob.status === 'running' ? 'blue' : 'default'}>
                    {jobStatusLabel[latestJob.status] || latestJob.status}
                  </Tag>
                )}
              </Space>
            </div>
            <Space>
              <Tooltip title="刷新抓取舱状态">
                <Button
                  icon={<ReloadOutlined spin={checking || loadingDownloads} />}
                  onClick={() => {
                    void checkStatus();
                    void loadDownloads();
                  }}
                />
              </Tooltip>
              <Tooltip title="在新窗口打开抓取舱">
                <Button icon={<ExportOutlined />} onClick={() => window.open(workbenchUrl, '_blank', 'noopener,noreferrer')} />
              </Tooltip>
              <Button icon={<CheckCircleOutlined />} onClick={() => setShowLegacyWorkbench(false)}>
                收起
              </Button>
            </Space>
          </div>

          {online === false && !frameLoaded && (
            <Alert
              className="research-workbench-alert"
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message="资料抓取舱服务未启动"
              description={
                <Space direction="vertical" size={6}>
                  <Text>
                    后端托管地址：<Text code copyable>{workbenchUrl}</Text>
                  </Text>
                  <Text>
                    启动命令：<Text code copyable>{START_COMMAND}</Text>；首次运行依赖：<Text code copyable>{INSTALL_COMMAND}</Text>
                  </Text>
                </Space>
              }
            />
          )}

          <div className="research-workbench-frame-shell research-workbench-frame-shell--compact">
            <iframe
              className="research-workbench-frame"
              title="研报抓取工作台"
              src={frameUrl}
              allow="clipboard-read; clipboard-write"
              onLoad={() => {
                setFrameLoaded(true);
              }}
            />
            {online === null && !frameLoaded ? (
              <div className="research-workbench-empty">
                <Spin />
              </div>
            ) : online === false && !frameLoaded ? (
              <div className="research-workbench-empty">
                <Space direction="vertical" align="center" size={12}>
                  <LinkOutlined />
                  <Text type="secondary">等待本地模块服务</Text>
                </Space>
              </div>
            ) : null}
          </div>
        </div>
      )}
      </>
      )}
    </div>
  );
};

export default ResearchWorkbench;
