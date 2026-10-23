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
            <span className={`mp-state ${TECH_POSITIVE.has(gauge!.tech_state || '') ? 'up' : TECH_NEGATIVE.has(gauge!.tech_state || '') ? 'down' : ''}`}>{gauge!.tech_state}</span>
            <span className={`mp-state ${HEAT_POSITIVE.has(gauge!.heat_state || '') ? 'up' : HEAT_NEGATIVE.has(gauge!.heat_state || '') ? 'down' : ''}`}>{gauge!.heat_state}</span>
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
              {typeof gauge.chg_1d_pct === 'number' && (
                <span className={`mp-chg ${gauge.chg_1d_pct >= 0 ? 'up' : 'down'}`}>
                  {gauge.chg_1d_pct >= 0 ? '+' : ''}{gauge.chg_1d_pct}%
                </span>
              )}
            </div>
            <Meter value={gauge.temperature} leftLabel="冰点" rightLabel="沸点" />
            <div className="mp-row">
              <span className="mp-k">技术形态</span>
              <span className={`mp-state ${TECH_POSITIVE.has(gauge.tech_state || '') ? 'up' : TECH_NEGATIVE.has(gauge.tech_state || '') ? 'down' : ''}`}>
                {gauge.tech_state || '未知'}
              </span>
            </div>
            <div className="mp-row">
              <span className="mp-k">资金热度</span>
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
