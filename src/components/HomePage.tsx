import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Checkbox,
  Input,
  Progress,
  Select,
  Segmented,
  Space,
  Tag,
  Typography,
  Upload
} from 'antd';
import {
  ApiOutlined,
  AuditOutlined,
  AudioOutlined,
  BarChartOutlined,
  CalendarOutlined,
  CheckOutlined,
  CloudServerOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileImageOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  FundProjectionScreenOutlined,
  HistoryOutlined,
  LineChartOutlined,
  PaperClipOutlined,
  PartitionOutlined,
  PlusOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  ShoppingOutlined,
  FileSearchOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  VideoCameraOutlined
} from '@ant-design/icons';
import { AppState, Product, Stock, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketDataService';
import StockList from './StockList';
import Shop from './Shop';
import { formatQuoteSourceLine, formatQuoteTimestamp } from '../utils/marketData';
import { MarketSegmentKey, countStocksBySegment, marketSegments } from '../utils/marketSegments';
import { ModelConfig, getModelConfig } from '../services/aiResearchService';
import {
  DataSourceItemRecord,
  DataSourceRecord,
  interpretDataItem,
  listDataItems,
  listDataSources,
  uploadDataFile
} from '../services/dataSourceService';
import { McpServerRecord, listMcpServers } from '../services/mcpService';
import {
  ProfessionalReportAnalysis,
  ProfessionalReportRecord,
  ProfessionalReportType,
  analyzeProfessionalReport,
  listProfessionalReports,
  uploadProfessionalReport
} from '../services/proResearchService';
import {
  ResearchWorkbenchSearchItem,
  searchResearchWorkbenchReports,
  startResearchWorkbenchDownload,
  summarizeResearchWorkbenchHits
} from '../services/researchWorkbenchService';
import {
  AgentChatBlock,
  AgentRunBlocks,
  blockFromAgentRunEvent,
  blocksFromInvestmentTask,
  mergeAgentBlock
} from './agent/AgentRunBlocks';
import {
  AgentEngine,
  AgentRunEvent,
  InvestmentTaskCreate,
  InvestmentTaskRecord,
  OrchestratorReasoningStep,
  createAgentTask,
  getAgentTask,
  listAgentTasks,
  runGeneralChat,
  runOrchestratorChat,
  subscribeAgentTaskEvents
} from '../services/agentTaskService';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;
const HOME_RESEARCH_SEARCH_PAGES = 100;

interface HomePageProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
  onProductClick: (product: Product) => void;
  onAddToCart: (product: Product, variantId: string, quantity: number) => void;
  onViewChange: (view: ViewType) => void;
  onAddStock: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock: (symbol: string) => void;
  onToggleStockSubscription: (symbol: string) => void;
  onRefreshMarketData: () => void;
  isMarketDataRefreshing: boolean;
}

type ChatRole = 'assistant' | 'user';
type ChatMode = 'research' | 'risk' | 'portfolio' | 'monitor';
type ReasoningMode = 'fast' | 'thinking';
type ThreadViewMode = 'compact' | 'full';
type CoreAgentPhase = 'orchestrator' | 'evidence' | 'research' | 'risk' | 'report';
type AttachmentKind = 'image' | 'video' | 'audio' | 'document';
type GuideActionKind = 'prompt' | 'view' | 'context' | 'mode' | 'reasoning' | 'agent';
type GuidePanelVariant = 'generating' | 'ready' | 'analysis' | 'research' | 'error';

type StockOption = {
  symbol?: string;
  name?: string;
};

const knownStockAliases: Array<StockOption & { aliases: string[] }> = [
  {
    symbol: '300750.SZ',
    name: '宁德时代',
    aliases: ['宁德时代', '宁王', 'CATL', 'Contemporary Amperex', '300750', '300750.SZ']
  },
  {
    symbol: '00148.HK',
    name: '建滔集团',
    aliases: ['建滔集团', 'Kingboard Holdings', 'Kingboard Holdings Limited', '00148', '00148.HK', '0148', '148.HK']
  },
  {
    symbol: '01888.HK',
    name: '建滔积层板',
    aliases: ['建滔积层板', '建滔板', 'Kingboard Laminates', 'Kingboard Laminates Holdings', '01888', '01888.HK', '1888', '1888.HK']
  }
];

interface ChatMessage {
  id: string;
  role: ChatRole;
  title?: string;
  content: string;
  chips?: string[];
  attachments?: ChatAttachmentMeta[];
  reasoningTrace?: ChatReasoningStep[];
  agentBlocks?: AgentChatBlock[];
  reportCards?: ReportInsightCard[];
  guide?: ChatGuidePanel;
  status?: 'working' | 'done' | 'error';
  taskId?: string;
  thinkingEnabled?: boolean;
  pendingRun?: PersistedPendingAgentRun;
}

interface ChatGuideAction {
  id: string;
  label: string;
  detail?: string;
  kind: GuideActionKind;
  prompt?: string;
  view?: ViewType;
  mode?: ChatMode;
  reasoning?: ReasoningMode;
  agent?: AgentEngine;
  primary?: boolean;
}

interface ChatGuideStep {
  label: string;
  status: 'done' | 'working' | 'wait';
}

interface ChatGuidePanel {
  variant: GuidePanelVariant;
  title: string;
  description: string;
  steps?: ChatGuideStep[];
  actions?: ChatGuideAction[];
}

interface ReportInsightCard {
  id: string;
  title: string;
  kind?: 'full-report' | 'hit-summary';
  summary: string;
  metrics: string[];
  flags: string[];
  risks: string[];
  questions: string[];
  confidence: number;
  citations: number;
}

interface ChatAttachmentMeta {
  id: string;
  name: string;
  size: number;
  type?: string;
  kind: AttachmentKind;
}

type ChatReasoningStatus = 'done' | 'working' | 'wait' | 'error';

interface ChatReasoningStep {
  phase: string;
  title: string;
  detail: string;
  status: ChatReasoningStatus;
}

interface ContextAction {
  key: string;
  title: string;
  detail: string;
  icon: React.ReactNode;
  view: ViewType;
  status: string;
}

interface ModuleLink {
  key: string;
  title: string;
  detail: string;
  icon: React.ReactNode;
  view: ViewType;
  tone?: 'primary' | 'quiet';
}

interface ModuleGroup {
  label: string;
  detail: string;
  items: ModuleLink[];
}

interface ChatConversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
  activePromptStock?: StockOption;
  chatMode: ChatMode;
  reasoningMode: ReasoningMode;
  agentEngine: AgentEngine;
  selectedSymbol?: string;
}

interface PendingAgentRun {
  id: string;
  messageId: string;
  userText: string;
  files: File[];
  chatStock?: StockOption;
  chatMode: ChatMode;
  reasoningMode: ReasoningMode;
  agentEngine: AgentEngine;
  thinkingEnabled: boolean;
  dataSourceCount: number;
  mcpServerCount: number;
  modelProvider?: string;
  modelName?: string;
  createdAt: string;
}

interface PersistedPendingAgentRun extends Omit<PendingAgentRun, 'files'> {
  fileNames: string[];
  hasFiles: boolean;
}

interface PersistedHomeChatState {
  version: 1;
  activeConversationId: string | null;
  conversations: ChatConversation[];
}

interface PendingAiDraft {
  prompt: string;
  source?: string;
  references?: string[];
  skill?: string;
  createdAt?: string;
}

const modeMeta: Record<ChatMode, {
  label: string;
  taskType: InvestmentTaskCreate['task_type'];
  titlePrefix: string;
}> = {
  research: {
    label: '投研',
    taskType: 'investment_research',
    titlePrefix: '对话投研'
  },
  risk: {
    label: '风控',
    taskType: 'risk_review',
    titlePrefix: '风险审查'
  },
  portfolio: {
    label: '复盘',
    taskType: 'portfolio_review',
    titlePrefix: '组合复盘'
  },
  monitor: {
    label: '监控',
    taskType: 'watchlist_monitor',
    titlePrefix: '观察名单监控'
  }
};

const reasoningModeMeta: Record<ReasoningMode, {
  label: string;
  shortLabel: string;
  pendingTitle: string;
}> = {
  fast: {
    label: '快速',
    shortLabel: '快',
    pendingTitle: 'DeepFocus 正在快速回复'
  },
  thinking: {
    label: '思考',
    shortLabel: '思考',
    pendingTitle: 'OrchestratorAgent 正在思考'
  }
};

const coreAgentRoster = ['Orchestrator', 'Evidence', 'Research', 'Risk', 'Report'];

const agentEngineMeta: Record<AgentEngine, {
  label: string;
  shortLabel: string;
  agents: string[];
  description: string;
}> = {
  deepfocus: {
    label: 'DeepFocus 多 Agent',
    shortLabel: 'DeepFocus',
    agents: coreAgentRoster,
    description: '本地证据层 + 5 个核心投研 Agent 链路'
  },
  tradingagents: {
    label: 'TradingAgents',
    shortLabel: 'TradingAgents',
    agents: coreAgentRoster,
    description: '底层映射 TradingAgents 的分析、辩论、交易、风控和组合经理链路'
  },
  financial_services: {
    label: 'Financial Services Playbook',
    shortLabel: 'FSI Playbook',
    agents: ['Orchestrator', 'Evidence', 'FSI Workflow', 'Control', 'Report'],
    description: '参考 financial-services cookbook，把财报、模型、Pitch、估值、KYC 和对账工作流接入 DeepFocus'
  }
};

const tradingAgentsCockpitEngineConfig = {
  max_debate_rounds: 1,
  max_risk_discuss_rounds: 1,
  selected_analysts: ['market', 'news', 'fundamentals'],
  timeout_seconds: 120,
  tool_timeout_seconds: 12,
  web_search_limit: 4,
  web_search_timeout_seconds: 6
};

const skillCount = 18;
const uid = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const trimTitle = (value: string, fallback: string) => {
  const text = value.replace(/\s+/g, ' ').trim();
  return text ? text.slice(0, 28) : fallback;
};
const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));
const HOME_CHAT_STORAGE_KEY = 'deepfocus.homeChat.v1';
const AI_DRAFT_STORAGE_KEY = 'deepfocus.aiDraft.v1';
const MAX_SAVED_CONVERSATIONS = 30;
const MAX_SAVED_MESSAGES = 80;
const MAX_ATTACHED_FILES = 10;
const chatModeValues: ChatMode[] = ['research', 'risk', 'portfolio', 'monitor'];
const reasoningModeValues: ReasoningMode[] = ['fast', 'thinking'];
const agentEngineValues: AgentEngine[] = ['deepfocus', 'tradingagents', 'financial_services'];

const attachmentKindLabel: Record<AttachmentKind, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
  document: '文件'
};

const isChatModeValue = (value: unknown): value is ChatMode => (
  typeof value === 'string' && chatModeValues.includes(value as ChatMode)
);

const isReasoningModeValue = (value: unknown): value is ReasoningMode => (
  typeof value === 'string' && reasoningModeValues.includes(value as ReasoningMode)
);

const isAgentEngineValue = (value: unknown): value is AgentEngine => (
  typeof value === 'string' && agentEngineValues.includes(value as AgentEngine)
);

const isChatReasoningStatus = (value: unknown): value is ChatReasoningStatus => (
  value === 'done' || value === 'working' || value === 'wait' || value === 'error'
);

const isChatMessageStatus = (value: unknown): value is ChatMessage['status'] => (
  value === 'working' || value === 'done' || value === 'error'
);

const isGuideActionKind = (value: unknown): value is GuideActionKind => (
  value === 'prompt'
  || value === 'view'
  || value === 'context'
  || value === 'mode'
  || value === 'reasoning'
  || value === 'agent'
);

const isGuidePanelVariant = (value: unknown): value is GuidePanelVariant => (
  value === 'generating'
  || value === 'ready'
  || value === 'analysis'
  || value === 'research'
  || value === 'error'
);

const createChatConversation = (overrides: Partial<ChatConversation> = {}): ChatConversation => {
  const now = new Date().toISOString();
  return {
    id: uid(),
    title: '新的投研对话',
    createdAt: now,
    updatedAt: now,
    messages: [],
    chatMode: 'research',
    reasoningMode: 'thinking',
    agentEngine: 'deepfocus',
    ...overrides
  };
};

const conversationTitleFromMessages = (messages: ChatMessage[], fallback = '新的投研对话'): string => {
  const firstUserMessage = messages.find(item => item.role === 'user' && item.content.trim());
  return trimTitle(firstUserMessage?.content || '', fallback);
};

const normalizeStringArray = (value: unknown): string[] | undefined => {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const items = value.map(item => String(item || '').trim()).filter(Boolean);
  return items.length > 0 ? items : undefined;
};

const normalizeGuidePanel = (value: unknown): ChatGuidePanel | undefined => {
  if (!value || typeof value !== 'object') {
    return undefined;
  }

  const record = value as Record<string, unknown>;
  const title = typeof record.title === 'string' ? record.title.trim() : '';
  const description = typeof record.description === 'string' ? record.description.trim() : '';
  if (!title && !description) {
    return undefined;
  }

  const steps = Array.isArray(record.steps)
    ? record.steps.map(step => {
        if (!step || typeof step !== 'object') return null;
        const stepRecord = step as Record<string, unknown>;
        const label = typeof stepRecord.label === 'string' ? stepRecord.label.trim() : '';
        const status = stepRecord.status === 'done' || stepRecord.status === 'working' || stepRecord.status === 'wait'
          ? stepRecord.status
          : 'wait';
        return label ? { label, status } : null;
      }).filter(Boolean).slice(0, 4) as ChatGuideStep[]
    : undefined;

  const actions = Array.isArray(record.actions)
    ? record.actions.map(action => {
        if (!action || typeof action !== 'object') return null;
        const actionRecord = action as Record<string, unknown>;
        const label = typeof actionRecord.label === 'string' ? actionRecord.label.trim() : '';
        const kind = isGuideActionKind(actionRecord.kind) ? actionRecord.kind : undefined;
        if (!label || !kind) return null;
        const view = typeof actionRecord.view === 'string' ? actionRecord.view as ViewType : undefined;
        const mode = isChatModeValue(actionRecord.mode) ? actionRecord.mode : undefined;
        const reasoning = isReasoningModeValue(actionRecord.reasoning) ? actionRecord.reasoning : undefined;
        const agent = isAgentEngineValue(actionRecord.agent) ? actionRecord.agent : undefined;
        return {
          id: typeof actionRecord.id === 'string' ? actionRecord.id : `${kind}:${label}`,
          label,
          detail: typeof actionRecord.detail === 'string' ? actionRecord.detail : undefined,
          kind,
          prompt: typeof actionRecord.prompt === 'string' ? actionRecord.prompt : undefined,
          view,
          mode,
          reasoning,
          agent,
          primary: Boolean(actionRecord.primary)
        };
      }).filter(Boolean).slice(0, 4) as ChatGuideAction[]
    : undefined;

  return {
    variant: isGuidePanelVariant(record.variant) ? record.variant : 'ready',
    title: title || '下一步',
    description,
    steps: steps?.length ? steps : undefined,
    actions: actions?.length ? actions : undefined
  };
};

const fileKey = (file: Pick<File, 'name' | 'size' | 'lastModified'>) => (
  `${file.name}:${file.size}:${file.lastModified}`
);

const inferAttachmentKind = (file: { name: string; type?: string }): AttachmentKind => {
  const lowerName = file.name.toLowerCase();
  if (file.type?.startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(lowerName)) return 'image';
  if (file.type?.startsWith('video/') || /\.(mp4|mov|webm|m4v|avi)$/i.test(lowerName)) return 'video';
  if (file.type?.startsWith('audio/') || /\.(mp3|wav|m4a|aac|flac)$/i.test(lowerName)) return 'audio';
  return 'document';
};

const fileToAttachmentMeta = (file: File): ChatAttachmentMeta => ({
  id: fileKey(file),
  name: file.name,
  size: file.size,
  type: file.type,
  kind: inferAttachmentKind(file)
});

const normalizeAttachmentMeta = (value: unknown): ChatAttachmentMeta[] | undefined => {
  if (!Array.isArray(value)) {
    return undefined;
  }

  const items = value.map(item => {
    if (!item || typeof item !== 'object') {
      return null;
    }
    const record = item as Record<string, unknown>;
    const name = typeof record.name === 'string' ? record.name.trim() : '';
    const kind = typeof record.kind === 'string' && Object.prototype.hasOwnProperty.call(attachmentKindLabel, record.kind)
      ? record.kind as AttachmentKind
      : inferAttachmentKind({
          name,
          type: typeof record.type === 'string' ? record.type : undefined
        });

    if (!name) {
      return null;
    }

    return {
      id: typeof record.id === 'string' ? record.id : `${name}:${record.size || 0}`,
      name,
      size: typeof record.size === 'number' && Number.isFinite(record.size) ? record.size : 0,
      type: typeof record.type === 'string' ? record.type : undefined,
      kind
    };
  }).filter(Boolean) as ChatAttachmentMeta[];

  return items.length > 0 ? items.slice(0, MAX_ATTACHED_FILES) : undefined;
};

