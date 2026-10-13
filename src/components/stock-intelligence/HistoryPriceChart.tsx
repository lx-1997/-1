import React, { useId, useMemo, useState } from 'react';
import type { IntelligenceSource, StockIntelligence } from './types';

const WINDOWS = [{ label: '1月', days: 31 }, { label: '3月', days: 93 }, { label: '1年', days: 366 }];
const number = (value: number) => value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function HistoryPriceChart({ technical, currency, source, onSource }: {
  technical: StockIntelligence['technical']; currency: string; source?: IntelligenceSource; onSource: (id: string) => void;
}) {
  const [windowIndex, setWindowIndex] = useState(1);
  const [cursor, setCursor] = useState<number | null>(null);
  const id = useId();
  const series = useMemo(() => Array.from(new Map((technical?.series || []).filter(point => {
    const time = Date.parse(`${point.date}T00:00:00Z`);
    return typeof point.close === 'number' && Number.isFinite(point.close) && point.close > 0
      && /^\d{4}-\d{2}-\d{2}$/.test(point.date) && Number.isFinite(time)
      && new Date(time).toISOString().slice(0, 10) === point.date;
  }).map(point => [point.date, point] as const)).values()).sort((a, b) => a.date.localeCompare(b.date)), [technical?.series]);
  const end = series[series.length - 1];
  const cutoff = end ? new Date(Date.parse(`${end.date}T00:00:00Z`) - WINDOWS[windowIndex].days * 86400000).toISOString().slice(0, 10) : '';
  const points = series.filter(point => point.date >= cutoff);
  const selectedIndex = Math.min(Math.max(cursor ?? points.length - 1, 0), points.length - 1);
  const selected = points[selectedIndex];
  const seriesCurrency = technical?.series_currency || '币种未核验';
  const values = points.map(point => point.close);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const padding = Math.max((max - min) * 0.12, max * 0.003, 0.01);
  const floor = Math.max(0, min - padding), ceiling = max + padding;
  const x = (index: number) => 8 + index / Math.max(1, points.length - 1) * 566;
  const y = (value: number) => 18 + (ceiling - value) / Math.max(0.01, ceiling - floor) * 177;
  const path = points.map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(2)} ${y(point.close).toFixed(2)}`).join(' ');
  const area = points.length ? `${path} L${x(points.length - 1)} 195 L8 195 Z` : '';
  const returnPct = points.length > 1 ? (points[points.length - 1].close / points[0].close - 1) * 100 : null;
  const move = (index: number) => setCursor(Math.max(0, Math.min(points.length - 1, index)));

  return <section className="si-history-panel" aria-label="历史价格走势">
    <div className="si-panel-head"><h4>价格走势</h4><span>{source?.status === 'stale' && '历史快照 · 数据滞后 · '}{technical?.adjustment || '复权口径待核验'}</span></div>
    {points.length > 1 && selected ? <>
      <div className="si-history-toolbar"><div className="si-chart-readout" aria-live="polite"><b>{number(selected.close)} <small>{seriesCurrency}</small></b><span>{selected.date} 收盘</span></div>
        <div className="si-chart-periods" role="group" aria-label="历史图周期">{WINDOWS.map((item, index) => <button key={item.label} type="button" aria-pressed={windowIndex === index} onClick={() => { setWindowIndex(index); setCursor(null); }}>{item.label}</button>)}</div>
      </div>
      <svg className="si-history-svg" viewBox="0 0 640 230" role="img" tabIndex={0} aria-label="历史收盘图，使用左右方向键查看交易日" aria-describedby={`${id}-chart-description`}
        onPointerMove={event => { const box = event.currentTarget.getBoundingClientRect(); const position = (event.clientX - box.left) / box.width * 640; move(Math.round((position - 8) / 566 * (points.length - 1))); }}
        onPointerLeave={() => setCursor(null)} onKeyDown={event => {
          const next = event.key === 'ArrowLeft' ? selectedIndex - 1 : event.key === 'ArrowRight' ? selectedIndex + 1 : event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : null;
          if (next == null) return; event.preventDefault(); move(next);
        }}>
        <defs><linearGradient id={`${id}-area`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".15" /><stop offset="100%" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
        {[0, 1, 2, 3].map(index => { const value = ceiling - (ceiling - floor) * index / 3; return <g key={index}><line x1="8" x2="574" y1={y(value)} y2={y(value)} className="si-chart-grid" /><text x="586" y={y(value) + 4} className="si-chart-axis">{value.toLocaleString('zh-CN', { maximumFractionDigits: max >= 1000 ? 0 : 2 })}</text></g>; })}
        <path d={area} fill={`url(#${id}-area)`} /><path d={path} fill="none" className="si-history-line" />
        <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1="18" y2="195" className="si-chart-cursor" /><circle cx={x(selectedIndex)} cy={y(selected.close)} r="4" className="si-chart-point" />
        <text x="8" y="221" className="si-chart-axis">{points[0].date}</text><text x="574" y="221" textAnchor="end" className="si-chart-axis">{points[points.length - 1].date}</text>
      </svg>
      <div className="si-chart-bottom"><span>区间收盘涨幅 <b className={returnPct != null && returnPct < 0 ? 'si-negative' : 'si-positive'}>{returnPct == null ? '—' : `${returnPct > 0 ? '+' : ''}${returnPct.toFixed(2)}%`}</b></span><span>{points.length} 个交易日</span></div>
      <p id={`${id}-chart-description`} className="si-chart-source">真实日线收盘 · 截至 {end?.date} · 历史走势不代表未来。<button type="button" onClick={() => onSource(technical?.source_ids?.[0] || 'history')}>查看行情依据 ↗</button></p>
      {technical?.series_currency && technical.series_currency !== currency && <p className="si-chart-source">历史价格以 {technical.series_currency} 计价，与页头报价币种不同，不作同轴比较。</p>}
    </> : <div className="si-chart-unavailable"><span aria-hidden="true">⌁</span><b>暂无可用日线</b><p>历史数据不足，不绘制示意价格走势。</p></div>}
  </section>;
}
