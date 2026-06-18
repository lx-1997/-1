import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { apiGet } from '../services/apiClient';
import './TerminalAiFund.css';

/**
 * AI 模拟盘弹层「阿尔法」——与独立页 /ai-fund 同款 V3：
 * 人设 + 心情 + 战绩 + 五维打分可视化 + 严谨买卖点 + 操盘解说 + 思考链。
 * 催化剂来自站内快讯/文章/研报，结合均线趋势·主力资金·估值自动模拟买卖。
 * ⚠️ 虚拟资金、不接券商、不构成投资建议。只读公开接口 /api/ai-fund/snapshot，45s 轮询。
 */

interface Position { symbol: string; name: string; qty: number; avg_cost: number; current_price: number; market_value: number; weight: number; pnl_pct: number; }
interface FeedItem {
  ts: string; kind: 'trade' | 'watch'; side: 'buy' | 'sell' | 'watch'; symbol: string; name: string;
  qty: number; price: number | null; pnl_pct: number | null; confidence: number | null;
  catalyst: string; buy_point: string; narrative: string;
  scores: Record<string, number>; thinking: { icon: string; label: string; text: string }[];
}
interface Snapshot {
  started_at: string; nav: number; nav_unit: number; nav_pct: number; cash: number; market_value: number;
  position_count: number; max_positions: number; last_tick_at: string | null; commentary: string;
  persona: { name: string; tag: string }; mood: { emoji: string; label: string; key: string };
  stats: { win_rate: number | null; closed: number; win_streak: number; best: number | null };
  positions: Position[]; feed: FeedItem[]; history: { date: string; value: number }[];
  data_quality: { level: 'live' | 'degraded' | 'mock'; label: string; detail: string }; disclaimer: string;
  risk?: {
    sample_days: number; sufficient: boolean; max_drawdown_pct: number | null;
    annualized_vol: number | null; sharpe: number | null; sortino: number | null;
    calmar: number | null; beta: number | null; alpha_annual: number | null;
  };
}

const fmtMoney = (v: number) => '¥' + Math.round(v).toLocaleString('zh-CN');
const fmtPct = (v: number | null | undefined) => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
const fmtTime = (iso: string | null) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString('zh-CN', { hour12: false }).slice(5); } catch { return '—'; } };
const SCORE_ORDER = ['消息面', '技术面', '资金面', '基本面', '情绪'];
const TAG_LABEL: Record<string, string> = { buy: '买入', sell: '卖出', watch: '观察' };

