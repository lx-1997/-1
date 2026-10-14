import React from 'react';
import ResearchIcon from './ResearchIcon';
import type {
  PlatformResearch, PlatformResearchBodyState, PlatformResearchClaim,
  PlatformResearchCoverage, PlatformResearchRole, PlatformResearchScenarioLink, StockIntelligence,
} from './types';

interface Props {
  data: StockIntelligence;
  onSource: (id: string) => void;
  onClaim: (id: string) => void;
  onCoverage: () => void;
  onConflict: (id: string) => void;
}

export const PLATFORM_ROLE_LABELS: Record<PlatformResearchRole, string> = {
  fact: '原文事实陈述', opinion: '来源观点', analysis: '来源分析', context: '背景线索',
};
export const PLATFORM_BODY_LABELS: Record<PlatformResearchBodyState, string> = {
  full: '可见全文', excerpt: '可见节选', teaser: '仅导语', summary_only: '仅摘要', title_only: '仅标题',
};
const SCENARIOS = [{ key: 'optimistic', label: '乐观' }, { key: 'base', label: '中性' }, { key: 'pessimistic', label: '悲观' }] as const;

function dateLabel(value?: string | null) {
  if (!value) return '时点未提供';
  if (/(Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    const date = new Date(value);
    if (Number.isFinite(date.getTime())) return date.toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
  }
  return value.replace('T', ' ').slice(0, 10);
}
function count(value: number) { return Number.isFinite(value) ? Math.max(0, value).toLocaleString('zh-CN') : '0'; }

export function platformCoverageLabel(coverage: PlatformResearchCoverage) {
  if (['access_required', 'denied'].includes(coverage.status)) return '当前账号未授权';
  if (['not_enabled', 'disabled'].includes(coverage.status)) return '来源未启用';
  if (coverage.status === 'error') return '检索失败';
  if (coverage.status === 'empty') return '本次未命中';
  if (coverage.status === 'partial' || coverage.limit_hit || !coverage.complete) return '部分覆盖';
  if (coverage.status === 'available') return '本次窗口已检索';
  return '待检索';
}

function relationLabel(claim: PlatformResearchClaim, research: PlatformResearch) {
  const relations = research.relations.filter(relation => claim.relation_ids.includes(relation.id));
  if (relations.some(relation => relation.kind === 'company' && relation.verification === 'verified')) return '公司直接资料';
  const relation = relations.find(item => item.kind !== 'company') || relations[0];
  if (!relation) return '主体关联待核';
  const label = { company: '公司关联', industry: '行业背景', peer: '同行背景', upstream: '上游线索', downstream: '下游线索' }[relation.kind];
  return relation.verification === 'candidate' ? `${label} · 待核` : label;
}

function normalizedLinks(research: PlatformResearch): PlatformResearchScenarioLink[] {
  if (Array.isArray(research.scenario_links)) return research.scenario_links;
  // Older snapshots used a keyed object; retain their explicit links without inventing any.
  const legacy = research.scenario_links as unknown as Record<string, Omit<PlatformResearchScenarioLink, 'key'>>;
  return SCENARIOS.filter(item => legacy?.[item.key]).map(item => ({ ...legacy[item.key], key: item.key }));
}

export function isPlatformDirectional(claim: PlatformResearchClaim, research: PlatformResearch) {
  return claim.freshness === 'current' && ['full', 'excerpt'].includes(claim.body_state) && claim.role !== 'context'
    && research.relations.some(relation => claim.relation_ids.includes(relation.id) && relation.kind === 'company' && relation.verification === 'verified');
}

function scenarioConnections(claim: PlatformResearchClaim, research: PlatformResearch) {
  if (!isPlatformDirectional(claim, research)) return [];
  return normalizedLinks(research).flatMap(link => {
    const support = link.support_claim_ids?.includes(claim.id);
    const counter = link.counter_claim_ids?.includes(claim.id);
    const context = link.context_claim_ids?.includes(claim.id);
    if (!support && !counter && !context) return [];
    return [{ key: link.key, label: SCENARIOS.find(item => item.key === link.key)?.label || link.key,
      role: support && counter ? 'mixed' : counter ? 'counter' : support ? 'support' : 'context' }];
  });
}

function prioritizedClaims(research: PlatformResearch) {
  const score = (claim: PlatformResearchClaim) => (claim.freshness === 'current' ? 8 : 0)
    + (['full', 'excerpt'].includes(claim.body_state) ? 3 : 0) + (claim.transmission_basis === 'source_explicit' ? 2 : 0)
    + (research.relations.some(relation => claim.relation_ids.includes(relation.id) && relation.kind === 'company' && relation.verification === 'verified') ? 4 : 0)
    + (claim.role !== 'context' ? 1 : 0);
  const sorted = [...research.claims].sort((a, b) => score(b) - score(a) || (b.as_of || '').localeCompare(a.as_of || ''));
  const seen = new Set<string>();
  // One paragraph can match several dimensions. Show its evidence once in the compact overview.
  return sorted.filter(claim => {
    const key = claim.citations?.length ? claim.citations.map(citation => citation.id).sort().join('|') : `${claim.document_ids.join('|')}:${claim.claim}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

function Track({ claim, research, index, onClaim, onSource }: {
  claim: PlatformResearchClaim; research: PlatformResearch; index: number;
  onClaim: Props['onClaim']; onSource: Props['onSource'];
}) {
  const document = research.documents.find(item => claim.document_ids.includes(item.id));
  const source = research.sources.find(item => claim.source_ids.includes(item.id));
  const connections = scenarioConnections(claim, research);
  const background = !isPlatformDirectional(claim, research);
  const sourceId = claim.citations?.[0]?.source_id || claim.source_ids?.[0];
  const quotation = claim.citations?.[0]?.quote || claim.claim;
  const documentTitle = document?.title || source?.label;
  const compactText = (value: string) => value.replace(/[\s。.!！？?]/g, '');
  const showDocumentTitle = documentTitle && !compactText(quotation).includes(compactText(documentTitle));
  const freshnessLabel = claim.freshness === 'historical' ? '历史资料 · 仅作背景' : claim.freshness === 'unknown' ? '时点待核 · 仅作背景' : '';
  return <article className="si-platform-track" data-background={background}>
    <header className="si-platform-track-head"><div><span className="si-platform-track-index">{String(index + 1).padStart(2, '0')}</span><h5>{claim.topic}</h5><span className="si-platform-relation">{relationLabel(claim, research)}</span></div><button type="button" className="si-text-button" onClick={() => onClaim(claim.id)}>完整论据 ↗</button></header>
    <div className="si-platform-tags"><span>{PLATFORM_ROLE_LABELS[claim.role] || '来源陈述'}</span><span>{PLATFORM_BODY_LABELS[claim.body_state] || '可见范围待核'}</span>{claim.verification === 'source_forecast' && <span>预测观点 · 尚未兑现</span>}{freshnessLabel && <span className="si-platform-history">{freshnessLabel}</span>}<span>待复核</span></div>
    <div className="si-platform-chain">
      <section className="si-platform-step"><h6><span><ResearchIcon name="quote" className="si-step-icon" size={16} /></span>{claim.body_state === 'summary_only' ? '已有解读引用' : claim.body_state === 'teaser' ? '导语引用' : claim.body_state === 'excerpt' ? '原文节选' : claim.body_state === 'title_only' ? '标题线索' : claim.role === 'analysis' ? '平台分析引用' : '原文证据'}</h6><div><blockquote>{quotation}</blockquote>{showDocumentTitle && <p className="si-platform-document">{documentTitle}</p>}</div></section>
      <section className="si-platform-step"><h6><span><ResearchIcon name="business" className="si-step-icon" size={16} /></span>经营传导</h6><div><p>{claim.transmission || '原文尚未提供可核验的经营传导。'}</p><small>{claim.transmission_basis === 'source_explicit' ? '原文表达的传导' : '规则假设 · 需验证'}</small></div></section>
      <section className="si-platform-step"><h6><span><ResearchIcon name="link" className="si-step-icon" size={16} /></span>情景关联</h6><div>{connections.length ? <div className="si-platform-scenario-links">{connections.map(connection => <span key={connection.key} data-role={connection.role}>{connection.label} · {({ support: '条件支持', counter: '反证线索', mixed: '正反线索', context: '研究背景' } as Record<string, string>)[connection.role]}</span>)}</div> : <p className="si-platform-background">{background ? '仅供研究背景，未作为当前情景的方向依据。' : '当前未建立明确情景关联。'}</p>}<small>文字论据未自动调整情景价格</small></div></section>
      <section className="si-platform-step"><h6><span><ResearchIcon name="target" className="si-step-icon" size={16} /></span>接下来验证</h6><div><p>{claim.watch || '原文未给出明确的验证条件。'}</p><small>{claim.watch_basis === 'source_explicit' ? '原文给出的观察条件' : '规则核验清单'}</small></div></section>
    </div>
    <footer className="si-platform-track-source">{sourceId && <button type="button" title={document?.title} onClick={() => onSource(sourceId)}><ResearchIcon name="document" className="si-source-icon" size={16} />{document?.source_name || source?.label || '资料来源'} · {dateLabel(claim.as_of)}{claim.citations?.[0]?.paragraph ? ` · 第 ${claim.citations[0].paragraph} 段` : ''} ↗</button>}</footer>
  </article>;
}

export default function PlatformResearchPanel({ data, onSource, onClaim, onCoverage, onConflict }: Props) {
  const research = data.platform_research;
  const claims = research ? prioritizedClaims(research) : [];
  const primaryClaims = claims.filter(claim => claim.role !== 'context');
  const featured = (primaryClaims.length ? primaryClaims : claims.slice(0, 1)).slice(0, 3);
  const secondary = claims.filter(claim => !featured.some(item => item.id === claim.id));
  const coverage = research?.coverage || [];
  const documents = research?.documents || [];
  const covered = coverage.filter(item => item.read > 0).length;
  return <section className="si-platform-research" aria-label="平台研究主线">
    <header className="si-platform-heading"><div><h4><ResearchIcon name="research" className="si-section-icon" />平台研究主线</h4><p>把当前账号可见的资料，连接到经营假设与验证条件。</p></div><button type="button" className="si-text-button" onClick={onCoverage}><ResearchIcon name="search" className="si-action-icon" size={16} />检索覆盖 ↗</button></header>
    {research && <div className="si-platform-summary"><span>{covered}/{coverage.length} 类来源有可读资料</span><span>{count(documents.length)} 条可见资料</span><span>{count(research.claims.length)} 条候选论据</span>{research.status !== 'available' && <span>覆盖仍有缺口</span>}</div>}
    {claims.length ? <div className="si-platform-tracks">{featured.map((claim, index) => <Track key={claim.id} claim={claim} research={research!} index={index} onClaim={onClaim} onSource={onSource} />)}</div> : <div className="si-platform-empty"><b>{!research ? '平台资料待检索' : documents.length ? '已有可见资料，尚未形成可引用的研究主线' : '当前账号未找到可读的匹配证据'}</b><p>{documents.length ? '仅标题或信息不足的资料保留为线索，不能补造经营结论。' : '平台主线将以可见原文为依据；当前公开行情与新闻单独展示。'}</p>{documents.slice(0, 3).map(document => <button type="button" key={document.id} onClick={() => onSource(document.source_id)}><span>{document.title}</span><small>{PLATFORM_BODY_LABELS[document.body_state]} · {dateLabel(document.published_at)}</small></button>)}</div>}
    {!!secondary.length && <details className="si-platform-more"><summary>更多与背景线索 <span>{count(secondary.length)} 条去重论据</span></summary><div>{secondary.map(claim => <button type="button" key={claim.id} onClick={() => onClaim(claim.id)}><span>{claim.topic}</span><b>{claim.claim}</b><small>{PLATFORM_ROLE_LABELS[claim.role]} · {PLATFORM_BODY_LABELS[claim.body_state]} · {claim.freshness === 'historical' ? '历史资料' : dateLabel(claim.as_of)}</small><i aria-hidden="true">↗</i></button>)}</div></details>}
    {!!research?.conflicts?.length && <div className="si-platform-conflicts"><h5><ResearchIcon name="warning" className="si-section-icon" size={18} />待复核的观点分歧</h5>{research.conflicts.slice(0, 2).map(conflict => <button type="button" key={conflict.id} onClick={() => onConflict(conflict.id)}><span>{conflict.topic} · {conflict.type === 'different_period' ? '报告期不同' : conflict.type === 'unverified_comparability' ? '比较口径待核' : '存在正反线索'}</span><p>{conflict.summary}</p><i aria-hidden="true">查看对照 ↗</i></button>)}</div>}
    {!!coverage.length && <details className="si-platform-coverage"><summary>各类平台资料的读取范围 <span>{count(coverage.length)} 类来源 · 按当前账号权限</span></summary><div className="si-platform-coverage-table"><table><thead><tr><th>来源类别</th><th>本次读取</th><th>可见内容</th><th>检索窗口</th><th>覆盖状态</th></tr></thead><tbody>{coverage.map(item => <tr key={item.key}><th scope="row">{item.label}</th><td data-label="本次读取">{count(item.read)} 条<small>{count(item.selected_claims)} 条候选论据</small></td><td data-label="可见内容">{Object.entries(item.body_states || {}).filter(([, value]) => !!value).map(([state, value]) => `${PLATFORM_BODY_LABELS[state as PlatformResearchBodyState] || state} ${count(value || 0)}`).join(' · ') || '无可读内容'}</td><td data-label="检索窗口">{item.window?.days ? `最近 ${item.window.days} 天` : item.window?.from ? `${dateLabel(item.window.from)} 至 ${dateLabel(item.window.to)}` : '窗口未提供'}<small>资料日期：{item.period?.from ? `${dateLabel(item.period.from)} — ${dateLabel(item.period.to)}` : '无可核验时点'}</small></td><td data-label="覆盖状态"><span>{platformCoverageLabel(item)}</span>{item.reason && <small>{item.reason}</small>}</td></tr>)}</tbody></table></div><button type="button" className="si-text-button" onClick={onCoverage}>查看完整取证范围与缺口 ↗</button></details>}
    {research && <details className="si-platform-method"><summary>分析口径与取证边界</summary><p>{research.method}</p>{!!research.gaps?.length && <ul>{research.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul>}</details>}
  </section>;
}
