import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApartmentOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BankOutlined,
  BranchesOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ControlOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FundProjectionScreenOutlined,
  HistoryOutlined,
  LinkOutlined,
  LockOutlined,
  NodeIndexOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Button, Select, Tag, Tooltip, message } from 'antd';
import CenterShell from './common/CenterShell';
import {
  fetchOntologyDemo,
  OntologyDemoAction,
  OntologyDemoSnapshot,
  OntologyEdge,
  OntologyEntityType,
  OntologyNode,
  recordOntologyDemoAction,
} from '../services/ontologyService';
import {
  listRealtimeMessages,
  RealtimeMessageRecord,
} from '../services/eventService';
import './InvestmentOntologyCenter.css';

type WorkspaceView = 'decision' | 'network' | 'objects' | 'actions' | 'governance';
type LiveEvidenceTone = 'positive' | 'risk' | 'neutral';
type LiveEvidenceFilter = 'all' | LiveEvidenceTone;
type NetworkFilter = 'all' | 'risk' | 'positive';

interface ObjectTypeDefinition {
  type: OntologyEntityType;
  label: string;
  description: string;
  primaryKey: string;
  source: string;
  properties: string[];
  icon: React.ReactNode;
  accent: string;
}

const OBJECT_TYPES: ObjectTypeDefinition[] = [
  {
    type: 'Portfolio',
    label: '组合',
    description: '投资账户或策略组合，是风险预算和行动汇总的边界。',
    primaryKey: 'portfolio_id',
    source: '模拟盘 / 用户组合',
    properties: ['名称', '净值', '币种', '运行模式'],
    icon: <ApartmentOutlined />,
    accent: '#34d399',
  },
  {
    type: 'Issuer',
    label: '公司',
    description: '证券背后的真实经营主体，用于承接行业、财务和经营事实。',
    primaryKey: 'issuer_id',
    source: '证券主数据',
    properties: ['公司名', '行业', '板块', '市场'],
    icon: <BankOutlined />,
    accent: '#60a5fa',
  },
  {
    type: 'Security',
    label: '证券',
    description: '可交易证券的统一身份，将代码、简称和供应商别名合并。',
    primaryKey: 'security_id',
    source: '行情 / 主数据',
    properties: ['代码', '交易所', '币种', '价格', '时间'],
    icon: <FundProjectionScreenOutlined />,
    accent: '#2dd4bf',
  },
  {
    type: 'Position',
    label: '持仓',
    description: '某组合对某证券的真实暴露，连接成本、盈亏和风险预算。',
    primaryKey: 'position_id',
    source: '模拟盘 / 券商导入',
    properties: ['权重', '成本', '盈亏', '风险上限'],
    icon: <AuditOutlined />,
    accent: '#10b981',
  },
  {
    type: 'Thesis',
    label: '投资论点',
    description: '持有一只股票的核心理由，包含置信度和可验证的失效条件。',
    primaryKey: 'thesis_id',
    source: '投研工作流',
    properties: ['状态', '置信度', '失效条件'],
    icon: <BulbOutlined />,
    accent: '#a78bfa',
  },
  {
    type: 'Event',
    label: '事件',
    description: '被证据支持的现实变化，用来增强或削弱投资论点。',
    primaryKey: 'event_id',
    source: '事件抽取',
    properties: ['事件类型', '发生时间', '重要性', '方向'],
    icon: <ThunderboltOutlined />,
    accent: '#f59e0b',
  },
  {
    type: 'Evidence',
    label: '证据',
    description: '快讯、文章、研报或数据快照，是每条推断可以回看的原始依据。',
    primaryKey: 'evidence_id',
    source: 'DAO 财经信息库',
    properties: ['来源', '入库时间', '可信度', '原文链接'],
    icon: <DatabaseOutlined />,
    accent: '#22d3ee',
  },
];

const OBJECT_META = Object.fromEntries(
  OBJECT_TYPES.map(item => [item.type, item]),
) as Record<OntologyEntityType, ObjectTypeDefinition>;

const LINK_TYPES = [
  ['EVIDENCES', '证据 → 事件', '证明某件事确实发生'],
  ['SUPPORTS', '事件 → 论点', '增强投资逻辑'],
  ['WEAKENS', '事件 → 论点', '削弱投资逻辑'],
  ['CONTRADICTS', '事件 → 论点', '触发论点复核'],
  ['ABOUT', '论点 → 证券', '论点属于哪只股票'],
  ['REPRESENTS', '证券 ↔ 公司', '证券代表经营主体'],
  ['GOVERNS', '论点 → 持仓', '论点变化约束仓位'],
  ['HOLDS', '持仓 ↔ 证券', '持仓持有哪只证券'],
  ['POSITION_IN', '持仓 → 组合', '风险传导到组合'],
] as const;

const EDGE_LABELS: Record<string, string> = Object.fromEntries(
  LINK_TYPES.map(([apiName, label]) => [apiName, label.split(' ')[0]]),
);

const ACTION_DEFINITIONS = [
  {
    type: 'keep_watch',
    label: '维持观察',
    description: '保持当前仓位，将新证据纳入下一次复核。',
    guardrail: '不改变仓位，只写入决策记录',
  },
  {
    type: 'request_research',
    label: '发起补证',
    description: '把当前缺口交给投研流程，要求补充可核验材料。',
    guardrail: '必须保留标的、缺口和发起理由',
  },
  {
    type: 'reduce_paper',
    label: '模拟减仓',
    description: '当仓位超过风险预算时，记录一笔模拟降仓动作。',
    guardrail: '仅模拟盘，不连接券商',
  },
  {
    type: 'invalidate_thesis',
    label: '标记论点失效',
    description: '失效条件被证据满足时，冻结原论点并留下原因。',
    guardrail: '必须由可追溯证据支持',
  },
] as const;

const POSITIVE_TERMS = [
  '增长', '回购', '增持', '上调', '突破', '中标', '改善', '看好', '机会',
  '盈利企稳', '超预期', '创新高', '政策支持', '成本回落', '份额提升',
];

const RISK_TERMS = [
  '风险', '承压', '减持', '下调', '处罚', '诉讼', '亏损', '低于预期',
  '下滑', '疲弱', '警示', '违约', '监管', '不确定', '尚需等待', '未临',
];

const ATTRIBUTE_LABELS: Record<string, string> = {
  ticker: '股票代码',
  exchange: '交易所',
  currency: '币种',
  price: '最新价',
  source: '数据来源',
  credibility: '可信度',
  known_at: '入库时间',
  event_type: '事件类型',
  occurred_at: '发生时间',
  severity: '重要性',
  confidence: '论点置信度',
  invalidation: '失效条件',
  weight_pct: '当前仓位',
  risk_budget_pct: '风险预算',
  pnl_pct: '浮动盈亏',
  status: '状态',
  name: '名称',
};

