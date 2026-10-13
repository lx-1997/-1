import React, { useId, useMemo, useState } from 'react';
import type { StockIntelligence } from './types';

type FlowKey = 'main_net' | 'super_large_net' | 'large_net' | 'medium_net' | 'small_net';
const FLOWS: Array<{ key: FlowKey; label: string }> = [
  { key: 'main_net', label: '主力' }, { key: 'super_large_net', label: '超大单' }, { key: 'large_net', label: '大单' }, { key: 'medium_net', label: '中单' }, { key: 'small_net', label: '小单' },
];
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
const amountText = (value: number) => `${value > 0 ? '+' : ''}${(value / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
const priceText = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 });

function CapitalFlowChart({ data, onSource }: { data: StockIntelligence; onSource: (id: string) => void }) {
  const capital = data.capital;
  const [category, setCategory] = useState<FlowKey>('main_net');
  const [period, setPeriod] = useState(20);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const id = useId();
  const series = useMemo(() => Array.from(new Map((capital?.series || []).filter(point => validDate(point.date) && FLOWS.some(item => finite(point[item.key]))).map(point => [point.date, point] as const)).values()).sort((a, b) => a.date.localeCompare(b.date)), [capital?.series]);
  const start = Math.max(0, Math.min(selection?.start ?? Math.max(0, series.length - period), series.length - 1));
  const end = Math.max(start, Math.min(selection?.end ?? series.length - 1, series.length - 1));
  const points = series.slice(start, end + 1);
  const selectedIndex = Math.min(Math.max(cursor ?? points.length - 1, 0), points.length - 1);
  const selected = points[selectedIndex];
  const shownValues = points.map(point => point[category]).filter(finite);
  const total = shownValues.reduce((sum, value) => sum + value, 0);
  const maximum = Math.max(...shownValues.map(Math.abs), 1);
  const canPrice = ['CNY', 'HKD', 'USD'].includes(capital?.currency || '') && capital?.currency === capital?.price_currency;
  const prices = canPrice ? points.map(point => point.close).filter((value): value is number => finite(value) && value > 0) : [];
  const lowPrice = prices.length ? Math.min(...prices) : 0, highPrice = prices.length ? Math.max(...prices) : 0;
  const pricePadding = Math.max((highPrice - lowPrice) * .1, highPrice * .003, .01);
  const minPrice = Math.max(0, lowPrice - pricePadding), maxPrice = highPrice + pricePadding;
  const x = (index: number) => 58 + (index + .5) / Math.max(points.length, 1) * 514;
  const y = (value: number) => 115 - value / maximum * 84;
  const priceY = (value: number) => 24 + (maxPrice - value) / Math.max(maxPrice - minPrice, .01) * 180;
  let pricePath = '', previousPrice = false;
  points.forEach((point, index) => {
    const present = canPrice && finite(point.close) && point.close > 0;
    if (present) pricePath += `${previousPrice ? 'L' : 'M'}${x(index).toFixed(2)} ${priceY(point.close!).toFixed(2)} `;
    previousPrice = present;
  });
  const categories = FLOWS.filter(item => series.some(point => finite(point[item.key])));
  const move = (index: number) => setCursor(Math.max(0, Math.min(points.length - 1, index)));
  const hasData = points.length > 0 && capital?.amount_unit === '元';

  return <section className="si-flow-panel" aria-label="资金与股价联动">
    <div className="si-panel-head"><h4>资金与股价联动</h4><span>{capital?.status === 'stale' ? '历史快照 · 数据滞后' : '日资金分类统计'}</span></div>
    {hasData && selected ? <>
      <div className="si-flow-summary"><div><span>所选区间 · {FLOWS.find(item => item.key === category)?.label}净额</span><b className={total < 0 ? 'si-negative' : total > 0 ? 'si-positive' : 'si-flat'}>{shownValues.length ? amountText(total) : '未提供'} <small>{shownValues.length > 0 && `亿元 · ${capital.currency || '币种未核验'}`}</small></b></div><div className="si-chart-periods" role="group" aria-label="资金图交易日区间">{[5, 20, 60].map(days => <button type="button" key={days} aria-pressed={!selection && period === days} onClick={() => { setPeriod(days); setSelection(null); setCursor(null); }}>{days}日</button>)}</div></div>
      <div className="si-flow-categories" role="group" aria-label="资金分类">{categories.map(item => <button type="button" key={item.key} aria-pressed={category === item.key} onClick={() => { setCategory(item.key); setCursor(null); }}>{item.label}</button>)}</div>
      <div className="si-flow-legend"><span><i className="si-legend-positive" />净流入</span><span><i className="si-legend-negative" />净流出</span>{prices.length > 1 && <span><i className="si-legend-price" />同日收盘 · {capital.price_currency}</span>}<span>左轴：亿元</span></div>
      <div className="si-flow-readout" aria-live="polite"><time>{selected.date}</time><span>{FLOWS.find(item => item.key === category)?.label}净额 <b className={finite(selected[category]) && selected[category]! < 0 ? 'si-negative' : finite(selected[category]) && selected[category]! > 0 ? 'si-positive' : 'si-flat'}>{finite(selected[category]) ? `${amountText(selected[category]!)} 亿元` : '未提供'}</b></span><span>同日收盘 <b>{canPrice && finite(selected.close) && selected.close > 0 ? `${priceText(selected.close)} ${capital.price_currency}` : '未提供可比价格'}</b></span></div>
      {shownValues.length ? <svg className="si-flow-svg" viewBox="0 0 640 245" role="img" tabIndex={0} aria-label="每日资金净额与同日收盘图，使用左右方向键查看交易日" aria-describedby={`${id}-flow-description`}
        onPointerMove={event => { const box = event.currentTarget.getBoundingClientRect(); move(Math.floor(((event.clientX - box.left) / box.width * 640 - 58) / 514 * points.length)); }} onPointerLeave={() => setCursor(null)} onKeyDown={event => {
          const next = event.key === 'ArrowLeft' ? selectedIndex - 1 : event.key === 'ArrowRight' ? selectedIndex + 1 : event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : null;
          if (next == null) return; event.preventDefault(); move(next);
        }}>
        {[-1, -.5, 0, .5, 1].map(ratio => <g key={ratio}><line x1="58" x2="572" y1={y(maximum * ratio)} y2={y(maximum * ratio)} className={ratio === 0 ? 'si-flow-zero' : 'si-chart-grid'} /><text x="47" y={y(maximum * ratio) + 4} textAnchor="end" className="si-chart-axis">{(maximum * ratio / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}</text></g>)}
        {points.map((point, index) => finite(point[category]) ? <rect key={point.date} x={x(index) - Math.min(514 / points.length * .65, 22) / 2} y={Math.min(y(point[category]!), 115)} width={Math.min(514 / points.length * .65, 22)} height={Math.max(Math.abs(y(point[category]!) - 115), .6)} className={point[category]! < 0 ? 'si-flow-bar-negative' : point[category]! > 0 ? 'si-flow-bar-positive' : 'si-flow-bar-zero'} /> : null)}
        {prices.length > 1 && <><path d={pricePath} fill="none" className="si-flow-price-line" />{[0, .5, 1].map(ratio => <text key={ratio} x="587" y={priceY(maxPrice - (maxPrice - minPrice) * ratio) + 4} className="si-chart-axis">{(maxPrice - (maxPrice - minPrice) * ratio).toLocaleString('zh-CN', { maximumFractionDigits: highPrice >= 1000 ? 0 : 2 })}</text>)}</>}
        <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1="24" y2="204" className="si-chart-cursor" />
        <text x="58" y="232" className="si-chart-axis">{points[0].date}</text><text x="572" y="232" textAnchor="end" className="si-chart-axis">{points[points.length - 1].date}</text>
      </svg> : <div className="si-market-empty si-flow-interval-empty"><b>当前区间暂无{FLOWS.find(item => item.key === category)?.label}净额</b><p>切换分类或扩大交易区间，缺失金额不按零处理。</p></div>}
      <div className="si-flow-window"><span>{points[0].date} — {points[points.length - 1].date}</span><span>{points.length} 个有效交易日{shownValues.length < points.length ? ` · ${shownValues.length} 日有分类数据` : ''}</span></div>
      {series.length > 1 && <div className="si-flow-brush"><div className="si-flow-brush-title"><span>拖动选择交易区间</span><button type="button" onClick={() => { setSelection({ start: 0, end: series.length - 1 }); setCursor(null); }}>全部 {series.length} 日</button></div><div className="si-flow-sliders"><label>开始 <input type="range" aria-label="资金图开始交易日" aria-valuetext={series[start]?.date} min={0} max={series.length - 1} value={start} onChange={event => { setSelection({ start: Math.min(Number(event.target.value), end), end }); setCursor(null); }} /><time>{series[start]?.date}</time></label><label>结束 <input type="range" aria-label="资金图结束交易日" aria-valuetext={series[end]?.date} min={0} max={series.length - 1} value={end} onChange={event => { setSelection({ start, end: Math.max(start, Number(event.target.value)) }); setCursor(null); }} /><time>{series[end]?.date}</time></label></div></div>}
      <p id={`${id}-flow-description`} className="si-chart-source">成交分类净额，不能证明特定机构买卖。{prices.length > 1 && ` 价格：${capital.price_adjustment}，右轴 ${capital.price_currency}。`}{!canPrice && ' 价格币种未核验或不一致，未叠加价格线。'}<button type="button" onClick={() => onSource(capital.source_ids[0] || 'flow')}>查看资金依据 ↗</button></p>
      {capital.note && <details className="si-market-method"><summary>统计口径与数据覆盖</summary><p>{capital.note}</p></details>}
    </> : <div className="si-market-empty"><span aria-hidden="true">↕</span><b>暂无可核验的日资金序列</b><p>{data.market === 'CN' ? '交易日期或金额缺失，暂不绘制资金走势。' : '当前市场暂无可核验的日资金分类数据。'}</p><button type="button" className="si-text-button" onClick={() => onSource(capital?.source_ids?.[0] || 'flow')}>查看数据覆盖 ↗</button></div>}
  </section>;
}

function BrokerRatingChart({ data, onSource, onRatings }: { data: StockIntelligence; onSource: (id: string) => void; onRatings: (key?: string) => void }) {
  const ratings = data.broker_ratings;
  const groups = (ratings?.distribution || []).filter(group => Number.isInteger(group.count) && group.count > 0);
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  const samples = ratings?.samples || [];
  const valid = !!ratings && ['available', 'stale'].includes(ratings.status) && total > 0 && total === ratings.sample_count && samples.length === total
    && new Set(groups.map(group => group.key)).size === groups.length
    && samples.every(sample => !!sample.broker_id && validDate(sample.published_at.slice(0, 10)))
    && new Set(samples.map(sample => sample.broker_id)).size === total && ratings?.broker_count === total
    && groups.every(group => samples.filter(sample => sample.rating_key === group.key).length === group.count);
  const majorityCount = Math.max(...groups.map(group => group.count), 0);
  const majorityGroups = groups.filter(group => group.count === majorityCount);
  const radius = 53, circumference = Math.PI * 2 * radius;
  let offset = 0;
  return <section className="si-ratings-panel" aria-label="券商评级分布">
    <div className="si-panel-head"><h4>券商评级分布</h4><span>{ratings?.status === 'stale' ? '历史样本 · 数据滞后' : '公开卖方研究'}</span></div>
    {valid ? <>
      <div className="si-rating-overview"><div className="si-rating-ring"><svg viewBox="0 0 144 144" role="img" aria-label={`券商评级分布，共 ${total} 条最新评级样本`}><circle cx="72" cy="72" r={radius} className="si-rating-track" />{groups.map(group => {
        const length = group.count / total * circumference;
        const start = offset; offset += length;
        return <circle key={group.key} cx="72" cy="72" r={radius} fill="none" strokeWidth="17" strokeDasharray={`${length} ${circumference - length}`} strokeDashoffset={-start} transform="rotate(-90 72 72)" className={`si-rating-segment si-rating--${group.key}`}><title>{group.label} {group.count} 条</title></circle>;
      })}</svg><div><b>{total}</b><span>条最新评级</span></div></div><div className="si-rating-distribution">{groups.map(group => <button type="button" key={group.key} aria-label={`查看${group.label}评级样本`} onClick={() => onRatings(group.key)}><span><i className={`si-rating-dot si-rating--${group.key}`} />{group.label}</span><b>{group.count}<small> 条</small></b><span>{(group.count / total * 100).toFixed(0)}%</span></button>)}</div></div>
      <div className="si-rating-takeaway"><span>样本中最多{majorityGroups.length > 1 ? '（并列）' : ''}</span><b>{majorityGroups.map(group => group.label).join(' / ')}</b><span>{ratings!.broker_count} 家机构 · 每机构最新一条</span></div>
      <div className="si-rating-sample-list">{[...samples].sort((a, b) => b.published_at.localeCompare(a.published_at)).slice(0, 3).map(sample => <button type="button" key={sample.broker_id} onClick={() => onRatings(sample.rating_key)}><span>{sample.broker}</span><b className={`si-rating-text--${sample.rating_key}`}>{sample.rating}</b><time>{sample.published_at.slice(0, 10)}</time></button>)}</div>
      <button type="button" className="si-text-button si-rating-all" onClick={() => onRatings()}>查看全部 {total} 条评级样本 ↗</button>
      <p className="si-chart-source">每家机构最新观点 · 非全市场覆盖，非上涨概率。<br />样本日期 {ratings!.period.from || '未提供'} — {ratings!.period.to || '未提供'}。<button type="button" onClick={() => onSource(ratings!.source_ids[0] || 'broker-ratings')}>查看评级来源 ↗</button></p>
    </> : <div className="si-market-empty"><span aria-hidden="true">◌</span><b>暂无可核验的评级分布</b><p>需要机构、发布日期与明确评级记录，才能统计分布。</p><button type="button" className="si-text-button" onClick={() => onSource(ratings?.source_ids?.[0] || 'broker-ratings')}>查看数据覆盖 ↗</button></div>}
  </section>;
}

export default function MarketEvidenceCharts({ data, onSource, onRatings }: { data: StockIntelligence; onSource: (id: string) => void; onRatings: (key?: string) => void }) {
  return <div className="si-market-charts"><CapitalFlowChart data={data} onSource={onSource} /><BrokerRatingChart data={data} onSource={onSource} onRatings={onRatings} /></div>;
}
