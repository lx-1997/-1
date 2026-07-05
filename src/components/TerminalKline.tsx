import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { apiGet } from '../services/apiClient';

/**
 * 终端个股日线蜡烛图（真实 OHLC，东财直连·前复权）。
 *
 * 取代历史上从未上线的 KlineChart.tsx 假数据（Math.random）——这里走后端 /api/market/kline
 * 拉真实 O/H/L/C+量，纯 SVG 渲染、零新依赖。绿涨红跌（与终端既定语义一致，用 --up/--down）。
 * A股/港股可达；美股东财无 secid → 后端返回空，组件显示「暂未接入」而非空图。
 * 1:1 像素渲染（ResizeObserver 测宽）：文字/蜡烛不被 viewBox 拉伸变形。
 */

interface Candle { d: string; o: number; c: number; h: number; l: number; v: number }
interface KlineResp { symbol: string; market: string; points: number; candles: Candle[]; source: string }
interface TrendPoint { t: string; p: number; avg: number; v: number }
interface TrendResp { pre_close: number | null; points: TrendPoint[] }

// 模块级缓存：同股来回切换不重复请求（与后端 6h 缓存呼应，前端 10min 足够）
const _CACHE = new Map<string, { ts: number; candles: Candle[]; source: string }>();
const _TTL = 10 * 60 * 1000;
// 分时缓存：分钟级数据，盘中 60s 就过期
const _TREND_CACHE = new Map<string, { ts: number; trend: TrendResp }>();
const _TREND_TTL = 60 * 1000;

// A股盘中判定（客户端轻量版）：工作日 09:15-15:05 北京时间——盘中默认展示分时（散户第一视图）
const inCnSession = (): boolean => {
  try {
    const now = new Date();
    const bj = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000);
    if (bj.getDay() === 0 || bj.getDay() === 6) return false;
    const hm = bj.getHours() * 60 + bj.getMinutes();
    return hm >= 555 && hm <= 905;
  } catch { return false; }
};

const sma = (vals: number[], period: number): (number | null)[] => {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < vals.length; i += 1) {
    sum += vals[i];
    if (i >= period) sum -= vals[i - period];
    out.push(i >= period - 1 ? sum / period : null);
  }
  return out;
};

const fmtPx = (v: number): string => {
  if (!Number.isFinite(v)) return '-';
  if (v >= 1000) return v.toFixed(1);
  if (v >= 1) return v.toFixed(2);
  return v.toFixed(3);
};

const fmtVol = (v: number): string => {
  if (!Number.isFinite(v) || v <= 0) return '-';
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)}亿手`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)}万手`;
  return `${Math.round(v)}手`;
};

interface TerminalKlineProps { symbol: string; name?: string }