const NAV_ITEMS: Array<{
  id: WorkspaceView;
  label: string;
  helper: string;
  icon: React.ReactNode;
}> = [
  { id: 'decision', label: '决策台', helper: '今天先看什么', icon: <ControlOutlined /> },
  { id: 'network', label: '关系网络', helper: '结论如何形成', icon: <BranchesOutlined /> },
  { id: 'objects', label: '对象目录', helper: '系统认识什么', icon: <AppstoreOutlined /> },
  { id: 'actions', label: '动作中心', helper: '把判断变成流程', icon: <PlayCircleOutlined /> },
  { id: 'governance', label: '治理与溯源', helper: '数据从哪来', icon: <LockOutlined /> },
];

function liveEvidenceTone(item: RealtimeMessageRecord): LiveEvidenceTone {
  if (item.severity === 'critical' || item.severity === 'warning') return 'risk';
  const text = `${item.title} ${item.content}`;
  const positiveHits = POSITIVE_TERMS.filter(term => text.includes(term)).length;
  const riskHits = RISK_TERMS.filter(term => text.includes(term)).length;
  if (riskHits > positiveHits) return 'risk';
  if (positiveHits > riskHits) return 'positive';
  return 'neutral';
}

function cleanEvidenceText(value: string): string {
  return value.replace(/\*+/g, '').replace(/#+/g, '').replace(/\s+/g, ' ').trim();
}

function liveEvidenceTitle(item: RealtimeMessageRecord): string {
  const title = cleanEvidenceText(item.title);
  if (title && !/^【?(研报)?快讯】?$/.test(title)) return title;
  return item.content
    .split(/\n+/)
    .map(cleanEvidenceText)
    .find(line => line.length > 8) || title || 'DAO 财经相关信息';
}

function liveEvidenceSummary(item: RealtimeMessageRecord): string {
  const title = liveEvidenceTitle(item);
  const content = cleanEvidenceText(item.content);
  const summary = content.startsWith(title) ? content.slice(title.length).trim() : content;
  return summary.length > 170 ? `${summary.slice(0, 170)}…` : summary;
}

function percentage(value: unknown): string {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? `${Math.round(numberValue * 100)}%` : '—';
}

function numberText(value: unknown, suffix = ''): string {
  const numberValue = Number(value);
  return Number.isFinite(numberValue)
    ? `${numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}${suffix}`
    : '—';
}

function attributeText(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (key === 'confidence' || key === 'credibility') return percentage(value);
  if (key.endsWith('_pct')) return numberText(value, '%');
  return String(value);
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function edgeClass(edge: OntologyEdge): string {
  if (edge.polarity > 0) return 'positive';
  if (edge.polarity < 0) return 'negative';
  return 'neutral';
}

function graphNodeMetric(node: OntologyNode): string {
  if (node.type === 'Evidence') {
    return `${node.attributes.source || 'DAO 财经'} · 可信度 ${percentage(node.attributes.credibility)}`;
  }
  if (node.type === 'Event') {
    const tone = node.attributes.severity;
    return tone === 'risk' ? '风险信号' : tone === 'positive' ? '积极信号' : '等待确认';
  }
  if (node.type === 'Thesis') return `置信度 ${percentage(node.attributes.confidence)}`;
  if (node.type === 'Position') {
    return `仓位 ${numberText(node.attributes.weight_pct, '%')} / 上限 ${numberText(node.attributes.risk_budget_pct, '%')}`;
  }
  if (node.type === 'Portfolio') return '组合影响汇总';
  return node.canonical_key;
}

const InvestmentOntologyCenter: React.FC = () => {
  const [snapshot, setSnapshot] = useState<OntologyDemoSnapshot | null>(null);
  const [activeView, setActiveView] = useState<WorkspaceView>('decision');
  const [selectedSecurityId, setSelectedSecurityId] = useState<string>();
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [selectedObjectType, setSelectedObjectType] = useState<OntologyEntityType>('Security');
  const [networkFilter, setNetworkFilter] = useState<NetworkFilter>('all');
  const [liveEvidence, setLiveEvidence] = useState<RealtimeMessageRecord[]>([]);
  const [liveEvidenceLoading, setLiveEvidenceLoading] = useState(false);
  const [liveEvidenceError, setLiveEvidenceError] = useState<string | null>(null);
  const [liveEvidenceFilter, setLiveEvidenceFilter] = useState<LiveEvidenceFilter>('all');
  const [showAllEvidence, setShowAllEvidence] = useState(false);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionPulseId, setActionPulseId] = useState<string>();
  const [apiMessage, contextHolder] = message.useMessage();
  const loadSequence = useRef(0);

  const load = useCallback(async (securityId?: string, quiet = false) => {
    const sequence = ++loadSequence.current;
    if (!quiet) setLoading(true);
    setError(null);
    setLiveEvidenceLoading(true);
    setLiveEvidenceError(null);
    setShowAllEvidence(false);
    try {
      const result = await fetchOntologyDemo(securityId);
      if (sequence !== loadSequence.current) return;
      setSnapshot(result);
      setSelectedSecurityId(result.selected_security_id);
      setSelectedNodeId(result.decision.thesis.id);
      setNetworkFilter('all');
      const selectedAsset = result.assets.find(asset => asset.security_id === result.selected_security_id);
      const ticker = selectedAsset?.canonical_key.split('.')[0] || '';
      const aliases = [
        selectedAsset?.label,
        selectedAsset?.canonical_key,
        ticker,
      ].filter(Boolean).join(',');
      try {
        const messages = await listRealtimeMessages({ anyq: aliases, limit: 80 });
        if (sequence === loadSequence.current) {
          const unique = messages.filter((item, index, all) => (
            all.findIndex(candidate => candidate.id === item.id) === index
          ));
          setLiveEvidence(unique);
          setLiveEvidenceFilter(unique.some(item => liveEvidenceTone(item) === 'risk') ? 'risk' : 'all');
        }
      } catch (liveError) {
        if (sequence === loadSequence.current) {
          setLiveEvidence([]);
          setLiveEvidenceError(liveError instanceof Error ? liveError.message : 'DAO 财经信息读取失败');
        }
      }
    } catch (loadError) {
      if (sequence === loadSequence.current) {
        setError(loadError instanceof Error ? loadError.message : '本体快照加载失败');
      }
    } finally {
      if (sequence === loadSequence.current) {
        if (!quiet) setLoading(false);
        setLiveEvidenceLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const recordAction = useCallback(async (actionType: string, reason: string) => {
    if (!snapshot) return;
    setActionLoading(true);
    try {
      const created = await recordOntologyDemoAction({
        security_id: snapshot.selected_security_id,
        action_type: actionType,
        reason,
      });
      setSnapshot(current => current
        ? { ...current, actions: [created, ...current.actions.filter(item => item.id !== created.id)] }
        : current);
      setActionPulseId(created.id);
      window.setTimeout(() => setActionPulseId(undefined), 1800);
      apiMessage.success(`已记录“${created.action_label}”，未触发真实交易`);
    } catch (actionError) {
      apiMessage.error(actionError instanceof Error ? actionError.message : '动作记录失败');
    } finally {
      setActionLoading(false);
    }
  }, [apiMessage, snapshot]);

  const annotatedLiveEvidence = useMemo(
    () => liveEvidence.map(item => ({ item, tone: liveEvidenceTone(item) })),
    [liveEvidence],
  );

  const liveEvidenceStats = useMemo(() => {
    const positive = annotatedLiveEvidence.filter(entry => entry.tone === 'positive').length;
    const risk = annotatedLiveEvidence.filter(entry => entry.tone === 'risk').length;
    const neutral = annotatedLiveEvidence.length - positive - risk;
    const sources = new Set(
      annotatedLiveEvidence.map(entry => entry.item.source_name || 'DAO 财经').filter(Boolean),
    ).size;
    return { positive, risk, neutral, sources, total: annotatedLiveEvidence.length };
  }, [annotatedLiveEvidence]);

  const matchingLiveEvidence = useMemo(
    () => annotatedLiveEvidence.filter(
      entry => liveEvidenceFilter === 'all' || entry.tone === liveEvidenceFilter,
    ),
    [annotatedLiveEvidence, liveEvidenceFilter],
  );

  const filteredLiveEvidence = useMemo(
    () => matchingLiveEvidence.slice(0, showAllEvidence ? 8 : 3),
    [matchingLiveEvidence, showAllEvidence],
  );

  const selectedAsset = snapshot?.assets.find(asset => asset.security_id === selectedSecurityId);
  const decision = snapshot?.decision;
  const liveSignalTone: LiveEvidenceTone = liveEvidenceStats.risk > 0
    ? 'risk'
    : liveEvidenceStats.positive > 0
      ? 'positive'
      : 'neutral';

  const liveHeadline = liveEvidenceLoading
    ? '正在把 DAO 财经信息映射到投资对象…'
    : liveEvidenceStats.total === 0
      ? '暂时没有足够的新证据，不强行给出结论'
      : liveSignalTone === 'risk'
        ? `有 ${liveEvidenceStats.risk} 条风险证据需要先核对`
        : liveSignalTone === 'positive'
          ? `有 ${liveEvidenceStats.positive} 条积极证据，但仍需验证`
          : '多空证据暂未形成一致方向';

  const liveAction = liveEvidenceStats.total === 0
    ? '等待新的快讯、文章或研报进入 DAO 财经信息库。'
    : liveSignalTone === 'risk'
      ? '先判断这些证据是否满足论点失效条件，再决定是否调整仓位。'
      : liveSignalTone === 'positive'
        ? '先确认积极变化是否已经兑现到经营数据，不因单条信息追涨。'
        : '保留当前判断，同时跟踪相互矛盾的信息。';

  const dynamicGraph = useMemo(() => {
    if (!snapshot) return { nodes: [] as OntologyNode[], edges: [] as OntologyEdge[] };
    const tonePriority: Record<LiveEvidenceTone, number> = { risk: 0, positive: 1, neutral: 2 };
    const priorityEntries = [
      ...annotatedLiveEvidence.filter(entry => entry.tone === 'risk').slice(0, 2),
      ...annotatedLiveEvidence.filter(entry => entry.tone === 'positive').slice(0, 1),
    ];
    const priorityIds = new Set(priorityEntries.map(entry => entry.item.id));
    const evidenceEntries = [
      ...priorityEntries,
      ...[...annotatedLiveEvidence]
        .filter(entry => !priorityIds.has(entry.item.id))
        .sort((a, b) => tonePriority[a.tone] - tonePriority[b.tone]),
    ].slice(0, 3);
    const ys = evidenceEntries.length === 1 ? [50] : evidenceEntries.length === 2 ? [32, 68] : [22, 50, 78];
    const evidenceNodes: OntologyNode[] = evidenceEntries.map(({ item, tone }, index) => ({
      id: `live-evidence:${item.id}`,
      type: 'Evidence',
      label: liveEvidenceTitle(item),
      canonical_key: item.id,
      market: snapshot.identity.security.market,
      attributes: {
        source: item.source_name || 'DAO 财经',
        known_at: item.created_at,
        credibility: tone === 'neutral' ? 0.62 : 0.76,
        signal: tone,
        url: item.url || `/?article=${encodeURIComponent(item.id)}`,
      },
      position: { x: 8, y: ys[index] },
    }));
    const eventNodes: OntologyNode[] = evidenceEntries.map(({ item, tone }, index) => ({
      id: `live-event:${item.id}`,
      type: 'Event',
      label: `${item.topic || '资讯'} · ${
        tone === 'risk' ? '风险变化' : tone === 'positive' ? '积极变化' : '待确认变化'
      }`,
      canonical_key: `event:${item.id}`,
      market: snapshot.identity.security.market,
      attributes: {
        event_type: item.topic || '资讯事件',
        occurred_at: item.created_at,
        severity: tone,
      },
      position: { x: 30, y: ys[index] },
    }));
    const thesisNode = {
      ...snapshot.decision.thesis,
      position: { x: 53, y: 50 },
    };
    const positionNode = {
      ...snapshot.decision.position,
      position: { x: 74, y: 50 },
    };
    const portfolioNode = snapshot.graph.nodes.find(node => node.type === 'Portfolio');
    const finalPortfolio = portfolioNode
      ? { ...portfolioNode, position: { x: 92, y: 50 } }
      : undefined;
    const evidenceEdges: OntologyEdge[] = evidenceEntries.flatMap(({ item, tone }) => [
      {
        id: `live-rel-evidence:${item.id}`,
        source: `live-evidence:${item.id}`,
        target: `live-event:${item.id}`,
        type: 'EVIDENCES',
        polarity: 0,
        confidence: tone === 'neutral' ? 0.62 : 0.76,
      },
      {
        id: `live-rel-thesis:${item.id}`,
        source: `live-event:${item.id}`,
        target: thesisNode.id,
        type: tone === 'risk' ? 'WEAKENS' : tone === 'positive' ? 'SUPPORTS' : 'ABOUT',
        polarity: tone === 'risk' ? -1 : tone === 'positive' ? 1 : 0,
        confidence: tone === 'neutral' ? 0.55 : 0.72,
      },
    ]);
    const downstreamEdges: OntologyEdge[] = [
      {
        id: 'live-rel-position',
        source: thesisNode.id,
        target: positionNode.id,
        type: 'GOVERNS',
        polarity: 0,
        confidence: 0.9,
      },
      ...(finalPortfolio ? [{
        id: 'live-rel-portfolio',
        source: positionNode.id,
        target: finalPortfolio.id,
        type: 'POSITION_IN',
        polarity: 0 as const,
        confidence: 1,
      }] : []),
    ];
    return {
      nodes: [...evidenceNodes, ...eventNodes, thesisNode, positionNode, ...(finalPortfolio ? [finalPortfolio] : [])],
      edges: [...evidenceEdges, ...downstreamEdges],
    };
  }, [annotatedLiveEvidence, snapshot]);

  const displayedGraph = useMemo(() => {
    if (networkFilter === 'all') return dynamicGraph;
    const nodes = dynamicGraph.nodes.filter(node => (
      !['Evidence', 'Event'].includes(node.type)
      || node.attributes.signal === networkFilter
      || node.attributes.severity === networkFilter
    ));
    const visibleIds = new Set(nodes.map(node => node.id));
    return {
      nodes,
      edges: dynamicGraph.edges.filter(
        edge => visibleIds.has(edge.source) && visibleIds.has(edge.target),
      ),
    };
  }, [dynamicGraph, networkFilter]);

  const graphNodeMap = useMemo(
    () => new Map(displayedGraph.nodes.map(node => [node.id, node])),
    [displayedGraph.nodes],
  );
  const selectedNode = selectedNodeId ? graphNodeMap.get(selectedNodeId)
    || snapshot?.graph.nodes.find(node => node.id === selectedNodeId) : undefined;
  const selectedIncomingEdges = displayedGraph.edges.filter(edge => edge.target === selectedNode?.id);
  const selectedOutgoingEdges = displayedGraph.edges.filter(edge => edge.source === selectedNode?.id);

  const objectCounts = useMemo(() => {
    const counts = Object.fromEntries(OBJECT_TYPES.map(item => [item.type, 0])) as Record<OntologyEntityType, number>;
    (snapshot?.graph.nodes || []).forEach(node => { counts[node.type] += 1; });
    counts.Evidence = Math.max(counts.Evidence, liveEvidenceStats.total);
    counts.Event = Math.max(counts.Event, Math.min(liveEvidenceStats.total, 12));
    return counts;
  }, [liveEvidenceStats.total, snapshot]);

  const selectedTypeDefinition = OBJECT_META[selectedObjectType];
  const selectedTypeObjects = useMemo(() => {
    if (!snapshot) return [] as OntologyNode[];
    if (selectedObjectType === 'Evidence') return dynamicGraph.nodes.filter(node => node.type === 'Evidence');
    if (selectedObjectType === 'Event') return dynamicGraph.nodes.filter(node => node.type === 'Event');
    return snapshot.graph.nodes.filter(node => node.type === selectedObjectType);
  }, [dynamicGraph.nodes, selectedObjectType, snapshot]);

  const focusedNodeIds = useMemo(() => {
    if (!selectedNodeId) return null;
    const focused = new Set<string>([selectedNodeId]);
    displayedGraph.edges.forEach(edge => {
      if (edge.source === selectedNodeId) focused.add(edge.target);
      if (edge.target === selectedNodeId) focused.add(edge.source);
    });
    return focused;
  }, [displayedGraph.edges, selectedNodeId]);

  const renderEdge = (edge: OntologyEdge) => {
    const source = graphNodeMap.get(edge.source);
    const target = graphNodeMap.get(edge.target);
    if (!source || !target) return null;
    const isMuted = focusedNodeIds
      ? !(focusedNodeIds.has(edge.source) && focusedNodeIds.has(edge.target))
      : false;
    const midX = (source.position.x + target.position.x) / 2;
    const controlOffset = Math.max(4, Math.abs(target.position.y - source.position.y) * 0.28);
    const path = `M ${source.position.x} ${source.position.y} C ${midX - controlOffset} ${source.position.y}, ${midX + controlOffset} ${target.position.y}, ${target.position.x} ${target.position.y}`;
    const relationClass = `${edgeClass(edge)}${isMuted ? ' muted' : ''}`;
    return (
      <g key={edge.id} className={`ontology-edge-group ${relationClass}`}>
        <path d={path} className="ontology-edge-glow" />
        <path
          d={path}
          className="ontology-edge-flow"
          markerEnd={`url(#ontology-arrow-${edgeClass(edge)})`}
        />
      </g>
    );
  };

  const renderEvidenceList = () => (
    <section id="ontology-live-evidence" className="ontology-evidence-panel">
      <header className="ontology-section-head">
        <div>
          <span>LIVE OBJECT SET</span>
          <h3>与 {selectedAsset?.label} 关联的证据</h3>
          <p>实时从 DAO 财经信息库匹配，保留来源、时间和原文。</p>
        </div>
        <div className="ontology-evidence-filters" role="group" aria-label="筛选关联证据">
          {([
            ['all', `全部 ${liveEvidenceStats.total}`],
            ['risk', `风险 ${liveEvidenceStats.risk}`],
            ['positive', `积极 ${liveEvidenceStats.positive}`],
            ['neutral', `中性 ${liveEvidenceStats.neutral}`],
          ] as Array<[LiveEvidenceFilter, string]>).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={liveEvidenceFilter === value ? 'active' : ''}
              onClick={() => {
                setLiveEvidenceFilter(value);
                setShowAllEvidence(false);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {liveEvidenceLoading ? (
        <div className="ontology-empty">正在关联快讯、文章和研报…</div>
      ) : liveEvidenceError ? (
        <div className="ontology-empty error">
          <strong>实时信息暂时读取失败</strong>
          <span>{liveEvidenceError}</span>
        </div>
      ) : filteredLiveEvidence.length ? (
        <>
          <div className="ontology-evidence-list">
            {filteredLiveEvidence.map(({ item, tone }) => (
              <article key={item.id} className={tone}>
                <div className="ontology-evidence-meta">
                  <span>{item.topic || '资讯'}</span>
                  <em>{item.source_name || 'DAO 财经'}</em>
                  <small>{formatTimestamp(item.created_at)}</small>
                </div>
                <h4>{liveEvidenceTitle(item)}</h4>
                <p>{liveEvidenceSummary(item)}</p>
                <footer>
                  <span className={`ontology-signal-pill ${tone}`}>
                    {tone === 'risk' ? '削弱论点' : tone === 'positive' ? '支持论点' : '待确认'}
                  </span>
                  <span>对象：Evidence</span>
                  <a
                    href={item.url || `/?article=${encodeURIComponent(item.id)}`}
                    target={item.url ? '_blank' : undefined}
                    rel={item.url ? 'noreferrer' : undefined}
                  >
                    查看原文 <LinkOutlined />
                  </a>
                </footer>
              </article>
            ))}
          </div>
          {matchingLiveEvidence.length > 3 && (
            <button
              type="button"
              className="ontology-more-button"
              onClick={() => setShowAllEvidence(current => !current)}
            >
              {showAllEvidence ? '收起，只看优先证据' : `继续查看 ${matchingLiveEvidence.length - 3} 条`}
            </button>
          )}
        </>
      ) : (
        <div className="ontology-empty">当前筛选下没有证据，切换“全部”或等待新内容。</div>
      )}
    </section>
  );

  const renderDecisionWorkspace = () => (
    <div className="ontology-view-stack">
      <section className={`ontology-decision-hero ${liveSignalTone}`}>
        <div className="ontology-decision-copy">
          <div className="ontology-live-status">
            <i />
            <span>DAO 财经实时映射</span>
            <em>{selectedAsset?.label} · {selectedAsset?.canonical_key}</em>
          </div>
          <small>当前决策结论</small>
          <h2>{liveHeadline}</h2>
          <p>{liveAction}</p>
          <div className="ontology-decision-actions">
            <Button
              type="primary"
              icon={<FileSearchOutlined />}
              href="#ontology-live-evidence"
              onClick={() => {
                setLiveEvidenceFilter(liveEvidenceStats.risk ? 'risk' : 'all');
                setShowAllEvidence(false);
              }}
            >
              核对优先证据
            </Button>
            <Button
              icon={<CheckCircleOutlined />}
              loading={actionLoading}
              disabled={liveEvidenceStats.total === 0}
              onClick={() => void recordAction(
                'request_research',
                `${selectedAsset?.label || '当前标的'}：关联 ${liveEvidenceStats.total} 条信息，风险 ${liveEvidenceStats.risk} 条，积极 ${liveEvidenceStats.positive} 条`,
              )}
            >
              发起补证
            </Button>
          </div>
        </div>
        <div className="ontology-decision-facts">
          <div className="ontology-decision-score">
            <span>{decision?.verdict || '等待判断'}</span>
            <strong>{percentage(decision?.thesis.attributes.confidence)}</strong>
            <small>论点置信度</small>
          </div>
          <dl>
            <div><dt>关联信息</dt><dd>{liveEvidenceStats.total}</dd></div>
            <div className="risk"><dt>风险证据</dt><dd>{liveEvidenceStats.risk}</dd></div>
            <div className="positive"><dt>积极证据</dt><dd>{liveEvidenceStats.positive}</dd></div>
            <div><dt>数据来源</dt><dd>{liveEvidenceStats.sources}</dd></div>
          </dl>
        </div>
      </section>

      <section className="ontology-decision-grid">
        <article className="ontology-thesis-card">
          <header>
            <div>
              <span>THESIS OBJECT</span>
              <h3>{decision?.thesis.label}</h3>
            </div>
            <Tag color={decision?.tone === 'positive' ? 'green' : decision?.tone === 'warning' ? 'orange' : 'blue'}>
              {decision?.verdict}
            </Tag>
          </header>
          <p>{decision?.change_summary}</p>
          <div className="ontology-thesis-rail">
            <div>
              <span>支持路径</span>
              <strong className="positive">{liveEvidenceStats.positive}</strong>
            </div>
            <div>
              <span>反证路径</span>
              <strong className="risk">{liveEvidenceStats.risk}</strong>
            </div>
            <div>
              <span>当前仓位</span>
              <strong>{numberText(decision?.position.attributes.weight_pct, '%')}</strong>
            </div>
            <div>
              <span>风险预算</span>
              <strong>{numberText(decision?.position.attributes.risk_budget_pct, '%')}</strong>
            </div>
          </div>
          <footer>
            <span>失效条件</span>
            <strong>{String(decision?.thesis.attributes.invalidation || '尚未定义')}</strong>
          </footer>
        </article>

        <article className="ontology-next-action">
          <span>ACTION RECOMMENDATION</span>
          <h3>下一步怎么做</h3>
          <p>{decision?.recommended_action}</p>
          <div>
            <SafetyCertificateOutlined />
            <span>{decision?.recommended_reason}</span>
          </div>
          <Button
            type="primary"
            loading={actionLoading}
            onClick={() => void recordAction(
              decision?.recommended_action_type || 'keep_watch',
              decision?.recommended_reason || '按本体建议执行',
            )}
          >
            记录这次决策
          </Button>
          <small>仅写入审计台账，不连接真实券商</small>
        </article>
      </section>

      <button
        type="button"
        className="ontology-path-preview"
        onClick={() => setActiveView('network')}
      >
        <span><DatabaseOutlined /> DAO 证据</span>
        <i>→</i>
        <span><ThunderboltOutlined /> 现实事件</span>
        <i>→</i>
        <span><BulbOutlined /> 投资论点</span>
        <i>→</i>
        <span><AuditOutlined /> 当前持仓</span>
        <i>→</i>
        <span><ApartmentOutlined /> 组合风险</span>
        <em>查看完整关系网络</em>
      </button>

      {renderEvidenceList()}
    </div>
  );

  const renderNetworkWorkspace = () => (
    <div className="ontology-network-layout">
      <section className="ontology-network-panel">
        <header className="ontology-section-head">
          <div>
            <span>ONTOLOGY GRAPH</span>
            <h3>投资影响路径</h3>
            <p>真实证据如何改变论点、持仓和组合；点击对象即可聚焦整条上下游路径。</p>
          </div>
          <div className="ontology-network-legend">
            <span><i className="positive" />支持</span>
            <span><i className="negative" />削弱</span>
            <span><i className="neutral" />结构关系</span>
          </div>
        </header>

        <div className="ontology-network-toolbar">
          <div role="group" aria-label="筛选投资影响路径">
            {([
              ['all', '全部路径', dynamicGraph.nodes.length],
              ['risk', '只看风险', dynamicGraph.nodes.filter(
                node => node.type === 'Evidence' && node.attributes.signal === 'risk',
              ).length],
              ['positive', '只看支持', dynamicGraph.nodes.filter(
                node => node.type === 'Evidence' && node.attributes.signal === 'positive',
              ).length],
            ] as Array<[NetworkFilter, string, number]>).map(([value, label, count]) => (
              <button
                key={value}
                type="button"
                className={networkFilter === value ? 'active' : ''}
                onClick={() => {
                  setNetworkFilter(value);
                  setSelectedNodeId(undefined);
                }}
              >
                {label}<em>{count}</em>
              </button>
            ))}
          </div>
          <span>
            <i className={`signal-${networkFilter}`} />
            {networkFilter === 'risk'
              ? '正在追踪削弱投资论点的路径'
              : networkFilter === 'positive'
                ? '正在追踪支持投资论点的路径'
                : '显示证据到组合的完整传导'}
          </span>
          <button
            type="button"
            onClick={() => {
              setNetworkFilter('all');
              setSelectedNodeId(undefined);
            }}
          >
            重置聚焦
          </button>
        </div>

        <div className={`ontology-network-canvas filter-${networkFilter}`}>
          <div className="ontology-network-lanes" aria-hidden>
            <span style={{ left: '8%' }}><b>01</b> 原始证据</span>
            <span style={{ left: '30%' }}><b>02</b> 现实事件</span>
            <span style={{ left: '53%' }}><b>03</b> 投资论点</span>
            <span style={{ left: '74%' }}><b>04</b> 当前持仓</span>
            <span style={{ left: '92%' }}><b>05</b> 组合影响</span>
          </div>
          <div className="ontology-network-reading-hint">
            <span>信息流向</span>
            <i />
            <em>关系标签同时显示语义与置信度</em>
          </div>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="投资本体关系网络">
            <defs>
              <marker id="ontology-arrow-positive" markerWidth="5" markerHeight="5" refX="4.2" refY="2.5" orient="auto">
                <path d="M0,0 L5,2.5 L0,5 z" />
              </marker>
              <marker id="ontology-arrow-negative" markerWidth="5" markerHeight="5" refX="4.2" refY="2.5" orient="auto">
                <path d="M0,0 L5,2.5 L0,5 z" />
              </marker>
              <marker id="ontology-arrow-neutral" markerWidth="5" markerHeight="5" refX="4.2" refY="2.5" orient="auto">
                <path d="M0,0 L5,2.5 L0,5 z" />
              </marker>
            </defs>
            {displayedGraph.edges.map(renderEdge)}
          </svg>
          {displayedGraph.edges.map(edge => {
            const source = graphNodeMap.get(edge.source);
            const target = graphNodeMap.get(edge.target);
            if (!source || !target) return null;
            const isMuted = focusedNodeIds
              ? !(focusedNodeIds.has(edge.source) && focusedNodeIds.has(edge.target))
              : false;
            return (
              <span
                key={`label:${edge.id}`}
                className={`ontology-network-edge-label ${edgeClass(edge)}${isMuted ? ' muted' : ''}`}
                style={{
                  left: `${(source.position.x + target.position.x) / 2}%`,
                  top: `${(source.position.y + target.position.y) / 2}%`,
                }}
              >
                <strong>{EDGE_LABELS[edge.type] || edge.type}</strong>
                <small>{percentage(edge.confidence)}</small>
              </span>
            );
          })}
          {displayedGraph.nodes.map(node => {
            const meta = OBJECT_META[node.type];
            const selected = node.id === selectedNodeId;
            const muted = focusedNodeIds ? !focusedNodeIds.has(node.id) : false;
            return (
              <button
                key={node.id}
                type="button"
                aria-pressed={selected}
                className={`ontology-network-node ${node.type.toLowerCase()}${selected ? ' selected' : ''}${muted ? ' muted' : ''}`}
                style={{
                  left: `${node.position.x}%`,
                  top: `${node.position.y}%`,
                  '--node-accent': meta.accent,
                } as React.CSSProperties}
                onClick={() => setSelectedNodeId(current => current === node.id ? undefined : node.id)}
              >
                <i className="ontology-network-node-accent" />
                <span className="ontology-network-node-icon">{meta.icon}</span>
                <small>{meta.label}</small>
                <strong>{node.label}</strong>
                <em>{graphNodeMetric(node)}</em>
              </button>
            );
          })}
          <div className="ontology-network-canvas-footer">
            <span><SafetyCertificateOutlined /> 数据来自 DAO 财经，所有推断均保留原文和置信度</span>
            <em>{displayedGraph.nodes.length} 个对象 · {displayedGraph.edges.length} 条关系</em>
          </div>
        </div>
      </section>

      <aside className="ontology-inspector">
        <header>
          <span>OBJECT EXPLORER</span>
          <h3>{selectedNode ? '对象详情' : '选择一个对象'}</h3>
        </header>
        {selectedNode ? (
          <>
            <div className="ontology-inspector-type">
              <span style={{ color: OBJECT_META[selectedNode.type].accent }}>
                {OBJECT_META[selectedNode.type].label}
              </span>
              <em>{selectedNode.market || 'MULTI'}</em>
            </div>
            <div className="ontology-inspector-object">
              <span style={{ color: OBJECT_META[selectedNode.type].accent }}>
                {OBJECT_META[selectedNode.type].icon}
              </span>
              <div>
                <strong>{selectedNode.label}</strong>
                <small>{selectedNode.canonical_key}</small>
              </div>
            </div>
            <p>{OBJECT_META[selectedNode.type].description}</p>
            <div className="ontology-inspector-impact">
              <span>路径作用</span>
              <strong>
                {selectedNode.type === 'Evidence'
                  ? '这是推断起点，先核对来源与原文。'
                  : selectedNode.type === 'Event'
                    ? '它把原始信息转化为对论点的方向影响。'
                    : selectedNode.type === 'Thesis'
                      ? '这是整条路径的决策中枢。'
                      : selectedNode.type === 'Position'
                        ? '论点变化在这里转化为仓位约束。'
                        : '这里汇总单一持仓对整个组合的影响。'}
              </strong>
            </div>
            <dl>
              {Object.entries(selectedNode.attributes).slice(0, 6).map(([key, value]) => (
                <div key={key}>
                  <dt>{ATTRIBUTE_LABELS[key] || key}</dt>
                  <dd>{attributeText(key, value)}</dd>
                </div>
              ))}
            </dl>
            <div className="ontology-inspector-links">
              <span>上游依据 <b>{selectedIncomingEdges.length}</b></span>
              <span>下游影响 <b>{selectedOutgoingEdges.length}</b></span>
            </div>
            {[...selectedIncomingEdges, ...selectedOutgoingEdges].map(edge => {
              const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
              const forward = edge.source === selectedNode.id;
              return (
                <button key={edge.id} type="button" onClick={() => setSelectedNodeId(otherId)}>
                  <span>
                    {forward ? '下游' : '上游'} · {EDGE_LABELS[edge.type] || edge.type}
                    <em>{percentage(edge.confidence)}</em>
                  </span>
                  <strong>{graphNodeMap.get(otherId)?.label}</strong>
                </button>
              );
            })}
            {selectedNode.type === 'Evidence' && selectedNode.attributes.url && (
              <a
                className="ontology-inspector-source"
                href={String(selectedNode.attributes.url)}
                target="_blank"
                rel="noreferrer"
              >
                查看原始证据 <LinkOutlined />
              </a>
            )}
          </>
        ) : (
          <div className="ontology-inspector-empty">
            <NodeIndexOutlined />
            <strong>点击图中的任一对象</strong>
            <span>这里会显示属性、上游证据和下游影响。</span>
          </div>
        )}
      </aside>
    </div>
  );

  const renderObjectsWorkspace = () => (
    <div className="ontology-object-layout">
      <aside className="ontology-type-list">
        <header>
          <span>OBJECT TYPES</span>
          <strong>投资对象模型</strong>
        </header>
        {OBJECT_TYPES.map(item => (
          <button
            key={item.type}
            type="button"
            className={selectedObjectType === item.type ? 'active' : ''}
            onClick={() => setSelectedObjectType(item.type)}
          >
            <i style={{ color: item.accent }}>{item.icon}</i>
            <span>
              <strong>{item.label}</strong>
              <small>{item.type}</small>
            </span>
            <em>{objectCounts[item.type]}</em>
          </button>
        ))}
      </aside>

      <section className="ontology-object-catalog">
        <header className="ontology-section-head">
          <div>
            <span>{selectedTypeDefinition.type.toUpperCase()}</span>
            <h3>{selectedTypeDefinition.label}对象</h3>
            <p>{selectedTypeDefinition.description}</p>
          </div>
          <Tag color="green">Active</Tag>
        </header>
        <div className="ontology-schema-strip">
          <div><span>主键</span><strong>{selectedTypeDefinition.primaryKey}</strong></div>
          <div><span>来源</span><strong>{selectedTypeDefinition.source}</strong></div>
          <div><span>属性</span><strong>{selectedTypeDefinition.properties.length}</strong></div>
          <div><span>对象数</span><strong>{objectCounts[selectedObjectType]}</strong></div>
        </div>
        <div className="ontology-property-list">
          {selectedTypeDefinition.properties.map((property, index) => (
            <span key={property}>
              <i>{index + 1}</i>
              <strong>{property}</strong>
              <small>{index === 0 ? 'required' : 'property'}</small>
            </span>
          ))}
        </div>
        <div className="ontology-object-instances">
          <div className="ontology-object-table-head">
            <span>对象实例</span>
            <small>当前选择范围</small>
          </div>
          {selectedTypeObjects.length ? selectedTypeObjects.map(node => (
            <button
              key={node.id}
              type="button"
              onClick={() => {
                setSelectedNodeId(node.id);
                setActiveView('network');
              }}
            >
              <span style={{ color: selectedTypeDefinition.accent }}>{selectedTypeDefinition.icon}</span>
              <strong>{node.label}</strong>
              <small>{node.canonical_key}</small>
              <em>查看关系 →</em>
            </button>
          )) : (
            <div className="ontology-empty">当前标的没有这个类型的对象实例。</div>
          )}
        </div>
      </section>

      <aside className="ontology-links-catalog">
        <header>
          <span>LINK TYPES</span>
          <strong>9 种关系</strong>
        </header>
        {LINK_TYPES.map(([apiName, label, description]) => (
          <div key={apiName}>
            <span>{label}</span>
            <strong>{apiName}</strong>
            <small>{description}</small>
          </div>
        ))}
      </aside>
    </div>
  );

  const renderActionsWorkspace = () => (
    <div className="ontology-actions-layout">
      <section className="ontology-actions-main">
        <header className="ontology-section-head">
          <div>
            <span>ACTION TYPES</span>
            <h3>把判断变成受控动作</h3>
            <p>动作作用于本体对象，同时执行校验、权限和审计规则。</p>
          </div>
          <Tag color="blue">PAPER ONLY</Tag>
        </header>
        <div className="ontology-action-grid">
          {ACTION_DEFINITIONS.map((action, index) => {
            const recommended = action.type === decision?.recommended_action_type;
            return (
              <article key={action.type} className={recommended ? 'recommended' : ''}>
                <header>
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  {recommended && <em>系统建议</em>}
                </header>
                <h4>{action.label}</h4>
                <p>{action.description}</p>
                <div><SafetyCertificateOutlined /> {action.guardrail}</div>
                <Button
                  type={recommended ? 'primary' : 'default'}
                  loading={actionLoading}
                  onClick={() => void recordAction(
                    action.type,
                    action.type === decision?.recommended_action_type
                      ? decision.recommended_reason
                      : `${selectedAsset?.label || '当前标的'}：人工执行“${action.label}”`,
                  )}
                >
                  执行并记录
                </Button>
              </article>
            );
          })}
        </div>
        <div className="ontology-action-contract">
          <span><LockOutlined /> 权限检查</span>
          <i>→</i>
          <span><ControlOutlined /> 参数校验</span>
          <i>→</i>
          <span><PlayCircleOutlined /> 动作执行</span>
          <i>→</i>
          <span><HistoryOutlined /> 审计落账</span>
        </div>
      </section>

      <aside className="ontology-audit-log">
        <header>
          <span>ACTION LOG</span>
          <strong>决策审计</strong>
          <small>{snapshot?.actions.length || 0} 条记录</small>
        </header>
        {snapshot?.actions.length ? snapshot.actions.map((action: OntologyDemoAction) => (
          <div
            key={action.id}
            className={action.id === actionPulseId ? 'pulse' : ''}
          >
            <i />
            <span>
              <strong>{action.action_label}</strong>
              <p>{action.reason}</p>
              <small>{action.actor} · {formatTimestamp(action.created_at)}</small>
            </span>
          </div>
        )) : (
          <div className="ontology-audit-empty">
            <AuditOutlined />
            <strong>还没有动作记录</strong>
            <span>执行一次动作后，理由、时间和操作者会留在这里。</span>
          </div>
        )}
      </aside>
    </div>
  );

  const renderGovernanceWorkspace = () => (
    <div className="ontology-governance-stack">
      <section className="ontology-lineage-panel">
        <header className="ontology-section-head">
          <div>
            <span>DATA LINEAGE</span>
            <h3>每个结论都能回到原始数据</h3>
            <p>不是让 AI 凭空总结，而是让数据沿受控路径进入对象、逻辑和动作。</p>
          </div>
          <Tag color="green">可追溯</Tag>
        </header>
        <div className="ontology-lineage-flow">
          <article>
            <DatabaseOutlined />
            <span>数据源</span>
            <strong>DAO 财经</strong>
            <small>快讯 · 文章 · 研报</small>
          </article>
          <i>→</i>
          <article>
            <NodeIndexOutlined />
            <span>身份解析</span>
            <strong>Canonical ID</strong>
            <small>名称 · 代码 · 别名</small>
          </article>
          <i>→</i>
          <article>
            <ThunderboltOutlined />
            <span>语义映射</span>
            <strong>对象与关系</strong>
            <small>方向 · 时间 · 置信度</small>
          </article>
          <i>→</i>
          <article>
            <BulbOutlined />
            <span>决策逻辑</span>
            <strong>论点评估</strong>
            <small>支持 · 削弱 · 失效</small>
          </article>
          <i>→</i>
          <article>
            <AuditOutlined />
            <span>受控动作</span>
            <strong>Action Log</strong>
            <small>操作者 · 理由 · 时间</small>
          </article>
        </div>
      </section>

      <section className="ontology-governance-grid">
        <article>
          <header><LockOutlined /><div><span>SECURITY</span><h3>权限边界</h3></div></header>
          <div className="ontology-policy-row allow">
            <span>读取公开资讯对象</span><strong>允许</strong>
          </div>
          <div className="ontology-policy-row allow">
            <span>写入模拟决策记录</span><strong>允许</strong>
          </div>
          <div className="ontology-policy-row deny">
            <span>连接真实券商下单</span><strong>禁用</strong>
          </div>
          <div className="ontology-policy-row">
            <span>敏感对象字段</span><strong>按角色控制</strong>
          </div>
        </article>
        <article>
          <header><HistoryOutlined /><div><span>PROVENANCE</span><h3>来源与时效</h3></div></header>
          <dl>
            <div><dt>当前对象</dt><dd>{selectedAsset?.canonical_key}</dd></div>
            <div><dt>关联来源</dt><dd>{liveEvidenceStats.sources}</dd></div>
            <div><dt>最近刷新</dt><dd>{snapshot ? formatTimestamp(snapshot.generated_at) : '—'}</dd></div>
            <div><dt>证据保留</dt><dd>{liveEvidenceStats.total} 条</dd></div>
          </dl>
        </article>
        <article>
          <header><SafetyCertificateOutlined /><div><span>GUARDRAILS</span><h3>决策护栏</h3></div></header>
          {(snapshot?.guardrails || []).map(item => (
            <div className="ontology-guardrail-row" key={item}>
              <CheckCircleOutlined />
              <span>{item}</span>
            </div>
          ))}
          <div className="ontology-guardrail-row">
            <CheckCircleOutlined />
            <span>论点必须定义可验证的失效条件</span>
          </div>
        </article>
      </section>

      <section className="ontology-identity-panel">
        <div>
          <span>IDENTITY RESOLUTION</span>
          <h3>{snapshot?.identity.issuer.label}</h3>
          <p>不同供应商代码和自然语言名称，统一到同一个证券对象。</p>
        </div>
        <div className="ontology-aliases">
          {snapshot?.identity.aliases.map(alias => (
            <span key={`${alias.scheme}:${alias.alias}`}>
              <small>{alias.scheme}</small>
              <strong>{alias.alias}</strong>
              <em>{alias.market}</em>
            </span>
          ))}
          <i>→</i>
          <span className="canonical">
            <small>canonical</small>
            <strong>{snapshot?.identity.security.id}</strong>
            <em>唯一对象</em>
          </span>
        </div>
      </section>
    </div>
  );

  return (
    <CenterShell
      eyebrow="DAO 财经 · 投资语义层"
      title="投资决策本体"
      subtitle="把资讯、公司、论点、持仓和动作组织成同一套可追溯的决策系统"
      icon={<NodeIndexOutlined />}
      className="ontology-center ontology-v3"
      error={error}
      loading={loading}
      loadingText="正在构建投资对象与关系…"
      actions={snapshot && (
        <>
          <Select
            className="ontology-asset-select"
            value={selectedSecurityId}
            options={snapshot.assets.map(asset => ({
              value: asset.security_id,
              label: `${asset.label} · ${asset.canonical_key}`,
            }))}
            onChange={value => void load(value)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load(selectedSecurityId)}>
            刷新
          </Button>
        </>
      )}
    >
      {contextHolder}
      {snapshot && decision && (
        <div className="ontology-workspace">
          <section className="ontology-system-bar">
            <div>
              <i />
              <span>Ontology online</span>
              <em>v1.0 · 投资决策域</em>
            </div>
            <dl>
              <div><dt>对象类型</dt><dd>{OBJECT_TYPES.length}</dd></div>
              <div><dt>关系类型</dt><dd>{LINK_TYPES.length}</dd></div>
              <div><dt>动作类型</dt><dd>{ACTION_DEFINITIONS.length}</dd></div>
              <div><dt>实时证据</dt><dd>{liveEvidenceStats.total}</dd></div>
            </dl>
            <span><ClockCircleOutlined /> {formatTimestamp(snapshot.generated_at)}</span>
          </section>

          <nav className="ontology-workspace-nav" aria-label="本体工作区">
            {NAV_ITEMS.map(item => (
              <button
                key={item.id}
                type="button"
                className={activeView === item.id ? 'active' : ''}
                onClick={() => {
                  setActiveView(item.id);
                  if (item.id === 'network') {
                    setNetworkFilter('all');
                    setSelectedNodeId(undefined);
                  }
                }}
              >
                <i>{item.icon}</i>
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.helper}</small>
                </span>
              </button>
            ))}
          </nav>

          <main className={`ontology-workspace-content view-${activeView}`}>
            {activeView === 'decision' && renderDecisionWorkspace()}
            {activeView === 'network' && renderNetworkWorkspace()}
            {activeView === 'objects' && renderObjectsWorkspace()}
            {activeView === 'actions' && renderActionsWorkspace()}
            {activeView === 'governance' && renderGovernanceWorkspace()}
          </main>
        </div>
      )}
    </CenterShell>
  );
};

export default InvestmentOntologyCenter;
