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
  position_pct?: number;
  position_band?: string;
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

/** 仓位指引五档：名称 / 区间 / 阶段含义 */
const POSITION_BANDS: { name: string; max: number; desc: string }[] = [
  { name: '空仓观望', max: 20, desc: '市场冰点：以防守为主，等待企稳信号' },
  { name: '轻仓', max: 40, desc: '边际改善：试探性参与，严格控制回撤' },
  { name: '半仓', max: 60, desc: '多空均衡：均衡配置，进可攻退可守' },
  { name: '偏重', max: 80, desc: '趋势向好：积极配置，跟随量能确认' },
  { name: '重仓', max: 100, desc: '情绪沸点：全面进攻，注意过热兑现' },
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
      } catch { /* 静默 */ }
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
            <span className="mp-pos-band">仓位 {gauge!.position_band || '—'}</span>
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
              <span className="mp-k">仓位指引</span>
              <span className="mp-pos-row">
                <span className="mp-pos-num">{gauge.position_pct ?? '—'}%</span>
                <span className="mp-pos-band">{gauge.position_band || '—'}</span>
              </span>
            </div>
            <Meter value={gauge.position_pct ?? gauge.temperature} leftLabel="空仓" rightLabel="满仓" />
            <div className="mp-bands">
              {POSITION_BANDS.map(b => (
                <span
                  key={b.name}
                  className={`mp-band ${gauge.position_band === b.name ? 'on' : ''}`}
                  title={`${b.name}（≤${b.max}%）：${b.desc}`}
                >
                  {b.name}
                </span>
              ))}
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
            <div className="mp-row">
              <span className="mp-k">资金热度</span>
              {(gauge.temperature_trend?.length || 0) >= 2 && (
                <Sparkline values={(gauge.temperature_trend || []).map(t => t.heat_score ?? 50)} width={64} height={16} />
              )}
              <span className={`mp-state ${HEAT_POSITIVE.has(gauge.heat_state || '') ? 'up' : HEAT_NEGATIVE.has(gauge.heat_state || '') ? 'down' : ''}`}>
                {gauge.heat_state || '未知'}
              </span>
            </div>
            <div className="mp-disc">规则化推导 · 仅供参考</div>
          </div>
        ) : (
          <div className="mp-empty">市场状态暂不可用</div>
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