const TerminalKline: React.FC<TerminalKlineProps> = ({ symbol }) => {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [source, setSource] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [width, setWidth] = useState(760);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // 触屏抬手后保留读数的延时句柄（组件卸载时清理）
  const touchHoldRef = useRef<number | null>(null);

  // 由代码推断市场：6位=A股，5位=港股，其余=美股（与后端同款判断）
  const market = useMemo(() => {
    const s = (symbol || '').trim();
    if (/^\d{6}$/.test(s)) return 'CN';
    if (/^\d{5}$/.test(s)) return 'HK';
    return 'US';
  }, [symbol]);

  // 分时/日K 切换：A股盘中默认分时（散户交易时段第一视图），其余默认日K
  const [mode, setMode] = useState<'min' | 'day'>(() => (inCnSession() ? 'min' : 'day'));
  const [trend, setTrend] = useState<TrendResp | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const canTrend = market === 'CN' || market === 'HK';
  useEffect(() => {
    if (!canTrend || mode !== 'min') { setTrend(null); return; }
    const sym = (symbol || '').trim().toUpperCase();
    if (!sym) { setTrend(null); return; }
    const hit = _TREND_CACHE.get(sym);
    if (hit && Date.now() - hit.ts < _TREND_TTL) { setTrend(hit.trend); return; }
    let cancelled = false;
    setTrendLoading(true);
    apiGet<any>('/api/market/kline', { params: { symbol: sym, market, period: '1m' } })
      .then(resp => {
        if (cancelled) return;
        const tr: TrendResp | null = resp && Array.isArray(resp.points) && resp.points.length
          ? { pre_close: resp.pre_close ?? null, points: resp.points } : null;
        if (tr) _TREND_CACHE.set(sym, { ts: Date.now(), trend: tr });
        setTrend(tr);
        if (!tr) setMode('day');  // 后端暂无分时（旧版/美股/失败）→ 自动回落日K，体验不打断
      })
      .catch(() => { if (!cancelled) { setTrend(null); setMode('day'); } })
      .finally(() => { if (!cancelled) setTrendLoading(false); });
    return () => { cancelled = true; };
  }, [symbol, market, mode, canTrend]);

  // 测量容器宽度 → 1:1 渲染
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setWidth(Math.max(280, Math.round(el.clientWidth || 760)));
    update();
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(update);
      ro.observe(el);
    }
    return () => { if (ro) ro.disconnect(); };
  }, []);

  useEffect(() => {
    const sym = (symbol || '').trim().toUpperCase();
    if (!sym) { setCandles([]); setSource(''); return; }
    const cached = _CACHE.get(sym);
    if (cached && Date.now() - cached.ts < _TTL) {
      setCandles(cached.candles); setSource(cached.source); setHover(null);
      return;
    }
    let cancelled = false;
    setLoading(true); setHover(null);
    apiGet<KlineResp>('/api/market/kline', { params: { symbol: sym, market, points: 160 } })
      .then((resp) => {
        if (cancelled) return;
        const cs = resp?.candles || [];
        _CACHE.set(sym, { ts: Date.now(), candles: cs, source: resp?.source || '' });
        setCandles(cs); setSource(resp?.source || '');
      })
      .catch(() => { if (!cancelled) { setCandles([]); setSource('error'); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [symbol, market]);

  // ---- 几何（1:1，单位=像素）----
  const VW = width, VH = 210;
  const PAD_L = 6, PAD_R = 50, PAD_T = 10;
  const volH = 34, dateBand = 14, gap = 10;
  const priceH = VH - PAD_T - gap - volH - dateBand;          // 142
  const volTop = PAD_T + priceH + gap;
  const plotLeft = PAD_L, plotW = VW - PAD_L - PAD_R;

  const geo = useMemo(() => {
    if (!candles.length || plotW <= 0) return null;
    const n = candles.length;
    const slot = plotW / n;
    const bodyW = Math.max(1, Math.min(slot * 0.66, 11));
    let minL = Infinity, maxH = -Infinity, maxV = 0;
    candles.forEach((c) => { minL = Math.min(minL, c.l); maxH = Math.max(maxH, c.h); maxV = Math.max(maxV, c.v); });
    const padP = (maxH - minL) * 0.04 || 1;
    const lo = minL - padP, hi = maxH + padP;
    const xOf = (i: number) => plotLeft + slot * (i + 0.5);
    const yOf = (p: number) => PAD_T + (hi - p) / (hi - lo) * priceH;
    const closes = candles.map((c) => c.c);
    const path = (ma: (number | null)[]): string => {
      let d = ''; let started = false;
      ma.forEach((v, i) => {
        if (v == null) { started = false; return; }
        d += `${started ? 'L' : 'M'}${xOf(i).toFixed(1)} ${yOf(v).toFixed(1)} `;
        started = true;
      });
      return d.trim();
    };
    return { n, slot, bodyW, lo, hi, maxV, xOf, yOf, ma20Path: path(sma(closes, 20)), ma60Path: path(sma(closes, 60)) };
  }, [candles, plotW, plotLeft, priceH, PAD_T]);

  // 鼠标 / 触屏共用：clientX → 蜡烛索引
  const hoverFromClientX = (clientX: number) => {
    if (!geo || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const x = (clientX - rect.left) / rect.width * VW;
    const idx = Math.floor((x - plotLeft) / geo.slot);
    setHover(idx >= 0 && idx < geo.n ? idx : null);
  };

  const onMove = (e: React.MouseEvent) => hoverFromClientX(e.clientX);

  // 触屏滑动读数：横向由我们接管（svg 上 touch-action: pan-y 保住纵向页面滚动）
  const onTouch = (e: React.TouchEvent) => {
    if (!e.touches.length) return;
    if (touchHoldRef.current != null) { window.clearTimeout(touchHoldRef.current); touchHoldRef.current = null; }
    hoverFromClientX(e.touches[0].clientX);
  };

  // 抬手后保留最后一根的读数约 2 秒，再回落到最新一根
  const onTouchEnd = () => {
    if (touchHoldRef.current != null) window.clearTimeout(touchHoldRef.current);
    touchHoldRef.current = window.setTimeout(() => {
      touchHoldRef.current = null;
      setHover(null);
    }, 2000);
  };

  useEffect(() => () => {
    if (touchHoldRef.current != null) window.clearTimeout(touchHoldRef.current);
  }, []);

  const readout = useMemo(() => {
    if (!candles.length) return null;
    const i = hover != null && hover < candles.length ? hover : candles.length - 1;
    const c = candles[i];
    const prev = i > 0 ? candles[i - 1].c : c.o;
    const chg = c.c - prev;
    const pct = prev ? (chg / prev) * 100 : 0;
    return { c, chg, pct, up: chg >= 0 };
  }, [candles, hover]);

  const gridLines = geo ? [0, 0.25, 0.5, 0.75, 1].map((t) => ({
    y: PAD_T + priceH * t,
    p: geo.hi - (geo.hi - geo.lo) * t,
  })) : [];

  const empty = !loading && !candles.length;
  const emptyNote = market === 'US' ? '美股 K 线暂未接入（A股 / 港股可用），可用 AI 问答查美股走势'
    : source === 'error' ? 'K 线加载失败' : 'K 线暂无数据';

  // 分时几何：价格线 + 均价线 + 昨收基准 + 量柱
  const tGeo = useMemo(() => {
    if (mode !== 'min' || !trend || !trend.points.length || plotW <= 0) return null;
    const pts = trend.points;
    const vals = pts.flatMap(p => [p.p, p.avg]).concat(trend.pre_close != null ? [trend.pre_close] : []);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.06 || Math.max(0.01, hi * 0.002);
    lo -= pad; hi += pad;
    const maxV = Math.max(...pts.map(p => p.v), 1);
    const xOf = (i: number) => plotLeft + (plotW * i) / Math.max(1, pts.length - 1);
    const yOf = (p: number) => PAD_T + ((hi - p) / (hi - lo)) * priceH;
    const line = (get: (p: TrendPoint) => number) => pts.map((p, i) => `${i ? 'L' : 'M'}${xOf(i).toFixed(1)} ${yOf(get(p)).toFixed(1)}`).join(' ');
    const last = pts[pts.length - 1];
    const up = trend.pre_close != null ? last.p >= trend.pre_close : true;
    return { pts, lo, hi, maxV, xOf, yOf, pricePath: line(p => p.p), avgPath: line(p => p.avg), last, up };
  }, [mode, trend, plotW, plotLeft, priceH, PAD_T]);

  return (
    <div className={`bbt-kline${empty && mode === 'day' ? ' bbt-kline--empty' : ''}`} ref={wrapRef}>
      <div className="bbt-kline-head">
        <span className="bbt-kline-title">{mode === 'min' ? '分时 · 当日' : 'K线 · 日线 · 前复权'}</span>
        {canTrend && (
          <span style={{ display: 'inline-flex', gap: 4, marginLeft: 6 }}>
            <button onClick={() => setMode('min')} style={{ font: 'inherit', fontSize: 11, padding: '1px 8px', borderRadius: 999, cursor: 'pointer', background: mode === 'min' ? 'rgba(255,176,0,.18)' : 'transparent', color: 'inherit', border: '1px solid rgba(255,176,0,.35)' }}>分时</button>
            <button onClick={() => setMode('day')} style={{ font: 'inherit', fontSize: 11, padding: '1px 8px', borderRadius: 999, cursor: 'pointer', background: mode === 'day' ? 'rgba(255,176,0,.18)' : 'transparent', color: 'inherit', border: '1px solid rgba(255,176,0,.35)' }}>日K</button>
          </span>
        )}
        {mode === 'min' && tGeo && (
          <span className="bbt-kline-ohlc">
            <i>{tGeo.last.t}</i>
            现价<b>{fmtPx(tGeo.last.p)}</b> 均价<b>{fmtPx(tGeo.last.avg)}</b>
            {trend?.pre_close != null && (
              <em className={tGeo.up ? 'k-up-t' : 'k-down-t'}>
                {tGeo.up ? '+' : ''}{(tGeo.last.p - trend.pre_close).toFixed(2)}（{tGeo.up ? '+' : ''}{((tGeo.last.p - trend.pre_close) / trend.pre_close * 100).toFixed(2)}%）
              </em>
            )}
          </span>
        )}
        {mode === 'day' && readout && !empty && (
          <span className="bbt-kline-ohlc">
            <i>{readout.c.d}</i>
            开<b>{fmtPx(readout.c.o)}</b> 高<b>{fmtPx(readout.c.h)}</b> 低<b>{fmtPx(readout.c.l)}</b> 收<b>{fmtPx(readout.c.c)}</b>
            <em className={readout.up ? 'k-up-t' : 'k-down-t'}>{readout.up ? '+' : ''}{readout.chg.toFixed(2)}（{readout.up ? '+' : ''}{readout.pct.toFixed(2)}%）</em>
            <i>量 {fmtVol(readout.c.v)}</i>
          </span>
        )}
        <span className="bbt-kline-legend">
          {mode === 'day' && !empty && <><span className="k-ma20-t">MA20</span><span className="k-ma60-t">MA60</span></>}
          {mode === 'min' && <span className="k-ma20-t">均价</span>}
          <span className="bbt-kline-src">东财</span>
        </span>
      </div>

      {mode === 'min' && canTrend ? (
        trendLoading && !trend ? (
          <div className="bbt-kline-loading">分时加载中…</div>
        ) : tGeo ? (
          <svg className="bbt-kline-svg" width={VW} height={VH} viewBox={`0 0 ${VW} ${VH}`}>
            {[0, 0.5, 1].map((t, i) => {
              const y = PAD_T + priceH * t;
              return (
                <g key={`tg${i}`}>
                  <line x1={plotLeft} y1={y} x2={plotLeft + plotW} y2={y} className="k-grid" />
                  <text x={VW - PAD_R + 3} y={y + 3} className="k-axis">{fmtPx(tGeo.hi - (tGeo.hi - tGeo.lo) * t)}</text>
                </g>
              );
            })}
            {trend?.pre_close != null && (
              <line x1={plotLeft} y1={tGeo.yOf(trend.pre_close)} x2={plotLeft + plotW} y2={tGeo.yOf(trend.pre_close)}
                stroke="rgba(255,255,255,.35)" strokeDasharray="4 4" strokeWidth={1} />
            )}
            {tGeo.pts.map((p, i) => {
              const h = (p.v / tGeo.maxV) * volH;
              return <rect key={`tv${i}`} x={tGeo.xOf(i) - 0.6} y={volTop + volH - h} width={1.2} height={Math.max(0.4, h)}
                className={trend?.pre_close != null && p.p >= trend.pre_close ? 'k-vol-up' : 'k-vol-down'} />;
            })}
            <path d={tGeo.avgPath} className="k-line-ma60" />
            <path d={tGeo.pricePath} className={tGeo.up ? 'k-line-ma20' : 'k-line-ma20'} style={{ stroke: tGeo.up ? 'var(--up,#10b981)' : 'var(--down,#ef4444)', fill: 'none' }} />
            {[0, tGeo.pts.length - 1].map((i, k) => (
              <text key={`tt${k}`} x={k === 0 ? plotLeft + 2 : plotLeft + plotW - 2} y={VH - 3}
                className="k-axis" textAnchor={k === 0 ? 'start' : 'end'}>{tGeo.pts[i].t}</text>
            ))}
          </svg>
        ) : (
          <div className="bbt-kline-empty-note">分时暂无数据（休市/停牌）· 可切「日K」</div>
        )
      ) : empty ? (
        <div className="bbt-kline-empty-note">{emptyNote}</div>
      ) : loading && !candles.length ? (
        <div className="bbt-kline-loading">K 线加载中…</div>
      ) : geo && (
        <svg
          ref={svgRef}
          className="bbt-kline-svg"
          width={VW}
          height={VH}
          viewBox={`0 0 ${VW} ${VH}`}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          onTouchStart={onTouch}
          onTouchMove={onTouch}
          onTouchEnd={onTouchEnd}
          onTouchCancel={onTouchEnd}
          style={{ touchAction: 'pan-y' }}
        >
          {gridLines.map((g, i) => (
            <g key={`g${i}`}>
              <line x1={plotLeft} y1={g.y} x2={plotLeft + plotW} y2={g.y} className="k-grid" />
              <text x={VW - PAD_R + 3} y={g.y + 3} className="k-axis">{fmtPx(g.p)}</text>
            </g>
          ))}
          <line x1={plotLeft} y1={volTop - 6} x2={plotLeft + plotW} y2={volTop - 6} className="k-grid" />

          {candles.map((c, i) => {
            const h = geo.maxV > 0 ? (c.v / geo.maxV) * volH : 0;
            const up = c.c >= c.o;
            return (
              <rect key={`v${i}`} x={geo.xOf(i) - geo.bodyW / 2} y={volTop + volH - h}
                width={geo.bodyW} height={Math.max(0.5, h)} className={up ? 'k-vol-up' : 'k-vol-down'} />
            );
          })}

          {candles.map((c, i) => {
            const up = c.c >= c.o;
            const x = geo.xOf(i);
            const yO = geo.yOf(c.o), yC = geo.yOf(c.c);
            const top = Math.min(yO, yC);
            const bodyH = Math.max(0.8, Math.abs(yC - yO));
            const cls = up ? 'k-up' : 'k-down';
            return (
              <g key={`c${i}`} className={cls}>
                <line x1={x} y1={geo.yOf(c.h)} x2={x} y2={geo.yOf(c.l)} className="k-wick" />
                <rect x={x - geo.bodyW / 2} y={top} width={geo.bodyW} height={bodyH} className="k-body" />
              </g>
            );
          })}

          {geo.ma60Path && <path d={geo.ma60Path} className="k-line-ma60" />}
          {geo.ma20Path && <path d={geo.ma20Path} className="k-line-ma20" />}

          {hover != null && (
            <line x1={geo.xOf(hover)} y1={PAD_T} x2={geo.xOf(hover)} y2={volTop + volH} className="k-cross" />
          )}

          {[0, Math.floor(geo.n / 2), geo.n - 1].map((i, k) => (
            <text key={`d${k}`}
              x={Math.max(plotLeft + 14, Math.min(geo.xOf(i), plotLeft + plotW - 14))}
              y={VH - 3} className="k-axis"
              textAnchor={k === 0 ? 'start' : k === 2 ? 'end' : 'middle'}
            >{(candles[i].d || '').slice(2)}</text>
          ))}
        </svg>
      )}
    </div>
  );
};

export default TerminalKline;
