import React from 'react';
import ResearchIcon, { type ResearchIconName } from './ResearchIcon';
import { isPlatformDirectional, PLATFORM_BODY_LABELS, PLATFORM_ROLE_LABELS } from './PlatformResearchPanel';
import type { PlatformResearchClaim, ScenarioDriver, ScenarioObservation, ScenarioReasoning as Reasoning, StockIntelligence } from './types';

interface Props {
  reasoning: Reasoning;
  data: StockIntelligence;
  onSource: (id: string) => void;
  onClaim: (id: string) => void;
  expandedDriverKeys?: string[];
  onToggleDriver?: (key: string, open: boolean) => void;
}
const ASSESSMENT = { support: '有支持线索', counter: '存在反证', mixed: '支持与反证并存', unknown: '尚待验证' };
const ICONS: Record<string, ResearchIconName> = { business: 'business', fundamentals: 'fundamentals', earnings: 'earnings', technical: 'technical', capital: 'capital', news: 'news' };
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const numberText = (value: unknown, signed = false) => finite(value) ? `${signed && value > 0 ? '+' : ''}${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '未提供';
const dateText = (value: string | null) => {
  if (!value) return '时点未核验';
  const date = new Date(value);
  return /(Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(date.getTime()) ? date.toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' }) : value.replace('T', ' ').slice(0, 10);
};
function SourceButtons({ ids, data, onSource }: Pick<Props, 'data' | 'onSource'> & { ids: string[] }) {
  const sources = Array.from(new Set(ids)).map(id => data.sources.find(source => source.id === id)).filter(source => !!source);
  if (!sources.length) return null;
  return <div className="si-reasoning-sources">{sources.map(source => <button type="button" key={source!.id} onClick={() => onSource(source!.id)} aria-label={`核对${source!.label}来源`}><ResearchIcon name="link" className="si-source-icon" />{source!.label}<ResearchIcon name="arrow-up-right" className="si-action-icon" /></button>)}</div>;
}

/** A new explanation may only navigate to the exact visible document revision already in this response. */
function boundClaim(id: string, data: StockIntelligence): PlatformResearchClaim | undefined {
  const research = data.platform_research;
  const claim = research?.claims.find(item => item.id === id);
  if (!claim?.citations?.length || !research) return undefined;
  return claim.citations.every(citation => citation.quote.trim() && claim.document_ids.includes(citation.document_id) && claim.source_ids.includes(citation.source_id)
    && research.documents.some(document => document.id === citation.document_id && document.revision === citation.document_revision && document.source_id === citation.source_id)
    && research.citations.some(visible => visible.id === citation.id && visible.source_id === citation.source_id && visible.document_id === citation.document_id && visible.document_revision === citation.document_revision && visible.quote === citation.quote && visible.paragraph === citation.paragraph && visible.start === citation.start && visible.end === citation.end)
    && data.sources.some(source => source.id === citation.source_id)) ? claim : undefined;
}
function ClaimGroups({ driver, data, onClaim }: Pick<Props, 'data' | 'onClaim'> & { driver: ScenarioDriver }) {
  const directional = (id: string) => { const claim = boundClaim(id, data); return claim && data.platform_research && isPlatformDirectional(claim, data.platform_research) ? claim : undefined; };
  const support = Array.from(new Set(driver.platform_claim_ids)).map(directional).filter((claim): claim is PlatformResearchClaim => !!claim);
  const counter = Array.from(new Set(driver.counter_claim_ids)).map(directional).filter((claim): claim is PlatformResearchClaim => !!claim);
  const backgroundIds = Array.from(new Set([...driver.context_claim_ids, ...driver.platform_claim_ids.filter(id => !support.some(claim => claim.id === id)), ...driver.counter_claim_ids.filter(id => !counter.some(claim => claim.id === id))]));
  const context = backgroundIds.map(id => boundClaim(id, data)).filter((claim): claim is PlatformResearchClaim => !!claim);
  const groups = [{ key: 'support', label: '支持该情景的公司论据', claims: support }, { key: 'counter', label: '挑战该情景的公司反证', claims: counter }, { key: 'context', label: '历史、摘要与关联背景', claims: context }];
  return <div className="si-reasoning-claims">{groups.map(group => <section key={group.key} data-evidence-role={group.key}><h6>{group.label}</h6>{group.claims.length ? group.claims.map(claim => <article key={claim.id}><button type="button" className="si-platform-claim-link" onClick={() => onClaim(claim.id)} aria-label={`查看论据：${claim.claim}`}>{claim.claim}<ResearchIcon name="arrow-up-right" className="si-action-icon" /></button><p className="si-reasoning-meta">{PLATFORM_ROLE_LABELS[claim.role]} · {PLATFORM_BODY_LABELS[claim.body_state]} · {dateText(claim.as_of)}{group.key === 'context' ? ' · 不作当前方向依据' : ' · 来源表达仍需复核'}</p></article>) : <p className="si-reasoning-empty">{group.key === 'context' ? '本维度没有可绑定的背景引用。' : '本次未取得可引用的对应公司论据。'}</p>}</section>)}</div>;
}
function Observation({ item, data, onSource, onClaim }: Pick<Props, 'data' | 'onSource' | 'onClaim'> & { item: ScenarioObservation }) {
  const label = item.status === 'stale' ? '历史信息 · 不作当前方向依据' : item.status === 'missing' ? '数据缺口' : item.status === 'unknown' ? '状态待核验' : item.role === 'fact' ? item.origin === 'public' ? '公开数据事实' : '原文事实陈述 · 仍需交叉核验' : PLATFORM_ROLE_LABELS[item.role];
  const claims = Array.from(new Set(item.platform_claim_ids)).map(id => boundClaim(id, data)).filter((claim): claim is PlatformResearchClaim => !!claim);
  return <article className="si-reasoning-observation" data-status={item.status}><div><b>{item.label}</b><span>{label}</span></div><p>{item.statement}</p><p className="si-reasoning-meta">{item.origin === 'public' ? '公共信息' : '平台资料'} · {dateText(item.as_of)}</p>{claims.map(claim => <button type="button" key={claim.id} className="si-text-button" onClick={() => onClaim(claim.id)} aria-label={`核对${item.label}平台原文`}>核对平台原文<ResearchIcon name="arrow-up-right" className="si-action-icon" /></button>)}{!claims.length && <SourceButtons ids={item.source_ids} data={data} onSource={onSource} />}</article>;
}
function TextList({ items, label }: { items: string[]; label: string }) {
  if (!items.length) return null;
  return <section className="si-reasoning-checks"><h6>{label}</h6><ul className="si-detail-list">{Array.from(new Set(items)).map((item, index) => <li key={index}>{item}</li>)}</ul></section>;
}

export default function ScenarioReasoning({ reasoning, data, onSource, onClaim, expandedDriverKeys, onToggleDriver }: Props) {
  const bridge = reasoning.numeric_bridge;
  const usable = bridge.mode !== 'insufficient' && finite(bridge.price) && bridge.price > 0;
  return <div className="si-scenario-reasoning">
    <p className="si-reasoning-headline">{reasoning.headline}</p>
    <section className="si-detail-block si-reasoning-bridge" aria-label="价格形成过程"><h4><ResearchIcon name="valuation" className="si-section-icon" />价格形成过程<span className="si-model-tag">固定压力假设</span></h4><p className="si-detail-caption">经营资料用于检验假设是否成立；技术与资金用于交易确认。</p>
      {usable ? <><div className="si-formula">{bridge.formula}</div><dl className="si-reasoning-inputs">{bridge.mode === 'ready' && <><div><dt>{bridge.forecast_year || '预测年度待核'} 基准 EPS</dt><dd>{numberText(bridge.base_eps)} <small>{bridge.currency}</small></dd></div><div><dt>情景 EPS</dt><dd>{numberText(bridge.scenario_eps)} <small>{bridge.currency}</small></dd></div></>}<div><dt>基准 TTM PE</dt><dd>{numberText(bridge.base_pe_ttm)} <small>倍</small></dd></div><div><dt>情景 PE</dt><dd>{numberText(bridge.scenario_pe_ttm)} <small>倍</small></dd></div></dl><div className="si-reasoning-factors"><span>盈利变化 <b>{numberText(bridge.eps_factor)}×</b></span><span>倍数变化 <b>{numberText(bridge.pe_factor)}×</b></span></div><dl className="si-price-bridge"><div><dt>{bridge.mode === 'sensitivity' ? '当前价格基准' : '基准 EPS × TTM PE'}</dt><dd>{numberText(bridge.base_price)} <small>{bridge.currency}</small></dd></div><div><dt>盈利单独作用</dt><dd>{numberText(bridge.eps_effect, true)} <small>{bridge.currency}</small></dd></div><div><dt>倍数单独作用</dt><dd>{numberText(bridge.pe_effect, true)} <small>{bridge.currency}</small></dd></div><div><dt>二者交互作用</dt><dd>{numberText(bridge.combined_effect, true)} <small>{bridge.currency}</small></dd></div><div className="si-price-bridge-total"><dt>合计情景价</dt><dd>{numberText(bridge.price)} <small>{bridge.currency}</small></dd></div></dl>{bridge.reference_as_of && <p className="si-reasoning-meta">参考价 {numberText(bridge.reference_price)} {bridge.currency} · {dateText(bridge.reference_as_of)}</p>}</> : <p className="si-reasoning-empty">可核验的数值输入不足，暂不估价；下方仍可核对经营条件与证据。</p>}
      {bridge.forecast_sample && <section className="si-forecast-sample" aria-label="卖方盈利样本对照"><h5><ResearchIcon name="earnings" className="si-step-icon" />卖方盈利样本对照</h5><p>{bridge.forecast_sample.year || '年度待核验'} 年 EPS：{numberText(bridge.forecast_sample.eps_low)} — {numberText(bridge.forecast_sample.eps_high)} {bridge.forecast_sample.currency}<br />中位数 {numberText(bridge.forecast_sample.eps_median)} {bridge.forecast_sample.currency}{finite(bridge.forecast_sample.broker_count) && ` · ${bridge.forecast_sample.broker_count} 家机构样本`}</p><p className="si-reasoning-meta">截至 {dateText(bridge.forecast_sample.as_of)} · {({ within: '情景 EPS 在本次样本区间内', above: '情景 EPS 高于本次样本上沿，需要更强的盈利兑现', below: '情景 EPS 低于本次样本下沿，属于压力条件', unknown: '情景与样本可比性尚待核验' })[bridge.forecast_sample.scenario_vs_sample]}</p><p className="si-reasoning-meta">{bridge.forecast_sample.note}</p><SourceButtons ids={bridge.forecast_sample.source_ids} data={data} onSource={onSource} /></section>}
      <p className="si-detail-caption">{bridge.note}</p><SourceButtons ids={bridge.source_ids} data={data} onSource={onSource} /><TextList label="数值输入缺口" items={bridge.gaps} />
    </section>
    <section className="si-detail-block si-reasoning-drivers" aria-label="六维情景传导"><h4><ResearchIcon name="research" className="si-section-icon" />六维原因与依据</h4><p className="si-detail-caption">展开每个维度，核对已取得的信息、需要兑现的变化及其传导。资料量不代表实现概率。</p>{reasoning.drivers.map(driver => <details key={driver.key} className={`si-reasoning-driver si-reasoning-driver--${driver.key}`} open={expandedDriverKeys?.includes(driver.key)} onToggle={event => onToggleDriver?.(driver.key, event.currentTarget.open)}><summary><span className="si-reasoning-driver-title"><ResearchIcon name={ICONS[driver.key] || 'document'} className="si-section-icon" /><b>{driver.label}</b><span data-assessment={driver.assessment}>{ASSESSMENT[driver.assessment]}</span><ResearchIcon name="chevron-down" className="si-action-icon" /></span><span className="si-reasoning-driver-preview">{driver.observed[0]?.statement || '尚缺可核验的对应信息。'}</span>{driver.observed[0]?.status === 'stale' && <span className="si-reasoning-preview-note">历史信息，不作当前方向依据</span>}{['technical', 'capital'].includes(driver.key) && <span className="si-reasoning-preview-note">交易确认维度，不直接改写 EPS 或情景价</span>}</summary><div className="si-reasoning-driver-body"><section className="si-reasoning-observed"><h5>已取得的信息</h5>{driver.observed.length ? driver.observed.map(item => <Observation key={item.id} item={item} data={data} onSource={onSource} onClaim={onClaim} />) : <p className="si-reasoning-empty">本次没有可核验的数据，不补零或推断方向。</p>}</section><div className="si-reasoning-chain"><section><h5><ResearchIcon name="target" className="si-step-icon" />需要兑现的变化</h5><p>{driver.condition}</p></section><section><h5><ResearchIcon name="flow" className="si-step-icon" />{['technical', 'capital'].includes(driver.key) ? '对交易确认的传导' : '对盈利与估值的传导'}</h5><p>{driver.transmission}</p><small>条件推演，尚不代表假设已兑现。</small></section></div><ClaimGroups driver={driver} data={data} onClaim={onClaim} /><TextList label="接下来核验" items={driver.watch} /><TextList label="哪些变化会推翻判断" items={driver.invalidation} /><TextList label="本维度的资料限制" items={driver.limitations} /><SourceButtons ids={driver.source_ids} data={data} onSource={onSource} /></div></details>)}</section>
    <section className="si-detail-block"><h4><ResearchIcon name="target" className="si-section-icon" />下一步核验与失效条件</h4><TextList label="优先核验" items={reasoning.watch} /><TextList label="情景失效观察" items={reasoning.invalidation} />{data.scenario_reasoning?.method && <p className="si-detail-caption">{data.scenario_reasoning.method}</p>}<details className="si-reasoning-method"><summary>方法与本次资料限制</summary><TextList label="本次解释的边界" items={[...reasoning.limitations, ...(data.scenario_reasoning?.limitations || [])]} /></details></section>
  </div>;
}
