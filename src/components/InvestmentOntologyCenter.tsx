import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApartmentOutlined,
  AuditOutlined,
  BankOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FundProjectionScreenOutlined,
  LinkOutlined,
  NodeIndexOutlined,
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

const NODE_META: Record<OntologyEntityType, {
  label: string;
  className: string;
  icon: React.ReactNode;
}> = {
  Evidence: { label: '证据', className: 'evidence', icon: <DatabaseOutlined /> },
  Event: { label: '事件', className: 'event', icon: <ThunderboltOutlined /> },
  Thesis: { label: '投资论点', className: 'thesis', icon: <BulbOutlined /> },
  Security: { label: '证券', className: 'security', icon: <FundProjectionScreenOutlined /> },
  Issuer: { label: '公司', className: 'issuer', icon: <BankOutlined /> },
  Position: { label: '持仓', className: 'position', icon: <AuditOutlined /> },
  Portfolio: { label: '组合', className: 'portfolio', icon: <ApartmentOutlined /> },
};

const EDGE_LABELS: Record<string, string> = {
  EVIDENCES: '证明',
  SUPPORTS: '支持',
  WEAKENS: '削弱',
  CONTRADICTS: '反驳',
  ABOUT: '关于',
  REPRESENTS: '对应',
  GOVERNS: '约束',
  HOLDS: '持有',
  POSITION_IN: '属于',
};

const NODE_EXPLANATIONS: Record<OntologyEntityType, string> = {
  Evidence: '这是结论的原始依据。先看来源和可信度，再判断后面的投资逻辑是否站得住。',
  Event: '这是正在发生的变化。它会增强或削弱投资逻辑，并最终传导到你的持仓风险。',
  Thesis: '这是持有这只股票的核心理由。新证据都在回答：这个理由变强了，还是变弱了？',
  Security: '这是统一识别后的股票对象。不同代码和数据源的信息，会在这里汇总到同一个标的。',
  Issuer: '这是股票背后的公司主体，用来把公司基本面与具体证券准确关联起来。',
  Position: '这是你的真实持仓。系统会在这里比较投资逻辑、当前仓位和预设风险上限。',
  Portfolio: '这是对整个组合的影响，用来判断单只股票的变化是否需要转化为整体行动。',
};

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
  confidence: '逻辑可信度',
  invalidation: '失效条件',
  weight_pct: '当前仓位',
  risk_budget_pct: '仓位上限',
  pnl_pct: '浮动盈亏',
  name: '名称',
};

type LiveEvidenceTone = 'positive' | 'risk' | 'neutral';
type LiveEvidenceFilter = 'all' | LiveEvidenceTone;

const POSITIVE_TERMS = [
  '增长', '回购', '增持', '上调', '突破', '中标', '改善', '看好', '机会',
  '盈利企稳', '超预期', '创新高', '政策支持', '成本回落', '份额提升',
];

