import React, { useEffect, useState } from 'react';
import './MarketPulse.css';

/** 市场状态仪表 + 宏观数据日历（右栏卡）。数据源为公开端点，匿名可读。 */

interface GaugeData {
  available: boolean;
  as_of?: string;
  index?: string;
  close?: number;
  chg_1d_pct?: number | null;
  tech_state?: string;
  heat_state?: string;
  heat_ratio?: number | null;
  temperature?: number;
  regime_label?: string;
  signal_usable?: boolean;
  freshness?: string;
  reason?: string;
  model_version?: string;
  universe?: string[];
  warnings?: string[];
  sources?: { name: string; provider: string; as_of: string | null; cross_checked: boolean; conflict: boolean }[];
  temperature_trend?: {
    date: string;
    temperature: number;
    close?: number;
    tech_state?: string;
    tech_score?: number;
    heat_state?: string;
    heat_ratio?: number | null;
    heat_score?: number;
  }[];
  temp_delta_1d?: number | null;
  dimensions?: { key: string; name: string; score: number | null; weight: number; used: boolean }[];
  backtest?: {
    available: boolean;
    reason?: string;
    days?: number;
    start?: string;
    end?: string;
    split_date?: string;
    evaluation?: string;
    execution?: string;
    validation?: string;
    limitations?: string[];
    holdout?: {
      excess_pct: number;
      strategy: Experiment;
      benchmark: Experiment;
      fixed_half: Experiment;
    };
    folds?: { start: string; end: string; days: number; return_pct: number; benchmark_pct: number; excess_pct: number }[];
    cost_sensitivity?: { cost_bps_per_side: number; return_pct: number; excess_pct: number }[];
  };

}

interface Experiment {
  days: number;
  return_pct: number;
  max_drawdown_pct: number;
  sharpe: number | null;
  curve: { date: string; nav: number }[];
}

function Backtest({ data }: { data: NonNullable<GaugeData['backtest']> }) {
  if (!data.available || !data.holdout) return <div className="mp-disc">回测暂不可用：{data.reason || '共同历史不足'}</div>;
  const h = data.holdout;
  return <details className="mp-bt">
    <summary>固定规则回测 · {h.strategy.days} 日留出检验</summary>
    <div className="mp-bt-note">{data.split_date} 至 {data.end} · 四指数同口径</div>
    <table className="mp-bt-table"><thead><tr><th>模拟对照</th><th>收益</th><th>最大回撤</th></tr></thead>
      <tbody>{[['环境规则', h.strategy], ['沪深300持有', h.benchmark], ['固定半仓', h.fixed_half]].map(([name, result]) => {
        const r = result as Experiment;
        return <tr key={name as string}><td>{name as string}</td><td>{r.return_pct.toFixed(2)}%</td><td>{r.max_drawdown_pct.toFixed(2)}%</td></tr>;
      })}</tbody></table>
    <div className="mp-bt-row">相对持有：{h.excess_pct > 0 ? '+' : ''}{h.excess_pct.toFixed(2)} 个百分点</div>
    <div className="mp-bt-row">{data.validation}</div>
    <div className="mp-bt-note">双边成本与滑点假设：每边 5bp；{data.execution}。</div>
    <div className="mp-bt-note">留出段分窗对照</div>
    {(data.folds || []).map(f => <div className="mp-bt-row" key={f.start}>{f.start} · {f.days}日 · 规则 {f.return_pct.toFixed(2)}% / 持有 {f.benchmark_pct.toFixed(2)}%</div>)}
    <div className="mp-bt-note">成本敏感性（每边）</div>
    {(data.cost_sensitivity || []).map(c => <div className="mp-bt-row" key={c.cost_bps_per_side}>{c.cost_bps_per_side}bp · 规则收益 {c.return_pct.toFixed(2)}%</div>)}
    <div className="mp-bt-note">实验仓位档位：25/50/75/100%，由前一日分数按35/55/70分界；不作为当前仓位建议。</div>
    {(data.limitations || []).map(t => <div className="mp-bt-note" key={t}>{t}</div>)}
  </details>;
}

