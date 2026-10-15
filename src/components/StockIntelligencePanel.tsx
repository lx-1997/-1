import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { apiGet } from '../services/apiClient';
import { getAuthSnapshot, isAuthRevisionCurrent, subscribeAuth } from '../state/authSession';
import type { IntelligenceDimension, IntelligenceEvidence, IntelligenceScenario, IntelligenceSource, StockIntelligence } from './stock-intelligence/types';
import HistoryPriceChart from './stock-intelligence/HistoryPriceChart';
import IntelligenceInspector from './stock-intelligence/IntelligenceInspector';
import MarketEvidenceCharts from './stock-intelligence/MarketEvidenceCharts';
import ResearchIcon, { type ResearchIconName } from './stock-intelligence/ResearchIcon';
import ScenarioReasoning from './stock-intelligence/ScenarioReasoning';
import PlatformResearchPanel, { PLATFORM_BODY_LABELS, PLATFORM_ROLE_LABELS, isPlatformDirectional, platformCoverageLabel } from './stock-intelligence/PlatformResearchPanel';
import './stock-intelligence/stock-intelligence.css';

interface Props {
  symbol: string;
  name: string;
  loggedIn: boolean;
  username?: string | null;
  onRequireLogin: (why: string) => void;
}

type SectionKey = 'overview' | 'research' | 'evidence';
type SourceSelect = (id: string) => void;
const SECTIONS: Array<{ key: SectionKey; label: string; icon: ResearchIconName }> = [
  { key: 'overview', label: '研究概览', icon: 'overview' }, { key: 'research', label: '多维分析', icon: 'research' }, { key: 'evidence', label: '资讯与来源', icon: 'sources' },
];
const SIGNALS: Record<string, string> = { bullish: '偏多', bearish: '偏空', neutral: '中性', insufficient: '待补数据' };
const DIMENSION_LABELS: Record<string, string> = {
  business: '商业模式', fundamentals: '财务质量', earnings: '盈利预期', technical: '技术趋势', capital: '资金面', news: '消息面',
};
const SCENARIOS = [
  { key: 'optimistic', label: '乐观', mark: '上行条件', icon: 'bullish' },
  { key: 'base', label: '中性', mark: '基准假设', icon: 'neutral' },
  { key: 'pessimistic', label: '悲观', mark: '下行风险', icon: 'bearish' },
] as const;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const priceText = (value: unknown) => finite(value) && value > 0 ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
const percentText = (value: unknown) => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}%` : '—';
const metricText = (value: string | number | null) => value == null || (typeof value === 'number' && !finite(value)) ? '—'
  : typeof value === 'number' ? value.toLocaleString('zh-CN', { maximumFractionDigits: value !== 0 && Math.abs(value) < 0.01 ? 4 : 2 }) : value;
const directionClass = (value: unknown) => !finite(value) || value === 0 ? 'si-flat' : value < 0 ? 'si-negative' : 'si-positive';

/** Sources are data: never execute an upstream URL or accept protocol-relative links. */
export function intelligenceLink(value?: string | null): string | undefined {
  if (!value) return undefined;
  return /^https?:\/\//i.test(value) || /^\/(?!\/)/.test(value) ? value : undefined;
}
function dateText(value?: string | null): string {
  if (!value) return '源时间未提供';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(value)) return value.replace('T', ' ').slice(0, 19);
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? `${date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })} 北京时间` : value;
}
function sourceDay(value?: string | null): string {
  if (!value) return '未提供';
  const date = new Date(value);
  return /(Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(date.getTime())
    ? date.toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' }) : value.slice(0, 10);
}
function DimensionIcon({ dimension }: { dimension: string }) {
  const names: Record<string, ResearchIconName> = { business: 'business', fundamentals: 'fundamentals', earnings: 'earnings', technical: 'technical', capital: 'capital', news: 'news' };
  return <ResearchIcon name={names[dimension] || 'document'} />;
}
const sourceStatusIcon = (status: string): ResearchIconName => status === 'stale' ? 'calendar' : ['available', 'ok'].includes(status) ? 'check' : 'search';
function SourceStatus({ ids, sources }: { ids: string[]; sources: IntelligenceSource[] }) {
  const selected = sources.filter(source => ids.includes(source.id));
  const historical = selected.filter(source => source.status === 'stale').length;
  if (!historical) return null;
  return <span className="si-source-status"><ResearchIcon name="calendar" className="si-source-icon" size={18} />含历史资料</span>;
}
function References({ ids, sources, onSelect }: { ids?: string[]; sources: IntelligenceSource[]; onSelect: SourceSelect }) {
  const selected = Array.from(new Set(ids || [])).map(id => sources.find(source => source.id === id)).filter((source): source is IntelligenceSource => !!source);
  if (!selected.length) return null;
  return <div className="si-references"><ResearchIcon name="link" className="si-source-icon" size={18} />依据：{selected.map((source, index) => <React.Fragment key={source.id}>{index > 0 && ' · '}
    <a href={`#si-source-${encodeURIComponent(source.id)}`} title={`${source.label} · ${dateText(source.as_of)}`} onClick={event => { event.preventDefault(); onSelect(source.id); }}>{source.label}</a>
  </React.Fragment>)}</div>;
}
type DetailView = { kind: 'scenario'; key: IntelligenceScenario['key'] } | { kind: 'dimension'; key: string } | { kind: 'source'; id: string } | { kind: 'evidence'; id: string } | { kind: 'ratings'; key?: string } | { kind: 'platform-claim'; id: string } | { kind: 'platform-conflict'; id: string } | { kind: 'platform-coverage' } | { kind: 'method' } | { kind: 'risks' } | { kind: 'catalysts' };
const detailKey = (view: DetailView) => `${view.kind}:${'key' in view ? view.key : 'id' in view ? view.id : ''}`;
function ScenarioCard({ scenario, meta, currency, onOpen }: { scenario?: IntelligenceScenario; meta: typeof SCENARIOS[number]; currency: string; onOpen: () => void }) {
  const valid = finite(scenario?.price) && scenario!.price! > 0;
  const displayedPrice = valid ? priceText(scenario?.price) : '暂不估价';
  const reasoningSummary = scenario?.reasoning?.card_summary || scenario?.reasoning?.headline;
  return <article className={`si-scenario si-scenario--${meta.key}${displayedPrice.length >= 9 ? ' si-scenario--long-price' : ''}`} aria-label={`${meta.label}股价情景`}><button type="button" className="si-scenario-action" aria-label={`查看${meta.label}情景依据`} onClick={onOpen}>
    <span className="si-scenario-label"><b>{meta.label}</b></span><span className="si-scenario-number"><strong>{displayedPrice}</strong><small>{valid && currency}</small></span><span className={`si-scenario-return ${directionClass(scenario?.upside_pct)}`}>{valid ? percentText(scenario?.upside_pct) : '输入不足'}</span>
    {reasoningSummary && <span className="si-scenario-reason" title={reasoningSummary}>{reasoningSummary}</span>}
  </button></article>;
}
function Metrics({ metrics }: { metrics: IntelligenceDimension['metrics'] }) {
  return <dl className="si-metrics">{metrics.map((metric, index) => <div key={index}><dt>{metric.label}</dt><dd className="si-metric-value">{metricText(metric.value)}{metric.value != null && <small>{metric.unit}</small>}</dd></div>)}</dl>;
}
function FactorTable({ data, onOpen }: { data: StockIntelligence; onOpen: (key: string) => void }) {
  const preferred: Record<string, RegExp> = { fundamentals: /归母净利同比/, earnings: /EPS 中位数|预测 EPS|下季度 EPS/, technical: /^RSI14/, capital: /主力净流入/, news: /平台证据/ };
  return <div className="si-factor-table"><table><thead><tr><th>研究维度</th><th>关键数据</th><th>观察</th><th>资料时间</th><th><span className="si-visually-hidden">查看依据</span></th></tr></thead><tbody>{data.dimensions.map(dimension => {
    const metric = dimension.metrics?.find(item => preferred[dimension.key]?.test(item.label)) || dimension.metrics?.[0];
    const source = dimension.source_ids.map(id => data.sources.find(item => item.id === id)).find(item => item?.as_of);
    const label = DIMENSION_LABELS[dimension.key] || dimension.label;
    return <tr key={dimension.key}><th scope="row"><span className="si-factor-icon"><DimensionIcon dimension={dimension.key} /></span>{label}</th><td data-label="关键数据"><b>{metric ? metricText(metric.value) : dimension.key === 'business' ? dimension.headline : '—'}</b>{metric?.value != null && <small>{metric.unit}</small>}{metric && <span>{metric.label}</span>}</td><td data-label="观察"><span className={`si-signal si-signal--${dimension.signal}`}>{SIGNALS[dimension.signal] || '待核验'}</span><SourceStatus ids={dimension.source_ids} sources={data.sources} /></td><td data-label="资料时间">{source?.as_of ? dateText(source.as_of).slice(0, 10) : '未提供时点'}</td><td><button type="button" aria-label={`查看${label}研究依据`} onClick={() => onOpen(dimension.key)}>查看依据 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></td></tr>;
  })}</tbody></table></div>;
}
interface ResearchFact { label: string; value?: string; context: string; refs: string[]; date?: string | null; }
function keyFacts(data: StockIntelligence): { support: ResearchFact[]; challenge: ResearchFact[] } {
  const support: ResearchFact[] = [], challenge: ResearchFact[] = [];
  const find = (key: string, label: RegExp) => data.dimensions.find(dimension => dimension.key === key)?.metrics.find(item => label.test(item.label));
  const dated = (id: string) => data.sources.find(source => source.id === id)?.as_of;
  const current = (ids: string[]) => ids.every(id => data.sources.some(source => source.id === id && ['available', 'ok'].includes(source.status) && !!source.as_of));
  const capital = data.dimensions.find(dimension => dimension.key === 'capital');
  const flow = find('capital', /主力净流入/);
  if (flow && finite(flow.value) && flow.value !== 0 && capital && ['bullish', 'bearish'].includes(capital.signal) && current(['flow'])) (flow.value > 0 ? support : challenge).push({ label: capital.headline, value: `${metricText(flow.value)}${flow.unit || ''}`, context: '成交分类统计，不能证明特定机构买卖。', refs: ['flow'], date: dated('flow') });
  const profit = find('fundamentals', /归母净利同比/);
  if (profit && finite(profit.value) && profit.value !== 0 && current(['financials'])) (profit.value > 0 ? support : challenge).push({ label: '归母净利同比', value: `${metricText(profit.value)}${profit.unit || ''}`, context: profit.value > 0 ? '已披露盈利增长，仍需后续财报持续兑现。' : '已披露盈利回落，未来盈利假设需要验证。', refs: ['financials'], date: dated('financials') });
  const cash = find('fundamentals', /经营现金流\/归母净利/);
  if (cash && finite(cash.value) && current(['cashflow', 'financials'])) (cash.value >= 1 ? support : challenge).push({ label: '经营现金流 / 归母净利', value: `${metricText(cash.value)}${cash.unit || '倍'}`, context: '同报告期现金转化比较，合并与归母口径仍有差异。', refs: ['financials', 'cashflow'], date: dated('financials') });
  const technical = data.dimensions.find(dimension => dimension.key === 'technical');
  if (technical && ['bullish', 'bearish'].includes(technical.signal)) (technical.signal === 'bullish' ? support : challenge).push({ label: '技术趋势', value: technical.headline, context: '由同口径日线均线判断，历史动量不是未来承诺。', refs: technical.source_ids, date: data.technical?.as_of });
  if (!challenge.length && data.risks?.length) challenge.push({ label: '需要留意', context: data.risks[0], refs: [] });
  return { support: support.slice(0, 2), challenge: challenge.slice(0, 2) };
}
function FactList({ facts, positive, onSource }: { facts: ResearchFact[]; positive: boolean; onSource: SourceSelect }) {
  return <div className="si-fact-list">{facts.length ? facts.map((fact, index) => {
    const numeric = fact.value?.match(/^[+-]?[\d,.]+/);
    return <article className="si-fact-item" key={index}><div className="si-fact-number"><span>{fact.label}</span>{fact.value && <b className={`${positive ? 'si-positive' : 'si-negative'}${numeric ? '' : ' si-fact-value--text'}`}>{numeric ? <>{numeric[0]}<small>{fact.value.slice(numeric[0].length)}</small></> : fact.value}</b>}</div><p className="si-fact-context">{fact.context}</p><div className="si-fact-meta">{fact.date && <time dateTime={fact.date}>{dateText(fact.date).slice(0, 10)}</time>}{!!fact.refs.length && <button type="button" aria-label="查看依据 ↗" onClick={() => onSource(fact.refs[0])}>查看依据 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button>}</div></article>;
  }) : <p className="si-muted">暂无可核验的方向性支持信号，继续跟踪资料变化。</p>}</div>;
}
function EvidenceTimeline({ items, origin, sources, onOpen }: { items: IntelligenceEvidence[]; origin: 'platform' | 'public'; sources: IntelligenceSource[]; onOpen: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const selected = items.filter(item => item.origin === origin);
  const displayed = expanded ? selected : selected.slice(0, 6);
  return <section className="si-evidence-column"><h4><ResearchIcon name={origin === 'platform' ? 'research' : 'news'} className="si-section-icon" />{origin === 'platform' ? '平台研究资料' : '公开信息'}<span>{selected.length} 条</span></h4><div className="si-evidence-list">{displayed.map(item => <article className="si-evidence-row" key={item.id}><span className="si-evidence-date">{item.published_at ? dateText(item.published_at).slice(0, 10) : '时间未提供'}</span><div><button type="button" className="si-evidence-title" onClick={() => onOpen(item.id)}>{item.title}<ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button><div className="si-evidence-meta">{item.kind || '资料'} · {item.source}{sources.find(source => source.id === item.id)?.status === 'stale' && <span className="si-source-status"><ResearchIcon name="calendar" className="si-source-icon" size={18} />历史资料</span>}</div></div></article>)}</div>{!selected.length && <p className="si-muted">暂无当前权限下可读取的关联资料。</p>}{selected.length > 6 && <button type="button" className="si-button si-evidence-more" onClick={() => setExpanded(value => !value)}>{expanded ? '收起更多资料' : `查看全部 ${selected.length} 条${origin === 'platform' ? '平台' : '公开'}信息`}</button>}</section>;
}

function PlatformScenarioArguments({ data, scenarioKey, onClaim }: { data: StockIntelligence; scenarioKey: IntelligenceScenario['key']; onClaim: (id: string) => void }) {
  const research = data.platform_research;
  const link = research?.scenario_links.find(item => item.key === scenarioKey);
  if (!research || !link) return null;
  const directionIds = (ids: string[]) => ids.filter(id => { const claim = research.claims.find(item => item.id === id); return !!claim && isPlatformDirectional(claim, research); });
  const support = directionIds(link.support_claim_ids), counter = directionIds(link.counter_claim_ids);
  const context = Array.from(new Set([...link.context_claim_ids, ...link.support_claim_ids.filter(id => !support.includes(id)), ...link.counter_claim_ids.filter(id => !counter.includes(id))]));
  const groups = [
    { label: scenarioKey === 'base' ? '基准观察' : '支持该情景的条件', ids: support },
    { label: '挑战该情景的反证', ids: counter },
    { label: '历史、摘要与关联背景', ids: context },
  ];
  return <section className="si-detail-block si-platform-scenario-detail"><h4><ResearchIcon name="target" className="si-section-icon" />平台资料如何影响情景</h4>{groups.map(group => {
    const claims = group.ids.map(id => research.claims.find(item => item.id === id)).filter(item => !!item);
    return <div key={group.label}><h5>{group.label}</h5>{claims.length ? <ul className="si-detail-list">{claims.map(claim => <li key={claim!.id}><button type="button" className="si-platform-claim-link" aria-label={`${claim!.claim} ↗`} onClick={() => onClaim(claim!.id)}>{claim!.claim} <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></li>)}</ul> : <p className="si-detail-caption">本次未取得可引用的对应平台论据。</p>}</div>;
  })}<p className="si-detail-caption">平台文字论据用于核验假设；情景价格仍由明确的数值输入计算，未从自然语言数字自动重估。</p></section>;
}

function PlatformDimensionArguments({ data, dimension, onClaim }: { data: StockIntelligence; dimension: IntelligenceDimension; onClaim: (id: string) => void }) {
  const claims = (data.platform_research?.claims || []).filter(claim => dimension.platform_claim_ids?.includes(claim.id));
  if (!claims.length) return null;
  return <section className="si-detail-block"><h4><ResearchIcon name="document" className="si-section-icon" />平台段落论据</h4><p className="si-detail-caption">逐条核对可见范围、资料时点与经营传导。</p><ul className="si-detail-list">{claims.map(claim => <li key={claim.id}><button type="button" className="si-platform-claim-link" aria-label={`${claim.claim} ↗`} onClick={() => onClaim(claim.id)}>{claim.claim} <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></li>)}</ul></section>;
}

function PlatformSourceContext({ data, sourceId }: { data: StockIntelligence; sourceId: string }) {
  const research = data.platform_research;
  const document = research?.documents.find(item => item.source_id === sourceId);
  if (!research || !document) return null;
  const seen = new Set<string>();
  const citations = research.citations.filter(citation => {
    if (citation.source_id !== sourceId || citation.document_id !== document.id || citation.document_revision !== document.revision || seen.has(citation.id)) return false;
    seen.add(citation.id); return true;
  });
  return <section className="si-detail-block"><h4><ResearchIcon name="document" className="si-section-icon" />{document.title}</h4><p className="si-detail-caption">{PLATFORM_BODY_LABELS[document.body_state]} · {PLATFORM_ROLE_LABELS[document.role]}</p>{citations.length ? citations.map(citation => <article className="si-platform-citation" key={citation.id}><p>第 {citation.paragraph} 段 · 本次可见引用</p><blockquote>{citation.quote}</blockquote></article>) : <p>{document.excerpt || '本次只取得标题线索。'}</p>}<p className="si-detail-caption">文本版本 {document.revision}；引用范围受本次账号权限与读取上限约束。</p></section>;
}

export default function StockIntelligencePanel({ symbol, name, loggedIn, username, onRequireLogin }: Props) {
  const session = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot);
  const canRead = loggedIn && !!session.token;
  const scope = JSON.stringify([symbol, name, canRead, username, session.revision]);
  const [result, setResult] = useState<{ scope: string; data: StockIntelligence } | null>(null);
  const [loadingScope, setLoadingScope] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ scope: string; message: string } | null>(null);
  const [section, setSection] = useState<SectionKey>('overview');
  const [evidenceFilter, setEvidenceFilter] = useState<'all' | 'platform' | 'public'>('all');
  const [detail, setDetail] = useState<DetailView | null>(null);
  const [detailHistory, setDetailHistory] = useState<DetailView[]>([]);
  const [scenarioDriverOpen, setScenarioDriverOpen] = useState<Record<string, string[]>>({});
  const inspectorBody = useRef<HTMLDivElement>(null);
  const intentTrigger = useRef<HTMLElement | null>(null);
  const detailScroll = useRef(new Map<string, number>());
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const componentId = useId();
  const data = canRead && result?.scope === scope ? result.data : null;
  const load = useCallback(async (refresh = false) => {
    controller.current?.abort();
    if (!canRead) return;
    const currentSequence = ++sequence.current;
    const revision = session.revision;
    const aborter = new AbortController(); controller.current = aborter;
    setLoadingScope(scope); setFailure(null);
    try {
      const response = await apiGet<{ data: StockIntelligence }>('/api/stock/intelligence', { params: { symbol, name, refresh }, timeout: 45000, signal: aborter.signal });
      if (aborter.signal.aborted || currentSequence !== sequence.current || !isAuthRevisionCurrent(revision)) return;
      if (!response?.data?.valuation || !Array.isArray(response.data.dimensions)) throw new Error('分析数据暂不可用，请稍后重试');
      setResult({ scope, data: response.data });
    } catch (error: any) {
      if (aborter.signal.aborted || currentSequence !== sequence.current || !isAuthRevisionCurrent(revision)) return;
      setFailure({ scope, message: error?.message || '暂时无法获取分析数据，请稍后重试' });
    } finally { if (!aborter.signal.aborted && currentSequence === sequence.current) setLoadingScope(null); }
  }, [canRead, name, scope, session.revision, symbol]);
  useEffect(() => {
    setResult(null); setFailure(null); setSection('overview'); setEvidenceFilter('all'); setDetail(null); setDetailHistory([]); setScenarioDriverOpen({}); detailScroll.current.clear();
    void load();
    return () => { controller.current?.abort(); sequence.current += 1; };
  }, [load]);
  useLayoutEffect(() => { if (detail && inspectorBody.current) inspectorBody.current.scrollTop = detailScroll.current.get(detailKey(detail)) || 0; }, [detail]);
  const openDetail = (view: DetailView) => {
    if (detail) { detailScroll.current.set(detailKey(detail), inspectorBody.current?.scrollTop || 0); setDetailHistory(previous => [...previous, detail]); }
    setDetail(view);
  };
  const backDetail = () => {
    const previous = detailHistory[detailHistory.length - 1];
    if (!previous) return;
    if (detail) detailScroll.current.set(detailKey(detail), inspectorBody.current?.scrollTop || 0);
    setDetailHistory(history => history.slice(0, -1)); setDetail(previous);
  };
  const closeDetail = () => { setDetail(null); setDetailHistory([]); setScenarioDriverOpen({}); detailScroll.current.clear(); };
  const openSource = (id: string) => openDetail({ kind: 'source', id });
  const openDimension = (key: string) => openDetail({ kind: 'dimension', key });
  const keyboardTab = (event: React.KeyboardEvent, index: number) => {
    const next = event.key === 'ArrowRight' ? (index + 1) % SECTIONS.length : event.key === 'ArrowLeft' ? (index + SECTIONS.length - 1) % SECTIONS.length : event.key === 'Home' ? 0 : event.key === 'End' ? SECTIONS.length - 1 : null;
    if (next == null) return; event.preventDefault(); setSection(SECTIONS[next].key); tabs.current[next]?.focus();
  };
  const busy = canRead && loadingScope === scope;
  const error = failure?.scope === scope ? failure.message : null;
  const sources = data?.sources || [];
  const valuation = data?.valuation;
  const anchor = valuation?.anchor;
  const platform = (data?.evidence || []).filter(item => item.origin === 'platform');
  const publicItems = (data?.evidence || []).filter(item => item.origin === 'public');
  const platformHistorical = platform.length > 0 && platform.every(item => sources.find(source => source.id === item.id)?.status === 'stale');
  const fullHeadline = data?.summary?.headline?.split(/\s*·\s*/)[0];
  const headline = fullHeadline?.replace(`${data?.summary?.stance}：`, '') || '按已有信息评估，持续验证关键假设';
  const mode = valuation?.status === 'ready' ? '条件估值' : valuation?.status === 'sensitivity' ? '估值敏感性' : '估值输入不足';
  const facts = data ? keyFacts(data) : { support: [], challenge: [] };
  const titleOf = (view: DetailView) => view.kind === 'scenario' ? `${SCENARIOS.find(item => item.key === view.key)?.label || ''}情景` : view.kind === 'dimension' ? DIMENSION_LABELS[view.key] || view.key : view.kind === 'source' ? sources.find(item => item.id === view.id)?.label || '资料来源' : view.kind === 'evidence' ? '关联资料' : view.kind === 'ratings' ? '券商评级样本' : view.kind === 'platform-claim' ? '平台完整论据' : view.kind === 'platform-conflict' ? '平台观点对照' : view.kind === 'platform-coverage' ? '平台取证范围与缺口' : view.kind === 'method' ? '估值方法与关键假设' : view.kind === 'risks' ? '风险反证与数据缺口' : '催化与验证清单';
  const previousTitle = detailHistory.length ? titleOf(detailHistory[detailHistory.length - 1]) : undefined;
  const scenario = detail?.kind === 'scenario' ? valuation?.scenarios.find(item => item.key === detail.key) : undefined;
  const dimension = detail?.kind === 'dimension' ? data?.dimensions.find(item => item.key === detail.key) : undefined;
  const source = detail?.kind === 'source' ? sources.find(item => item.id === detail.id) : undefined;
  const evidence = detail?.kind === 'evidence' ? data?.evidence.find(item => item.id === detail.id) : undefined;
  const platformClaim = detail?.kind === 'platform-claim' ? data?.platform_research?.claims.find(item => item.id === detail.id) : undefined;
  const platformConflict = detail?.kind === 'platform-conflict' ? data?.platform_research?.conflicts.find(item => item.id === detail.id) : undefined;

  return <section className="stock-intelligence" aria-label={`${name || symbol}个股研究台`} aria-busy={busy} onClickCapture={event => {
    if (detail || !(event.target instanceof Element)) return;
    const control = event.target.closest('button, a');
    if (control instanceof HTMLElement) intentTrigger.current = control;
  }}>
    <header className="si-company-header"><div className="si-company"><div className="si-company-identity"><h3>{name || symbol}</h3><div className="si-company-meta"><b>{symbol}</b><span>{({ CN: 'A股', HK: '港股', US: '美股' } as Record<string, string>)[data?.market || ''] || '个股研究'}</span></div></div></div>
      {data && <div className="si-header-quote"><div className="si-header-price"><b>{priceText(data.quote?.price)}</b><span>{data.currency}</span>{finite(data.quote?.change_percent) && <em className={directionClass(data.quote.change_percent)}>{percentText(data.quote.change_percent)}</em>}</div><small><span>分析参考价 · {data.quote?.source || '来源待核验'}</span><time dateTime={data.quote?.as_of || undefined}>{dateText(data.quote?.as_of)}</time></small></div>}
      {canRead && <button type="button" className="si-button" disabled={busy} onClick={() => void load(true)}><ResearchIcon name="refresh" className="si-action-icon" size={18} /> {busy ? '更新中…' : '刷新'}</button>}
    </header>
    {!canRead ? <div className="si-gate"><h4>从事实出发，判断价格背后的机会与风险。</h4><p>平台研究资料与公开行情、财报，为你呈现三种股价情景、关键指标和可追溯的依据。</p><button type="button" className="si-button si-button--primary" onClick={() => onRequireLogin('查看个股情景估值与多维研究证据')}><ResearchIcon name="shield" className="si-action-icon" size={18} />登录查看完整研究</button></div> : <>
      {busy && !data && <div className="si-loading" role="status"><span className="si-loading-dot" />正在汇集平台内容、公开行情、财务与资金证据…<div>缺失的数据会逐项标明。</div></div>}
      {error && <div className="si-error" role="alert">{error}{data && <span>当前展示上次分析，刷新未成功。</span>}<button type="button" className="si-button" disabled={busy} onClick={() => void load(true)}><ResearchIcon name="refresh" className="si-action-icon" size={18} />重试</button></div>}
      {data && <>
        <div className="si-tabs" role="tablist" aria-label="个股研究内容">{SECTIONS.map((item, index) => <button key={item.key} type="button" role="tab" id={`${componentId}-tab-${item.key}`} aria-controls={`${componentId}-panel-${item.key}`} aria-selected={section === item.key} tabIndex={section === item.key ? 0 : -1} ref={element => { tabs.current[index] = element; }} onClick={() => setSection(item.key)} onKeyDown={event => keyboardTab(event, index)}>{item.label}</button>)}</div>
        <div className="si-tab-panel" role="tabpanel" id={`${componentId}-panel-${section}`} aria-labelledby={`${componentId}-tab-${section}`} tabIndex={0}>
          {section === 'overview' && <>
            <div className="si-research-brief"><h4>{headline}</h4></div>
            <div className="si-research-layout"><section className="si-valuation-panel"><div className="si-panel-head"><h4><ResearchIcon name="valuation" className="si-section-icon" />三种股价情景</h4><span className="si-model-tag">{mode}</span></div><p className="si-valuation-horizon"><span>{valuation?.horizon || '期限待核验'}</span><span>较上述参考价的测算空间</span></p><div className="si-scenarios">{SCENARIOS.map(meta => <ScenarioCard key={meta.key} meta={meta} scenario={valuation?.scenarios.find(item => item.key === meta.key)} currency={data.currency} onOpen={() => openDetail({ kind: 'scenario', key: meta.key })} />)}</div><p className="si-assumption-footnote">盈利 ±15%、估值倍数 ±20% 为固定压力假设，非预测、实现概率或公允价值；风险可能超出区间。</p><button type="button" className="si-method-link" onClick={() => openDetail({ kind: 'method' })}>估值方法与关键假设 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></section><HistoryPriceChart technical={data.technical} currency={data.currency} source={sources.find(item => item.id === (data.technical?.source_ids?.[0] || 'history'))} onSource={openSource} /></div>
            <details className="si-key-evidence"><summary><span>关键依据与风险</span>{facts.challenge[0] && <span className="si-risk-preview">{facts.challenge[0].label}{facts.challenge[0].value ? ` ${facts.challenge[0].value}` : `：${facts.challenge[0].context}`}</span>}</summary><div className="si-thesis-grid"><section className="si-thesis-column"><div className="si-thesis-head"><h4><ResearchIcon name="check" className="si-section-icon" />支持判断的事实</h4></div><FactList facts={facts.support} positive onSource={openSource} /></section><section className="si-thesis-column si-thesis-column--risk"><div className="si-thesis-head"><h4><ResearchIcon name="warning" className="si-section-icon" />需要验证的反证</h4><button type="button" aria-label="全部风险 ↗" onClick={() => openDetail({ kind: 'risks' })}>全部风险 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></div><FactList facts={facts.challenge} positive={false} onSource={openSource} /></section></div></details>
          </>}
          {section === 'research' && <><div className="si-section-head"><div><h4><ResearchIcon name="research" className="si-section-icon" />六维研究</h4><span>事实、预期与观察分开判断；点击查看完整指标及证据。</span></div></div><FactorTable data={data} onOpen={openDimension} />{data.technical?.range && <div className="si-technical-range"><div><b><ResearchIcon name="chart" className="si-section-icon" />历史位置参考</b><span>{data.technical.range.horizon} · {data.technical.range.method}</span></div><strong>{priceText(data.technical.range.lower)} — {priceText(data.technical.range.upper)} <small>{data.technical.series_currency || '币种未核验'}</small></strong><p>历史收盘价格带，与未来估值情景分开判断。{data.technical.adjustment} · 截至 {dateText(data.technical.as_of)}</p></div>}<div className="si-section-head"><div><h4><ResearchIcon name="flow" className="si-section-icon" />资金与市场预期</h4><span>成交分类、同日价格与卖方研究样本</span></div></div><MarketEvidenceCharts key={scope} data={data} onSource={openSource} onRatings={key => openDetail({ kind: 'ratings', key })} /><div className="si-review-next"><div><span><ResearchIcon name="target" className="si-step-icon" size={18} />接下来验证</span><b>{data.catalysts?.find(item => item.event === '下一次财报验证')?.watch || data.catalysts?.[0]?.watch || '继续核对正式公告与后续财报。'}</b></div><button type="button" aria-label="验证清单 ↗" onClick={() => openDetail({ kind: 'catalysts' })}>验证清单 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></div></>}
          {section === 'evidence' && <><div className="si-platform-anchor"><PlatformResearchPanel data={data} onSource={openSource} onClaim={id => openDetail({ kind: 'platform-claim', id })} onCoverage={() => openDetail({ kind: 'platform-coverage' })} onConflict={id => openDetail({ kind: 'platform-conflict', id })} /></div><div className="si-section-head"><div><h4><ResearchIcon name="sources" className="si-section-icon" />研究资料与证据</h4><span>平台 {platform.length} 条{platformHistorical ? '（历史资料）' : ''} · 公开 {publicItems.length} 条，均保留时间与出处。</span></div></div><div className="si-evidence-controls" role="group" aria-label="资讯来源筛选">{(['all', 'platform', 'public'] as const).map(origin => <button type="button" key={origin} aria-pressed={evidenceFilter === origin} onClick={() => setEvidenceFilter(origin)}>{origin === 'all' ? '全部' : origin === 'platform' ? '平台' : '公开'}</button>)}</div><div className={`si-evidence-grid${evidenceFilter !== 'all' ? ' si-evidence-grid--single' : ''}`}>{evidenceFilter !== 'public' && <EvidenceTimeline origin="platform" items={data.evidence || []} sources={sources} onOpen={id => openDetail({ kind: 'evidence', id })} />}{evidenceFilter !== 'platform' && <EvidenceTimeline origin="public" items={data.evidence || []} sources={sources} onOpen={id => openDetail({ kind: 'evidence', id })} />}</div><details className="si-details si-source-details"><summary><ResearchIcon name="calendar" className="si-section-icon" />数据来源与时间 <span>{sources.length} 条可追溯记录</span></summary><div className="si-source-list si-details-body">{sources.map(item => <article key={item.id}><div><button type="button" aria-label={`${item.label} ↗`} onClick={() => openSource(item.id)}><ResearchIcon name="document" className="si-source-icon" size={18} />{item.label} <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button><span><ResearchIcon name={sourceStatusIcon(item.status)} className="si-source-icon" size={18} />{item.status === 'stale' ? '数据滞后' : ['available', 'ok'].includes(item.status) ? '可用' : '待补数据'}</span></div><p>{dateText(item.as_of)}</p></article>)}</div></details></>}
        </div>
        {SECTIONS.filter(item => item.key !== section).map(item => <div key={item.key} hidden role="tabpanel" id={`${componentId}-panel-${item.key}`} aria-labelledby={`${componentId}-tab-${item.key}`} />)}
        <footer className="si-footer"><span><ResearchIcon name="calendar" className="si-source-icon" size={18} />本次汇集 {dateText(data.generated_at)}</span><span>{data.disclaimer || '仅供研究参考，不构成投资建议；历史资料与条件测算不代表未来表现。'}</span></footer>
      </>}
    </>}
    {data && detail && <IntelligenceInspector title={titleOf(detail)} category={detail.kind === 'source' ? 'DATA SOURCE' : detail.kind === 'evidence' ? 'RESEARCH EVIDENCE' : 'RESEARCH DETAIL'} previousTitle={previousTitle} viewKey={detailKey(detail)} onClose={closeDetail} onBack={backDetail} bodyRef={inspectorBody} returnFocus={intentTrigger.current}>
      {detail.kind === 'scenario' && scenario && <><div className="si-detail-price"><b>{priceText(scenario.price)} <small>{data.currency}</small></b><span className={directionClass(scenario.upside_pct)}>较参考价 {percentText(scenario.upside_pct)}</span></div><p className="si-detail-caption">{mode} · {valuation?.horizon} · 固定压力假设</p>{scenario.reasoning ? <ScenarioReasoning reasoning={scenario.reasoning} data={data} onSource={openSource} onClaim={id => openDetail({ kind: 'platform-claim', id })} expandedDriverKeys={scenarioDriverOpen[scenario.key] || []} onToggleDriver={(key, open) => setScenarioDriverOpen(previous => {
        const selected = previous[scenario.key] || [];
        if (selected.includes(key) === open) return previous;
        return { ...previous, [scenario.key]: open ? [...selected, key] : selected.filter(item => item !== key) };
      })} /> : <><section className="si-detail-block"><h4><ResearchIcon name="target" className="si-section-icon" />需要发生什么</h4><ul className="si-detail-list">{scenario.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul></section><section className="si-detail-block"><h4><ResearchIcon name="research" className="si-section-icon" />原因与交叉验证</h4><ul className="si-detail-list">{scenario.reasons.map((item, index) => <li key={index}>{item}</li>)}</ul></section><section className="si-detail-block"><h4><ResearchIcon name="warning" className="si-section-icon" />哪些事实会推翻假设</h4><ul className="si-detail-list">{scenario.invalidation.map((item, index) => <li key={index}>{item}</li>)}</ul></section><PlatformScenarioArguments data={data} scenarioKey={scenario.key} onClaim={id => openDetail({ kind: 'platform-claim', id })} /></>}<p className="si-detail-caption">{valuation?.note}</p><References ids={scenario.source_ids} sources={sources} onSelect={openSource} /></>}
      {detail.kind === 'dimension' && dimension && <><p className="si-detail-lead">{dimension.headline}</p><SourceStatus ids={dimension.source_ids} sources={sources} /><Metrics metrics={dimension.metrics || []} /><section className="si-detail-block"><h4><ResearchIcon name="document" className="si-section-icon" />研究依据</h4><ul className="si-detail-list">{dimension.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul></section><PlatformDimensionArguments data={data} dimension={dimension} onClaim={id => openDetail({ kind: 'platform-claim', id })} /><References ids={dimension.source_ids} sources={sources} onSelect={openSource} /></>}
      {detail.kind === 'method' && <><p className="si-detail-lead">{valuation?.method}</p>{valuation?.formula && <div className="si-formula">{valuation.formula}</div>}{anchor != null && (typeof anchor === 'object' ? <Metrics metrics={[{ label: '参考价', value: anchor.price ?? null, unit: data.currency }, { label: '当前 TTM PE', value: anchor.pe_ttm ?? null, unit: '倍' }, { label: `${anchor.forecast_year || ''} 年度预测 EPS`, value: anchor.forecast_eps ?? null, unit: data.currency }]} /> : <p>估值锚：{anchor}</p>)}<p className="si-detail-caption">{valuation?.note}</p><References ids={valuation?.source_ids} sources={sources} onSelect={openSource} /></>}
      {detail.kind === 'source' && <div className="si-source-context">{source ? <><span className="si-model-tag"><ResearchIcon name={sourceStatusIcon(source.status)} className="si-source-icon" size={18} />{source.status === 'stale' ? '数据滞后' : ['ok', 'available'].includes(source.status) ? '可用' : '待补数据'}</span><dl><div><dt>资料时点</dt><dd>{dateText(source.as_of)}</dd></div><div><dt>本次汇集时间</dt><dd>{dateText(source.fetched_at)}</dd></div></dl>{source.freshness && <p>{source.freshness}</p>}<PlatformSourceContext data={data} sourceId={source.id} />{intelligenceLink(source.url) ? <a href={intelligenceLink(source.url)} target="_blank" rel="noopener noreferrer" aria-label="打开原始来源 ↗">打开原始来源 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></a> : <p>该记录未提供可访问的原文链接。</p>}</> : <p>该条来源记录暂不可用，不能核验其原文。</p>}</div>}
      {detail.kind === 'evidence' && evidence && <><span className="si-model-tag"><ResearchIcon name={evidence.origin === 'platform' ? 'research' : 'news'} className="si-source-icon" size={18} />{evidence.origin === 'platform' ? '平台资料' : '公开信息'}</span><h4 className="si-evidence-detail-title"><ResearchIcon name="document" className="si-section-icon" />{evidence.title}</h4><p className="si-detail-caption">{evidence.source} · {dateText(evidence.published_at)}</p>{sources.find(item => item.id === evidence.id)?.status === 'stale' && <span className="si-source-status"><ResearchIcon name="calendar" className="si-source-icon" size={18} />历史资料</span>}<section className="si-detail-block"><h4><ResearchIcon name="quote" className="si-section-icon" />原文摘要</h4><p>{evidence.summary || '该资料未提供摘要，可通过原始链接进一步核对。'}</p></section>{intelligenceLink(evidence.url) && <a className="si-original-link" href={intelligenceLink(evidence.url)} target="_blank" rel="noopener noreferrer" aria-label="打开资料原文 ↗">打开资料原文 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></a>}<References ids={[evidence.id]} sources={sources} onSelect={openSource} /></>}
      {detail.kind === 'ratings' && <><p className="si-detail-caption">{data.broker_ratings?.note}</p><section className="si-detail-block"><h4><ResearchIcon name="ratings" className="si-section-icon" />{detail.key ? `${data.broker_ratings?.distribution.find(item => item.key === detail.key)?.label || '所选'}评级记录` : '每家机构最新评级'}</h4><div className="si-rating-detail-list">{(data.broker_ratings?.samples || []).filter(item => !detail.key || item.rating_key === detail.key).map(item => <article key={item.broker_id}><div><b>{item.broker}</b><span>{item.rating}</span></div><time>{item.published_at}</time>{intelligenceLink(item.url) && <a href={intelligenceLink(item.url)} target="_blank" rel="noopener noreferrer" aria-label="打开评级原始来源 ↗">打开评级原始来源 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></a>}</article>)}</div></section><p className="si-detail-caption">评级是券商原文观点；样本分布不代表上涨概率，也不替代平台的情景估值。</p><References ids={data.broker_ratings?.source_ids} sources={sources} onSelect={openSource} /></>}
      {detail.kind === 'platform-claim' && platformClaim && <><p className="si-detail-lead">{platformClaim.topic}</p><div className="si-platform-tags"><span>{PLATFORM_ROLE_LABELS[platformClaim.role]}</span><span>{PLATFORM_BODY_LABELS[platformClaim.body_state]}</span><span>{platformClaim.freshness === 'historical' ? '历史资料 · 仅作背景' : platformClaim.freshness === 'unknown' ? '时点待核' : '来源观点待复核'}</span></div><section className="si-detail-block"><h4><ResearchIcon name="quote" className="si-section-icon" />本次可见文本引用</h4>{platformClaim.citations.map(citation => { const document = data.platform_research?.documents.find(item => item.id === citation.document_id); return <article className="si-platform-citation" key={citation.id}><blockquote>{citation.quote}</blockquote><p>{document?.title} · {dateText(document?.published_at)} · 第 {citation.paragraph} 段</p><button type="button" className="si-text-button" aria-label="查看此段来源 ↗" onClick={() => openSource(citation.source_id)}>查看此段来源 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></article>; })}</section><section className="si-detail-block"><h4><ResearchIcon name="business" className="si-section-icon" />经营传导</h4><p>{platformClaim.transmission}</p><p className="si-detail-caption">{platformClaim.transmission_basis === 'source_explicit' ? '这是来源表达的因果关系，仍需公司公告与财报交叉核验。' : '这是经营传导规则提出的条件假设，尚未证实。'}</p></section><section className="si-detail-block"><h4><ResearchIcon name="target" className="si-section-icon" />验证与失效观察</h4><p>{platformClaim.watch}</p><p className="si-detail-caption">{platformClaim.watch_basis === 'source_explicit' ? '来源提出的观察条件。' : '规则提出的核验清单，需要结合实际经营信息。'}</p></section><section className="si-detail-block"><h4><ResearchIcon name="link" className="si-section-icon" />主体与关系</h4>{data.platform_research?.relations.filter(item => platformClaim.relation_ids.includes(item.id)).map(item => <p key={item.id}>{item.label} · {({ company: '公司', industry: '行业', peer: '同行', upstream: '上游', downstream: '下游' } as Record<string,string>)[item.kind]} · {item.verification === 'verified' ? '主体关联已核验' : item.verification === 'candidate' ? '关系待核，不能当作已确认事实' : '关联背景'}</p>)}</section><p className="si-detail-caption">引用绑定本次可见文本版本；全文、导语与已有解读按当前账号权限区分。{data.platform_research?.method}</p></>}
      {detail.kind === 'platform-conflict' && platformConflict && <><p className="si-detail-lead">{platformConflict.topic}</p><p>{platformConflict.summary}</p>{platformConflict.claim_ids.map(id => { const claim = data.platform_research?.claims.find(item => item.id === id); return claim && <section className="si-detail-block" key={id}><h4>{PLATFORM_ROLE_LABELS[claim.role]} · {dateText(claim.as_of)}</h4><blockquote className="si-platform-conflict-quote">{claim.claim}</blockquote><p className="si-detail-caption">报告期 {claim.period || '未核验'} · 币种 {claim.currency || '未核验'} · {PLATFORM_BODY_LABELS[claim.body_state]}</p><button type="button" className="si-text-button" aria-label="查看这条完整论据 ↗" onClick={() => openDetail({ kind: 'platform-claim', id })}>查看这条完整论据 <ResearchIcon name="arrow-up-right" className="si-action-icon" size={18} /></button></section>; })}<p className="si-detail-caption">先核对同一主体、业务、指标、报告期和币种，再判断是否矛盾；当期事实与未来预测可能同时成立。</p><References ids={platformConflict.source_ids} sources={sources} onSelect={openSource} /></>}
      {detail.kind === 'platform-coverage' && <><p className="si-detail-lead">{data.platform_research?.coverage.length || 0} 类来源 · 当前账号可见范围</p>{(data.platform_research?.coverage || []).map(item => <section className="si-detail-block" key={item.key}><h4><ResearchIcon name="sources" className="si-section-icon" />{item.label} · {platformCoverageLabel(item)}</h4><p>本次读取 {item.read} 条可见资料，提取 {item.selected_claims} 条候选论据。{item.window?.days ? `检索范围：最近 ${item.window.days} 天。` : ''}</p><p className="si-detail-caption">实际资料日期 {sourceDay(item.period?.from)} — {sourceDay(item.period?.to)}；{Object.entries(item.body_states || {}).filter(([, count]) => !!count).map(([state,count]) => `${PLATFORM_BODY_LABELS[state as keyof typeof PLATFORM_BODY_LABELS] || state} ${count} 条`).join(' · ') || '暂无可读文本'}</p>{item.reason && <p className="si-detail-caption">{item.reason}</p>}</section>)}<section className="si-detail-block"><h4><ResearchIcon name="shield" className="si-section-icon" />方法与缺口</h4><p>{data.platform_research?.method || '平台资料尚未检索，不能声称全库融合。'}</p><ul className="si-detail-list">{data.platform_research?.gaps.map((gap,index) => <li key={index}>{gap}</li>)}</ul></section></>}
      {detail.kind === 'risks' && <><section className="si-detail-block"><h4><ResearchIcon name="warning" className="si-section-icon" />风险与反证</h4><ul className="si-detail-list">{data.risks.map((item, index) => <li key={index}>{item}</li>)}</ul></section><section className="si-detail-block"><h4><ResearchIcon name="search" className="si-section-icon" />还缺哪些信息</h4><ul className="si-detail-list">{data.data_gaps.map((item, index) => <li key={index}>{item}</li>)}</ul></section></>}
      {detail.kind === 'catalysts' && <>{data.catalysts.map((item, index) => <section className="si-detail-block" key={index}><h4><ResearchIcon name="calendar" className="si-section-icon" />{item.event}</h4><p>{item.watch}</p><References ids={item.source_ids} sources={sources} onSelect={openSource} /></section>)}</>}
    </IntelligenceInspector>}
  </section>;
}