const ScoreBars: React.FC<{ scores: Record<string, number> }> = ({ scores }) => {
  const keys = SCORE_ORDER.filter(k => scores && scores[k] != null);
  if (!keys.length) return null;
  return (
    <div className="aif-scores">
      {keys.map(k => {
        const v = scores[k]; const pos = v >= 0; const w = Math.min(50, Math.abs(v) * 50);
        const col = pos ? 'var(--up)' : 'var(--down)';
        return (
          <div className="aif-sc" key={k}>
            <span className="aif-sc-k">{k}</span>
            <span className="aif-sc-track"><span className="aif-sc-mid" />
              <span className="aif-sc-fill" style={pos ? { left: '50%', width: `${w}%`, background: col } : { right: '50%', width: `${w}%`, background: col }} /></span>
            <span className={`aif-sc-v ${pos ? 'up' : 'down'}`}>{v > 0 ? '+' : ''}{v.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  );
};

const TerminalAiFund: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [data, setData] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const firstRef = useRef(true);

  const load = useCallback(async () => {
    if (firstRef.current) setLoading(true);
    try { setData(await apiGet<Snapshot>('/api/ai-fund/snapshot')); setErr(''); }
    catch { setErr('模拟盘数据加载失败，正在自动重试…'); }
    finally { setLoading(false); firstRef.current = false; }
  }, []);

  useEffect(() => { void load(); const t = window.setInterval(load, 45000); return () => window.clearInterval(t); }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const up = (data?.nav_pct ?? 0) >= 0;
  const curveColor = up ? 'var(--up)' : 'var(--down)';
  const s = data?.stats;

  return createPortal(
    <div className="aif-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="aif-modal" role="dialog" aria-modal="true" aria-label="AI 模拟盘 阿尔法">
        {/* 人设头条 */}
        <div className="aif-persona">
          <div className="aif-ava">🤖<span className="aif-ava-dot" /></div>
          <div className="aif-who">
            <div className="aif-nm">{data?.persona?.name || '阿尔法'}<span className="aif-livetag">● 模拟盘·实时盯市</span></div>
            <div className="aif-tg">{data?.persona?.tag || 'DeepFocus AI 操盘手'}</div>
          </div>
          {data?.mood && <span className="aif-mood">{data.mood.emoji} {data.mood.label}</span>}
          <span className={`aif-dq aif-dq--${data?.data_quality.level || 'degraded'}`}>
            {data?.data_quality.level === 'live' ? '● 实时' : '○ ' + (data?.data_quality.label || '加载中')}</span>
          <button className="aif-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        <div className="aif-scroll">
          {loading && <div className="aif-loading">加载中…</div>}
          {err && !data && <div className="aif-error">{err}</div>}

          {data && (
            <>
              {/* 战绩条 */}
              <div className="aif-stats">
                {[['胜率', s?.win_rate != null ? s.win_rate + '%' : '—'], ['已平仓', (s?.closed || 0) + ' 笔'],
                  ['连胜', (s?.win_streak || 0) + ' 连'], ['最佳一笔', s?.best != null ? '+' + s.best + '%' : '—']]
                  .map(([k, v]) => <div className="aif-stat" key={k}><b>{v}</b><span>{k}</span></div>)}
              </div>

              {/* 当前观点 */}
              <div className="aif-say"><span className="aif-say-q">“</span><span className="aif-say-t">{data.commentary || '盯盘中…'}</span></div>

              {/* 净值 hero */}
              <div className="aif-hero">
                <div className="aif-hero-main">
                  <div className="aif-hero-label">单位净值</div>
                  <div className="aif-hero-nav">{data.nav_unit.toFixed(4)}</div>
                  <div className={`aif-hero-ret ${up ? 'up' : 'down'}`}>累计收益 {fmtPct(data.nav_pct)}</div>
                </div>
                <div className="aif-hero-stats">
                  <div><span>总资产</span><b>{fmtMoney(data.nav)}</b></div>
                  <div><span>持仓市值</span><b>{fmtMoney(data.market_value)}</b></div>
                  <div><span>可用现金</span><b>{fmtMoney(data.cash)}</b></div>
                  <div><span>持仓/上限</span><b>{data.position_count}/{data.max_positions}</b></div>
                </div>
              </div>

              {/* 风险度量（专业纪律：样本不足只报最大回撤+天数，绝不瞎算夏普） */}
              {data.risk && (data.risk.max_drawdown_pct != null ? (
                <div className="aif-risk">
                  <span className="aif-risk-k">📊 风险</span>
                  <span>最大回撤 <b>-{data.risk.max_drawdown_pct.toFixed(1)}%</b></span>
                  {data.risk.sharpe != null && <span>夏普 <b>{data.risk.sharpe.toFixed(2)}</b></span>}
                  {data.risk.annualized_vol != null && <span>年化波动 <b>{data.risk.annualized_vol.toFixed(1)}%</b></span>}
                  {data.risk.beta != null && <span>β <b>{data.risk.beta.toFixed(2)}</b></span>}
                  {!data.risk.sufficient && <span className="aif-mute">·比率待 {data.risk.sample_days}/5 日</span>}
                </div>
              ) : (
                <div className="aif-risk"><span className="aif-risk-k">📊 风险</span><span className="aif-mute">指标积累中（{data.risk.sample_days}/5 日）</span></div>
              ))}

              {data.data_quality.level !== 'live' && <div className="aif-banner">⚠ {data.data_quality.detail}</div>}

              {/* 净值曲线 */}
              <div className="aif-card">
                <div className="aif-card-h">📈 净值走势 <span className="aif-mute">起始 1.0000 · 自 {(data.started_at || '').slice(0, 10)}</span></div>
                {data.history.length > 1 ? (
                  <ResponsiveContainer width="100%" height={170}>
                    <AreaChart data={data.history} margin={{ top: 8, right: 10, left: 0, bottom: 0 }}>
                      <defs><linearGradient id="aifGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={curveColor} stopOpacity={0.28} /><stop offset="100%" stopColor={curveColor} stopOpacity={0} /></linearGradient></defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                      <XAxis dataKey="date" tickFormatter={d => (d || '').slice(5)} fontSize={10} stroke="var(--mute)" minTickGap={36} />
                      <YAxis domain={['auto', 'auto']} fontSize={10} width={46} stroke="var(--mute)" tickFormatter={v => v.toFixed(3)} />
                      <Tooltip formatter={(v: number) => [v.toFixed(4), '净值']} contentStyle={{ background: 'var(--elevated)', border: '1px solid var(--line)', fontSize: 12 }} />
                      <Area type="monotone" dataKey="value" stroke={curveColor} strokeWidth={2} fill="url(#aifGrad)" dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : <div className="aif-empty">净值快照积累中，开盘后自动更新…</div>}
              </div>

              {/* 持仓 */}
              <div className="aif-card">
                <div className="aif-card-h">💼 当前持仓 <span className="aif-mute">{data.position_count} 只 · 实时盯市</span></div>
                {data.positions.length ? (
                  <div className="aif-table">
                    <div className="aif-tr aif-th"><span className="c-nm">标的</span><span className="c-n">现价</span><span className="c-n">成本</span><span className="c-n">收益</span><span className="c-n">仓位</span></div>
                    {data.positions.map(p => (
                      <div className="aif-tr" key={p.symbol}>
                        <span className="c-nm"><b>{p.name}</b><i>{p.symbol}</i></span>
                        <span className="c-n">{p.current_price}</span>
                        <span className="c-n aif-mute">{p.avg_cost}</span>
                        <span className={`c-n ${p.pnl_pct >= 0 ? 'up' : 'down'}`}>{fmtPct(p.pnl_pct)}</span>
                        <span className="c-n">{(p.weight * 100).toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                ) : <div className="aif-empty">空仓待机 · 等待看得懂的买点</div>}
              </div>

              {/* 实时解盘 */}
              <div className="aif-card">
                <div className="aif-card-h">🎙️ 实时解盘 <span className="aif-mute">五维打分 · 思考链 · 催化剂</span></div>
                {data.feed.length ? (
                  <div className="aif-feed">
                    {data.feed.map(f => (
                      <div className={`aif-ev aif-ev--${f.side}`} key={f.ts + f.symbol + f.side}>
                        <div className="aif-ev-h">
                          <span className={`aif-tag aif-tag--${f.side}`}>{TAG_LABEL[f.side] || f.side}</span>
                          <span className="aif-ev-nm">{f.name}</span><span className="aif-ev-cd">{f.symbol}</span>
                          {f.pnl_pct != null && <span className={f.pnl_pct >= 0 ? 'up' : 'down'} style={{ fontWeight: 600 }}>{fmtPct(f.pnl_pct)}</span>}
                          <span className="aif-ev-meta">{f.kind === 'trade' ? `${f.qty} 股 @ ${f.price} · ` : ''}{fmtTime(f.ts)}</span>
                        </div>
                        {f.narrative && <div className="aif-narr">{f.narrative}</div>}
                        {(f.catalyst || (f.buy_point && f.side === 'buy')) && (
                          <div className="aif-chips">
                            {f.catalyst && <span className="aif-chip aif-chip--cat">📰 {f.catalyst}</span>}
                            {f.buy_point && f.side === 'buy' && <span className="aif-chip aif-chip--bp">🎯 {f.buy_point}</span>}
                          </div>
                        )}
                        <ScoreBars scores={f.scores} />
                        {f.confidence != null && (
                          <div className="aif-conf">信心<span className="aif-conf-bar"><i style={{ width: `${Math.round(f.confidence * 100)}%` }} /></span>{Math.round(f.confidence * 100)}%</div>
                        )}
                        {f.thinking?.length > 0 && (
                          <div className="aif-think">
                            {f.thinking.map((st, i) => (
                              <div className="aif-step" key={i}><b>{st.icon} {st.label}</b><span>{st.text}</span></div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : <div className="aif-empty">阿尔法待机中，正在扫描本站快讯/研报…</div>}
              </div>

              <div className="aif-disclaimer">⚠ {data.disclaimer}</div>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default TerminalAiFund;