function Sparkline({ values, width = 96, height = 26 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const w = width;
  const h = height;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * (w - 4) + 2;
    const y = h - 3 - ((v - min) / span) * (h - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const rising = values[values.length - 1] >= values[0];
  const stroke = rising ? 'var(--up, #2bd96a)' : 'var(--down, #ff5d5d)';
  return (
    <svg className="mp-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polyline points={pts.join(' ')} fill="none" stroke={stroke} strokeWidth="1.6" />
      <circle cx={pts[pts.length - 1].split(',')[0]} cy={pts[pts.length - 1].split(',')[1]} r="2.2" fill={stroke} />
    </svg>
  );
}

function TrendChip({ delta }: { delta: number }) {
  const up = delta >= 0;
  return (
    <span className={`mp-delta ${up ? 'up' : 'down'}`} title="较前一交易日">
      {up ? '▲' : '▼'} {Math.abs(delta)}
    </span>
  );
}

interface CalEvent {
  time: string;
  region: string;
  name: string;
  stars?: number;
  prev?: number | null;
  forecast?: number | null;
}

interface CalData {
  today?: { date?: string; events?: CalEvent[] } | null;
  tomorrow?: { date?: string; events?: CalEvent[] } | null;
}

const TECH_POSITIVE = new Set(['上涨右侧', '上涨左侧']);
const TECH_NEGATIVE = new Set(['下跌右侧', '下跌左侧']);
const HEAT_POSITIVE = new Set(['沸点', '偏热']);
const HEAT_NEGATIVE = new Set(['冰点', '偏冷']);

/** 技术形态五段：均线结构的五个阶段 */
const TECH_BANDS: { name: string; desc: string }[] = [
  { name: '下跌左侧', desc: '20日均线低于60日均线，收盘未满足双重下穿' },
  { name: '下跌右侧', desc: '沪深300收盘价低于20日均线，20日均线低于60日均线' },
  { name: '震荡', desc: '均线交织：方向不明，以区间思维应对' },
  { name: '上涨左侧', desc: '20日均线高于60日均线，收盘未满足双重上穿' },
  { name: '上涨右侧', desc: '沪深300收盘价高于20日均线，20日均线高于60日均线' },
];

/** 资金热度五档：当日成交量为 20 日均量的比值区间 */
const HEAT_BANDS: { name: string; desc: string }[] = [
  { name: '冰点', desc: '量能不足均量一半：成交极度萎缩' },
  { name: '偏冷', desc: '量能低于均量：参与意愿偏弱' },
  { name: '中性', desc: '量能接近均量：交投正常' },
  { name: '偏热', desc: '量能高于均量：资金参与度抬升' },
  { name: '沸点', desc: '量能达均量 1.5 倍以上：交投极度活跃，警惕过热' },
];

const fallbackColor = (v: string, fb: string) => `var(${v}, ${fb})`;

function Meter({ value, leftLabel, rightLabel }: { value: number; leftLabel: string; rightLabel: string }) {
  return (
    <div className="mp-meter">
      <span className="mp-meter-l">{leftLabel}</span>
      <span className="mp-meter-track">
        <span className="mp-meter-cursor" style={{ left: `${Math.max(0, Math.min(100, value))}%` }} />
      </span>
      <span className="mp-meter-r">{rightLabel}</span>
    </div>
  );
}

const MarketPulse: React.FC<{ variant?: 'rail' | 'strip' }> = ({ variant = 'rail' }) => {
  const [gauge, setGauge] = useState<GaugeData | null>(null);
  const [cal, setCal] = useState<CalData | null>(null);
  const [day, setDay] = useState<'today' | 'tomorrow'>('today');

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const g = await fetch('/api/market/gauge').then(r => (r.ok ? r.json() : null));
        if (alive && g) setGauge(g);
      } catch { if (alive) setGauge({ available: false, reason: '状态接口暂不可用' }); }
      try {
        const c = await fetch('/api/econ/calendar').then(r => (r.ok ? r.json() : null));
        if (alive && c) setCal(c);
      } catch { /* 静默 */ }
    };
    load();
    const timer = window.setInterval(load, 30 * 60 * 1000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  if (variant === 'strip') {
    const avail = gauge?.available && typeof gauge.temperature === 'number';
    const allEvents = [...((cal?.today?.events) || []), ...((cal?.tomorrow?.events) || [])];
    const stripEvents = allEvents.slice(0, 5);
    return (
      <div className="mp-wrap mp-wrap--strip" role="status">
        {avail ? (
          <span className="mp-chip mp-chip-temp">
            <span className="mp-chip-k">市场温度</span>
            <b className="mp-chip-v" style={{ color: gauge!.temperature! >= 60 ? fallbackColor('--up', '#2bd96a') : gauge!.temperature! <= 30 ? fallbackColor('--down', '#ff5d5d') : fallbackColor('--warn', '#ffb000') }}>{gauge!.temperature}</b>
            {typeof gauge!.temp_delta_1d === 'number' && gauge!.temp_delta_1d !== 0 && <TrendChip delta={gauge!.temp_delta_1d} />}
            <span className={`mp-state ${TECH_POSITIVE.has(gauge!.tech_state || '') ? 'up' : TECH_NEGATIVE.has(gauge!.tech_state || '') ? 'down' : ''}`}>{gauge!.tech_state}</span>
            <span className={`mp-state ${HEAT_POSITIVE.has(gauge!.heat_state || '') ? 'up' : HEAT_NEGATIVE.has(gauge!.heat_state || '') ? 'down' : ''}`}>{gauge!.heat_state}</span>
            <span className="mp-pos-band">{gauge!.regime_label} · 日线 {gauge!.as_of}</span>
          </span>
        ) : (
          <span className="mp-chip mp-chip-temp"><span className="mp-chip-k">市场状态</span><span className="mp-empty">暂不可用</span></span>
        )}
        <span className="mp-divider" />
        <span className="mp-chip mp-chip-k mp-cal-k">数据日历</span>
        {stripEvents.length ? stripEvents.map((e, i) => (
          <span key={i} className="mp-chip mp-ev-chip" title={`${e.region} ${e.name} · 前值 ${e.prev ?? '—'} · 预期 ${e.forecast ?? '—'}`}>
            <span className="mp-ev-time">{e.time}</span>
            <span className="mp-ev-name">{e.name}</span>
            {typeof e.stars === 'number' && <span className="mp-ev-stars">{'★'.repeat(Math.max(1, Math.min(5, e.stars)))}</span>}
          </span>
        )) : <span className="mp-empty">暂无事件</span>}
      </div>
    );
  }

  const events = ((day === 'today' ? cal?.today : cal?.tomorrow)?.events) || [];

  return (
    <div className="mp-wrap">
      <div className="bbt-rail-sec mp-sec">
        <div className="bbt-rail-sec-h">市场状态</div>
        {gauge?.available && typeof gauge.temperature === 'number' ? (
          <div className="mp-body">
            <div className="mp-temp">
              <span className="mp-temp-num" style={{ color: gauge.temperature >= 60 ? fallbackColor('--up', '#2bd96a') : gauge.temperature <= 30 ? fallbackColor('--down', '#ff5d5d') : fallbackColor('--warn', '#ffb000') }}>
                {gauge.temperature}
              </span>
              <span className="mp-temp-unit">市场温度</span>
              {typeof gauge.temp_delta_1d === 'number' && gauge.temp_delta_1d !== 0 && <TrendChip delta={gauge.temp_delta_1d} />}
              {typeof gauge.chg_1d_pct === 'number' && (
                <span className={`mp-chg ${gauge.chg_1d_pct >= 0 ? 'up' : 'down'}`}>
                  {gauge.chg_1d_pct >= 0 ? '+' : ''}{gauge.chg_1d_pct}%
                </span>
              )}
            </div>
            {(gauge.temperature_trend?.length || 0) >= 2 && (
              <div className="mp-trend">
                <Sparkline values={(gauge.temperature_trend || []).map(t => t.temperature)} />
                <span className="mp-trend-cap">近 {(gauge.temperature_trend || []).length} 日趋势</span>
              </div>
            )}
            <Meter value={gauge.temperature} leftLabel="冰点" rightLabel="沸点" />
            <div className="mp-row">
              <span className="mp-k">风险环境</span>
              <strong>{gauge.regime_label || '待核对'}</strong>
            </div>
            <div className={`mp-disc ${gauge.signal_usable === false ? 'mp-quality-warning' : ''}`}>
              已完成日线 · {gauge.as_of} {gauge.freshness === 'lagging' ? '· 数据滞后' : ''}
            </div>
            <div className="mp-row">
              <span className="mp-k">技术形态</span>
              {(gauge.temperature_trend?.length || 0) >= 2 && (
                <Sparkline values={(gauge.temperature_trend || []).map(t => t.tech_score ?? 50)} width={64} height={16} />
              )}
              <span className={`mp-state ${TECH_POSITIVE.has(gauge.tech_state || '') ? 'up' : TECH_NEGATIVE.has(gauge.tech_state || '') ? 'down' : ''}`}>
                {gauge.tech_state || '未知'}
              </span>
            </div>
            <div className="mp-bands mp-bands--tight">
              {TECH_BANDS.map(b => (
                <span
                  key={b.name}
                  className={`mp-band ${gauge.tech_state === b.name ? 'on' : ''}`}
                  title={`${b.name}：${b.desc}`}
                >
                  {b.name}
                </span>
              ))}
            </div>
            <div className="mp-row">
              <span className="mp-k">资金热度</span>
              {(gauge.temperature_trend?.length || 0) >= 2 && (
                <Sparkline values={(gauge.temperature_trend || []).map(t => t.heat_score ?? 50)} width={64} height={16} />
              )}
              <span className={`mp-state ${HEAT_POSITIVE.has(gauge.heat_state || '') ? 'up' : HEAT_NEGATIVE.has(gauge.heat_state || '') ? 'down' : ''}`}>
                {gauge.heat_state || '未知'}
              </span>
            </div>
            <div className="mp-bands mp-bands--tight">
              {HEAT_BANDS.map(b => (
                <span
                  key={b.name}
                  className={`mp-band ${gauge.heat_state === b.name ? 'on' : ''}`}
                  title={`${b.name}：${b.desc}`}
                >
                  {b.name}
                </span>
              ))}
            </div>
            {(gauge.dimensions || []).some(d => d.used && d.key !== 'technique') && (
              <div className="mp-dims">
                {(gauge.dimensions || []).filter(d => d.used && d.key !== 'technique').map(d => (
                  <div className="mp-row" key={d.key}>
                    <span className="mp-k">{d.name}</span>
                    <span className="mp-dim">{d.score ?? '—'}</span>
                  </div>
                ))}
              </div>
            )}
            <details className="mp-bt">
              <summary>数据与评分依据</summary>
              <div className="mp-bt-note">{gauge.universe?.join(' / ')}；共用完成日线，当前分数与10日曲线使用相同规则。</div>
              {(gauge.dimensions || []).map(d => <div className="mp-bt-row" key={d.key}>{d.name} · 权重 {d.weight}% · {d.score}分</div>)}
              {(gauge.sources || []).map(source => <div className="mp-bt-row" key={source.name}>{source.name} · {source.provider === 'eastmoney' ? '东方财富' : source.provider === 'sina' ? '新浪财经' : '不可用'} · {source.as_of || '缺失'} · {source.conflict ? '跨源差异待核对' : source.cross_checked ? '双源已核对' : '单源可用'}</div>)}
              {(gauge.warnings || []).map(w => <div className="mp-bt-note" key={w}>{w}</div>)}
              <div className="mp-bt-note">资金热度是沪深300成交量比，不代表资金净流入；跨指数共振不等于全市场涨跌家数。</div>
              <div className="mp-bt-note">规则版本 {gauge.model_version}</div>
            </details>
            {gauge.backtest && <Backtest data={gauge.backtest} />}
            <div className="mp-disc">描述市场环境 · 不提供精确仓位建议</div>
          </div>
        ) : (
          <div className="mp-empty">{gauge?.reason || '市场状态暂不可用'}</div>
        )}
      </div>

      <div className="bbt-rail-sec mp-sec">
        <div className="bbt-rail-sec-h mp-cal-h">
          <span>数据日历</span>
          <span className="mp-tabs">
            <button type="button" className={day === 'today' ? 'on' : ''} onClick={() => setDay('today')}>今天</button>
            <button type="button" className={day === 'tomorrow' ? 'on' : ''} onClick={() => setDay('tomorrow')}>明天</button>
          </span>
        </div>
        {events.length ? (
          <ul className="mp-cal">
            {events.slice(0, 12).map((e, i) => (
              <li key={i} className="mp-ev">
                <span className="mp-ev-time">{e.time}</span>
                <span className="mp-ev-main">
                  <span className="mp-ev-name" title={e.name}>{e.region} {e.name}</span>
                  <span className="mp-ev-meta">
                    前值 {e.prev ?? '—'} · 预期 {e.forecast ?? '—'}
                    {typeof e.stars === 'number' && <span className="mp-ev-stars">{'★'.repeat(Math.max(1, Math.min(5, e.stars)))}</span>}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mp-empty">暂无日历事件</div>
        )}
      </div>
    </div>
  );
};

export default MarketPulse;