const formatFileSize = (size: number): string => {
  if (!Number.isFinite(size) || size <= 0) {
    return '未知大小';
  }
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

const researchHitKey = (item: ResearchWorkbenchSearchItem, index = 0): string => (
  item.fileId || `${item.topicId || 'topic'}:${item.name}:${index}`
);

const professionalReportKey = (report: ProfessionalReportRecord): string => report.id;

const formatResearchHitDate = (value?: string): string => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

const reportTypeLabel: Record<string, string> = {
  annual: '年报',
  semiannual: '半年报',
  quarterly: '季报',
  research: '研报',
  transcript: '电话会',
  other: '资料'
};

const reportSearchText = (report: ProfessionalReportRecord): string => [
  report.title,
  report.symbol || '',
  report.period || '',
  report.report_type,
  String(report.metadata?.filename || '')
].join(' ').toLowerCase();

const isVisibleProfessionalReport = (report: ProfessionalReportRecord): boolean => {
  const filename = String(report.metadata?.filename || report.title || '').trim();
  if (!filename || filename.startsWith('.') || filename.startsWith('._')) return false;
  return !/^(?:\.DS_Store|Thumbs\.db|desktop\.ini|__MACOSX)$/i.test(filename);
};

const formatProfessionalReportMeta = (report: ProfessionalReportRecord): string => (
  [
    report.symbol || '',
    reportTypeLabel[report.report_type] || report.report_type,
    report.period || '',
    `${report.metrics_count} 指标`,
    `${report.chunks_count} 引用块`,
    formatResearchHitDate(report.updated_at)
  ].filter(Boolean).join(' · ')
);

const evidenceItemSearchText = (item: DataSourceItemRecord): string => [
  item.title,
  item.symbol || '',
  item.source_name,
  item.source_category,
  item.source_type,
  ...item.tags,
  String(item.metadata?.filename || '')
].join(' ').toLowerCase();

const isVisibleEvidenceItem = (item: DataSourceItemRecord): boolean => {
  const filename = String(item.metadata?.filename || item.title || '').trim();
  if (!filename || filename.startsWith('.') || filename.startsWith('._')) return false;
  if (/^(?:\.DS_Store|Thumbs\.db|desktop\.ini|__MACOSX)$/i.test(filename)) return false;
  return /研报|报告|research|pdf|投行|证券|策略|行业|公司|财报|纪要/i.test([
    item.title,
    item.source_name,
    item.source_category,
    item.source_type,
    item.tags.join(' '),
    filename
  ].join(' '));
};

const formatEvidenceItemMeta = (item: DataSourceItemRecord): string => (
  [
    item.symbol || '',
    item.source_name || '证据库',
    item.tags.slice(0, 2).join(' / '),
    formatResearchHitDate(item.collected_at || item.created_at)
  ].filter(Boolean).join(' · ')
);

const buildEvidenceItemContext = (items: DataSourceItemRecord[]): string => {
  if (!items.length) return '';
  return [
    '已选证据库资料（可作为 Agent 上下文）：',
    ...items.slice(0, 12).map((item, index) => (
      `${index + 1}. ${item.title}；资料ID=${item.id}；${formatEvidenceItemMeta(item)}`
    ))
  ].join('\n');
};

const cleanUserFacingReportText = (value?: string | null): string => (
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

const isReportSectionLabel = (value: string): boolean => /^(投资判断|核心观点|关键数字|关键指标|预期差|情景推演|投资逻辑|催化剂|跟踪清单|推翻条件|风险|风险提示|证据质量|待确认|下一步问题|主题线索|建议动作|下载优先级)$/i
  .test(value.replace(/[:：]\s*$/, '').trim());

const splitReportPoints = (value?: string | null, limit = 4): string[] => (
  cleanUserFacingReportText(value)
    .split(/\n+|；|;|(?<=。)/)
    .map(item => item.trim())
    .filter(item => item && !isReportSectionLabel(item))
    .slice(0, limit)
);

const uniqueReportTexts = (items: Array<string | null | undefined>, limit: number): string[] => {
  const seen = new Set<string>();
  const result: string[] = [];
  items.forEach(item => {
    const clean = cleanUserFacingReportText(item);
    if (!clean || seen.has(clean)) return;
    seen.add(clean);
    result.push(clean);
  });
  return result.slice(0, limit);
};

const buildProfessionalReportContext = (reports: ProfessionalReportRecord[]): string => {
  if (!reports.length) return '';
  return [
    '已选入库研报（可用专业研报库/RAG/解读能力）：',
    ...reports.slice(0, 12).map((report, index) => (
      `${index + 1}. ${report.title}；报告ID=${report.id}；${formatProfessionalReportMeta(report)}`
    ))
  ].join('\n');
};

const buildProfessionalReportCard = (
  report: ProfessionalReportRecord,
  analysis: ProfessionalReportAnalysis
): ReportInsightCard => ({
  id: report.id,
  title: report.title,
  kind: 'full-report',
  summary: splitReportPoints(analysis.summary, 2).join(' ') || '已完成正文级解读。',
  metrics: (analysis.key_metrics || []).slice(0, 6).map(metric => (
    `${metric.metric_label}：${metric.raw_value || (metric.value != null ? metric.value : '未识别')}${metric.period ? `（${metric.period}）` : ''}`
  )),
  flags: uniqueReportTexts(analysis.quality_flags || [], 5),
  risks: uniqueReportTexts(analysis.risks || [], 5),
  questions: uniqueReportTexts(analysis.follow_up_questions || [], 4),
  confidence: analysis.confidence || 0,
  citations: analysis.citations?.length || 0
});

const buildEvidenceItemCard = (
  item: DataSourceItemRecord,
  interpretation: string
): ReportInsightCard => ({
  id: item.id,
  title: item.title,
  kind: 'hit-summary',
  summary: splitReportPoints(interpretation || item.text_preview, 2).join(' ') || '已完成证据库资料解读。',
  metrics: uniqueReportTexts([
    item.symbol ? `标的：${item.symbol}` : '',
    `来源：${item.source_name || '证据库'}`,
    `可信度：${Math.round((item.credibility_score || 0) * 100)}%`
  ], 3),
  flags: splitReportPoints(interpretation || item.text_preview, 5),
  risks: [],
  questions: [
    '把这份资料和当前标的基本面交叉验证',
    '继续查原文中的关键数字和反证条件'
  ],
  confidence: item.credibility_score || 0,
  citations: item.text ? 1 : 0
});

const buildResearchHitSummaryCard = (
  reply: string,
  items: ResearchWorkbenchSearchItem[],
  keyword: string
): ReportInsightCard => {
  const buckets = {
    points: [] as string[],
    risks: [] as string[],
    actions: [] as string[]
  };
  let current: keyof typeof buckets = 'points';
  cleanUserFacingReportText(reply)
    .split(/\n+/)
    .map(item => item.trim())
    .filter(Boolean)
    .forEach(line => {
      const normalized = line.replace(/[:：]\s*$/, '');
      if (/风险|缺口|不确定|待确认|谨慎|推翻|红旗|问题/.test(normalized)) {
        current = 'risks';
        return;
      }
      if (/下一步|追问|建议|下载|优先|动作|跟踪|清单/.test(normalized)) {
        current = 'actions';
        return;
      }
      if (/结论|主题|摘要|判断|快筛|聚类|重点/.test(normalized) && normalized.length <= 18) {
        current = 'points';
        return;
      }
      buckets[current].push(line);
    });

  const newest = items
    .map(item => item.topicCreateTime || item.createTime || '')
    .filter(Boolean)
    .sort()
    .pop();
  const headline = uniqueReportTexts(buckets.points, 1)[0]
    || `${keyword || '当前条件'}下共找到 ${items.length} 条研报命中。`;
  const points = uniqueReportTexts(buckets.points.slice(1), 5);

  return {
    id: `research-hit-summary-${Date.now()}`,
    title: keyword ? `${keyword} 研报命中快筛` : '研报命中快筛',
    kind: 'hit-summary',
    summary: headline,
    metrics: uniqueReportTexts([
      `命中：${items.length} 条`,
      newest ? `最新：${formatResearchHitDate(newest)}` : '',
      '范围：标题、标签、日期和下载热度'
    ], 3),
    flags: points.length ? points : uniqueReportTexts(splitReportPoints(reply, 6), 5),
    risks: uniqueReportTexts(buckets.risks, 4),
    questions: uniqueReportTexts(buckets.actions, 4),
    confidence: 0,
    citations: 0
  };
};

const buildResearchHitSummaryPrompt = (
  items: ResearchWorkbenchSearchItem[],
  keyword: string
): string => {
  const limited = items.slice(0, 120);
  const lines = limited.map((item, index) => [
    `[${index + 1}] ${item.name}`,
    item.hashtag ? `标签：${item.hashtag}` : '',
    item.size ? `大小：${formatFileSize(item.size)}` : '',
    item.topicCreateTime || item.createTime ? `日期：${formatResearchHitDate(item.topicCreateTime || item.createTime)}` : '',
    item.downloadCount ? `下载：${item.downloadCount}` : ''
  ].filter(Boolean).join('；'));

  return [
    `请基于下面的知识星球研报搜索命中列表，做一页中文投研情报总结。关键词：${keyword || '未指定'}`,
    '',
    '注意：你现在只能看到标题、标签、大小、日期和下载次数，不能假装读过 PDF 正文。请明确说明结论来自标题级线索。',
    '',
    '输出结构：',
    '1. 一句话总览：这批研报主要在讨论什么',
    '2. 主题地图：按主题聚类，并指出哪类证据最密集',
    '3. 最值得优先下载深读的 Top 10：给出选择理由',
    '4. 重复/同主题报告合并建议',
    '5. 投资跟踪清单：接下来应该追哪些数据、公司、事件和风险',
    '',
    `命中数量：${items.length}，本次用于总结：${limited.length}`,
    lines.join('\n')
  ].join('\n');
};

const attachmentIcon = (kind: AttachmentKind) => {
  if (kind === 'image') return <FileImageOutlined />;
  if (kind === 'video') return <VideoCameraOutlined />;
  if (kind === 'audio') return <AudioOutlined />;
  return <FileTextOutlined />;
};

const normalizeReasoningSteps = (value: unknown): ChatReasoningStep[] | undefined => {
  if (!Array.isArray(value)) {
    return undefined;
  }

  const steps = value.map(step => {
    if (!step || typeof step !== 'object') {
      return null;
    }
    const record = step as Record<string, unknown>;
    return {
      phase: String(record.phase || 'step'),
      title: String(record.title || '思路摘要'),
      detail: String(record.detail || '已进入 Agent 工作流。'),
      status: isChatReasoningStatus(record.status) ? record.status : 'done'
    };
  }).filter(Boolean) as ChatReasoningStep[];

  return steps.length > 0 ? steps.slice(0, 8) : undefined;
};

const normalizeStockOption = (value: unknown): StockOption | undefined => {
  if (!value || typeof value !== 'object') {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const symbol = typeof record.symbol === 'string' ? record.symbol : undefined;
  const name = typeof record.name === 'string' ? record.name : undefined;
  return symbol || name ? { symbol, name } : undefined;
};

const normalizePersistedPendingRun = (value: unknown): PersistedPendingAgentRun | undefined => {
  if (!value || typeof value !== 'object') {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const userText = typeof record.userText === 'string' ? record.userText : '';
  if (!userText.trim()) {
    return undefined;
  }
  const chatMode = isChatModeValue(record.chatMode) ? record.chatMode : 'research';
  const reasoningMode = isReasoningModeValue(record.reasoningMode) ? record.reasoningMode : 'thinking';
  const agentEngine = isAgentEngineValue(record.agentEngine) ? record.agentEngine : 'deepfocus';
  const fileNames = Array.isArray(record.fileNames)
    ? record.fileNames.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  return {
    id: typeof record.id === 'string' ? record.id : uid(),
    messageId: typeof record.messageId === 'string' ? record.messageId : '',
    userText,
    chatStock: normalizeStockOption(record.chatStock),
    chatMode,
    reasoningMode,
    agentEngine,
    thinkingEnabled: typeof record.thinkingEnabled === 'boolean' ? record.thinkingEnabled : reasoningMode === 'thinking',
    dataSourceCount: typeof record.dataSourceCount === 'number' ? record.dataSourceCount : 0,
    mcpServerCount: typeof record.mcpServerCount === 'number' ? record.mcpServerCount : 0,
    modelProvider: typeof record.modelProvider === 'string' ? record.modelProvider : undefined,
    modelName: typeof record.modelName === 'string' ? record.modelName : undefined,
    createdAt: typeof record.createdAt === 'string' ? record.createdAt : new Date().toISOString(),
    fileNames,
    hasFiles: Boolean(record.hasFiles) || fileNames.length > 0,
  };
};

const normalizeChatMessages = (value: unknown): ChatMessage[] => {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.map(item => {
    if (!item || typeof item !== 'object') {
      return null;
    }
    const record = item as Record<string, unknown>;
    const role = record.role === 'assistant' || record.role === 'user' ? record.role : null;
    const content = typeof record.content === 'string' ? record.content : '';
    if (!role || !content.trim()) {
      return null;
    }
    return {
      id: typeof record.id === 'string' ? record.id : uid(),
      role,
      title: typeof record.title === 'string' ? record.title : undefined,
      content,
      chips: normalizeStringArray(record.chips),
      attachments: normalizeAttachmentMeta(record.attachments),
      reasoningTrace: normalizeReasoningSteps(record.reasoningTrace),
      agentBlocks: Array.isArray(record.agentBlocks) ? record.agentBlocks as AgentChatBlock[] : undefined,
      guide: normalizeGuidePanel(record.guide),
      status: isChatMessageStatus(record.status) ? record.status : undefined,
      taskId: typeof record.taskId === 'string' ? record.taskId : undefined,
      thinkingEnabled: typeof record.thinkingEnabled === 'boolean' ? record.thinkingEnabled : undefined,
      pendingRun: normalizePersistedPendingRun(record.pendingRun)
    };
  }).filter(Boolean).slice(-MAX_SAVED_MESSAGES) as ChatMessage[];
};

const normalizeChatConversation = (
  value: unknown,
  defaultSymbol?: string,
  index = 0
): ChatConversation | null => {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const record = value as Record<string, unknown>;
  const messages = normalizeChatMessages(record.messages);
  const createdAt = typeof record.createdAt === 'string' ? record.createdAt : new Date().toISOString();
  const updatedAt = typeof record.updatedAt === 'string' ? record.updatedAt : createdAt;
  const fallbackTitle = index === 0 ? '新的投研对话' : `投研对话 ${index + 1}`;

  return {
    id: typeof record.id === 'string' ? record.id : uid(),
    title: typeof record.title === 'string'
      ? record.title
      : conversationTitleFromMessages(messages, fallbackTitle),
    createdAt,
    updatedAt,
    messages,
    activePromptStock: normalizeStockOption(record.activePromptStock),
    chatMode: isChatModeValue(record.chatMode) ? record.chatMode : 'research',
    reasoningMode: isReasoningModeValue(record.reasoningMode) ? record.reasoningMode : 'thinking',
    agentEngine: isAgentEngineValue(record.agentEngine) ? record.agentEngine : 'deepfocus',
    selectedSymbol: typeof record.selectedSymbol === 'string' ? record.selectedSymbol : defaultSymbol
  };
};

const loadHomeChatState = (defaultSymbol?: string): PersistedHomeChatState => {
  const fallbackConversation = createChatConversation({ selectedSymbol: defaultSymbol });
  if (typeof window === 'undefined') {
    return {
      version: 1,
      activeConversationId: fallbackConversation.id,
      conversations: [fallbackConversation]
    };
  }

  try {
    const raw = window.localStorage.getItem(HOME_CHAT_STORAGE_KEY);
    if (!raw) {
      return {
        version: 1,
        activeConversationId: fallbackConversation.id,
        conversations: [fallbackConversation]
      };
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const conversations = Array.isArray(parsed.conversations)
      ? parsed.conversations
          .map((item, index) => normalizeChatConversation(item, defaultSymbol, index))
          .filter(Boolean)
          .slice(0, MAX_SAVED_CONVERSATIONS) as ChatConversation[]
      : [];

    if (conversations.length === 0) {
      return {
        version: 1,
        activeConversationId: fallbackConversation.id,
        conversations: [fallbackConversation]
      };
    }

    const savedActiveId = typeof parsed.activeConversationId === 'string'
      ? parsed.activeConversationId
      : null;
    const activeConversationId = savedActiveId && conversations.some(item => item.id === savedActiveId)
      ? savedActiveId
      : conversations[0].id;

    return {
      version: 1,
      activeConversationId,
      conversations
    };
  } catch (error) {
    console.warn('Failed to load home chat state:', error);
    return {
      version: 1,
      activeConversationId: fallbackConversation.id,
      conversations: [fallbackConversation]
    };
  }
};

const persistHomeChatState = (activeConversationId: string | null, conversations: ChatConversation[]) => {
  if (typeof window === 'undefined') {
    return;
  }

  const payload: PersistedHomeChatState = {
    version: 1,
    activeConversationId,
    conversations: conversations
      .slice(0, MAX_SAVED_CONVERSATIONS)
      .map(conversation => ({
        ...conversation,
        title: conversationTitleFromMessages(conversation.messages, conversation.title),
        messages: conversation.messages.slice(-MAX_SAVED_MESSAGES)
      }))
  };

  try {
    window.localStorage.setItem(HOME_CHAT_STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Failed to persist home chat state:', error);
  }
};

const formatConversationMeta = (conversation: ChatConversation): string => {
  const updatedAt = new Date(conversation.updatedAt);
  const time = Number.isNaN(updatedAt.getTime())
    ? ''
    : updatedAt.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      });
  const questionCount = conversation.messages.filter(item => item.role === 'user').length;
  return [time, questionCount > 0 ? `${questionCount} 问` : '空对话'].filter(Boolean).join(' · ');
};

const takePendingAiDraft = (): PendingAiDraft | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(AI_DRAFT_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    window.localStorage.removeItem(AI_DRAFT_STORAGE_KEY);
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const prompt = typeof parsed.prompt === 'string' ? parsed.prompt.trim() : '';
    if (!prompt) {
      return null;
    }
    return {
      prompt,
      source: typeof parsed.source === 'string' ? parsed.source : undefined,
      references: Array.isArray(parsed.references)
        ? parsed.references.map(item => String(item || '').trim()).filter(Boolean)
        : undefined,
      skill: typeof parsed.skill === 'string' ? parsed.skill : undefined,
      createdAt: typeof parsed.createdAt === 'string' ? parsed.createdAt : undefined
    };
  } catch (error) {
    console.warn('Failed to read pending AI draft:', error);
    return null;
  }
};

const formatPendingAiDraft = (payload: PendingAiDraft): string => {
  const contextLines = [
    payload.source === 'research-workbench' ? '来源：研报工作台' : payload.source ? `来源：${payload.source}` : '',
    payload.skill ? `任务类型：${payload.skill}` : '',
    payload.references?.length ? `引用文件：${payload.references.join('、')}` : ''
  ].filter(Boolean);

  return contextLines.length > 0
    ? `${payload.prompt}\n\n${contextLines.join('\n')}`
    : payload.prompt;
};

const compactFileLabel = (files: File[]): string => (
  files.length > 0
    ? files.slice(0, 4).map(file => file.name).join('、') + (files.length > 4 ? ` 等 ${files.length} 个` : '')
    : '无附件'
);

const formatStockOptionLabel = (stock: StockOption | undefined, fallback: string): string => {
  if (stock?.symbol) {
    return `${stock.name || stock.symbol}（${stock.symbol}）`;
  }
  if (stock?.name) {
    return `${stock.name}（待补充代码）`;
  }
  return fallback;
};

const stockOptionToken = (stock: StockOption | undefined, fallback = 'portfolio'): string => (
  stock?.symbol || stock?.name || fallback
);

const serializePendingRun = (plan: PendingAgentRun): PersistedPendingAgentRun => ({
  id: plan.id,
  messageId: plan.messageId,
  userText: plan.userText,
  chatStock: plan.chatStock,
  chatMode: plan.chatMode,
  reasoningMode: plan.reasoningMode,
  agentEngine: plan.agentEngine,
  thinkingEnabled: plan.thinkingEnabled,
  dataSourceCount: plan.dataSourceCount,
  mcpServerCount: plan.mcpServerCount,
  modelProvider: plan.modelProvider,
  modelName: plan.modelName,
  createdAt: plan.createdAt,
  fileNames: plan.files.map(file => file.name),
  hasFiles: plan.files.length > 0
});

const restorePendingRun = (plan?: PersistedPendingAgentRun): PendingAgentRun | undefined => {
  if (!plan) {
    return undefined;
  }
  return {
    id: plan.id,
    messageId: plan.messageId,
    userText: plan.userText,
    files: [],
    chatStock: plan.chatStock,
    chatMode: plan.chatMode,
    reasoningMode: plan.reasoningMode,
    agentEngine: plan.agentEngine,
    thinkingEnabled: plan.thinkingEnabled,
    dataSourceCount: plan.dataSourceCount,
    mcpServerCount: plan.mcpServerCount,
    modelProvider: plan.modelProvider,
    modelName: plan.modelName,
    createdAt: plan.createdAt
  };
};

const formatPendingRunPlan = (plan: PendingAgentRun): string => {
  const engine = agentEngineMeta[plan.agentEngine];
  const stockLabel = formatStockOptionLabel(plan.chatStock, '未指定标的/组合级问题');
  return [
    '我先把这次多 Agent Run 收束成可确认的研究计划，确认后再写入后台队列。',
    '',
    `目标：${plan.userText}`,
    `标的：${stockLabel}`,
    `模式：${modeMeta[plan.chatMode].label} · ${plan.reasoningMode === 'thinking' ? '展示推理摘要' : '快速执行'}`,
    `引擎：${engine.label}`,
    `证据范围：${plan.dataSourceCount} 个数据源、${plan.mcpServerCount} 个 MCP 工具、附件 ${compactFileLabel(plan.files)}`,
    `模型：${plan.modelProvider || 'unknown'} / ${plan.modelName || 'unknown'}`,
    '',
    '执行标准：先查证据，再拆反证和亏损路径；事实、推断、风险、下一步动作分开；资料不足时明确降级或拒答，不把推测写成结论。'
  ].join('\n');
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const compactText = (value: string) => (
  value
    .trim()
    .replace(/[\s，。！？、；;:：,.!?~～"'“”‘’（）()【】[\]{}]/g, '')
    .toLowerCase()
);

const normalizePromptAssetMention = (value: string): string => (
  value
    .trim()
    .replace(/^(一下|下|看看|看下|看一下|这个|这只|这支|该股|该标的|当前标的)/, '')
    .replace(/(明日|明天|今天|昨日|后市|近期|未来|走势|趋势|股价|价格|表现|风险|基本面|技术面|财报|业绩|估值|机会|是否|能否|怎么样|如何).*$/, '')
    .replace(/[，。！？、；;:：,.!?~～"'“”‘’（）()【】[\]{}]/g, '')
    .trim()
);

const ignoredAssetMentions = new Set([
  '大盘',
  '市场',
  '港股',
  '美股',
  'a股',
  '行情',
  '组合',
  '持仓',
  '上传文件',
  '附件'
]);

const extractPromptAssetMention = (question: string): string | undefined => {
  const text = question.trim();
  const marketCodeMatch = text.match(/\b(?:0?\d{4,5}\.HK|[036]\d{5}\.(?:SH|SZ)|[A-Z]{1,5}\.[A-Z]{1,3})\b/i);
  if (marketCodeMatch) {
    return marketCodeMatch[0].toUpperCase();
  }

  const mentionPatterns = [
    /(?:预测|分析|研究|跟踪|复盘|审查|判断|观察|监控)\s*(?:一下|下)?\s*([\u4e00-\u9fffA-Za-z0-9.&-]{2,24})/,
    /(?:围绕|关于)\s*([\u4e00-\u9fffA-Za-z0-9.&-]{2,24})/,
    /(?:给|为)\s*([\u4e00-\u9fffA-Za-z0-9.&-]{2,24})\s*(?:做|生成|出|写|看|分析|预测|研究)/,
    /([\u4e00-\u9fffA-Za-z0-9.&-]{2,24})\s*(?:明日|明天|今天|后市|近期|未来)?\s*(?:走势|趋势|股价|风险|财报|业绩|估值|机会|预测|分析|研究)/
  ];

  for (const pattern of mentionPatterns) {
    const match = text.match(pattern);
    const mention = match ? normalizePromptAssetMention(match[1]) : '';
    if (mention && mention.length >= 2 && !ignoredAssetMentions.has(compactText(mention))) {
      return mention;
    }
  }

  return undefined;
};

const shouldUseFallbackStock = (question: string, fallback?: StockOption): boolean => {
  const text = question.trim();
  if (!fallback?.symbol) {
    return false;
  }
  if (!text) {
    return true;
  }

  const symbol = fallback.symbol.trim();
  const baseSymbol = symbol.split('.')[0];
  const compact = compactText(text);
  const fallbackName = fallback.name?.trim();
  const explicitlyMentionsFallback = Boolean(
    (fallbackName && compact.includes(compactText(fallbackName)))
    || new RegExp(`\\b${escapeRegExp(symbol)}\\b`, 'i').test(text)
    || (baseSymbol.length >= 3 && new RegExp(`\\b${escapeRegExp(baseSymbol)}\\b`, 'i').test(text))
  );
  if (explicitlyMentionsFallback) {
    return true;
  }

  if (/(它|这只|这支|该股|该标的|这个标的|当前标的|选中标的|当前股票|这家公司|其)/.test(text)) {
    return true;
  }

  return !extractPromptAssetMention(text);
};

const hasInvestmentIntent = (question: string, stock?: StockOption, attachedFileCount = 0): boolean => {
  if (attachedFileCount > 0) {
    return true;
  }

  const text = question.trim();
  if (!text) {
    return false;
  }

  const investmentKeywordPattern = /投研|投资|股票|个股|标的|证券|基金|ETF|财报|业绩|营收|利润|毛利|现金流|估值|市盈率|PE|EPS|目标价|股价|行情|K线|技术面|基本面|公告|研报|新闻|催化|风险|风控|仓位|持仓|增持|减持|增减持|股东|股份变动|持股变动|组合|复盘|监控|观察|跟踪|买入|卖出|买|卖|看多|看空|看涨|看跌|多空|止损|止盈|回撤|收益|亏损|分析|研究|预测|机会|反证|证据|三表|模型|路演|Pitch|尽调|KYC|对账|月结|关账|analy[sz]e|research|stock|share|earning|earnings|revenue|valuation|portfolio|risk|price target|bullish|bearish|buy|sell|hold|DCF|LBO|comps|reconcile/i;
  if (investmentKeywordPattern.test(text)) {
    return true;
  }

  if (stock?.symbol && new RegExp(`\\b${escapeRegExp(stock.symbol)}\\b`, 'i').test(text)) {
    return true;
  }

  if (stock?.name && stock.name.length > 1 && text.includes(stock.name)) {
    return true;
  }

  if (knownStockAliases.some(stockAlias => (
    stockAlias.aliases.some(alias => compactText(text).includes(compactText(alias)))
  ))) {
    return true;
  }

  const tickerPattern = /\b(?!HI\b|OK\b|YES\b|NO\b|HELP\b|THANKS\b)[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b/;
  return tickerPattern.test(text);
};

const resolvePromptStock = (
  question: string,
  stocks: StockOption[],
  fallback?: StockOption
): StockOption | undefined => {
  const text = question.trim();
  const compact = compactText(text);
  if (!text) {
    return fallback;
  }

  const matchedWatchStock = stocks.find(stock => {
    const symbol = stock.symbol?.trim();
    const name = stock.name?.trim();
    const baseSymbol = symbol?.split('.')[0];
    return Boolean(
      (name && name.length > 1 && compact.includes(compactText(name)))
      || (symbol && new RegExp(`\\b${escapeRegExp(symbol)}\\b`, 'i').test(text))
      || (baseSymbol && baseSymbol.length >= 3 && new RegExp(`\\b${escapeRegExp(baseSymbol)}\\b`, 'i').test(text))
    );
  });
  if (matchedWatchStock) {
    return matchedWatchStock;
  }

  const matchedKnownStock = knownStockAliases.find(stock => (
    stock.aliases.some(alias => compact.includes(compactText(alias)))
  ));
  if (matchedKnownStock) {
    return matchedKnownStock;
  }

  const explicitAssetMention = extractPromptAssetMention(text);
  if (explicitAssetMention) {
    return { name: explicitAssetMention };
  }

  return shouldUseFallbackStock(text, fallback) ? fallback : undefined;
};

const hasProfessionalReportIntent = (question: string, files: File[] = []): boolean => {
  const text = `${question} ${files.map(file => file.name).join(' ')}`;
  return /专业财报|财报库|财报|年报|半年报|季报|季报|研报|电话会|业绩|营收|利润|扣非|毛利率|ROE|现金流|资本开支|引用|溯源|评测|pdf|annual|quarter|earnings|transcript|research/i.test(text);
};

const buildLocalPlainChatReply = (question: string): string | null => {
  const compact = compactText(question);
  if (/^(你好|您好|嗨|哈喽|hello|hi|hey|在吗|在不在|早上好|上午好|中午好|下午好|晚上好)$/i.test(compact)) {
    return '你好，我在。今天想聊点什么？';
  }
  if (/^(谢谢|谢了|感谢|多谢|thanks|thankyou|thx)$/i.test(compact)) {
    return '不客气。';
  }
  if (/^(好的|好|ok|收到|明白|了解|嗯|嗯嗯)$/i.test(compact)) {
    return '好，我跟着。你继续说。';
  }
  if (/^(ping|测试|测试一下|联通测试)$/i.test(compact)) {
    return '在，连接正常。';
  }
  if (/^(你是谁|你能做什么|你会做什么|能干嘛|帮助|help)$/i.test(compact)) {
    return '我是 DeepFocus。你可以正常和我聊天；需要时，我也能帮你读研报、找证据、做风险复核和生成投研任务。';
  }
  return null;
};

const guideAction = (action: Omit<ChatGuideAction, 'id'>): ChatGuideAction => ({
  id: [
    action.kind,
    action.label,
    action.prompt || action.view || action.mode || action.reasoning || action.agent || ''
  ].join(':'),
  ...action
});

const buildGeneratingGuide = (
  kind: 'plain' | 'agent' | 'report' | 'research-hit',
  count = 0
): ChatGuidePanel => {
  if (kind === 'agent') {
    return {
      variant: 'generating',
      title: '正在判断是否进入分析 Agent',
      description: '我会先判断这是普通对话还是投研任务；确认需要证据链时，才调度 Orchestrator 和研究链路。',
      steps: [
        { label: '理解你的目标', status: 'done' },
        { label: '匹配标的、文件和研报上下文', status: 'working' },
        { label: '生成可执行下一步', status: 'wait' }
      ]
    };
  }

  if (kind === 'report') {
    return {
      variant: 'research',
      title: '正在生成研报解读界面',
      description: `已接入 ${count || 1} 份入库研报，正在整理摘要、指标、红旗、风险和后续追问。`,
      steps: [
        { label: '读取入库正文', status: 'working' },
        { label: '抽取关键指标', status: 'wait' },
        { label: '生成追问选项', status: 'wait' }
      ]
    };
  }

  if (kind === 'research-hit') {
    return {
      variant: 'research',
      title: '正在生成标题快筛界面',
      description: `基于 ${count} 条搜索命中做主题聚类、风险缺口和下载优先级判断，不假装读过 PDF 正文。`,
      steps: [
        { label: '聚类标题线索', status: 'working' },
        { label: '识别重复和缺口', status: 'wait' },
        { label: '输出下载优先级', status: 'wait' }
      ]
    };
  }

  return {
    variant: 'generating',
    title: '正在组织回复',
    description: '先按普通聊天回答；如果你接下来给出标的、研报或风险目标，我再切到分析 Agent。',
    steps: [
      { label: '理解上下文', status: 'done' },
      { label: '生成回答', status: 'working' },
      { label: '准备下一步选项', status: 'wait' }
    ]
  };
};

const buildPlainReplyGuide = (question: string, stock?: StockOption): ChatGuidePanel => {
  const stockLabel = stockOptionToken(stock, '一个标的');
  const isHelpLike = /帮助|怎么用|你能做什么|你是谁|能干嘛|help/i.test(question);

  return {
    variant: 'ready',
    title: isHelpLike ? '可以这样开始' : '你可以继续这样做',
    description: '我可以继续普通聊天，也可以在你需要时切换成有证据链的投研分析。',
    actions: [
      guideAction({
        kind: 'prompt',
        label: '分析一个标的',
        detail: '生成带支撑位、风险和情景的投研问题',
        prompt: `分析 ${stockLabel} 明日走势，给我支撑、压力、风险和盘中观察点`,
        primary: true
      }),
      guideAction({
        kind: 'context',
        label: '加入研报或文件',
        detail: '打开上下文面板，勾选入库研报或上传文件'
      }),
      guideAction({
        kind: 'view',
        label: '去研报工作台',
        detail: '抓取、入库、精读研报',
        view: 'research-workbench'
      })
    ]
  };
};

const buildAgentReplyGuide = (
  question: string,
  stock?: StockOption,
  suggestedActions: string[] = [],
  shouldCreateTask = false
): ChatGuidePanel => {
  const stockLabel = stockOptionToken(stock, '当前标的');
  const promptActions = suggestedActions
    .map(action => cleanUserFacingReportText(action))
    .filter(Boolean)
    .slice(0, 2)
    .map(action => guideAction({
      kind: 'prompt' as GuideActionKind,
      label: action.length > 18 ? `${action.slice(0, 18)}...` : action,
      detail: '放入输入框继续追问',
      prompt: action
    }));

  return {
    variant: shouldCreateTask ? 'analysis' : 'ready',
    title: shouldCreateTask ? '建议进入后台 Run 前再确认一次' : '下一步怎么推进',
    description: shouldCreateTask
      ? '我已经把任务路径收束出来了；你可以确认执行，也可以先补充证据范围。'
      : '可以继续追问反证、拉入证据，或把当前判断升级成完整 Agent 分析。',
    actions: [
      ...promptActions,
      guideAction({
        kind: 'prompt',
        label: '继续拆反证',
        detail: '先找会推翻结论的条件',
        prompt: `继续拆 ${stockLabel} 的反证、亏损路径和需要验证的数据`,
        primary: promptActions.length === 0
      }),
      guideAction({
        kind: 'context',
        label: '补充证据',
        detail: '加入研报、资料文件或数据源'
      })
    ].slice(0, 4)
  };
};

const buildResearchReplyGuide = (
  kind: 'report' | 'hit-summary' | 'download',
  count = 0,
  keyword = ''
): ChatGuidePanel => {
  if (kind === 'download') {
    return {
      variant: 'research',
      title: '下载后可以继续精读',
      description: '全文下载完成后，把研报入库再做正文级解读，结论会比标题快筛可靠得多。',
      actions: [
        guideAction({
          kind: 'view',
          label: '打开研报工作台',
          detail: '查看下载、入库和索引状态',
          view: 'research-workbench',
          primary: true
        }),
        guideAction({
          kind: 'prompt',
          label: '生成精读要求',
          detail: '准备下一步正文解读问题',
          prompt: '下载完成后，帮我精读刚入库的研报，提取投资判断、关键指标、风险和可验证问题'
        })
      ]
    };
  }

  if (kind === 'hit-summary') {
    return {
      variant: 'research',
      title: '接下来建议做全文验证',
      description: `这只是 ${count} 条标题级命中的快筛；需要下载和入库后，才能做正文引用核验。`,
      actions: [
        guideAction({
          kind: 'context',
          label: '选择并下载全文',
          detail: '回到研报证据面板勾选最相关资料',
          primary: true
        }),
        guideAction({
          kind: 'prompt',
          label: '只看优先级',
          detail: '让 AI 再压缩下载清单',
          prompt: `基于刚才的 ${keyword || '研报'} 命中，只给我最值得下载的 Top 10 和理由`
        }),
        guideAction({
          kind: 'view',
          label: '研报工作台',
          detail: '完整抓取、入库、解析',
          view: 'research-workbench'
        })
      ]
    };
  }

  return {
    variant: 'research',
    title: '已具备正文级追问条件',
    description: `已完成 ${count || 1} 份入库研报解读，可以继续问指标、风险、反证或生成投委会摘要。`,
    actions: [
      guideAction({
        kind: 'prompt',
        label: '生成投委会摘要',
        detail: '把研报压缩成决策页',
        prompt: '基于刚才的入库研报解读，生成一页投委会摘要：结论、证据、风险、反证和下一步动作',
        primary: true
      }),
      guideAction({
        kind: 'prompt',
        label: '追问关键指标',
        detail: '只看数字和假设',
        prompt: '继续追问刚才研报中的关键指标、预测假设、目标价和需要核验的数据'
      }),
      guideAction({
        kind: 'view',
        label: '打开研报工作台',
        detail: '查看原文和入库状态',
        view: 'research-workbench'
      })
    ]
  };
};

const buildErrorGuide = (retryPrompt?: string): ChatGuidePanel => ({
  variant: 'error',
  title: '这一步没有完成',
  description: '可以重试同一个问题，或先补充证据后再让 Agent 分析。',
  actions: [
    guideAction({
      kind: 'prompt',
      label: '重试刚才的问题',
      detail: '把原问题放回输入框',
      prompt: retryPrompt || '请重试刚才的问题',
      primary: true
    }),
    guideAction({
      kind: 'context',
      label: '检查上下文',
      detail: '打开研报、文件和数据源面板'
    })
  ]
});

const inferProfessionalReportType = (value: string): ProfessionalReportType => {
  if (/年报|年度|annual|10-k/i.test(value)) return 'annual';
  if (/半年|半年度|semi|interim|h1/i.test(value)) return 'semiannual';
  if (/季报|季度|quarter|q[1-4]|10-q/i.test(value)) return 'quarterly';
  if (/电话会|纪要|transcript|call/i.test(value)) return 'transcript';
  if (/研报|research/i.test(value)) return 'research';
  return 'other';
};

const buildAgentConsoleFallback = (
  question: string,
  stock?: StockOption,
  agentEngine: AgentEngine = 'deepfocus',
  mode: ChatMode = 'research',
  attachedFileCount = 0,
  shouldRunTask = false
): Pick<ChatMessage, 'title' | 'content' | 'chips' | 'reasoningTrace'> => {
  const compact = compactText(question);
  const greetingPattern = /^(你好|您好|嗨|哈喽|hello|hi|hey|在吗|在不在|早上好|上午好|中午好|下午好|晚上好)$/i;
  const thanksPattern = /^(谢谢|谢了|感谢|多谢|thanks|thankyou|thx|好的|好|ok|收到|明白|了解|嗯|嗯嗯)$/i;
  const helpPattern = /你是谁|你能做什么|你会做什么|怎么用|如何使用|使用说明|帮助|help|能干嘛/i;
  const engine = agentEngineMeta[agentEngine];
  const stockLabel = formatStockOptionLabel(stock, '当前工作区');

  if (!shouldRunTask && attachedFileCount === 0) {
    if (greetingPattern.test(compact)) {
      return {
        title: 'DeepFocus',
        content: '你好，我在。你可以直接和我聊天；聊到标的、研报、文件或风险时，我再切到投研工作流。',
        chips: [],
        reasoningTrace: []
      };
    }

    if (thanksPattern.test(compact)) {
      return {
        title: 'DeepFocus',
        content: '不客气。你继续说就行，我会跟着当前对话走。',
        chips: [],
        reasoningTrace: []
      };
    }

    if (helpPattern.test(question)) {
      return {
        title: 'DeepFocus',
        content: `我可以正常聊天，也可以在你需要时切到投研模式：找证据、读研报、查风险、做组合复核或创建 Agent 任务。当前可用引擎是 ${engine.label}。`,
        chips: ['聊天', '投研', engine.shortLabel],
        reasoningTrace: []
      };
    }

    return {
      title: 'DeepFocus',
      content: '我在，但刚刚没有连上模型回复通道。你可以直接重试这句话；如果是投研问题，我也可以退回到本地 Agent 工作流继续接住。',
      chips: ['模型待重试'],
      reasoningTrace: []
    };
  }

  if (greetingPattern.test(compact)) {
    return {
      title: 'OrchestratorAgent',
      content: `你好，我在。这里是 DeepFocus 多 Agent 工作台。你可以像使用 Claude Code 一样直接把目标交给我：我会先由 Orchestrator 理解任务，再按需要调度 ${engine.agents.join(' / ')}。\n\n当前上下文已选中 ${stockLabel}。你可以直接说“分析它的风险”“复盘这份研报”“生成明早监控清单”。`,
      chips: ['OrchestratorAgent', engine.shortLabel, '随时可编排'],
      reasoningTrace: buildRouteTrace(question, stock, agentEngine, mode, attachedFileCount, shouldRunTask)
    };
  }

  if (thanksPattern.test(compact)) {
    return {
      title: 'OrchestratorAgent',
      content: '收到。我会保持 Agent 工作台状态，后续你直接继续下达目标即可；如果问题涉及标的、研报、风险、仓位或事件，我会自动进入多 Agent 编排。',
      chips: ['OrchestratorAgent', '上下文已保留'],
      reasoningTrace: buildRouteTrace(question, stock, agentEngine, mode, attachedFileCount, shouldRunTask)
    };
  }

  if (helpPattern.test(question)) {
    return {
      title: 'DeepFocus 多 Agent',
      content: `我可以作为总调度入口使用：\n- OrchestratorAgent 理解目标并决定是否建任务\n- EvidenceAgent 对齐数据源、研报、新闻、公告和上传文件\n- ResearchAgent 梳理基本面、情绪和情景假设\n- RiskAgent 先找亏损路径、反证和仓位纪律\n- ReportAgent 合成投资者可读结论\n\n当前引擎是 ${engine.label}：${engine.description}。`,
      chips: ['OrchestratorAgent', ...engine.agents.slice(0, 3)],
      reasoningTrace: buildRouteTrace(question, stock, agentEngine, mode, attachedFileCount, shouldRunTask)
    };
  }

  return {
    title: 'OrchestratorAgent',
    content: `我已接住这条消息。为了让多 Agent 输出更像可执行研究任务，可以继续补充三个信息之一：标的、时间范围、你想判断的问题。\n\n例如：围绕 ${stockOptionToken(stock, '当前标的')} 做“风险优先审查”、把上传文件整理成证据链，或让 ReportAgent 给出观察名单动作。`,
    chips: ['OrchestratorAgent', '等待目标', engine.shortLabel],
    reasoningTrace: buildRouteTrace(question, stock, agentEngine, mode, attachedFileCount, shouldRunTask)
  };
};

const decisionLabel: Record<string, string> = {
  avoid: '暂不行动',
  watch: '观察',
  research_more: '继续研究',
  candidate: '候选机会'
};

const statusLabel: Record<ChatReasoningStatus, string> = {
  done: '完成',
  working: '进行中',
  wait: '等待',
  error: '异常'
};

const normalizeTrace = (steps?: OrchestratorReasoningStep[]): ChatReasoningStep[] => (
  (steps || [])
    .filter(step => step.title || step.detail)
    .slice(0, 5)
    .map(step => ({
      phase: step.phase || 'step',
      title: step.title || '思路摘要',
      detail: step.detail || '已进入 Agent 工作流。',
      status: step.status || 'done'
    }))
);

const buildRouteTrace = (
  question: string,
  stock?: StockOption,
  engine: AgentEngine = 'deepfocus',
  mode: ChatMode = 'research',
  attachedFileCount = 0,
  shouldRunTask = false
): ChatReasoningStep[] => {
  const engineMeta = agentEngineMeta[engine];
  const stockLabel = formatStockOptionLabel(stock, '未选择标的');
  if (!shouldRunTask && !stock?.symbol && attachedFileCount === 0) {
    return [
      {
        phase: 'orchestrator',
        title: 'OrchestratorAgent',
        detail: '识别为普通问答，优先按用户问题直接回复。',
        status: 'done'
      },
      {
        phase: 'evidence',
        title: 'EvidenceAgent',
        detail: '当前不需要调用行情、证据链或上传文件。',
        status: 'done'
      },
      {
        phase: 'report',
        title: 'ReportAgent',
        detail: '整理为简短回答；如果用户继续给出投资目标，再升级为多 Agent Run。',
        status: 'done'
      }
    ];
  }

  return [
    {
      phase: 'orchestrator',
      title: 'OrchestratorAgent',
      detail: `${modeMeta[mode].label}模式，理解目标和标的：${stockLabel}。`,
      status: 'done'
    },
    {
      phase: 'evidence',
      title: 'EvidenceAgent',
      detail: `${attachedFileCount} 个附件，准备连同数据源交给 ${engineMeta.shortLabel} 判断。`,
      status: 'done'
    },
    {
      phase: 'research',
      title: 'ResearchAgent',
      detail: shouldRunTask ? '识别为投资研究目标，适合进入后台研究任务。' : '适合即时回答，暂不创建后台任务。',
      status: shouldRunTask ? 'working' : 'done'
    },
    {
      phase: 'risk',
      title: 'RiskAgent',
      detail: '回答会区分事实、推断、反证和动作，不承诺收益。',
      status: shouldRunTask ? 'wait' : 'done'
    },
    {
      phase: 'report',
      title: 'ReportAgent',
      detail: shouldRunTask ? '等待研究完成后输出行动清单。' : '把即时回复整理成可继续执行的下一步。',
      status: shouldRunTask ? 'wait' : 'done'
    }
  ];
};

const phaseFromAgent = (agent?: string | null): CoreAgentPhase => {
  const text = (agent || '').toLowerCase();
  if (text.includes('orchestrator') || text.includes('taskcenter')) return 'orchestrator';
  if (text.includes('datasource') || text.includes('evidence')) return 'evidence';
  if (
    text.includes('analyst') ||
    text.includes('research') ||
    text.includes('sentiment') ||
    text.includes('scenario') ||
    text.includes('debate') ||
    text.includes('trader')
  ) return 'research';
  if (text.includes('risk')) return 'risk';
  if (text.includes('portfolio') || text.includes('report') || text.includes('resultmapper') || text.includes('modelrouter')) return 'report';
  return 'orchestrator';
};

const coreAgentName = (agent?: string | null): string => {
  const phase = phaseFromAgent(agent);
  const names: Record<CoreAgentPhase, string> = {
    orchestrator: 'OrchestratorAgent',
    evidence: 'EvidenceAgent',
    research: 'ResearchAgent',
    risk: 'RiskAgent',
    report: 'ReportAgent'
  };
  return names[phase];
};

const AGENT_HEARTBEAT_WARN_MS = 90_000;

const taskHeartbeatLagMs = (task: InvestmentTaskRecord): number => {
  if (task.status !== 'running') return 0;
  const updatedAt = new Date(task.updated_at).getTime();
  return Number.isFinite(updatedAt) ? Math.max(0, Date.now() - updatedAt) : 0;
};

const formatLag = (lagMs: number): string => {
  const seconds = Math.max(0, Math.round(lagMs / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
};

const isHeartbeatDelayed = (task: InvestmentTaskRecord): boolean => (
  taskHeartbeatLagMs(task) >= AGENT_HEARTBEAT_WARN_MS
);

const runningTaskMessage = (task: InvestmentTaskRecord): string => {
  const latestLog = task.logs[task.logs.length - 1]?.message;
  const lagMs = taskHeartbeatLagMs(task);
  if (lagMs >= AGENT_HEARTBEAT_WARN_MS) {
    return (
      `外部引擎已有 ${formatLag(lagMs)} 没有更新心跳。`
      + '如果正在热重载或模型接口卡住，后端会自动标记失败；可以打开 Agent 任务取消或重跑。'
    );
  }
  return latestLog || '任务引擎正在生成报告，完成后会自动回到这里。';
};

const traceFromTask = (task: InvestmentTaskRecord): ChatReasoningStep[] => {
  const findings = task.result?.agent_findings;
  if (task.status === 'completed' && findings && typeof findings === 'object') {
    const phaseMeta: Array<{
      phase: CoreAgentPhase;
      title: string;
      fallback: string;
    }> = [
      {
        phase: 'orchestrator',
        title: '目标与约束',
        fallback: `围绕 ${task.asset_name || task.symbol || '目标资产'} 建立投研问题，先判断能不能形成可执行结论。`
      },
      {
        phase: 'evidence',
        title: '证据质量',
        fallback: '检查资料是否足以支撑结论，资料不足时降低置信度并列出缺口。'
      },
      {
        phase: 'research',
        title: '核心假设',
        fallback: '拆分商业质量、增长驱动、竞争格局和估值验证。'
      },
      {
        phase: 'risk',
        title: '反证与亏损路径',
        fallback: '先找会让判断失效的条件，再给仓位和止损纪律。'
      },
      {
        phase: 'report',
        title: '行动结论',
        fallback: '把证据、情景和风险压缩为观察、继续研究或候选动作。'
      }
    ];

    return phaseMeta.map(meta => {
      const value = (findings as Record<string, unknown>)[meta.phase];
      const items = Array.isArray(value)
        ? value.map(item => String(item).trim()).filter(Boolean)
        : [];
      return {
        phase: meta.phase,
        title: meta.title,
        detail: items.length > 0 ? items.slice(0, 2).join('；') : meta.fallback,
        status: 'done' as ChatReasoningStatus
      };
    });
  }

  const latestLogs = task.logs.slice(-5);
  const steps = latestLogs.map(log => ({
    phase: phaseFromAgent(log.agent),
    title: coreAgentName(log.agent),
    detail: log.message,
    status: task.status === 'failed' ? 'error' as ChatReasoningStatus : 'done' as ChatReasoningStatus
  }));
  const currentStatus: ChatReasoningStatus = task.status === 'failed'
    ? 'error'
    : task.status === 'completed'
      ? 'done'
      : 'working';

  return [
    {
      phase: 'progress',
      title: isHeartbeatDelayed(task) ? `任务心跳延迟 · ${task.progress}%` : `任务进度 ${task.progress}%`,
      detail: isHeartbeatDelayed(task)
        ? runningTaskMessage(task)
        : `${coreAgentName(task.assigned_agent)} 正在处理 ${task.symbol || task.asset_name || '投资任务'}。`,
      status: currentStatus
    },
    ...steps
  ].slice(0, 6);
};

const traceStepFromEvent = (event: AgentRunEvent): ChatReasoningStep => {
  const status: ChatReasoningStatus = event.type === 'error'
    ? 'error'
    : event.type === 'run_complete' || event.type === 'tool_result' || event.type === 'artifact_update'
      ? 'done'
      : 'working';

  return {
    phase: event.phase || phaseFromAgent(event.agent),
    title: event.agent || coreAgentName(event.agent),
    detail: event.message || event.title,
    status
  };
};

const mergeTraceStep = (
  steps: ChatReasoningStep[] | undefined,
  next: ChatReasoningStep
): ChatReasoningStep[] => {
  const existing = steps || [];
  const nextKey = `${next.phase}:${next.title}:${next.detail}`;
  const filtered = existing.filter(step => `${step.phase}:${step.title}:${step.detail}` !== nextKey);
  return [...filtered, next].slice(-6);
};

const formatAgentResultForChat = (task: InvestmentTaskRecord): string => {
  const result = task.result;
  if (!result) {
    return task.error || '任务结束但没有返回报告内容。';
  }

  const actionItems = (result.action_plan || []).slice(0, 6);
  const risks = (result.risk_controls || result.disconfirming_evidence || []).slice(0, 5);
  const researchFindings = (result.agent_findings?.research || []).slice(0, 5);
  const evidenceFindings = (result.agent_findings?.evidence || []).slice(0, 3);
  const scenarios = (result.scenarios || []).slice(0, 3);
  const disconfirming = (result.disconfirming_evidence || []).slice(0, 4);
  const watchlist = (result.watchlist || []).slice(0, 4);
  const evidenceCount = Array.isArray(result.evidence) ? result.evidence.length : 0;
  const parts = [
    result.investor_summary,
    '',
    `结论：${decisionLabel[result.decision] || result.decision} · 置信度 ${Math.round((result.confidence || 0) * 100)}%`,
    result.plain_language_takeaway ? `白话总结：${result.plain_language_takeaway}` : '',
    researchFindings.length ? `核心研判：\n${researchFindings.map(item => `- ${item}`).join('\n')}` : '',
    scenarios.length ? `情景框架：\n${scenarios.map(item => `- ${item.case}（${item.probability}%）：${item.thesis}`).join('\n')}` : '',
    actionItems.length ? `下一步：\n${actionItems.map(item => `- ${item}`).join('\n')}` : '',
    watchlist.length ? `观察清单：\n${watchlist.map(item => `- ${item}`).join('\n')}` : '',
    disconfirming.length ? `反证条件：\n${disconfirming.map(item => `- ${item}`).join('\n')}` : '',
    risks.length ? `风险控制：\n${risks.map(item => `- ${item}`).join('\n')}` : '',
    evidenceFindings.length ? `证据状态（${evidenceCount} 条入选）：\n${evidenceFindings.map(item => `- ${item}`).join('\n')}` : ''
  ];

  return parts.filter(Boolean).join('\n');
};

const ReasoningTrace: React.FC<{
  steps?: ChatReasoningStep[];
  mode?: ReasoningMode;
  compact?: boolean;
}> = ({ steps, mode = 'thinking', compact = false }) => {
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);

  useEffect(() => {
    setManualOpen(null);
  }, [compact]);

  if (!steps || steps.length === 0) {
    return null;
  }

  const open = manualOpen ?? !compact;
  const visibleSteps = compact ? steps.slice(-3) : steps.slice(0, 6);
  const workingCount = visibleSteps.filter(step => step.status === 'working').length;
  const doneCount = visibleSteps.filter(step => step.status === 'done').length;

  return (
    <div className={`agent-chat-reasoning ${open ? 'open' : 'collapsed'} ${compact ? 'compact' : 'full'}`}>
      <button className="agent-chat-reasoning-head" type="button" onClick={() => setManualOpen(value => !(value ?? !compact))}>
        <span><RobotOutlined /> {mode === 'fast' ? '快速路由' : '思考过程'}</span>
        <span className="agent-chat-reasoning-summary">
          {compact && !open ? `${visibleSteps.length} 个关键节点` : workingCount > 0 ? `${workingCount} 进行中` : `${doneCount}/${visibleSteps.length} 完成`}
          <Tag>{open ? '收起' : '展开'}</Tag>
        </span>
      </button>
      <Text type="secondary" className="agent-chat-reasoning-note">
        可展示推理摘要，不展示模型隐藏思维原文。
      </Text>
      {open && (
      <div className="agent-chat-reasoning-steps">
        {visibleSteps.map((step, index) => (
          <div key={`${step.phase}-${step.title}-${index}`} className={`agent-chat-reasoning-step ${step.status}`}>
            <span className="reasoning-step-dot" />
            <div>
              <div className="reasoning-step-title">
                <strong>{step.title}</strong>
                <Tag>{statusLabel[step.status]}</Tag>
              </div>
              <Text type="secondary">{step.detail}</Text>
            </div>
          </div>
        ))}
      </div>
      )}
    </div>
  );
};

const formatChatMessageForCopy = (item: ChatMessage): string => {
  const sections: string[] = [];

  if (item.title) {
    sections.push(item.title);
  }

  if (item.reasoningTrace && item.reasoningTrace.length > 0) {
    sections.push([
      '思考过程',
      ...item.reasoningTrace.map(step => `- [${step.status}] ${step.title}: ${step.detail}`)
    ].join('\n'));
  }

  if (item.attachments && item.attachments.length > 0) {
    sections.push([
      '附件',
      ...item.attachments.map(attachment => (
        `- ${attachment.name} (${attachmentKindLabel[attachment.kind]} · ${formatFileSize(attachment.size)})`
      ))
    ].join('\n'));
  }

  if (item.content.trim()) {
    sections.push(item.content.trim());
  }

  if (item.reportCards && item.reportCards.length > 0) {
    sections.push(item.reportCards.map(card => [
      card.title,
      card.summary,
      card.metrics.length ? `关键指标：${card.metrics.join('；')}` : '',
      card.flags.length ? `红旗：${card.flags.join('；')}` : '',
      card.risks.length ? `风险：${card.risks.join('；')}` : '',
      card.questions.length ? `后续追问：${card.questions.join('；')}` : '',
      `引用块：${card.citations} · 置信度：${Math.round(card.confidence * 100)}%`
    ].filter(Boolean).join('\n')).join('\n\n'));
  }

  if (item.guide?.actions && item.guide.actions.length > 0) {
    sections.push([
      item.guide.title,
      ...item.guide.actions.map(action => `- ${action.label}${action.detail ? `：${action.detail}` : ''}`)
    ].join('\n'));
  }

  if (item.chips && item.chips.length > 0) {
    sections.push(`标签：${item.chips.join(' / ')}`);
  }

  return sections.filter(Boolean).join('\n\n');
};

const copyTextToClipboard = async (text: string) => {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Use the DOM fallback below when browser clipboard permissions are unavailable.
    }
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
};

const AttachmentPreviewList: React.FC<{
  attachments: ChatAttachmentMeta[];
  compact?: boolean;
  onRemove?: (attachment: ChatAttachmentMeta) => void;
}> = ({ attachments, compact = false, onRemove }) => {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <div className={`chat-attachment-list ${compact ? 'compact' : ''}`}>
      {attachments.map(attachment => (
        <div key={attachment.id} className={`chat-attachment-pill ${attachment.kind}`}>
          <span className="chat-attachment-icon">{attachmentIcon(attachment.kind)}</span>
          <span className="chat-attachment-copy">
            <strong>{attachment.name}</strong>
            <small>{attachmentKindLabel[attachment.kind]} · {formatFileSize(attachment.size)}</small>
          </span>
          {onRemove && (
            <Button
              type="text"
              size="small"
              shape="circle"
              icon={<DeleteOutlined />}
              aria-label={`移除 ${attachment.name}`}
              onClick={() => onRemove(attachment)}
            />
          )}
        </div>
      ))}
    </div>
  );
};

const HomePage: React.FC<HomePageProps> = ({
  appState,
  onStockSelect,
  onProductClick,
  onAddToCart,
  onViewChange,
  onAddStock,
  onRemoveStock,
  onToggleStockSubscription,
  onRefreshMarketData,
  isMarketDataRefreshing
}) => {
  const { message } = AntdApp.useApp();
  const initialChatState = useMemo(() => loadHomeChatState(appState.stocks[0]?.symbol), []);
  const initialConversation = useMemo(
    () => initialChatState.conversations.find(item => item.id === initialChatState.activeConversationId)
      || initialChatState.conversations[0],
    [initialChatState]
  );
  const [draft, setDraft] = useState('');
  const [chatMode, setChatMode] = useState<ChatMode>(initialConversation?.chatMode || 'research');
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>(initialConversation?.reasoningMode || 'thinking');
  const [threadViewMode, setThreadViewMode] = useState<ThreadViewMode>('compact');
  const [agentEngine, setAgentEngine] = useState<AgentEngine>(initialConversation?.agentEngine || 'deepfocus');
  const [selectedSymbol, setSelectedSymbol] = useState(initialConversation?.selectedSymbol || appState.stocks[0]?.symbol);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [composerDragActive, setComposerDragActive] = useState(false);
  const [sending, setSending] = useState(false);
  const [modelConfig, setModelConfig] = useState<ModelConfig | null>(null);
  const [dataSources, setDataSources] = useState<DataSourceRecord[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [tasks, setTasks] = useState<InvestmentTaskRecord[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>(initialConversation?.messages || []);
  const [conversations, setConversations] = useState<ChatConversation[]>(initialChatState.conversations);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(initialChatState.activeConversationId);
  const [activePromptStock, setActivePromptStock] = useState<StockOption | undefined>(initialConversation?.activePromptStock);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [contextRailOpen, setContextRailOpen] = useState(false);
  const [pendingRuns, setPendingRuns] = useState<Record<string, PendingAgentRun>>({});
  const [researchKeyword, setResearchKeyword] = useState(initialConversation?.activePromptStock?.name || appState.stocks[0]?.name || '');
  const [indexedReportQuery, setIndexedReportQuery] = useState('');
  const [researchTag, setResearchTag] = useState('海外投行报告');
  const [researchResults, setResearchResults] = useState<ResearchWorkbenchSearchItem[]>([]);
  const [selectedResearchKeys, setSelectedResearchKeys] = useState<Set<string>>(() => new Set());
  const [professionalReports, setProfessionalReports] = useState<ProfessionalReportRecord[]>([]);
  const [selectedProfessionalReportIds, setSelectedProfessionalReportIds] = useState<Set<string>>(() => new Set());
  const [evidenceItems, setEvidenceItems] = useState<DataSourceItemRecord[]>([]);
  const [selectedEvidenceItemIds, setSelectedEvidenceItemIds] = useState<Set<string>>(() => new Set());
  const [researchSearching, setResearchSearching] = useState(false);
  const [researchSummarizing, setResearchSummarizing] = useState(false);
  const [researchDownloading, setResearchDownloading] = useState(false);
  const [professionalReportsLoading, setProfessionalReportsLoading] = useState(false);
  const [evidenceItemsLoading, setEvidenceItemsLoading] = useState(false);
  const [professionalInterpreting, setProfessionalInterpreting] = useState(false);
  const activeStreamStops = useRef<Record<string, () => void>>({});
  const chatStageRef = useRef<HTMLElement | null>(null);
  const composerRef = useRef<HTMLDivElement | null>(null);

  const selectedStock = useMemo(
    () => appState.stocks.find(stock => stock.symbol === selectedSymbol) || appState.stocks[0],
    [appState.stocks, selectedSymbol]
  );

  const strongestStock = useMemo(
    () => [...appState.stocks].sort((a, b) => b.changePercent - a.changePercent)[0],
    [appState.stocks]
  );

  const realQuoteCount = appState.stocks.filter(stock => stock.quoteProvider && stock.quoteProvider !== 'mock').length;
  const quoteAnchor = appState.stocks.find(stock => stock.quoteProvider && stock.quoteProvider !== 'mock')
    || appState.stocks[0];
  const sourceItemsCount = dataSources.reduce((sum, source) => sum + (source.items_count || 0), 0);
  const connectedMcpCount = mcpServers.filter(server => server.status === 'connected').length;
  const runningTasks = tasks.filter(task => task.status === 'pending' || task.status === 'running').length;
  const subscribedCount = appState.stocks.filter(stock => stock.isSubscribed ?? true).length;
  const marketSegmentCounts = useMemo(
    () => countStocksBySegment(appState.stocks),
    [appState.stocks]
  );
  const activeEngine = agentEngineMeta[agentEngine];
  const visibleContextStock = activePromptStock || selectedStock;
  const compactThread = threadViewMode === 'compact';
  const activeConversation = useMemo(
    () => conversations.find(item => item.id === activeConversationId) || conversations[0] || null,
    [conversations, activeConversationId]
  );
  const conversationOptions = useMemo(
    () => conversations.map(conversation => ({
      value: conversation.id,
      label: `${conversation.title} · ${formatConversationMeta(conversation)}`
    })),
    [conversations]
  );
  const selectedResearchResults = useMemo(
    () => researchResults.filter((item, index) => selectedResearchKeys.has(researchHitKey(item, index))),
    [researchResults, selectedResearchKeys]
  );
  const activeResearchResults = selectedResearchResults.length > 0 ? selectedResearchResults : researchResults;
  const indexedProfessionalReports = useMemo(() => (
    professionalReports
      .filter(isVisibleProfessionalReport)
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  ), [professionalReports]);
  const visibleProfessionalReports = useMemo(() => {
    const keyword = indexedReportQuery.trim().toLowerCase();
    const selectedIds = selectedProfessionalReportIds;
    return professionalReports
      .filter(isVisibleProfessionalReport)
      .filter(report => !keyword || selectedIds.has(professionalReportKey(report)) || reportSearchText(report).includes(keyword))
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [professionalReports, indexedReportQuery, selectedProfessionalReportIds]);
  const selectedProfessionalReports = useMemo(
    () => professionalReports.filter(report => selectedProfessionalReportIds.has(professionalReportKey(report))),
    [professionalReports, selectedProfessionalReportIds]
  );
  const visibleEvidenceItems = useMemo(() => {
    const keyword = indexedReportQuery.trim().toLowerCase();
    return evidenceItems
      .filter(isVisibleEvidenceItem)
      .filter(item => !keyword || selectedEvidenceItemIds.has(item.id) || evidenceItemSearchText(item).includes(keyword))
      .sort((a, b) => new Date(b.collected_at || b.created_at).getTime() - new Date(a.collected_at || a.created_at).getTime());
  }, [evidenceItems, indexedReportQuery, selectedEvidenceItemIds]);
  const selectedEvidenceItems = useMemo(
    () => evidenceItems.filter(item => selectedEvidenceItemIds.has(item.id)),
    [evidenceItems, selectedEvidenceItemIds]
  );
  const totalSelectableReports = indexedProfessionalReports.length + evidenceItems.filter(isVisibleEvidenceItem).length;
  const totalSelectedReportContext = selectedProfessionalReports.length + selectedEvidenceItems.length;

  const activeStocks = useMemo(
    () => [...appState.stocks]
      .sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent))
      .slice(0, 4),
    [appState.stocks]
  );

  const riskStocks = useMemo(
    () => [...appState.stocks]
      .sort((a, b) => a.changePercent - b.changePercent)
      .slice(0, 3),
    [appState.stocks]
  );
  const moverChartData = useMemo(
    () => activeStocks.map(stock => ({
      symbol: stock.symbol,
      change: Number(stock.changePercent.toFixed(2))
    })),
    [activeStocks]
  );
  const maxMoverAbs = Math.max(1, ...moverChartData.map(item => Math.abs(item.change)));
  const decisionReadinessScore = Math.min(100, Math.round(
    (sourceItemsCount > 0 ? 22 : 0)
    + (dataSources.length > 0 ? 16 : 0)
    + (realQuoteCount > 0 ? 18 : 8)
    + (connectedMcpCount > 0 ? 12 : 0)
    + (modelConfig?.provider && modelConfig.provider !== 'mock' ? 22 : 8)
    + (subscribedCount > 0 ? 10 : 0)
  ));
  const riskPressureScore = Math.min(100, Math.round(
    riskStocks.reduce((sum, stock) => sum + Math.max(0, -stock.changePercent), 0) * 8
  ));
  const evidenceHealthLabel = sourceItemsCount > 0
    ? `${sourceItemsCount} 条证据`
    : dataSources.length > 0
      ? '数据源待同步'
      : '证据链待补';
  const agentFlow = [
    {
      phase: 'Orchestrator',
      label: '目标',
      detail: stockOptionToken(visibleContextStock, '组合'),
      active: true
    },
    {
      phase: 'Evidence',
      label: '证据',
      detail: evidenceHealthLabel,
      active: sourceItemsCount > 0 || dataSources.length > 0
    },
    {
      phase: 'Research',
      label: '研判',
      detail: activeEngine.shortLabel,
      active: Boolean(modelConfig)
    },
    {
      phase: 'Risk',
      label: '风控',
      detail: riskPressureScore > 0 ? `${riskPressureScore}% 压力` : '平稳',
      active: true
    },
    {
      phase: 'Report',
      label: '报告',
      detail: runningTasks > 0 ? `${runningTasks} 运行中` : '待触发',
      active: runningTasks > 0
    }
  ];

  const copyAssistantMessage = async (item: ChatMessage) => {
    const text = formatChatMessageForCopy(item);
    if (!text) {
      message.warning('这条回复还没有可复制内容。');
      return;
    }

    try {
      await copyTextToClipboard(text);
      setCopiedMessageId(item.id);
      message.success('已复制本次回复');
      window.setTimeout(() => {
        setCopiedMessageId(current => current === item.id ? null : current);
      }, 1800);
    } catch {
      message.error('复制失败，请手动选择文本复制。');
    }
  };

  const addAttachedFiles = (files: File[]) => {
    const incoming = files.filter(file => file.size >= 0);
    if (incoming.length === 0) {
      return;
    }

    setAttachedFiles(prev => {
      const seen = new Set(prev.map(fileKey));
      const merged = [...prev];
      incoming.forEach(file => {
        const key = fileKey(file);
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(file);
        }
      });

      if (merged.length > MAX_ATTACHED_FILES) {
        window.setTimeout(() => {
          message.warning(`最多一次加入 ${MAX_ATTACHED_FILES} 个附件。`);
        }, 0);
      }

      return merged.slice(0, MAX_ATTACHED_FILES);
    });
  };

  const removeAttachedFile = (attachment: ChatAttachmentMeta) => {
    setAttachedFiles(prev => prev.filter(file => fileKey(file) !== attachment.id));
  };

  const handleComposerDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (!Array.from(event.dataTransfer.types).includes('Files')) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setComposerDragActive(true);
  };

  const handleComposerDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
      return;
    }
    setComposerDragActive(false);
  };

  const handleComposerDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setComposerDragActive(false);
    addAttachedFiles(Array.from(event.dataTransfer.files));
  };

  const loadProfessionalReports = async (silent = false) => {
    setProfessionalReportsLoading(true);
    try {
      const reports = await listProfessionalReports({ limit: 160 });
      setProfessionalReports(reports);
      setSelectedProfessionalReportIds(prev => {
        const liveIds = new Set(reports.map(report => report.id));
        return new Set(Array.from(prev).filter(id => liveIds.has(id)));
      });
    } catch (error) {
      if (!silent) {
        message.error('入库研报列表读取失败');
      }
    } finally {
      setProfessionalReportsLoading(false);
    }
  };

  const loadEvidenceItems = async (silent = false) => {
    setEvidenceItemsLoading(true);
    try {
      const items = await listDataItems({ limit: 220, sort: 'time_desc' });
      setEvidenceItems(items);
      setSelectedEvidenceItemIds(prev => {
        const liveIds = new Set(items.map(item => item.id));
        return new Set(Array.from(prev).filter(id => liveIds.has(id)));
      });
    } catch (error) {
      if (!silent) {
        message.error('证据库资料读取失败');
      }
    } finally {
      setEvidenceItemsLoading(false);
    }
  };

  useEffect(() => {
    if (selectedSymbol && appState.stocks.some(stock => stock.symbol === selectedSymbol)) {
      return;
    }
    if (appState.stocks[0]?.symbol) {
      setSelectedSymbol(appState.stocks[0].symbol);
    }
  }, [appState.stocks, selectedSymbol]);

  useEffect(() => {
    if (appState.currentView !== 'home') {
      return;
    }
    void loadProfessionalReports(true);
    void loadEvidenceItems(true);
  }, [appState.currentView]);

  useEffect(() => {
    if (researchKeyword.trim() || !selectedStock) {
      return;
    }
    setResearchKeyword(selectedStock.name || selectedStock.symbol);
  }, [researchKeyword, selectedStock]);

  useEffect(() => {
    if (appState.currentView !== 'home' || messages.length > 0) {
      return;
    }
    window.setTimeout(() => {
      chatStageRef.current?.scrollTo({ top: 0 });
    }, 0);
  }, [appState.currentView, messages.length]);

  useEffect(() => {
    if (appState.currentView !== 'home') {
      return undefined;
    }

    const handleWheel = (event: WheelEvent) => {
      const target = event.target;
      const chatHome = document.querySelector('.investor-chat-home');
      const scrollContainer = document.querySelector('.workspace-content') as HTMLElement | null;
      if (!(target instanceof Node) || !chatHome?.contains(target) || !scrollContainer) {
        return;
      }
      if (scrollContainer.scrollHeight <= scrollContainer.clientHeight + 1) {
        return;
      }

      scrollContainer.scrollTop += event.deltaY;
      event.preventDefault();
    };

    window.addEventListener('wheel', handleWheel, { passive: false });
    return () => window.removeEventListener('wheel', handleWheel);
  }, [appState.currentView]);

  useEffect(() => {
    if (!activeConversationId) {
      return;
    }

    setConversations(prev => {
      const now = new Date().toISOString();
      const existing = prev.find(item => item.id === activeConversationId);
      const nextMessages = messages.slice(-MAX_SAVED_MESSAGES);
      const nextConversation: ChatConversation = {
        ...(existing || createChatConversation({ id: activeConversationId })),
        messages: nextMessages,
        activePromptStock,
        chatMode,
        reasoningMode,
        agentEngine,
        selectedSymbol,
        title: conversationTitleFromMessages(nextMessages, existing?.title || '新的投研对话'),
        updatedAt: now
      };
      const sorted = [
        nextConversation,
        ...prev.filter(item => item.id !== activeConversationId)
      ].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
      return sorted.slice(0, MAX_SAVED_CONVERSATIONS);
    });
  }, [
    activeConversationId,
    messages,
    activePromptStock,
    chatMode,
    reasoningMode,
    agentEngine,
    selectedSymbol
  ]);

  useEffect(() => {
    persistHomeChatState(activeConversationId, conversations);
  }, [activeConversationId, conversations]);

  const applyConversation = (conversation: ChatConversation) => {
    setActiveConversationId(conversation.id);
    setMessages(conversation.messages);
    setChatMode(conversation.chatMode);
    setReasoningMode(conversation.reasoningMode);
    setAgentEngine(conversation.agentEngine);
    setSelectedSymbol(conversation.selectedSymbol || appState.stocks[0]?.symbol);
    setActivePromptStock(conversation.activePromptStock);
    setDraft('');
    setAttachedFiles([]);
    setCopiedMessageId(null);
    setPendingRuns({});
  };

  const handleConversationChange = (conversationId: string) => {
    const conversation = conversations.find(item => item.id === conversationId);
    if (conversation) {
      applyConversation(conversation);
    }
  };

  const handleNewConversation = () => {
    setDraft('');
    setAttachedFiles([]);
    setCopiedMessageId(null);
    setActivePromptStock(undefined);
    setPendingRuns({});

    if (messages.length === 0 && activeConversation) {
      return;
    }

    const nextConversation = createChatConversation({
      chatMode,
      reasoningMode,
      agentEngine,
      selectedSymbol: selectedStock?.symbol || selectedSymbol
    });
    setConversations(prev => [nextConversation, ...prev].slice(0, MAX_SAVED_CONVERSATIONS));
    setActiveConversationId(nextConversation.id);
    setMessages([]);
  };

  const focusAiChat = () => {
    onViewChange('home');
    window.setTimeout(() => {
      composerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      composerRef.current?.querySelector('textarea')?.focus();
    }, 0);
  };

  useEffect(() => {
    if (appState.currentView !== 'home') {
      return;
    }
    const pendingDraft = takePendingAiDraft();
    if (!pendingDraft) {
      return;
    }

    setDraft(formatPendingAiDraft(pendingDraft));
    setChatMode('research');
    setReasoningMode('thinking');
    setAgentEngine('deepfocus');
    setContextRailOpen(true);
    window.setTimeout(() => {
      composerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      composerRef.current?.querySelector('textarea')?.focus();
    }, 0);
    message.success('已从研报工作台带入 Agent Cockpit');
  }, [appState.currentView, message]);

  useEffect(() => {
    let mounted = true;
    Promise.allSettled([
      getModelConfig(),
      listDataSources(),
      listMcpServers(),
      listAgentTasks()
    ]).then(results => {
      if (!mounted) {
        return;
      }
      if (results[0].status === 'fulfilled') setModelConfig(results[0].value);
      if (results[1].status === 'fulfilled') setDataSources(results[1].value);
      if (results[2].status === 'fulfilled') setMcpServers(results[2].value);
      if (results[3].status === 'fulfilled') setTasks(results[3].value);
    });
    return () => {
      mounted = false;
    };
  }, []);

  const stockOptions = appState.stocks.map(stock => ({
    value: stock.symbol,
    label: `${stock.name} (${stock.symbol})`
  }));

  const contextActions: ContextAction[] = [
    {
      key: 'files',
      title: '资料文件',
      detail: attachedFiles.length > 0 ? `${attachedFiles.length} 个待加入上下文` : `${sourceItemsCount} 条资料`,
      icon: <FolderOpenOutlined />,
      view: 'data-sources',
      status: attachedFiles.length > 0 ? '待入库' : 'Evidence'
    },
    {
      key: 'data',
      title: '数据源',
      detail: `${dataSources.length} 个数据源`,
      icon: <DatabaseOutlined />,
      view: 'data-sources',
      status: realQuoteCount > 0 ? '行情接入' : '样例行情'
    },
    {
      key: 'skills',
      title: '技能链',
      detail: `${skillCount} 个投研技能`,
      icon: <ToolOutlined />,
      view: 'skills',
      status: 'Agent 可调'
    },
    {
      key: 'plugins',
      title: '工具连接',
      detail: `${connectedMcpCount}/${mcpServers.length} 已连接`,
      icon: <ApiOutlined />,
      view: 'mcp-center',
      status: 'Tooling'
    },
    {
      key: 'model',
      title: '模型路由',
      detail: modelConfig ? modelConfig.model : '读取中',
      icon: <CloudServerOutlined />,
      view: 'profile',
      status: modelConfig?.provider || '设置'
    }
  ];

  const quickPromptStockToken = visibleContextStock?.symbol
    || visibleContextStock?.name
    || selectedStock?.symbol
    || 'TSLA';
  const quickPrompts = agentEngine === 'financial_services'
    ? [
        `给 ${quickPromptStockToken} 做 earnings-reviewer 财报复核`,
        `为 ${quickPromptStockToken} 生成 DCF 模型输入清单`,
        '把上传文件整理成 Pitch Agent 输入包',
        '检查 KYC/对账资料缺口和人工复核点'
      ]
    : [
        `审查 ${quickPromptStockToken} 的主要风险`,
        `判断 ${quickPromptStockToken} 是否进入观察名单`,
        '整理上传文件里的投资证据',
        '生成明早盘前关注清单'
      ];

  const toggleResearchHit = (item: ResearchWorkbenchSearchItem, index: number) => {
    const key = researchHitKey(item, index);
    setSelectedResearchKeys(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const toggleProfessionalReport = (report: ProfessionalReportRecord) => {
    const key = professionalReportKey(report);
    setSelectedProfessionalReportIds(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const toggleEvidenceItem = (item: DataSourceItemRecord) => {
    setSelectedEvidenceItemIds(prev => {
      const next = new Set(prev);
      if (next.has(item.id)) {
        next.delete(item.id);
      } else {
        next.add(item.id);
      }
      return next;
    });
  };

  const selectVisibleProfessionalReports = () => {
    setSelectedProfessionalReportIds(prev => {
      const next = new Set(prev);
      visibleProfessionalReports.slice(0, 20).forEach(report => {
        next.add(professionalReportKey(report));
      });
      return next;
    });
    setSelectedEvidenceItemIds(prev => {
      const next = new Set(prev);
      visibleEvidenceItems.slice(0, 20).forEach(item => {
        next.add(item.id);
      });
      return next;
    });
  };

  const clearSelectedProfessionalReports = () => {
    setSelectedProfessionalReportIds(new Set());
    setSelectedEvidenceItemIds(new Set());
  };

  const interpretSelectedProfessionalReports = async () => {
    const reports = selectedProfessionalReports.length
      ? selectedProfessionalReports
      : visibleProfessionalReports.slice(0, 1);
    const evidence = selectedEvidenceItems.length
      ? selectedEvidenceItems
      : reports.length
        ? []
        : visibleEvidenceItems.slice(0, 1);
    if (!reports.length && !evidence.length) {
      message.warning('还没有可解读的入库研报或证据库资料。');
      return;
    }
    const totalCount = reports.length + evidence.length;

    const userMessage: ChatMessage = {
      id: uid(),
      role: 'user',
      content: selectedProfessionalReports.length || selectedEvidenceItems.length
        ? `解读选中的 ${totalCount} 份入库资料`
        : reports.length
          ? `解读最新入库研报：${reports[0].title}`
          : `解读最新证据库资料：${evidence[0].title}`,
      chips: ['入库资料', '专业解读', `${totalCount} 份`]
    };
    const pendingId = uid();
    const pendingMessage: ChatMessage = {
      id: pendingId,
      role: 'assistant',
      title: '入库研报解读中',
      content: '正在调用已入库正文、证据库资料和引用块，生成可追问的投委会式解读。',
      chips: ['ProfessionalResearchAgent', '正文/证据解读', `${totalCount} 份`],
      reasoningTrace: [
        {
          phase: 'evidence',
          title: '读取入库研报',
          detail: `选中 ${totalCount} 份入库资料，准备逐份生成摘要、指标、风险和追问。`,
          status: 'working'
        }
      ],
      guide: buildGeneratingGuide('report', totalCount),
      status: 'working',
      thinkingEnabled: true
    };

    setMessages(prev => [...prev, userMessage, pendingMessage]);
    setProfessionalInterpreting(true);
    setContextRailOpen(false);
    try {
      const analyses: Array<{ report: ProfessionalReportRecord; analysis: ProfessionalReportAnalysis }> = [];
      for (const report of reports) {
        const analysis = await analyzeProfessionalReport(report.id, {
          focus: draft || researchKeyword || '请生成可供 Agent Cockpit 继续追问的投委会级研报解读',
          use_cloud_model: false
        });
        analyses.push({ report, analysis });
      }
      const evidenceAnalyses: Array<{ item: DataSourceItemRecord; interpretation: string }> = [];
      for (const item of evidence) {
        const result = await interpretDataItem(item.id, true);
        evidenceAnalyses.push({ item: result.item || item, interpretation: result.interpretation || item.text_preview });
      }
      setMessages(prev => prev.map(item => item.id === pendingId ? {
        ...item,
        title: '入库研报解读完成',
        content: `已完成 ${analyses.length + evidenceAnalyses.length} 份入库资料解读。摘要、关键指标、红旗、风险和追问已经整理成卡片。`,
        reportCards: [
          ...analyses.map(entry => buildProfessionalReportCard(entry.report, entry.analysis)),
          ...evidenceAnalyses.map(entry => buildEvidenceItemCard(entry.item, entry.interpretation))
        ],
        chips: ['ProfessionalResearchAgent', '入库资料', `${analyses.length + evidenceAnalyses.length} 份`, `${analyses.reduce((sum, entry) => sum + (entry.analysis.citations?.length || 0), 0) + evidenceAnalyses.length} 引用`],
        reasoningTrace: [
          {
            phase: 'evidence',
            title: '正文与引用块读取',
            detail: '已使用入库后的正文索引、证据库文本和引用块，不是标题级快筛。',
            status: 'done'
          },
          {
            phase: 'research',
            title: '研报解读',
            detail: '已输出摘要、关键指标、红旗、风险和后续追问，可继续让 Agent 生成任务。',
            status: 'done'
          }
        ],
        guide: buildResearchReplyGuide('report', analyses.length + evidenceAnalyses.length),
        status: 'done'
      } : item));
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || '入库研报解读失败，请检查后端或模型配置';
      setMessages(prev => prev.map(item => item.id === pendingId ? {
        ...item,
        title: '入库研报解读失败',
        content: detail,
        chips: ['ProfessionalResearchAgent', '需要检查'],
        reasoningTrace: [{
          phase: 'research',
          title: '解读失败',
          detail,
          status: 'error'
        }],
        guide: buildErrorGuide(userMessage.content),
        status: 'error'
      } : item));
      message.error('入库研报解读失败');
    } finally {
      setProfessionalInterpreting(false);
    }
  };

  const runResearchSearch = async () => {
    const keyword = researchKeyword.trim();
    if (!keyword) {
      message.warning('先输入研报关键词，比如 英伟达、HBM、AI capex。');
      return;
    }

    setResearchSearching(true);
    try {
      const result = await searchResearchWorkbenchReports({
        keyword,
        tag: researchTag,
        searchPages: HOME_RESEARCH_SEARCH_PAGES,
        resultLimit: 0
      });
      setResearchResults(result.items || []);
      setSelectedResearchKeys(new Set());
      setContextRailOpen(true);
      if (!result.items?.length) {
        message.info('没有搜到匹配研报，可以放宽关键词或清空标签。');
        return;
      }
      message.success(`已命中 ${result.count || result.items.length} 份研报`);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.response?.data?.error || error?.message || '请确认研报工作台和登录凭证正常';
      message.error(`研报搜索失败：${detail}`);
    } finally {
      setResearchSearching(false);
    }
  };

  const summarizeResearchHitsInChat = async () => {
    const keyword = researchKeyword.trim();
    const items = activeResearchResults;
    if (!items.length) {
      message.warning('先搜索研报命中，再做 AI 总结。');
      return;
    }

    const userMessage: ChatMessage = {
      id: uid(),
      role: 'user',
      content: selectedResearchResults.length
        ? `AI 总结选中的 ${selectedResearchResults.length} 条研报命中：${keyword || '未指定关键词'}`
        : `AI 总结 ${items.length} 条研报命中：${keyword || '未指定关键词'}`,
      chips: ['研报证据', '标题级快筛']
    };
    const pendingId = uid();
    const pendingMessage: ChatMessage = {
      id: pendingId,
      role: 'assistant',
      title: '研报命中总结中',
      content: '正在基于标题、标签、日期和下载次数做投研快筛，不下载 PDF，也不会假装读过正文。',
      chips: ['ResearchWorkbench', '不下载 PDF', `${items.length} 条`],
      reasoningTrace: [
        {
          phase: 'evidence',
          title: '标题级证据',
          detail: '使用搜索命中列表做主题聚类、去重和下载优先级判断。',
          status: 'working'
        }
      ],
      guide: buildGeneratingGuide('research-hit', items.length),
      status: 'working',
      thinkingEnabled: true
    };

    setMessages(prev => [...prev, userMessage, pendingMessage]);
    setResearchSummarizing(true);
    setContextRailOpen(false);
    try {
      const prompt = buildResearchHitSummaryPrompt(items, keyword);
      const result = await summarizeResearchWorkbenchHits(prompt);
      setMessages(prev => prev.map(item => item.id === pendingId ? {
        ...item,
        title: '研报命中快筛完成',
        content: `已完成 ${items.length} 条研报命中的标题级快筛。主题线索、风险缺口和下载优先级已经整理成卡片。`,
        reportCards: [buildResearchHitSummaryCard(result.reply || '', items, keyword)],
        chips: ['ResearchWorkbench', '标题级快筛', `${items.length} 条`],
        reasoningTrace: [
          {
            phase: 'evidence',
            title: '命中列表读取',
            detail: `已基于 ${items.length} 条搜索命中生成主题地图和优先下载清单。`,
            status: 'done'
          },
          {
            phase: 'report',
            title: '主 AI 对话归档',
            detail: '结果已写回主页 AI 对话，可继续追问或要求下载全文精读。',
            status: 'done'
          }
        ],
        guide: buildResearchReplyGuide('hit-summary', items.length, keyword),
        status: 'done'
      } : item));
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.response?.data?.error || error?.message || '模型暂时没有返回可用结果';
      setMessages(prev => prev.map(item => item.id === pendingId ? {
        ...item,
        title: '研报命中总结失败',
        content: detail,
        chips: ['ResearchWorkbench', '需要检查模型'],
        reasoningTrace: [
          {
            phase: 'evidence',
            title: '总结失败',
            detail,
            status: 'error'
          }
        ],
        guide: buildErrorGuide(userMessage.content),
        status: 'error'
      } : item));
      message.error('研报总结失败');
    } finally {
      setResearchSummarizing(false);
    }
  };

  const downloadResearchHits = async () => {
    const items = selectedResearchResults;
    if (!items.length) {
      message.warning('请先勾选要下载并引用全文的研报。');
      return;
    }

    setResearchDownloading(true);
    try {
      const job = await startResearchWorkbenchDownload(items, { tag: researchTag });
      setMessages(prev => [...prev, {
        id: uid(),
        role: 'assistant',
        title: '研报全文下载已启动',
        content: `已把 ${items.length} 份研报交给资料抓取舱下载。下载完成后可在研报工作台入库，或继续在主页 AI 对话里要求“精读刚下载的研报”。任务 ID：${job.id}`,
        chips: ['下载任务', job.status, `${items.length} 份`],
        guide: buildResearchReplyGuide('download', items.length),
        status: 'done'
      }]);
      message.success('下载任务已启动');
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.response?.data?.error || error?.message || '请确认知识星球登录态有效';
      message.error(`下载任务启动失败：${detail}`);
    } finally {
      setResearchDownloading(false);
    }
  };

  const handleMarketSegmentChange = (segment: MarketSegmentKey) => {
    if (segment === 'a-share') {
      onViewChange('a-share-market');
      return;
    }
    if (segment === 'global') {
      onViewChange('global-market');
      return;
    }
    onViewChange('stocks');
  };

  const moduleGroups: ModuleGroup[] = [
    {
      label: '投研工作流',
      detail: '从问题到结论',
      items: [
        {
          key: 'home',
          title: 'Agent Cockpit',
          detail: `${activeEngine.shortLabel} · ${selectedStock?.symbol || '未选标的'}`,
          icon: <RobotOutlined />,
          view: 'home',
          tone: 'primary'
        },
        {
          key: 'agent-center',
          title: 'Agent 任务',
          detail: `${runningTasks} 个运行中`,
          icon: <CloudServerOutlined />,
          view: 'agent-center'
        },
        {
          key: 'stocks',
          title: '观察池',
          detail: `${subscribedCount}/${appState.stocks.length} 监控中`,
          icon: <EyeOutlined />,
          view: 'stocks'
        },
        {
          key: 'research-workbench',
          title: '研报工作台',
          detail: '文件 / 引用',
          icon: <FolderOpenOutlined />,
          view: 'research-workbench'
        },
        {
          key: 'data-sources',
          title: '证据库',
          detail: `${sourceItemsCount} 条资料`,
          icon: <DatabaseOutlined />,
          view: 'data-sources'
        }
      ]
    },
    {
      label: '决策与风险',
      detail: '交易前复核',
      items: [
        {
          key: 'multi-market-decision',
          title: '策略与组合',
          detail: '多市场决策',
          icon: <FundProjectionScreenOutlined />,
          view: 'multi-market-decision'
        },
        {
          key: 'earnings-calendar',
          title: '事件日历',
          detail: '财报 / 催化',
          icon: <CalendarOutlined />,
          view: 'earnings-calendar'
        },
        {
          key: 'options-signal',
          title: '期权雷达',
          detail: '异动 / 风险',
          icon: <BarChartOutlined />,
          view: 'options-signal'
        },
        {
          key: 'realtime-messages',
          title: '信号流',
          detail: '新闻 / 告警',
          icon: <ThunderboltOutlined />,
          view: 'realtime-messages'
        },
        {
          key: 'ai-research',
          title: '单标的体检',
          detail: selectedStock?.symbol || '选择标的',
          icon: <SafetyCertificateOutlined />,
          view: 'ai-research'
        }
      ]
    },
    {
      label: '专题分析',
      detail: '事件与产业线索',
      items: [
        {
          key: 'cn-earnings',
          title: 'A股财报',
          detail: '公告 / 指标',
          icon: <FileTextOutlined />,
          view: 'cn-earnings'
        },
        {
          key: 'shareholder-changes',
          title: '股东变动',
          detail: '增减持 / 证据',
          icon: <AuditOutlined />,
          view: 'shareholder-changes'
        },
        {
          key: 'major-events',
          title: '重大事项',
          detail: '预警 / 反证',
          icon: <ThunderboltOutlined />,
          view: 'major-events'
        },
        {
          key: 'ai-supply-chain',
          title: 'AI 供应链',
          detail: '产业链追踪',
          icon: <PartitionOutlined />,
          view: 'ai-supply-chain'
        }
      ]
    },
    {
      label: '系统能力',
      detail: '工具与治理',
      items: [
        {
          key: 'mcp-center',
          title: '工具连接',
          detail: `${connectedMcpCount}/${mcpServers.length} 已连接`,
          icon: <ApiOutlined />,
          view: 'mcp-center'
        },
        {
          key: 'skills',
          title: '技能编排',
          detail: `${skillCount} 个技能`,
          icon: <ToolOutlined />,
          view: 'skills'
        },
        {
          key: 'profile',
          title: '模型与权限',
          detail: modelConfig?.provider || '系统设置',
          icon: <CloudServerOutlined />,
          view: 'profile',
          tone: 'quiet'
        }
      ]
    }
  ];

  const renderMarketStrip = () => (
    <div className="market-strip">
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>Agent 任务</span>
          <span>Running</span>
        </div>
        <div className="market-strip-value">{runningTasks}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>覆盖个股</span>
          <span>Universe</span>
        </div>
        <div className="market-strip-value">{appState.stocks.length}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>A股池</span>
          <span>CN</span>
        </div>
        <div className="market-strip-value">{marketSegmentCounts.aShare}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>港美股池</span>
          <span>HK / US</span>
        </div>
        <div className="market-strip-value">{marketSegmentCounts.global}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>领涨标的</span>
          <span>{strongestStock?.symbol || '--'}</span>
        </div>
        <div className={`market-strip-value ${strongestStock && strongestStock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
          {strongestStock ? `${strongestStock.changePercent >= 0 ? '+' : ''}${strongestStock.changePercent.toFixed(2)}%` : '--'}
        </div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>证据链</span>
          <span>Evidence</span>
        </div>
        <div className="market-strip-value">{sourceItemsCount}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>行情源</span>
          <span>{quoteAnchor?.symbol || '--'}</span>
        </div>
        <div className="market-strip-value market-strip-value-sm">
          {quoteAnchor ? formatQuoteSourceLine(quoteAnchor) : '--'}
        </div>
        <div className="market-strip-note">{quoteAnchor ? formatQuoteTimestamp(quoteAnchor) : '待刷新'}</div>
      </div>
    </div>
  );

  const renderToolchainSpine = () => {
    const toolchainSteps: ContextAction[] = [
      {
        key: 'intent',
        title: 'Agent 目标',
        detail: visibleContextStock?.symbol
          ? `${visibleContextStock.name || visibleContextStock.symbol} · ${visibleContextStock.symbol}`
          : visibleContextStock?.name || '组合级任务',
        icon: <RobotOutlined />,
        view: 'home',
        status: modeMeta[chatMode].label
      },
      ...contextActions,
      {
        key: 'runs',
        title: '执行队列',
        detail: `${runningTasks} 个 Run 运行中`,
        icon: <CloudServerOutlined />,
        view: 'agent-center',
        status: activeEngine.shortLabel
      }
    ];

    return (
      <div className="agent-toolchain-spine" aria-label="Agent 工具链总览">
        <div className="agent-toolchain-spine-head">
          <span><RobotOutlined /> Agent 作为入口</span>
          <small>数据、文件、MCP、Skills 和模型都作为可编排工具链注入上下文</small>
        </div>
        <div className="agent-toolchain-spine-grid">
          {toolchainSteps.map((step, index) => (
            <button
              key={step.key}
              type="button"
              className={index === 0 ? 'primary' : ''}
              onClick={() => onViewChange(step.view)}
            >
              <span className="agent-toolchain-index">{index + 1}</span>
              <span className="agent-toolchain-icon">{step.icon}</span>
              <span className="agent-toolchain-copy">
                <strong>{step.title}</strong>
                <small>{step.detail}</small>
              </span>
              <em>{step.status}</em>
            </button>
          ))}
        </div>
      </div>
    );
  };

  const renderVisualBoard = (variant: 'landing' | 'rail' = 'landing') => (
    <div className={`investor-visual-board investor-visual-board-${variant}`}>
      <section className="visual-panel visual-readiness-panel">
        <div className="visual-panel-head">
          <span>决策准备度</span>
          <Tag>{modelConfig?.provider || 'model'}</Tag>
        </div>
        <div className="visual-readiness-body">
          <Progress
            type="dashboard"
            percent={decisionReadinessScore}
            size={variant === 'rail' ? 82 : 104}
            strokeColor={decisionReadinessScore >= 72 ? '#12805c' : decisionReadinessScore >= 46 ? '#b7791f' : '#c43e3e'}
          />
          <div className="visual-readiness-copy">
            <strong>{evidenceHealthLabel}</strong>
            <span>{realQuoteCount > 0 ? `${realQuoteCount} 个实时/外部行情源` : '当前主要使用样例行情'}</span>
            <span>{connectedMcpCount}/{mcpServers.length} 工具连接可用</span>
          </div>
        </div>
      </section>

      <section className="visual-panel visual-flow-panel">
        <div className="visual-panel-head">
          <span>Agent Run 路径</span>
          <Tag>{activeEngine.shortLabel}</Tag>
        </div>
        <div className="visual-agent-flow">
          {agentFlow.map((step, index) => (
            <div key={step.phase} className={`visual-agent-step ${step.active ? 'active' : ''}`}>
              <span className="visual-agent-index">{index + 1}</span>
              <div>
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="visual-panel visual-market-panel">
        <div className="visual-panel-head">
          <span>波动扫描</span>
          <Tag>{activeStocks.length} 标的</Tag>
        </div>
        <div className="visual-mover-bars">
          {moverChartData.map(item => (
            <div key={item.symbol} className="visual-mover-row">
              <span>{item.symbol}</span>
              <div className="visual-mover-track">
                <i
                  className={item.change >= 0 ? 'positive' : 'negative'}
                  style={{ width: `${Math.max(8, Math.round((Math.abs(item.change) / maxMoverAbs) * 100))}%` }}
                />
              </div>
              <em className={item.change >= 0 ? 'quote-positive' : 'quote-negative'}>
                {item.change >= 0 ? '+' : ''}{item.change}%
              </em>
            </div>
          ))}
        </div>
      </section>

      <section className="visual-panel visual-risk-panel">
        <div className="visual-panel-head">
          <span>风险雷达</span>
          <Tag>{riskPressureScore}%</Tag>
        </div>
        <div className="visual-risk-list">
          {riskStocks.map(stock => (
            <button key={stock.symbol} type="button" onClick={() => onStockSelect(stock)}>
              <span>
                <strong>{stock.symbol}</strong>
                <small>{stock.name}</small>
              </span>
              <em className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}>
                {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
              </em>
            </button>
          ))}
        </div>
      </section>
    </div>
  );

  const renderModuleMap = () => (
    <div className="investor-module-map">
      {moduleGroups.map(group => (
        <section key={group.label} className="module-map-group">
          <div className="module-map-head">
            <strong>{group.label}</strong>
            <span>{group.detail}</span>
          </div>
          <div className="module-map-items">
            {group.items.map(item => (
              <button
                key={item.key}
                type="button"
                className={item.tone || ''}
                onClick={() => onViewChange(item.view)}
              >
                {item.icon}
                <span>{item.title}</span>
                <small>{item.detail}</small>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );

  const renderDecisionPath = () => (
    <div className="investor-decision-path" aria-label="投研决策路径">
      <section>
        <span>1</span>
        <strong>定义问题</strong>
        <small>{visibleContextStock?.symbol ? `${visibleContextStock.symbol} · ${modeMeta[chatMode].label}` : visibleContextStock?.name || '标的、组合或事件'}</small>
      </section>
      <section>
        <span>2</span>
        <strong>拉取证据</strong>
        <small>{sourceItemsCount} 条资料 · {realQuoteCount > 0 ? `${realQuoteCount} 个行情源` : '行情待接入'}</small>
      </section>
      <section>
        <span>3</span>
        <strong>输出动作</strong>
        <small>结论、反证、风险纪律和下一步验证</small>
      </section>
    </div>
  );

  const renderInsightRail = () => (
    <aside className="chatgpt-insight-rail">
      {renderVisualBoard('rail')}
      <section className="insight-rail-panel">
        <div className="visual-panel-head">
          <span>当前上下文</span>
          <Tag>{stockOptionToken(visibleContextStock)}</Tag>
        </div>
        <div className="insight-source-list">
          {contextActions.map(action => (
            <button key={action.key} type="button" onClick={() => onViewChange(action.view)}>
              <span className="context-source-icon">{action.icon}</span>
              <span>
                <strong>{action.title}</strong>
                <small>{action.detail}</small>
              </span>
            </button>
          ))}
        </div>
      </section>
    </aside>
  );

  const renderReportInsightCards = (cards?: ReportInsightCard[]) => {
    if (!cards?.length) return null;

    return (
      <div className="chat-report-card-grid">
        {cards.map(card => {
          const confidenceValue = card.confidence > 1 ? card.confidence : card.confidence * 100;
          const confidenceLabel = confidenceValue > 0
            ? `${Math.round(confidenceValue)}% 置信度`
            : '标题级快筛';
          const flagTitle = card.kind === 'hit-summary' ? '主题线索' : '红旗信号';
          const riskTitle = card.kind === 'hit-summary' ? '风险缺口' : '风险提示';
          const questionTitle = card.kind === 'hit-summary' ? '建议动作' : '后续追问';

          return (
            <section key={card.id} className={`chat-report-card ${card.kind || 'full-report'}`}>
              <div className="chat-report-card-head">
                <span><FileTextOutlined /> {card.kind === 'hit-summary' ? '标题快筛' : '正文解读'}</span>
                <div>
                  <Tag>{confidenceLabel}</Tag>
                  {card.citations > 0 && <Tag color="blue">{card.citations} 引用块</Tag>}
                </div>
              </div>
              <h4>{card.title}</h4>
              <p className="chat-report-card-summary">{card.summary}</p>
              {card.metrics.length > 0 && (
                <div className="chat-report-metrics" aria-label="关键指标">
                  {card.metrics.map(metric => <span key={metric}>{metric}</span>)}
                </div>
              )}
              <div className="chat-report-section-grid">
                {card.flags.length > 0 && (
                  <div className="chat-report-section">
                    <strong><BarChartOutlined /> {flagTitle}</strong>
                    {card.flags.map(point => <span key={point}>{point}</span>)}
                  </div>
                )}
                {card.risks.length > 0 && (
                  <div className="chat-report-section warning">
                    <strong><SafetyCertificateOutlined /> {riskTitle}</strong>
                    {card.risks.map(risk => <span key={risk}>{risk}</span>)}
                  </div>
                )}
                {card.questions.length > 0 && (
                  <div className="chat-report-section action">
                    <strong><FileSearchOutlined /> {questionTitle}</strong>
                    {card.questions.map(question => <span key={question}>{question}</span>)}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    );
  };

  const focusComposerInput = () => {
    window.setTimeout(() => composerRef.current?.querySelector('textarea')?.focus(), 0);
  };

  const handleGuideAction = (action: ChatGuideAction) => {
    if (action.kind === 'prompt') {
      if (action.prompt) {
        setDraft(action.prompt);
      }
      focusComposerInput();
      return;
    }

    if (action.kind === 'view') {
      if (action.view) {
        onViewChange(action.view);
      }
      return;
    }

    if (action.kind === 'context') {
      setContextRailOpen(true);
      focusComposerInput();
      return;
    }

    if (action.kind === 'mode' && action.mode) {
      setChatMode(action.mode);
      focusComposerInput();
      return;
    }

    if (action.kind === 'reasoning' && action.reasoning) {
      setReasoningMode(action.reasoning);
      focusComposerInput();
      return;
    }

    if (action.kind === 'agent' && action.agent) {
      setAgentEngine(action.agent);
      focusComposerInput();
    }
  };

  const guideActionIcon = (kind: GuideActionKind) => {
    if (kind === 'view') return <FolderOpenOutlined />;
    if (kind === 'context') return <ToolOutlined />;
    if (kind === 'reasoning') return <ThunderboltOutlined />;
    if (kind === 'agent') return <RobotOutlined />;
    return <SendOutlined />;
  };

  const renderGuidePanel = (guide?: ChatGuidePanel) => {
    if (!guide) return null;

    return (
      <section className={`chat-guide-panel ${guide.variant}`}>
        <div className="chat-guide-head">
          <span>
            {guide.variant === 'research' ? <FileSearchOutlined /> : guide.variant === 'error' ? <SafetyCertificateOutlined /> : <RobotOutlined />}
            {guide.title}
          </span>
          {guide.variant === 'generating' && <Tag className="chat-guide-live-tag">生成中</Tag>}
        </div>
        {guide.description && <p>{guide.description}</p>}
        {guide.steps && guide.steps.length > 0 && (
          <div className="chat-guide-steps">
            {guide.steps.map(step => (
              <span key={`${guide.title}-${step.label}`} className={step.status}>
                <i />
                {step.label}
              </span>
            ))}
          </div>
        )}
        {guide.actions && guide.actions.length > 0 && (
          <div className="chat-guide-actions">
            {guide.actions.map(action => (
              <button
                key={action.id}
                type="button"
                className={action.primary ? 'primary' : ''}
                onClick={() => handleGuideAction(action)}
              >
                {guideActionIcon(action.kind)}
                <span>
                  <strong>{action.label}</strong>
                  {action.detail && <small>{action.detail}</small>}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
    );
  };

  const refreshChatTask = async (taskId: string, messageId: string) => {
    const nextReasoningTrace = (item: ChatMessage, task: InvestmentTaskRecord) => (
      item.thinkingEnabled === false ? item.reasoningTrace : traceFromTask(task)
    );

    for (let attempt = 0; attempt < 90; attempt += 1) {
      await wait(attempt === 0 ? 1200 : 2500);
      let task: InvestmentTaskRecord;
      try {
        task = await getAgentTask(taskId);
      } catch (error: any) {
        setMessages(prev => prev.map(item => item.id === messageId ? {
          ...item,
          title: '任务状态读取失败',
          content: error?.response?.data?.detail || error?.message || '暂时无法读取 Agent 任务状态。',
          chips: ['状态同步失败', '可打开 Agent 任务'],
          reasoningTrace: [{
            phase: 'sync',
            title: '状态同步失败',
            detail: '前端暂时无法读取后台 Agent 任务状态。',
            status: 'error'
          }],
          guide: buildErrorGuide('打开 Agent 任务并检查这次后台 Run 的状态'),
          status: 'error'
        } : item));
        return;
      }

      setTasks(prev => [task, ...prev.filter(item => item.id !== task.id)]);

      if (task.status === 'completed') {
        setMessages(prev => prev.map(item => item.id === messageId ? {
          ...item,
          title: 'Agent 报告已完成',
          content: formatAgentResultForChat(task),
          chips: [
            task.engine,
            task.result?.engine_status || task.status,
            decisionLabel[task.result?.decision || ''] || task.result?.decision || task.symbol || 'report'
          ],
          reasoningTrace: nextReasoningTrace(item, task),
          agentBlocks: blocksFromInvestmentTask(task),
          guide: buildAgentReplyGuide(String(task.input?.objective || task.input?.context || task.title || ''), {
            symbol: task.symbol || undefined,
            name: task.asset_name || undefined
          }, task.result?.action_plan || [], false),
          status: 'done',
          taskId: task.id
        } : item));
        return;
      }

      if (task.status === 'failed' || task.status === 'cancelled') {
        setMessages(prev => prev.map(item => item.id === messageId ? {
          ...item,
          title: task.status === 'failed' ? 'Agent 任务失败' : 'Agent 任务已取消',
          content: task.error || task.logs[task.logs.length - 1]?.message || '任务没有返回可用结果。',
          chips: [task.engine, task.status, `${task.progress}%`],
          reasoningTrace: nextReasoningTrace(item, task),
          agentBlocks: blocksFromInvestmentTask(task),
          guide: buildErrorGuide(String(task.input?.objective || task.title || '重新运行这个 Agent 任务')),
          status: 'error',
          taskId: task.id
        } : item));
        return;
      }

      const heartbeatDelayed = isHeartbeatDelayed(task);
      setMessages(prev => prev.map(item => item.id === messageId ? {
        ...item,
        title: heartbeatDelayed ? `Agent 心跳等待中 · ${task.progress}%` : `Agent 正在运行 · ${task.progress}%`,
        content: runningTaskMessage(task),
        chips: [
          task.engine,
          heartbeatDelayed ? '心跳延迟' : task.status,
          coreAgentName(task.assigned_agent)
        ],
        reasoningTrace: nextReasoningTrace(item, task),
        agentBlocks: blocksFromInvestmentTask(task),
        guide: heartbeatDelayed
          ? buildErrorGuide('打开 Agent 任务查看心跳状态，必要时取消后重跑')
          : buildGeneratingGuide('agent'),
        status: 'working',
        taskId: task.id
      } : item));
    }

    setMessages(prev => prev.map(item => item.id === messageId ? {
      ...item,
      title: 'Agent 仍在运行',
      content: '这个任务还没结束，我已经保留了任务入口；可以继续等，或打开 Agent 任务查看实时进度。',
      chips: ['running', '可打开 Agent 任务'],
      reasoningTrace: [{
        phase: 'heartbeat',
        title: '保持追踪',
        detail: '任务未结束，对话中保留入口，可到 Agent 任务查看完整日志。',
        status: 'working'
      }],
      guide: buildGeneratingGuide('agent'),
      status: 'working'
    } : item));
  };

  const streamChatTask = (
    taskId: string,
    messageId: string,
    onStop?: () => void
  ): (() => void) => {
    let fallbackStarted = false;
    let stop = () => {};
    let stopped = false;
    let watchdog = window.setTimeout(() => fallbackToPolling(), 50000);

    const cleanup = () => {
      if (stopped) {
        return;
      }
      stopped = true;
      window.clearTimeout(watchdog);
      stop();
      onStop?.();
    };

    const fallbackToPolling = () => {
      if (fallbackStarted || stopped) {
        return;
      }
      fallbackStarted = true;
      window.clearTimeout(watchdog);
      stop();
      void refreshChatTask(taskId, messageId);
    };

    stop = subscribeAgentTaskEvents(taskId, {
      onEvent: event => {
        if (event.type === 'run_complete') {
          setMessages(prev => prev.map(item => item.id === messageId ? {
            ...item,
            agentBlocks: mergeAgentBlock(item.agentBlocks, blockFromAgentRunEvent(event)),
            reasoningTrace: item.thinkingEnabled === false ? item.reasoningTrace : mergeTraceStep(item.reasoningTrace, traceStepFromEvent(event)),
            status: 'done',
            taskId
          } : item));
          cleanup();
          void refreshChatTask(taskId, messageId);
          return;
        }

        if (event.type === 'error') {
          setMessages(prev => prev.map(item => item.id === messageId ? {
            ...item,
            title: event.title || 'Agent Run 异常',
            content: event.message || '任务执行异常，请打开 Agent 任务查看完整记录。',
            chips: [event.agent, event.type, `${event.progress ?? 0}%`],
            reasoningTrace: item.thinkingEnabled === false ? item.reasoningTrace : mergeTraceStep(item.reasoningTrace, traceStepFromEvent(event)),
            agentBlocks: mergeAgentBlock(item.agentBlocks, blockFromAgentRunEvent(event)),
            guide: buildErrorGuide(event.message || '重新运行这个 Agent 任务'),
            status: 'error',
            taskId
          } : item));
          cleanup();
          return;
        }

        setMessages(prev => prev.map(item => item.id === messageId ? {
          ...item,
          title: event.progress != null ? `Agent 正在运行 · ${event.progress}%` : event.title,
          content: event.message || event.title,
          chips: [event.agent, event.type, event.phase],
          reasoningTrace: item.thinkingEnabled === false ? item.reasoningTrace : mergeTraceStep(item.reasoningTrace, traceStepFromEvent(event)),
          agentBlocks: mergeAgentBlock(item.agentBlocks, blockFromAgentRunEvent(event)),
          guide: buildGeneratingGuide('agent'),
          status: 'working',
          taskId
        } : item));
      },
      onDone: () => {
        cleanup();
        void refreshChatTask(taskId, messageId);
      },
      onError: fallbackToPolling
    });

    return cleanup;
  };

  const startChatTaskStream = (taskId: string, messageId: string) => {
    const key = `${taskId}:${messageId}`;
    if (activeStreamStops.current[key]) {
      return;
    }
    activeStreamStops.current[key] = streamChatTask(taskId, messageId, () => {
      delete activeStreamStops.current[key];
    });
  };

  const persistedPendingRunForMessage = (messageId: string): PersistedPendingAgentRun | undefined => (
    messages.find(item => item.id === messageId)?.pendingRun
  );

  const pendingRunForMessage = (messageId: string): PendingAgentRun | undefined => (
    pendingRuns[messageId] || restorePendingRun(persistedPendingRunForMessage(messageId))
  );

  const removePendingRun = (messageId: string) => {
    setPendingRuns(prev => {
      if (!prev[messageId]) {
        return prev;
      }
      const next = { ...prev };
      delete next[messageId];
      return next;
    });
    setMessages(prev => prev.map(item => item.id === messageId ? {
      ...item,
      pendingRun: undefined
    } : item));
  };

  const executePendingRun = async (messageId: string) => {
    const persistedPlan = persistedPendingRunForMessage(messageId);
    const plan = pendingRunForMessage(messageId);
    if (!plan) {
      message.warning('这份研究计划已失效，请重新发送问题生成新的计划。');
      return;
    }
    if (!pendingRuns[messageId] && persistedPlan?.hasFiles) {
      message.warning('这份计划包含本地附件，刷新后需要重新选择文件再执行。');
      return;
    }

    const engineMeta = agentEngineMeta[plan.agentEngine];
    removePendingRun(messageId);
    if (plan.chatStock?.symbol) {
      setSelectedSymbol(plan.chatStock.symbol);
    }
    if (plan.chatStock?.symbol || plan.chatStock?.name) {
      setActivePromptStock(plan.chatStock);
    }
    setSending(true);
    setMessages(prev => prev.map(item => item.id === messageId ? {
      ...item,
      title: '多 Agent 任务创建中',
      content: `确认收到，正在准备 ${engineMeta.agents.join(' / ')} 的执行上下文。`,
      chips: ['OrchestratorAgent', engineMeta.shortLabel, '创建任务'],
      reasoningTrace: plan.thinkingEnabled
        ? [
            ...buildRouteTrace(plan.userText, plan.chatStock, plan.agentEngine, plan.chatMode, plan.files.length, true).slice(0, 3),
            {
              phase: 'enqueue',
              title: '创建后台 Run',
              detail: '用户已确认研究计划，开始上传附件并写入 Agent 任务队列。',
              status: 'working' as ChatReasoningStatus
            }
          ]
        : undefined,
      guide: buildGeneratingGuide('agent'),
      status: 'working',
      thinkingEnabled: plan.thinkingEnabled
    } : item));

    try {
      const uploadedNames: string[] = [];
      const professionalReportIds: string[] = [];
      const shouldUseProfessionalReportUpload = hasProfessionalReportIntent(plan.userText, plan.files);
      for (const file of plan.files) {
        if (shouldUseProfessionalReportUpload) {
          const report = await uploadProfessionalReport({
            file,
            symbol: plan.chatStock?.symbol,
            title: file.name,
            report_type: inferProfessionalReportType(`${plan.userText} ${file.name}`),
            tags: ['chat-home', 'agent-context']
          });
          professionalReportIds.push(report.id);
          uploadedNames.push(`${report.title}（专业财报库：${report.metrics_count} 个指标 / ${report.chunks_count} 个证据片段）`);
        } else {
          const uploaded = await uploadDataFile(file, {
            symbol: plan.chatStock?.symbol,
            title: file.name,
            tags: 'chat-home,agent-context'
          });
          uploadedNames.push(uploaded.title);
        }
      }

      const taskPayload: InvestmentTaskCreate = {
        title: `${modeMeta[plan.chatMode].titlePrefix}：${trimTitle(plan.userText, stockOptionToken(plan.chatStock, '新任务'))}`,
        symbol: plan.chatStock?.symbol,
        asset_name: plan.chatStock?.name,
        task_type: modeMeta[plan.chatMode].taskType,
        engine: plan.agentEngine,
        horizon: plan.chatMode === 'monitor' ? '持续监控' : '1-4周',
        investor_profile: '稳健',
        objective: plan.userText,
        context: [
          '来源：主页多 Agent 对话入口，用户已确认研究计划后创建。',
          `Agent 引擎：${engineMeta.label}。参与角色：${engineMeta.agents.join('、')}。`,
          plan.agentEngine === 'financial_services' ? 'Financial Services Playbook：可路由到 market-researcher、earnings-reviewer、model-builder、pitch-agent、valuation-reviewer、KYC screener、GL reconciler 和 month-end closer。' : '',
          plan.chatStock?.symbol
            ? `当前标的：${plan.chatStock.name || plan.chatStock.symbol}（${plan.chatStock.symbol}）。`
            : plan.chatStock?.name
              ? `当前标的：${plan.chatStock.name}（待补充代码）。`
              : '',
          uploadedNames.length > 0 ? `已上传文件：${uploadedNames.join('、')}。` : '',
          professionalReportIds.length > 0 ? `专业财报库报告 ID：${professionalReportIds.join('、')}。后续对话可直接按报告 ID 调用引用型RAG、财报分析Agent和评测集。` : '',
          `可用上下文：${plan.dataSourceCount} 个数据源、${skillCount} 个技能、${plan.mcpServerCount} 个 MCP 工具、模型 ${plan.modelProvider || 'unknown'}/${plan.modelName || 'unknown'}。`,
          '请把事实、推断、风险、反证和下一步动作分开输出；资料不足时直接说明缺口。'
        ].filter(Boolean).join('\n'),
        engine_config: plan.agentEngine === 'financial_services'
          ? {
              source_project: 'anthropics/financial-services',
              playbooks: [
                'market-researcher',
                'earnings-reviewer',
                'model-builder',
                'pitch-agent',
                'valuation-reviewer',
                'kyc-screener',
                'gl-reconciler',
                'month-end-closer'
              ]
            }
          : plan.agentEngine === 'tradingagents'
            ? tradingAgentsCockpitEngineConfig
          : undefined,
        priority: plan.chatMode === 'risk' ? 2 : 3
      };

      const task = await createAgentTask(taskPayload);
      setTasks(prev => [task, ...prev.filter(item => item.id !== task.id)]);
      setMessages(prev => prev.map(item => item.id === messageId ? {
        ...item,
        title: '多 Agent 任务已入队',
        content: `我已经把确认后的计划转成 ${agentEngineMeta[task.engine].label} 任务：${task.title}。${professionalReportIds.length > 0 ? `附件已进入专业财报库（${professionalReportIds.length} 份），可继续在对话里追问指标、引用、财报分析或评测。` : '任务引擎会调用证据链、数据源、文件资料、MCP 工具和多 Agent 链路，完成后结果会自动回到这条对话里。'}`,
        chips: ['OrchestratorAgent', agentEngineMeta[task.engine].shortLabel, task.status, task.symbol || task.asset_name || 'portfolio', ...(professionalReportIds.length > 0 ? ['专业财报库'] : [])],
        reasoningTrace: item.thinkingEnabled === false ? item.reasoningTrace : traceFromTask(task),
        agentBlocks: blocksFromInvestmentTask(task),
        guide: buildGeneratingGuide('agent'),
        status: 'working',
        taskId: task.id
      } : item));
      startChatTaskStream(task.id, messageId);
      message.success('Agent 任务已创建');
    } catch (error: any) {
      setMessages(prev => prev.map(item => item.id === messageId ? {
        ...item,
        title: 'Agent 执行失败',
        content: error?.response?.data?.detail || error?.message || '后端暂时没有返回可用结果，请检查服务或模型配置。',
        chips: ['OrchestratorAgent', '需要检查后端', '可去设置'],
        reasoningTrace: [{
          phase: 'enqueue',
          title: '任务创建失败',
          detail: '计划已确认，但创建后台 Run 时失败。',
          status: 'error'
        }],
        guide: buildErrorGuide(plan.userText),
        status: 'error'
      } : item));
      message.error('任务创建失败');
    } finally {
      setSending(false);
    }
  };

  const editPendingRun = (messageId: string) => {
    const plan = pendingRunForMessage(messageId);
    if (!plan) {
      message.warning('这份研究计划已失效，请重新发送问题生成新的计划。');
      return;
    }
    setDraft(plan.userText);
    setAttachedFiles(plan.files);
    setChatMode(plan.chatMode);
    setReasoningMode(plan.reasoningMode);
    setAgentEngine(plan.agentEngine);
    if (plan.chatStock?.symbol) {
      setSelectedSymbol(plan.chatStock.symbol);
    }
    if (plan.chatStock?.symbol || plan.chatStock?.name) {
      setActivePromptStock(plan.chatStock);
    }
    removePendingRun(messageId);
    setMessages(prev => prev.map(item => item.id === messageId ? {
      ...item,
      title: '研究计划已放回输入框',
      content: '你可以修改目标、模式、标的或附件后重新发送，我会重新生成计划。',
      chips: ['待修改', '未创建任务'],
      guide: buildPlainReplyGuide(plan.userText, plan.chatStock),
      status: 'done'
    } : item));
    message.info('已放回输入框，可修改后重新发送');
  };

  const cancelPendingRun = (messageId: string) => {
    if (!pendingRuns[messageId] && !persistedPendingRunForMessage(messageId)) {
      return;
    }
    removePendingRun(messageId);
    setMessages(prev => prev.map(item => item.id === messageId ? {
      ...item,
      title: '研究计划已取消',
      content: '这次多 Agent Run 没有创建后台任务。你可以继续提问，或换一个更明确的目标重新开始。',
      chips: ['已取消', '未创建任务'],
      guide: buildPlainReplyGuide('', selectedStock),
      status: 'done'
    } : item));
  };

  useEffect(() => {
    const liveKeys = new Set<string>();
    messages.forEach(item => {
      if (item.role === 'assistant' && item.taskId && item.status === 'working') {
        const key = `${item.taskId}:${item.id}`;
        liveKeys.add(key);
        startChatTaskStream(item.taskId, item.id);
      }
    });

    Object.entries(activeStreamStops.current).forEach(([key, stop]) => {
      if (!liveKeys.has(key)) {
        stop();
        delete activeStreamStops.current[key];
      }
    });
  }, [messages]);

  useEffect(() => () => {
    Object.values(activeStreamStops.current).forEach(stop => stop());
    activeStreamStops.current = {};
  }, []);

  const submitChat = async () => {
    const question = draft.trim();
    const reportsForContext = selectedProfessionalReports;
    const evidenceForContext = selectedEvidenceItems;
    if (!question && attachedFiles.length === 0 && reportsForContext.length === 0 && evidenceForContext.length === 0) {
      message.warning('先输入一条消息、勾选入库研报，或附加文件。');
      return;
    }

    const filesForTask = [...attachedFiles];
    const attachmentMetas = filesForTask.map(fileToAttachmentMeta);
    const reportContext = [
      buildProfessionalReportContext(reportsForContext),
      buildEvidenceItemContext(evidenceForContext)
    ].filter(Boolean).join('\n\n');
    const baseUserText = question || (
      reportsForContext.length
        ? `请解读我选中的 ${reportsForContext.length} 份入库研报。`
        : evidenceForContext.length
          ? `请解读我选中的 ${evidenceForContext.length} 份证据库资料。`
        : `请分析我上传的 ${filesForTask.length} 个文件。`
    );
    const userText = [baseUserText, reportContext].filter(Boolean).join('\n\n');
    const shouldRunTask = hasInvestmentIntent(question, selectedStock, filesForTask.length);
    const isPlainChat = !shouldRunTask && filesForTask.length === 0 && reportsForContext.length === 0 && evidenceForContext.length === 0;
    const chatStock = shouldRunTask
      ? resolvePromptStock(question, appState.stocks, selectedStock)
      : undefined;
    if (chatStock?.symbol || chatStock?.name) {
      if (chatStock.symbol) {
        setSelectedSymbol(chatStock.symbol);
      }
      setActivePromptStock(chatStock);
    }
    const thinkingEnabled = reasoningMode === 'thinking';
    const userMessage: ChatMessage = {
      id: uid(),
      role: 'user',
      content: userText,
      chips: shouldRunTask
        ? [
            'OrchestratorAgent',
            modeMeta[chatMode].label,
            activeEngine.shortLabel,
            stockOptionToken(chatStock, '未选标的'),
            filesForTask.length > 0 ? `${filesForTask.length} 个文件` : '无附件',
            reportsForContext.length > 0 ? `${reportsForContext.length} 份入库研报` : '',
            evidenceForContext.length > 0 ? `${evidenceForContext.length} 份证据库资料` : ''
          ]
            .filter((chip): chip is string => Boolean(chip))
        : undefined,
      attachments: attachmentMetas.length > 0 ? attachmentMetas : undefined
    };

    if (isPlainChat) {
      const plainPendingId = uid();
      const plainPendingMessage: ChatMessage = {
        id: plainPendingId,
        role: 'assistant',
        content: '正在回复。',
        status: 'working',
        guide: buildGeneratingGuide('plain'),
        thinkingEnabled: false
      };
      setMessages(prev => [...prev, userMessage, plainPendingMessage]);
      setDraft('');
      setAttachedFiles([]);
      setSending(true);
      try {
        const reply = await runGeneralChat({
          message: question,
          history: messages.slice(-8).map(item => ({
            role: item.role,
            content: item.content
          })),
          context: {
            current_view: appState.currentView,
            selected_stock: selectedStock
              ? {
                  symbol: selectedStock.symbol,
                  name: selectedStock.name,
                  market: selectedStock.market
                }
              : null
          },
          locale: 'zh-CN'
        });
        setMessages(prev => prev.map(item => item.id === plainPendingId ? {
          ...item,
          title: undefined,
          content: reply.content,
          chips: undefined,
          reasoningTrace: undefined,
          guide: buildPlainReplyGuide(question, selectedStock),
          status: 'done',
          thinkingEnabled: false
        } : item));
      } catch (error: any) {
        const fallbackReply = buildLocalPlainChatReply(question)
          || error?.response?.data?.detail
          || error?.message
          || '我在，但普通聊天模型暂时没接上。你可以重试，或直接让我做投研分析。';
        setMessages(prev => prev.map(item => item.id === plainPendingId ? {
          ...item,
          title: undefined,
          content: fallbackReply,
          chips: ['聊天模型待重试'],
          reasoningTrace: undefined,
          guide: buildPlainReplyGuide(question, selectedStock),
          status: 'done',
          thinkingEnabled: false
        } : item));
      } finally {
        setSending(false);
      }
      return;
    }

    const pendingId = uid();
    const pendingMessage: ChatMessage = {
      id: pendingId,
      role: 'assistant',
      title: isPlainChat
        ? undefined
        : thinkingEnabled
        ? reasoningModeMeta.thinking.pendingTitle
        : shouldRunTask
          ? 'DeepFocus 正在快速研判'
          : reasoningModeMeta.fast.pendingTitle,
      content: isPlainChat
        ? '正在回复。'
        : thinkingEnabled
        ? '正在生成可展示推理摘要，并判断是否需要进入多 Agent Run。'
        : shouldRunTask
        ? `正在调用模型读取上下文，并判断是否需要调度 ${activeEngine.agents.join(' / ')}。`
        : '正在生成回复。',
      chips: shouldRunTask ? ['OrchestratorAgent', activeEngine.shortLabel, '模型回复中'] : undefined,
      reasoningTrace: thinkingEnabled && !isPlainChat
        ? buildRouteTrace(userText, chatStock, agentEngine, chatMode, filesForTask.length, shouldRunTask)
        : undefined,
      guide: buildGeneratingGuide(shouldRunTask ? 'agent' : 'plain'),
      status: 'working',
      thinkingEnabled: !isPlainChat && thinkingEnabled
    };

    setMessages(prev => [...prev, userMessage, pendingMessage]);
    setDraft('');
    setAttachedFiles([]);
    setSending(true);

    try {
      let createTaskAfterReply = shouldRunTask;
      try {
        const orchestratorReply = await runOrchestratorChat({
          message: userText,
          history: messages.slice(-8).map(item => ({
            role: item.role,
            content: item.content
          })),
          engine: agentEngine,
          mode: chatMode,
          stock: chatStock,
          attached_files: filesForTask.map(file => file.name),
          data_source_count: dataSources.length,
          mcp_server_count: mcpServers.length,
          reasoning_mode: reasoningMode,
          locale: 'zh-CN'
        });
        const handledInline = (Boolean(orchestratorReply.handled_inline) || isPlainChat) && filesForTask.length === 0;
        createTaskAfterReply = handledInline ? false : (shouldRunTask || (filesForTask.length > 0 && orchestratorReply.should_create_task));
        const showAgentTrace = thinkingEnabled && !handledInline;
        const replyChips = [
          orchestratorReply.agent || 'OrchestratorAgent',
          agentEngineMeta[orchestratorReply.engine]?.shortLabel || activeEngine.shortLabel,
          ...orchestratorReply.chips,
          ...orchestratorReply.suggested_actions.slice(0, 2)
        ];
        setMessages(prev => prev.map(item => item.id === pendingId ? {
          ...item,
          title: showAgentTrace ? (orchestratorReply.title || 'OrchestratorAgent') : undefined,
          content: orchestratorReply.content,
          chips: showAgentTrace ? Array.from(new Set(replyChips)).slice(0, 7) : undefined,
          reasoningTrace: showAgentTrace && normalizeTrace(orchestratorReply.reasoning_trace).length > 0
            ? normalizeTrace(orchestratorReply.reasoning_trace)
            : showAgentTrace
              ? buildRouteTrace(userText, chatStock, agentEngine, chatMode, filesForTask.length, createTaskAfterReply)
              : undefined,
          guide: buildAgentReplyGuide(userText, chatStock, orchestratorReply.suggested_actions, createTaskAfterReply),
          status: 'done'
        } : item));
      } catch (error: any) {
        const fallback = buildAgentConsoleFallback(
          question,
          chatStock,
          agentEngine,
          chatMode,
          filesForTask.length,
          createTaskAfterReply
        );
        setMessages(prev => prev.map(item => item.id === pendingId ? {
          ...item,
          title: fallback.title,
          content: fallback.content,
          chips: fallback.chips,
          reasoningTrace: thinkingEnabled ? fallback.reasoningTrace : undefined,
          guide: buildAgentReplyGuide(userText, chatStock, [], createTaskAfterReply),
          status: 'done'
        } : item));
        if (!createTaskAfterReply) {
          return;
        }
      }

      if (!createTaskAfterReply) {
        return;
      }

      const planMessageId = uid();
      const pendingRun: PendingAgentRun = {
        id: uid(),
        messageId: planMessageId,
        userText,
        files: filesForTask,
        chatStock,
        chatMode,
        reasoningMode,
        agentEngine,
        thinkingEnabled,
        dataSourceCount: dataSources.length,
        mcpServerCount: mcpServers.length,
        modelProvider: modelConfig?.provider,
        modelName: modelConfig?.model,
        createdAt: new Date().toISOString()
      };
      setPendingRuns(prev => ({ ...prev, [planMessageId]: pendingRun }));
      setMessages(prev => [...prev, {
        id: planMessageId,
        role: 'assistant',
        title: '待确认研究计划',
        content: formatPendingRunPlan(pendingRun),
        chips: ['待确认', activeEngine.shortLabel, modeMeta[chatMode].label, stockOptionToken(chatStock)],
        reasoningTrace: thinkingEnabled
          ? [
              ...buildRouteTrace(userText, chatStock, agentEngine, chatMode, filesForTask.length, true).slice(0, 3),
              {
                phase: 'approval',
                title: '等待确认',
                detail: '复杂投研任务会先确认计划，确认后再上传附件并创建后台 Run。',
                status: 'wait' as ChatReasoningStatus
              }
            ]
          : undefined,
        guide: {
          variant: 'analysis',
          title: '确认前可以再补一块证据',
          description: '后台 Run 会消耗更多时间；建议先确认标的、周期、研报和文件范围。',
          actions: [
            guideAction({
              kind: 'context',
              label: '补充研报/文件',
              detail: '打开上下文面板',
              primary: true
            }),
            guideAction({
              kind: 'prompt',
              label: '缩小问题范围',
              detail: '改成更明确的研究目标',
              prompt: `${stockOptionToken(chatStock, '当前标的')}：只分析未来 1-3 个交易日的风险、催化和关键价位`
            })
          ]
        },
        status: 'done',
        thinkingEnabled,
        pendingRun: serializePendingRun(pendingRun)
      }]);
      message.info('已生成研究计划，确认后再执行');
    } catch (error: any) {
      setMessages(prev => [...prev, {
        id: uid(),
        role: 'assistant',
        title: 'Agent 执行失败',
        content: error?.response?.data?.detail || error?.message || '后端暂时没有返回可用结果，请检查服务或模型配置。',
        chips: ['OrchestratorAgent', '需要检查后端', '可去设置'],
        guide: buildErrorGuide(userText),
        status: 'error'
      }]);
      message.error('任务创建失败');
    } finally {
      setSending(false);
    }
  };

  const renderMarketWorkspace = (segment: MarketSegmentKey) => {
    const meta = marketSegments[segment];
    const count = segment === 'a-share' ? marketSegmentCounts.aShare : marketSegmentCounts.global;

    return (
      <div className="home-shell">
        {renderMarketStrip()}
        <div className="page-heading-band market-workspace-heading">
          <Space direction="vertical" size={6}>
            <Text className="dashboard-eyebrow">MARKET WORKSPACE</Text>
            <Title level={3} style={{ margin: 0 }}>{meta.label}工作区</Title>
            <Text type="secondary">{meta.description}</Text>
            <Space wrap size={6}>
              {meta.chips.map(chip => (
                <Tag key={chip}>{chip}</Tag>
              ))}
              <Tag color={segment === 'a-share' ? 'red' : 'blue'}>{count} 个标的</Tag>
            </Space>
          </Space>
          <Space wrap>
            <Button icon={<EyeOutlined />} onClick={() => onViewChange('stocks')}>
              全市场自选
            </Button>
            <Button type="primary" icon={<LineChartOutlined />} loading={isMarketDataRefreshing} onClick={onRefreshMarketData}>
              刷新行情
            </Button>
          </Space>
        </div>
        <StockList
          stocks={appState.stocks}
          onStockSelect={onStockSelect}
          onAddStock={onAddStock}
          onRemoveStock={onRemoveStock}
          onToggleSubscription={onToggleStockSubscription}
          onRefreshMarketData={onRefreshMarketData}
          isMarketDataRefreshing={isMarketDataRefreshing}
          showHeader={false}
          marketSegment={segment}
          onMarketSegmentChange={handleMarketSegmentChange}
        />
      </div>
    );
  };

  if (appState.currentView === 'a-share-market') {
    return renderMarketWorkspace('a-share');
  }

  if (appState.currentView === 'global-market') {
    return renderMarketWorkspace('global');
  }

  if (appState.currentView === 'stocks') {
    return (
      <div className="home-shell">
        {renderMarketStrip()}
        <div className="page-heading-band">
          <Space direction="vertical" size={4}>
            <Text className="dashboard-eyebrow">STOCK RESEARCH</Text>
            <Title level={3} style={{ margin: 0 }}>全市场个股研究池</Title>
            <Text type="secondary">统一沉淀关注标的，A股和港美股按交易语境分区管理。</Text>
          </Space>
          <Button type="primary" icon={<LineChartOutlined />} onClick={() => onViewChange('home')}>
            回到 Agent
          </Button>
        </div>
        <StockList
          stocks={appState.stocks}
          onStockSelect={onStockSelect}
          onAddStock={onAddStock}
          onRemoveStock={onRemoveStock}
          onToggleSubscription={onToggleStockSubscription}
          onRefreshMarketData={onRefreshMarketData}
          isMarketDataRefreshing={isMarketDataRefreshing}
          showHeader={false}
          marketSegment="all"
          onMarketSegmentChange={handleMarketSegmentChange}
        />
      </div>
    );
  }

  if (appState.currentView === 'shop') {
    return (
      <div className="home-shell">
        {renderMarketStrip()}
        <div className="page-heading-band">
          <Space direction="vertical" size={4}>
            <Text className="dashboard-eyebrow">RESEARCH MARKETPLACE</Text>
            <Title level={3} style={{ margin: 0 }}>研究资产库</Title>
            <Text type="secondary">把高质量研报、策略模板、数据包和专家服务包装成可复用投研资产。</Text>
          </Space>
          <Button icon={<ShoppingOutlined />} onClick={() => onViewChange('orders')}>
            查看资产单
          </Button>
        </div>
        <Shop
          products={appState.products}
          onProductClick={onProductClick}
          onAddToCart={onAddToCart}
          showHeader={false}
        />
      </div>
    );
  }

  const pendingAttachmentMetas = attachedFiles.map(fileToAttachmentMeta);
  const composer = (
    <div
      className={`chatgpt-composer-shell ${composerDragActive ? 'is-dragging' : ''}`}
      ref={composerRef}
      onDragOver={handleComposerDragOver}
      onDragLeave={handleComposerDragLeave}
      onDrop={handleComposerDrop}
    >
      <div className="chatgpt-composer-label">
        <span><RobotOutlined /> Agent 任务输入</span>
        <small>{stockOptionToken(visibleContextStock)} · {activeEngine.shortLabel}</small>
      </div>
    <div className={`chatgpt-composer chatgpt-composer-minimal ${composerDragActive ? 'is-dragging' : ''}`}>
      {composerDragActive && (
        <div className="chatgpt-drop-overlay">
          <span><PaperClipOutlined /> 松开加入 Agent 上下文</span>
        </div>
      )}

      {pendingAttachmentMetas.length > 0 && (
        <div className="attached-file-dock">
          <div className="attached-file-dock-head">
            <span><PaperClipOutlined /> {pendingAttachmentMetas.length} 个附件</span>
            <Button
              type="text"
              size="small"
              icon={<DeleteOutlined />}
              onClick={() => setAttachedFiles([])}
            >
              清空
            </Button>
          </div>
          <AttachmentPreviewList
            attachments={pendingAttachmentMetas}
            compact
            onRemove={removeAttachedFile}
          />
        </div>
      )}

      {messages.length > 0 && draft.trim().length === 0 && (
        <div className="chatgpt-context-strip">
          {quickPrompts.map(prompt => (
            <button
              key={prompt}
              type="button"
              onClick={() => {
                setDraft(prompt);
                window.setTimeout(() => composerRef.current?.querySelector('textarea')?.focus(), 0);
              }}
            >
              {prompt}
            </button>
          ))}
        </div>
      )}

      <div className="chatgpt-input-row">
        <Upload
          showUploadList={false}
          beforeUpload={file => {
            addAttachedFiles([file]);
            return false;
          }}
          multiple
        >
          <Button
            type="text"
            shape="circle"
            className="chatgpt-icon-button"
            icon={<PlusOutlined />}
            title="添加文件"
          />
        </Upload>
        <TextArea
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void submitChat();
            }
          }}
          placeholder="把目标交给 Agent：标的、周期、要判断的问题..."
          autoSize={{ minRows: 1, maxRows: 7 }}
        />
        <Segmented
          size="small"
          className="chatgpt-reasoning-switch"
          value={reasoningMode}
          onChange={value => setReasoningMode(value as ReasoningMode)}
          options={Object.entries(reasoningModeMeta).map(([value, meta]) => ({
            value,
            label: meta.label
          }))}
        />
        <Button
          type="text"
          shape="circle"
          className="chatgpt-icon-button"
          icon={<ToolOutlined />}
          title="投研上下文"
          onClick={() => setContextRailOpen(value => !value)}
        />
        <Button
          type="primary"
          shape="circle"
          icon={<SendOutlined />}
          loading={sending}
          onClick={() => void submitChat()}
        />
      </div>

      {contextRailOpen && (
        <div className="chatgpt-context-tray">
          <div className="chatgpt-context-topbar">
            <div className="chatgpt-context-control">
              <span>模式</span>
              <Segmented
                size="small"
                value={chatMode}
                onChange={value => setChatMode(value as ChatMode)}
                options={Object.entries(modeMeta).map(([value, meta]) => ({ value, label: meta.label }))}
              />
            </div>
            <div className="chatgpt-context-control">
              <span>Agent</span>
              <Select
                size="small"
                value={agentEngine}
                onChange={value => setAgentEngine(value as AgentEngine)}
                options={Object.entries(agentEngineMeta).map(([value, meta]) => ({
                  value,
                  label: meta.shortLabel
                }))}
                popupMatchSelectWidth={false}
              />
            </div>
            <div className="chatgpt-context-control">
              <span>标的</span>
              <Select
                size="small"
                value={selectedStock?.symbol}
                onChange={setSelectedSymbol}
                options={stockOptions}
                placeholder="选择标的"
                popupMatchSelectWidth={false}
              />
            </div>
          </div>
          <div className="home-research-bridge">
            <div className="home-research-bridge-head">
              <span><FileSearchOutlined /> 入库研报</span>
              <small>
                {totalSelectedReportContext
                  ? `${totalSelectedReportContext} 份入库资料已选`
                  : totalSelectableReports
                    ? `${totalSelectableReports} 份可选`
                    : '勾选后直接进入 Agent 上下文'}
              </small>
            </div>
            <div className="home-indexed-report-toolbar">
              <Input
                size="small"
                allowClear
                value={indexedReportQuery}
                onChange={event => setIndexedReportQuery(event.target.value)}
                placeholder="筛选已入库研报：标题 / 代码 / 周期"
              />
              <Button
                size="small"
                loading={professionalReportsLoading || evidenceItemsLoading}
                onClick={() => {
                  void loadProfessionalReports();
                  void loadEvidenceItems();
                }}
              >
                刷新
              </Button>
              <Button size="small" disabled={!visibleProfessionalReports.length && !visibleEvidenceItems.length} onClick={selectVisibleProfessionalReports}>
                全选当前
              </Button>
              <Button size="small" disabled={!totalSelectedReportContext} onClick={clearSelectedProfessionalReports}>
                清空
              </Button>
              <Button
                size="small"
                type="primary"
                loading={professionalInterpreting}
                disabled={!totalSelectedReportContext && !visibleProfessionalReports.length && !visibleEvidenceItems.length}
                onClick={() => void interpretSelectedProfessionalReports()}
              >
                {totalSelectedReportContext ? `解读选中 ${totalSelectedReportContext}` : '解读最新'}
              </Button>
            </div>
            <div className="home-research-subhead">
              <span>勾选后会带入下一次对话和 Agent 分析</span>
              <Space size={6}>
                <Button size="small" onClick={() => onViewChange('research-workbench')}>
                  工作台
                </Button>
              </Space>
            </div>
            {professionalReportsLoading || evidenceItemsLoading ? (
              <Text type="secondary" className="home-research-more">正在读取入库研报和证据库资料...</Text>
            ) : visibleProfessionalReports.length > 0 || visibleEvidenceItems.length > 0 ? (
              <div className="home-research-results home-indexed-report-results">
                {visibleProfessionalReports.slice(0, 10).map(report => {
                  const key = professionalReportKey(report);
                  return (
                    <label key={key} className={`home-research-hit home-indexed-report-hit ${selectedProfessionalReportIds.has(key) ? 'selected' : ''}`}>
                      <Checkbox
                        checked={selectedProfessionalReportIds.has(key)}
                        onChange={() => toggleProfessionalReport(report)}
                      />
                      <span>
                        <strong>{report.title}</strong>
                        <small>{formatProfessionalReportMeta(report)}</small>
                      </span>
                    </label>
                  );
                })}
                {visibleEvidenceItems.slice(0, Math.max(0, 10 - Math.min(visibleProfessionalReports.length, 10))).map(item => (
                  <label key={item.id} className={`home-research-hit home-indexed-report-hit ${selectedEvidenceItemIds.has(item.id) ? 'selected' : ''}`}>
                    <Checkbox
                      checked={selectedEvidenceItemIds.has(item.id)}
                      onChange={() => toggleEvidenceItem(item)}
                    />
                    <span>
                      <strong>{item.title}</strong>
                      <small>{formatEvidenceItemMeta(item)}</small>
                    </span>
                  </label>
                ))}
                {visibleProfessionalReports.length + visibleEvidenceItems.length > 10 && (
                  <Text type="secondary" className="home-research-more">
                    还有 {visibleProfessionalReports.length + visibleEvidenceItems.length - 10} 份；用上方输入框筛选，或点全选当前批量加入上下文。
                  </Text>
                )}
              </div>
            ) : (
              <Text type="secondary" className="home-research-more">
                暂无匹配的入库研报；清空筛选词，或先在下方搜研报并下载入库。
              </Text>
            )}
            <div className="chatgpt-context-sources compact">
              {contextActions.map(action => (
                <button
                  key={action.key}
                  type="button"
                  className="chatgpt-context-source"
                  onClick={() => onViewChange(action.view)}
                >
                  <span className="context-source-icon">{action.icon}</span>
                  <span>
                    <strong>{action.title}</strong>
                    <small>{action.detail}</small>
                  </span>
                  <em>{action.status}</em>
                </button>
              ))}
            </div>
            <div className="home-research-subhead">
              <span>外部资料抓取</span>
              <small>默认搜前 {HOME_RESEARCH_SEARCH_PAGES} 页 · 时间倒序</small>
            </div>
            <div className="home-research-search">
              <Input
                size="small"
                value={researchKeyword}
                onChange={event => setResearchKeyword(event.target.value)}
                onPressEnter={() => void runResearchSearch()}
                placeholder="英伟达 / HBM / AI capex"
              />
              <Input
                size="small"
                value={researchTag}
                onChange={event => setResearchTag(event.target.value)}
                placeholder="标签：海外投行报告"
              />
              <Button size="small" type="primary" loading={researchSearching} onClick={() => void runResearchSearch()}>
                搜研报
              </Button>
              <Button
                size="small"
                loading={researchSummarizing}
                disabled={!researchResults.length}
                onClick={() => void summarizeResearchHitsInChat()}
              >
                AI总结命中
              </Button>
              <Button
                size="small"
                disabled={!selectedResearchResults.length}
                loading={researchDownloading}
                onClick={() => void downloadResearchHits()}
              >
                下载全文
              </Button>
            </div>
            {researchResults.length > 0 && (
              <div className="home-research-results">
                {researchResults.slice(0, 6).map((item, index) => {
                  const key = researchHitKey(item, index);
                  return (
                    <label key={key} className="home-research-hit">
                      <Checkbox
                        checked={selectedResearchKeys.has(key)}
                        onChange={() => toggleResearchHit(item, index)}
                      />
                      <span>
                        <strong>{item.name}</strong>
                        <small>
                          {[item.hashtag ? `#${item.hashtag}` : '', item.size ? formatFileSize(item.size) : '', formatResearchHitDate(item.topicCreateTime || item.createTime), item.downloadCount ? `${item.downloadCount} 次下载` : '']
                            .filter(Boolean)
                            .join(' · ')}
                        </small>
                      </span>
                    </label>
                  );
                })}
                {researchResults.length > 6 && (
                  <Text type="secondary" className="home-research-more">
                    还有 {researchResults.length - 6} 条；不勾选时会总结全部命中，勾选后只总结选中项。
                  </Text>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
    </div>
  );

  return (
    <div className={`chatgpt-home investor-chat-home ${messages.length > 0 ? 'has-chat' : 'is-empty'}`}>
      <section className="chatgpt-mini-nav investor-chat-nav">
        <div className="investor-chat-title">
          <span className="investor-chat-logo"><RobotOutlined /></span>
          <div>
            <strong>Agent Cockpit</strong>
            <small>{modeMeta[chatMode].label} · {reasoningModeMeta[reasoningMode].label} · {activeEngine.shortLabel}</small>
          </div>
        </div>
        <Space size={[6, 6]} wrap className="investor-chat-actions">
          <Button size="small" type="primary" className="ai-chat-top-pill" icon={<RobotOutlined />} onClick={focusAiChat}>
            Agent
          </Button>
          {conversationOptions.length > 0 && (
            <Select
              size="small"
              className="conversation-history-select"
              value={activeConversationId || undefined}
              onChange={handleConversationChange}
              options={conversationOptions}
              popupMatchSelectWidth={320}
              suffixIcon={<HistoryOutlined />}
            />
          )}
          {(visibleContextStock?.symbol || visibleContextStock?.name) && <Tag>{stockOptionToken(visibleContextStock)}</Tag>}
          <Tag>{runningTasks} Run</Tag>
          {messages.length > 0 && (
            <Segmented
              size="small"
              className="thread-view-switch"
              value={threadViewMode}
              onChange={value => setThreadViewMode(value as ThreadViewMode)}
              options={[
                { value: 'compact', label: '精简' },
                { value: 'full', label: '完整' }
              ]}
            />
          )}
          <Button size="small" type="text" onClick={() => onViewChange('agent-center')}>
            Agent 任务
          </Button>
          <Button size="small" type="text" onClick={handleNewConversation}>
            新聊天
          </Button>
        </Space>
      </section>

      <main className="chatgpt-stage" ref={chatStageRef}>
        {messages.length === 0 ? (
          <section className="chatgpt-landing investor-chat-landing">
            <Text className="chat-entry-eyebrow">AGENT COCKPIT</Text>
            <Title level={1}>今天要判断什么？</Title>
            <Paragraph type="secondary">
              输入标的、周期和要回答的问题；DeepFocus 会先找证据，再拆反证和风险，最后收束成可执行动作。
            </Paragraph>
            <div className="chatgpt-landing-composer">
              {composer}
            </div>
            <div className="chatgpt-suggestions">
              {quickPrompts.map(prompt => (
                <button key={prompt} type="button" onClick={() => setDraft(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>
            <div className="chatgpt-capability-pills">
              <Button icon={<EyeOutlined />} onClick={() => onViewChange('stocks')}>观察池</Button>
              <Button icon={<FileSearchOutlined />} onClick={() => setContextRailOpen(true)}>研报证据</Button>
              <Button icon={<DatabaseOutlined />} onClick={() => onViewChange('data-sources')}>证据库</Button>
              <Button icon={<SafetyCertificateOutlined />} onClick={() => setChatMode('risk')}>风险复核</Button>
              <Button icon={<FundProjectionScreenOutlined />} onClick={() => onViewChange('multi-market-decision')}>策略与组合</Button>
            </div>
            {renderDecisionPath()}
            {renderVisualBoard('landing')}
            {renderModuleMap()}
          </section>
        ) : (
          <div className="chatgpt-thread-shell">
            <div className="chatgpt-thread">
              {messages.map(item => (
                <div key={item.id} className={`chatgpt-message ${item.role} ${item.status || ''}`}>
                  <div className="chatgpt-message-avatar">
                    {item.role === 'assistant' ? <RobotOutlined /> : appState.user?.username?.[0]?.toUpperCase() || 'U'}
                  </div>
                  <div className="chatgpt-message-body">
                    {item.role === 'assistant' ? (
                      <div className="chatgpt-message-topline">
                        <span className="chatgpt-message-title">{item.title || 'DeepFocus'}</span>
                        <div className="chatgpt-message-toolbar">
                          <Button
                            size="small"
                            type="text"
                            shape="circle"
                            icon={copiedMessageId === item.id ? <CheckOutlined /> : <CopyOutlined />}
                            title={copiedMessageId === item.id ? '已复制' : '复制回复'}
                            aria-label={copiedMessageId === item.id ? '已复制回复' : '复制回复'}
                            onClick={() => void copyAssistantMessage(item)}
                          />
                          {item.taskId && (
                            <Button
                              size="small"
                              type="text"
                              shape="circle"
                              icon={<CloudServerOutlined />}
                              title="打开 Agent 任务"
                              aria-label="打开 Agent 任务"
                              onClick={() => onViewChange('agent-center')}
                            />
                          )}
                        </div>
                      </div>
                    ) : item.title ? <Text strong>{item.title}</Text> : null}
                    {item.attachments && <AttachmentPreviewList attachments={item.attachments} />}
                    {item.role === 'assistant' && (
                      <ReasoningTrace
                        steps={item.reasoningTrace}
                        mode={item.thinkingEnabled === false ? 'fast' : 'thinking'}
                        compact={compactThread}
                      />
                    )}
                    {item.role === 'assistant' && <AgentRunBlocks blocks={item.agentBlocks} compact={compactThread} />}
                    <Paragraph style={{ whiteSpace: 'pre-line' }}>{item.content}</Paragraph>
                    {item.role === 'assistant' && renderGuidePanel(item.guide)}
                    {item.role === 'assistant' && renderReportInsightCards(item.reportCards)}
                    {item.chips && (
                      <Space size={[6, 6]} wrap>
                        {item.chips.map(chip => <Tag key={chip}>{chip}</Tag>)}
                      </Space>
                    )}
                    {item.role === 'assistant' && (pendingRuns[item.id] || item.pendingRun) && (
                      <div className="chatgpt-message-actions">
                        <div className="chatgpt-plan-actions">
                          <Button
                            size="small"
                            type="primary"
                            icon={<SendOutlined />}
                            loading={sending}
                            onClick={() => void executePendingRun(item.id)}
                          >
                            确认执行
                          </Button>
                          <Button size="small" onClick={() => editPendingRun(item.id)}>
                            修改计划
                          </Button>
                          <Button size="small" danger onClick={() => cancelPendingRun(item.id)}>
                            取消
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {renderInsightRail()}
          </div>
        )}
      </main>

      {messages.length > 0 && (
        <div className="chatgpt-composer-anchor">
          {composer}
          <Text type="secondary" className="chatgpt-disclaimer">
            输出仅供投研参考，不构成投资建议。
          </Text>
        </div>
      )}
    </div>
  );
};

export default HomePage;