const RISK_TERMS = [
  '风险', '承压', '减持', '下调', '处罚', '诉讼', '亏损', '低于预期',
  '下滑', '疲弱', '警示', '违约', '监管', '不确定', '尚需等待', '未临',
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
  return value
    .replace(/\*+/g, '')
    .replace(/#+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function liveEvidenceTitle(item: RealtimeMessageRecord): string {
  const title = cleanEvidenceText(item.title);
  if (title && !/^【?(研报)?快讯】?$/.test(title)) return title;
  const contentLine = item.content
    .split(/\n+/)
    .map(cleanEvidenceText)
    .find(line => line.length > 8);
  return contentLine || title || 'DAO财经相关信息';
}

function liveEvidenceSummary(item: RealtimeMessageRecord): string {
  const title = liveEvidenceTitle(item);
  const content = cleanEvidenceText(item.content);
  const withoutTitle = content.startsWith(title) ? content.slice(title.length).trim() : content;
  const summary = withoutTitle || content;
  return summary.length > 150 ? `${summary.slice(0, 150)}…` : summary;
}

function percentage(value: unknown): string {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? `${Math.round(numberValue * 100)}%` : '—';
}

function attributeText(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (key === 'confidence' || key === 'credibility') return percentage(value);
  if (key.endsWith('_pct')) return numberText(value, '%');
  return String(value);
}

function numberText(value: unknown, suffix = ''): string {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return '—';
  return `${numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}${suffix}`;
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

const InvestmentOntologyCenter: React.FC = () => {
  const [snapshot, setSnapshot] = useState<OntologyDemoSnapshot | null>(null);
  const [selectedSecurityId, setSelectedSecurityId] = useState<string>();
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [liveEvidence, setLiveEvidence] = useState<RealtimeMessageRecord[]>([]);
  const [liveEvidenceLoading, setLiveEvidenceLoading] = useState(false);
  const [liveEvidenceError, setLiveEvidenceError] = useState<string | null>(null);
  const [liveEvidenceFilter, setLiveEvidenceFilter] = useState<LiveEvidenceFilter>('all');
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
    setLiveEvidenceFilter('all');
    try {
      const result = await fetchOntologyDemo(securityId);
      if (sequence !== loadSequence.current) return;
      setSnapshot(result);
      setSelectedSecurityId(result.selected_security_id);
      setSelectedNodeId(undefined);
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
        }
      } catch (liveError) {
        if (sequence === loadSequence.current) {
          setLiveEvidence([]);
          setLiveEvidenceError(liveError instanceof Error ? liveError.message : 'DAO财经信息读取失败');
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

  const nodeMap = useMemo(() => new Map(
    (snapshot?.graph.nodes || []).map(node => [node.id, node])
  ), [snapshot]);

  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) : undefined;

  const selectedIncomingEdges = useMemo(
    () => snapshot && selectedNodeId
      ? snapshot.graph.edges.filter(edge => edge.target === selectedNodeId)
      : [],
    [selectedNodeId, snapshot],
  );

  const selectedOutgoingEdges = useMemo(
    () => snapshot && selectedNodeId
      ? snapshot.graph.edges.filter(edge => edge.source === selectedNodeId)
      : [],
    [selectedNodeId, snapshot],
  );

  const focusedNodeIds = useMemo(() => {
    if (!snapshot || !selectedNodeId) return null;
    const incoming = new Map<string, OntologyEdge[]>();
    const outgoing = new Map<string, OntologyEdge[]>();
    snapshot.graph.edges.forEach(edge => {
      incoming.set(edge.target, [...(incoming.get(edge.target) || []), edge]);
      outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge]);
    });
    const focused = new Set<string>([selectedNodeId]);
    const walk = (
      edgeMap: Map<string, OntologyEdge[]>,
      nodeId: string,
      nextNode: (edge: OntologyEdge) => string,
    ): void => {
      (edgeMap.get(nodeId) || []).forEach(edge => {
        const nextId = nextNode(edge);
        if (focused.has(nextId)) return;
        focused.add(nextId);
        walk(edgeMap, nextId, nextNode);
      });
    };
    walk(incoming, selectedNodeId, edge => edge.source);
    walk(outgoing, selectedNodeId, edge => edge.target);
    return focused;
  }, [selectedNodeId, snapshot]);

  const recordAction = useCallback(async (
    actionType: string,
    reason: string,
  ) => {
    if (!snapshot) return;
    setActionLoading(true);
    try {
      const created = await recordOntologyDemoAction({
        security_id: snapshot.selected_security_id,
        action_type: actionType,
        reason,
      });
      setActionPulseId(created.id);
      setSnapshot(prev => prev ? { ...prev, actions: [created, ...prev.actions] } : prev);
      apiMessage.success(`已记录：${created.action_label}（仅演示审计，不产生真实交易）`);
    } catch (actionError) {
      apiMessage.error(actionError instanceof Error ? actionError.message : '动作记录失败');
    } finally {
      setActionLoading(false);
    }
  }, [apiMessage, snapshot]);

  const renderEdge = (edge: OntologyEdge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!source || !target) return null;
    const bendX = (source.position.x + target.position.x) / 2;
    const isMuted = focusedNodeIds
      ? !(focusedNodeIds.has(edge.source) && focusedNodeIds.has(edge.target))
      : false;
    return (
      <path
        key={edge.id}
        className={`ontology-edge-line ${edgeClass(edge)}${isMuted ? ' muted' : ''}`}
        d={[
          `M ${source.position.x} ${source.position.y}`,
          `C ${bendX} ${source.position.y},`,
          `${bendX} ${target.position.y},`,
          `${target.position.x} ${target.position.y}`,
        ].join(' ')}
        style={{ opacity: 0.28 + edge.confidence * 0.5 }}
        markerEnd={`url(#arrow-${edgeClass(edge)})`}
      />
    );
  };

  const renderEdgeLabel = (edge: OntologyEdge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!source || !target || edge.polarity === 0) return null;
    const x = (source.position.x + target.position.x) / 2;
    const y = (source.position.y + target.position.y) / 2;
    const isMuted = focusedNodeIds
      ? !(focusedNodeIds.has(edge.source) && focusedNodeIds.has(edge.target))
      : false;
    return (
      <Tooltip key={`label-${edge.id}`} title={`关系置信度 ${percentage(edge.confidence)}`}>
        <span
          className={`ontology-edge-label ${edgeClass(edge)}${isMuted ? ' muted' : ''}`}
          style={{ left: `${x}%`, top: `${y}%` }}
        >
          {EDGE_LABELS[edge.type] || edge.type}
        </span>
      </Tooltip>
    );
  };

  const actions = snapshot?.actions || [];
  const decision = snapshot?.decision;
  const thesisAttrs = decision?.thesis.attributes || {};
  const annotatedLiveEvidence = useMemo(
    () => liveEvidence.map(item => ({ item, tone: liveEvidenceTone(item) })),
    [liveEvidence],
  );
  const liveEvidenceStats = useMemo(() => {
    const positive = annotatedLiveEvidence.filter(entry => entry.tone === 'positive').length;
    const risk = annotatedLiveEvidence.filter(entry => entry.tone === 'risk').length;
    const neutral = annotatedLiveEvidence.length - positive - risk;
    const sources = new Set(
      annotatedLiveEvidence.map(entry => entry.item.source_name || 'DAO财经').filter(Boolean),
    ).size;
    return { positive, risk, neutral, sources, total: annotatedLiveEvidence.length };
  }, [annotatedLiveEvidence]);
  const filteredLiveEvidence = useMemo(
    () => annotatedLiveEvidence.filter(
      entry => liveEvidenceFilter === 'all' || entry.tone === liveEvidenceFilter,
    ).slice(0, 8),
    [annotatedLiveEvidence, liveEvidenceFilter],
  );
  const liveSignalTone: LiveEvidenceTone = liveEvidenceStats.risk > 0
    ? 'risk'
    : liveEvidenceStats.positive > 0
      ? 'positive'
      : 'neutral';
  const liveHeadline = liveEvidenceLoading
    ? '正在读取 DAO 财经的相关信息…'
    : liveEvidenceStats.total === 0
      ? '暂时没有足够的新信息，不强行给结论'
      : liveSignalTone === 'risk'
        ? `发现 ${liveEvidenceStats.risk} 条风险线索，先核对再决定是否行动`
        : liveSignalTone === 'positive'
          ? `发现 ${liveEvidenceStats.positive} 条积极线索，但仍要逐条验证来源`
          : '多空信息没有形成一致方向，保持观察';
  const liveAction = liveEvidenceStats.total === 0
    ? '等待新的快讯、文章或研报进入 DAO 财经信息库。'
    : liveSignalTone === 'risk'
      ? `先看下面 ${liveEvidenceStats.risk} 条风险线索，确认是否真的破坏原有投资逻辑。`
      : liveSignalTone === 'positive'
        ? `先看下面 ${liveEvidenceStats.positive} 条积极线索，确认它们是否已经兑现到经营数据。`
        : '把相互矛盾的证据放在一起看，不因为单条新闻追涨杀跌。';
  const selectedAsset = snapshot?.assets.find(asset => asset.security_id === selectedSecurityId);

  return (
    <CenterShell
      eyebrow="DAO财经 · 决策关联"
      title="这只股票最近发生了什么？"
      subtitle="把 DAO 财经已有的快讯、文章和研报自动归到同一只股票，再判断哪些值得你先看"
      icon={<NodeIndexOutlined />}
      className="ontology-center"
      error={error}
      loading={loading}
      loadingText="正在构建投资影响图…"
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
            刷新快照
          </Button>
        </>
      )}
    >
      {contextHolder}
      {snapshot && decision && (
        <div className="ontology-layout">
          <section className={`ontology-live-brief ${liveSignalTone}`}>
            <div className="ontology-live-main">
              <div className="ontology-live-status">
                <Tag color={liveSignalTone === 'risk' ? 'red' : liveSignalTone === 'positive' ? 'green' : 'blue'}>
                  {liveEvidenceLoading ? '正在关联' : 'DAO财经实时数据'}
                </Tag>
                <span>{selectedAsset?.label} · {selectedAsset?.canonical_key}</span>
              </div>
              <span className="ontology-live-label">基于网站当前已有信息</span>
              <h2>{liveHeadline}</h2>
              <p className="ontology-live-action">{liveAction}</p>
              <div className="ontology-live-proof">
                <span>不是演示数据</span>
                <strong>
                  已从 DAO 财经信息流命中 {liveEvidenceStats.total} 条相关内容，
                  包括快讯、文章与研报摘要；每条都能回到原始内容。
                </strong>
              </div>
              <div className="ontology-action-row">
                <Button
                  type="primary"
                  icon={<FileSearchOutlined />}
                  href="#ontology-live-evidence"
                  onClick={() => setLiveEvidenceFilter(liveEvidenceStats.risk ? 'risk' : 'all')}
                >
                  {liveEvidenceStats.risk ? '先看风险证据' : '查看关联证据'}
                </Button>
                <Button
                  icon={<CheckCircleOutlined />}
                  loading={actionLoading}
                  disabled={liveEvidenceStats.total === 0}
                  onClick={() => void recordAction(
                    'request_research',
                    `${selectedAsset?.label || '当前标的'}：DAO财经命中 ${liveEvidenceStats.total} 条，风险 ${liveEvidenceStats.risk} 条，积极 ${liveEvidenceStats.positive} 条`,
                  )}
                >
                  加入补证清单
                </Button>
              </div>
            </div>

            <aside className="ontology-live-summary" aria-label="DAO财经关联结果">
              <div className="ontology-live-summary-head">
                <span>信息关联结果</span>
                <small>{liveEvidenceLoading ? '读取中' : '来自现有内容库'}</small>
              </div>
              <dl>
                <div className="risk">
                  <dt>风险线索</dt>
                  <dd>{liveEvidenceStats.risk}</dd>
                </div>
                <div className="positive">
                  <dt>积极线索</dt>
                  <dd>{liveEvidenceStats.positive}</dd>
                </div>
                <div>
                  <dt>中性信息</dt>
                  <dd>{liveEvidenceStats.neutral}</dd>
                </div>
                <div>
                  <dt>来源数量</dt>
                  <dd>{liveEvidenceStats.sources}</dd>
                </div>
              </dl>
              <div className="ontology-live-latest">
                <span>最近更新</span>
                {annotatedLiveEvidence.slice(0, 2).map(({ item, tone }) => (
                  <a
                    key={item.id}
                    className={tone}
                    href={item.url || `/?article=${encodeURIComponent(item.id)}`}
                    target={item.url ? '_blank' : undefined}
                    rel={item.url ? 'noreferrer' : undefined}
                  >
                    <i />
                    <strong>{liveEvidenceTitle(item)}</strong>
                    <small>{formatTimestamp(item.created_at)}</small>
                  </a>
                ))}
                {!liveEvidenceLoading && !annotatedLiveEvidence.length && (
                  <em>当前标的暂无可关联信息</em>
                )}
              </div>
              <div className="ontology-brief-guardrail">
                <SafetyCertificateOutlined />
                <span>系统只做信息关联和风险提示，不自动下单</span>
              </div>
            </aside>
          </section>

          <section id="ontology-live-evidence" className="ontology-live-evidence">
            <header className="ontology-live-evidence-head">
              <div>
                <span>真正用到的 DAO 财经信息</span>
                <h3>为什么今天先看这些？</h3>
                <p>系统用股票名称和统一代码检索现有信息库，再把偏风险、偏积极和中性内容分开。</p>
              </div>
              <div className="ontology-live-filters" role="group" aria-label="筛选关联证据">
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
                    onClick={() => setLiveEvidenceFilter(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </header>

            {liveEvidenceLoading ? (
              <div className="ontology-live-empty">正在从 DAO 财经信息库关联快讯、文章和研报…</div>
            ) : liveEvidenceError ? (
              <div className="ontology-live-empty error">
                <strong>实时信息暂时读取失败</strong>
                <span>{liveEvidenceError}</span>
              </div>
            ) : filteredLiveEvidence.length ? (
              <div className="ontology-live-list">
                {filteredLiveEvidence.map(({ item, tone }) => (
                  <article key={item.id} className={tone}>
                    <i className="ontology-live-tone" />
                    <div className="ontology-live-item-copy">
                      <div>
                        <span>{item.topic || '资讯'}</span>
                        <em>{item.source_name || 'DAO财经'}</em>
                        <small>{formatTimestamp(item.created_at)}</small>
                      </div>
                      <h4>{liveEvidenceTitle(item)}</h4>
                      <p>{liveEvidenceSummary(item)}</p>
                      <footer>
                        <span>
                          关联原因：命中 {selectedAsset?.label} / {selectedAsset?.canonical_key.split('.')[0]}
                        </span>
                        <a
                          href={item.url || `/?article=${encodeURIComponent(item.id)}`}
                          target={item.url ? '_blank' : undefined}
                          rel={item.url ? 'noreferrer' : undefined}
                        >
                          查看原始内容 <LinkOutlined />
                        </a>
                      </footer>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="ontology-live-empty">
                当前筛选下没有信息。可以切换“全部”，或刷新等待新内容进入信息库。
              </div>
            )}
          </section>

          <section className="ontology-simple-chain" aria-label="本次判断的四步推导">
            <header className="ontology-section-heading">
              <span>本体真正做了什么</span>
              <h3>一条网站信息如何变成可执行的核对任务？</h3>
            </header>
            <div className="ontology-chain-grid">
              <article>
                <b>1</b>
                <span>找到真实信息</span>
                <strong>DAO 财经命中 {liveEvidenceStats.total} 条与 {selectedAsset?.label} 相关的内容</strong>
              </article>
              <article>
                <b>2</b>
                <span>先分方向</span>
                <strong>{liveEvidenceStats.risk} 条偏风险，{liveEvidenceStats.positive} 条偏积极</strong>
                <small>这是信息筛选，不是买卖预测</small>
              </article>
              <article>
                <b>3</b>
                <span>对照投资逻辑</span>
                <strong>{decision.thesis.label}</strong>
                <small>试点规则：失效条件为“{String(thesisAttrs.invalidation || '未设置')}”</small>
              </article>
              <article>
                <b>4</b>
                <span>变成下一步</span>
                <strong>{liveAction}</strong>
              </article>
            </div>
          </section>

          <details className="ontology-expert-details">
            <summary>
              <span>
                <strong>查看本体关系试验区</strong>
                <small>上面是真实 DAO 财经信息；下面仍是用于验证关系模型的示例规则</small>
              </span>
              <em>试点 · {snapshot.graph.nodes.length} 个对象 · {snapshot.graph.edges.length} 条关系</em>
            </summary>
            <section className="ontology-main-grid">
            <article className="ontology-panel ontology-graph-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">影响路径</span>
                  <h3>完整证据关系</h3>
                </div>
                <div className="ontology-legend">
                  <span><i className="positive" />支持</span>
                  <span><i className="negative" />反证</span>
                  <span><i className="neutral" />结构关系</span>
                </div>
              </header>

              <div className="ontology-sandbox-note">
                <SafetyCertificateOutlined />
                这里用示例投资逻辑验证关系推导，不作为实时投资建议；真实 DAO 财经信息在上方。
              </div>

              <div className="ontology-graph-guidance">
                <span>从左向右看：原始信息如何一步步影响到你的组合</span>
                <small>点击节点，立即查看它为什么重要</small>
              </div>

              <div
                className={`ontology-node-inspector${selectedNode ? ' has-selection' : ''}`}
                aria-live="polite"
              >
                {selectedNode ? (
                  <>
                    <div className="ontology-inspector-heading">
                      <span className={`ontology-node-inspector-icon ${NODE_META[selectedNode.type].className}`}>
                        {NODE_META[selectedNode.type].icon}
                      </span>
                      <div>
                        <small>当前查看 · {NODE_META[selectedNode.type].label}</small>
                        <strong>{selectedNode.label}</strong>
                      </div>
                    </div>
                    <div className="ontology-inspector-meaning">
                      <b>为什么重要</b>
                      <span>{NODE_EXPLANATIONS[selectedNode.type]}</span>
                    </div>
                    <Button
                      className="ontology-inspector-clear"
                      size="small"
                      onClick={() => setSelectedNodeId(undefined)}
                    >
                      取消聚焦
                    </Button>
                    <div className="ontology-node-relations">
                      <div>
                        <b>上游依据</b>
                        {selectedIncomingEdges.length ? selectedIncomingEdges.slice(0, 3).map(edge => (
                          <span key={edge.id}>
                            {nodeMap.get(edge.source)?.label || '未知对象'}
                            <i>{EDGE_LABELS[edge.type] || edge.type}</i>
                          </span>
                        )) : <em>这是影响路径的起点</em>}
                      </div>
                      <div>
                        <b>下游影响</b>
                        {selectedOutgoingEdges.length ? selectedOutgoingEdges.slice(0, 3).map(edge => (
                          <span key={edge.id}>
                            <i>{EDGE_LABELS[edge.type] || edge.type}</i>
                            {nodeMap.get(edge.target)?.label || '未知对象'}
                          </span>
                        )) : <em>这是影响路径的终点</em>}
                      </div>
                    </div>
                    <div className="ontology-node-properties">
                      {Object.entries(selectedNode.attributes).slice(0, 5).map(([key, value]) => (
                        <span key={key}>
                          <b>{ATTRIBUTE_LABELS[key] || key}</b>
                          {attributeText(key, value)}
                        </span>
                      ))}
                    </div>
                  </>
                ) : (
                  <span className="ontology-inspector-empty">
                    <NodeIndexOutlined />
                    <span>
                      <strong>点一个节点，不只看高亮</strong>
                      <small>这里会解释它为什么重要、从哪里来、接下来影响什么</small>
                    </span>
                  </span>
                )}
              </div>

              <div className="ontology-graph-scroll">
                <div className="ontology-graph">
                  <div className="ontology-graph-lanes" aria-hidden>
                    <span style={{ left: '8%' }}>原始证据</span>
                    <span style={{ left: '31%' }}>发生了什么</span>
                    <span style={{ left: '54%' }}>投资逻辑</span>
                    <span style={{ left: '73%' }}>我的资产</span>
                    <span style={{ left: '91%' }}>组合影响</span>
                  </div>
                  <svg
                    className="ontology-edge-layer"
                    viewBox="0 0 100 100"
                    preserveAspectRatio="none"
                    role="img"
                    aria-label="证据、事件、投资逻辑、持仓和组合之间的推导关系"
                  >
                    <defs>
                      <marker id="arrow-positive" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                        <path d="M0,0 L5,2.5 L0,5 z" className="ontology-arrow-positive" />
                      </marker>
                      <marker id="arrow-negative" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                        <path d="M0,0 L5,2.5 L0,5 z" className="ontology-arrow-negative" />
                      </marker>
                      <marker id="arrow-neutral" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                        <path d="M0,0 L5,2.5 L0,5 z" className="ontology-arrow-neutral" />
                      </marker>
                    </defs>
                    {snapshot.graph.edges.map(renderEdge)}
                  </svg>
                  {snapshot.graph.edges.map(renderEdgeLabel)}
                  {snapshot.graph.nodes.map(node => {
                    const meta = NODE_META[node.type];
                    const isSelected = node.id === selectedNodeId;
                    const isMuted = focusedNodeIds ? !focusedNodeIds.has(node.id) : false;
                    const signalEdge = snapshot.graph.edges.find(
                      edge => edge.source === node.id && edge.polarity !== 0,
                    );
                    const signalClass = signalEdge
                      ? signalEdge.polarity > 0 ? ' signal-positive' : ' signal-negative'
                      : '';
                    return (
                      <button
                        key={node.id}
                        type="button"
                        aria-pressed={isSelected}
                        className={[
                          'ontology-node',
                          meta.className,
                          isSelected ? 'selected' : '',
                          isMuted ? 'muted' : '',
                          signalClass,
                        ].filter(Boolean).join(' ')}
                        style={{ left: `${node.position.x}%`, top: `${node.position.y}%` }}
                        onClick={() => setSelectedNodeId(current => current === node.id ? undefined : node.id)}
                      >
                        <span className="ontology-node-icon">{meta.icon}</span>
                        <span className="ontology-node-copy">
                          <small>{meta.label}</small>
                          <strong>{node.label}</strong>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </article>

            </section>
          </details>

          <section className="ontology-bottom-grid">
            <article className="ontology-panel ontology-identity-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">系统为什么不会认错股票？</span>
                  <h3>不同代码，自动认成同一家公司</h3>
                </div>
                <LinkOutlined />
              </header>
              <p className="ontology-identity-explainer">
                财报里的 600519、行情里的 SH600519 和“贵州茅台”，都会自动合并到同一个对象。
              </p>
              <div className="ontology-identity-target">
                <span>{snapshot.identity.security.label}</span>
                <strong>{snapshot.identity.security.id}</strong>
              </div>
              <div className="ontology-alias-grid">
                {snapshot.identity.aliases.map(alias => (
                  <div key={`${alias.scheme}:${alias.alias}`}>
                    <span>{alias.scheme}</span>
                    <strong>{alias.alias}</strong>
                    <small>{alias.market}</small>
                  </div>
                ))}
              </div>
            </article>

            <article className="ontology-panel ontology-audit-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">以后可以回来复盘</span>
                  <h3>我的决策记录</h3>
                </div>
                <AuditOutlined />
              </header>
              {actions.length ? (
                <div className="ontology-audit-list">
                  {actions.map((action: OntologyDemoAction) => (
                    <div
                      key={action.id}
                      className={`ontology-audit-item${action.id === actionPulseId ? ' pulse' : ''}`}
                    >
                      <span className="ontology-audit-dot" />
                      <div>
                        <strong>{action.action_label}</strong>
                        <p>{action.reason}</p>
                        <small>{action.actor} · {formatTimestamp(action.created_at)} · PAPER ONLY</small>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="ontology-audit-empty">
                  <AuditOutlined />
                  <strong>还没有决策记录</strong>
                  <span>记录一次决策后，时间、理由和执行人会留在这里。</span>
                </div>
              )}
            </article>
          </section>
        </div>
      )}
    </CenterShell>
  );
};

export default InvestmentOntologyCenter;
