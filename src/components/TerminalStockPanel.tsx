import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet } from '../services/apiClient';

// ===== 术语悬浮释义（termLinkify 轻量版）=====
// 证据链里的「Put/Call 量比」「杯柄形态」对新手是天书——glossary 名词学堂早已建成，
// 这里按词典匹配把命中术语渲染成带释义 tooltip + 跳 /learn/{slug} 的下划线词。
interface GlossTerm { slug: string; term: string; aliases?: string[]; brief?: string }
let _glossCache: GlossTerm[] | null = null;
let _glossLoading: Promise<GlossTerm[]> | null = null;
function loadGlossary(): Promise<GlossTerm[]> {
  if (_glossCache) return Promise.resolve(_glossCache);
  if (!_glossLoading) {
    _glossLoading = apiGet<any>('/api/glossary/index')
      .then(r => { _glossCache = (Array.isArray(r?.terms) ? r.terms : Array.isArray(r) ? r : []) as GlossTerm[]; return _glossCache; })
      .catch(() => { _glossCache = []; return _glossCache; });
  }
  return _glossLoading;
}

function TermText({ text }: { text: string }) {
  const [terms, setTerms] = useState<GlossTerm[]>(_glossCache || []);
  useEffect(() => { if (!_glossCache) void loadGlossary().then(setTerms); }, []);
  if (!text || !terms.length) return <>{text}</>;
  // 词典匹配：按名称长度降序找首个命中，切段渲染（每段最多 linkify 4 个词防过度装饰）
  const dict = terms.flatMap(t => [t.term, ...(t.aliases || [])].filter(Boolean).map(w => ({ w, t })))
    .sort((a, b) => b.w.length - a.w.length);
  const out: React.ReactNode[] = [];
  let rest = text; let hits = 0; let k = 0;
  while (rest && hits < 4) {
    let found: { idx: number; w: string; t: GlossTerm } | null = null;
    for (const { w, t } of dict) {
      if (w.length < 2) continue;
      const idx = rest.indexOf(w);
      if (idx >= 0 && (!found || idx < found.idx)) found = { idx, w, t };
    }
    if (!found) break;
    if (found.idx > 0) out.push(<span key={k++}>{rest.slice(0, found.idx)}</span>);
    out.push(
      <a key={k++} href={`/learn/${found.t.slug}`} target="_blank" rel="noreferrer"
        title={found.t.brief || `什么是${found.w}？点击看名词学堂`}
        style={{ color: 'inherit', textDecorationStyle: 'dotted', textUnderlineOffset: 3 }}>{found.w}</a>
    );
    rest = rest.slice(found.idx + found.w.length);
    hits += 1;
  }
  if (rest) out.push(<span key={k++}>{rest}</span>);
  return <>{out}</>;
}

/**
 * 个股面板（终端原生，零 antd）：选中个股后一键展开——
 * 速判卡 9 维信号灯（后端 /api/stock/tear-sheet 现成引擎，此前只活在休眠 AppShell 和 SEO 页）
 * + 龙虎榜 / 一致预期 / 分红史 / 公司新闻 四个数据标签页（此前只有 AI 工具能调，用户无任何入口）
 * + 评级演变时间线（/api/data/history kind=verdict，公开）+ 风险体检（确定性位置事实）。
 * 「查一只股→看到结论」的最后一公里；全部数据展示均带免责，不构成投资建议。
 */

interface Props {
  symbol: string;
  name: string;
  loggedIn: boolean;
  onRequireLogin: (why: string) => void;
}

type TabKey = 'verdict' | 'lhb' | 'consensus' | 'dividends' | 'news';

const TABS: Array<[TabKey, string]> = [
  ['verdict', '⚡ 速判卡'],
  ['lhb', '龙虎榜'],
  ['consensus', '一致预期'],
  ['dividends', '分红'],
  ['news', '公司新闻'],
];

// 信号→终端色（本产品刻意绿涨红跌，沿用 bbt-up=涨色 / bbt-down=跌色 单一真源）
const sigCls = (s: string) => (s === 'bullish' ? 'bbt-up' : s === 'bearish' ? 'bbt-down' : '');
const sigTxt = (s: string) => (s === 'bullish' ? '偏多' : s === 'bearish' ? '偏空' : s === 'neutral' ? '中性' : '数据不足');
const verdictTxt = (v: string) => v || '数据不足';

const box: React.CSSProperties = { border: '1px solid var(--line-2,#233039)', borderRadius: 8, padding: '10px 12px', margin: '8px 0' };
const dim: React.CSSProperties = { opacity: 0.65, fontSize: 12 };

function Sparkline({ points }: { points: Array<{ verdict?: string; price?: number; ts?: string }> }) {
  if (!points.length) return null;
  const cls = (v?: string) => (String(v || '').includes('看多') || v === 'bullish' ? 'var(--up,#10b981)'
    : String(v || '').includes('看空') || v === 'bearish' ? 'var(--down,#ef4444)' : 'rgba(255,255,255,.28)');
  return (
    <div title="评级演变：由旧到新（判断从快照变成轨迹，全量可回看）" style={{ display: 'flex', gap: 2, alignItems: 'center', margin: '4px 0 2px' }}>
      <span style={dim}>评级演变</span>
      {points.slice(-24).map((p, i) => (
        <span key={i} title={`${(p.ts || '').slice(0, 10)} ${verdictTxt(String(p.verdict || ''))}${p.price != null ? ` @${p.price}` : ''}`}
          style={{ width: 8, height: 14, borderRadius: 2, background: cls(p.verdict) }} />
      ))}
    </div>
  );
}

export default function TerminalStockPanel({ symbol, name, loggedIn, onRequireLogin }: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabKey>('verdict');
  const [busy, setBusy] = useState(false);
  const cache = useRef<Record<string, any>>({});   // symbol:tab → data（会话内缓存，防重复拉）
  const [, force] = useState(0);
  const bump = () => force(v => v + 1);

  useEffect(() => { setOpen(false); setTab('verdict'); }, [symbol]);   // 换股复位

  const load = useCallback(async (t: TabKey) => {
    const key = `${symbol}:${t}`;
    if (cache.current[key] !== undefined) return;
    setBusy(true);
    try {
      if (t === 'verdict') {
        const [ts, hist, risk] = await Promise.all([
          apiGet<any>('/api/stock/tear-sheet', { params: { symbol, name }, timeout: 45000 }).catch(() => null),
          apiGet<any>('/api/data/history', { params: { symbol, kind: 'verdict', limit: 24 } }).catch(() => null),
          apiGet<any>('/api/stock/risk-check', { params: { symbol } }).catch(() => null),
        ]);
        // /api/data/history 返回 {items:[{recorded_at,payload}]} 新→旧；sparkline 由旧到新渲染 → 倒序
        const histItems = (hist?.items || hist?.points || hist?.data || []) as any[];
        cache.current[key] = { ts, hist: Array.isArray(histItems) ? histItems.slice().reverse() : [], risk: risk?.data || null };
      } else {
        const path = t === 'lhb' ? '/api/stock/dragon-tiger' : t === 'consensus' ? '/api/stock/consensus'
          : t === 'dividends' ? '/api/stock/dividends' : '/api/stock/news';
        const r = await apiGet<any>(path, { params: { symbol } }).catch(() => null);
        cache.current[key] = r?.data ?? null;
      }
    } finally { setBusy(false); bump(); }
  }, [symbol, name]);

  const openPanel = useCallback(() => {
    if (!loggedIn) { onRequireLogin('查看个股速判卡与数据面板'); return; }
    setOpen(true); void load('verdict');
  }, [loggedIn, onRequireLogin, load]);

  const switchTab = useCallback((t: TabKey) => { setTab(t); void load(t); }, [load]);

  if (!open) {
    return (
      <div style={{ margin: '6px 0' }}>
        <button className="bbt-nai" onClick={openPanel}
          title="9维证据速判 + 龙虎榜/一致预期/分红/新闻，一页看全这只股">📋 个股面板 · 速判/龙虎榜/分红</button>
      </div>
    );
  }

  const d = cache.current[`${symbol}:${tab}`];

  return (
    <div style={{ ...box, margin: '6px 0 10px' }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        {TABS.map(([k, label]) => (
          <button key={k} className={'bbt-chip' + (tab === k ? ' on' : '')} onClick={() => switchTab(k)}>{label}</button>
        ))}
        <button className="bbt-clear" style={{ marginLeft: 'auto' }} onClick={() => setOpen(false)}>收起 ▴</button>
      </div>

      {busy && d === undefined && <div className="bbt-empty">加载中…</div>}

      {tab === 'verdict' && d && (
        <div>
          {d.ts ? (
            <>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', marginTop: 8 }}>
                <b style={{ fontSize: 16 }}>{verdictTxt(d.ts.overall_verdict)}</b>
                <span style={dim}>综合分 {d.ts.overall_score} · 置信度 {(Number(d.ts.confidence || 0) * 100).toFixed(0)}%</span>
                <span style={dim}>🤖 AI 生成叙述 · 确定性引擎判定</span>
              </div>
              <Sparkline points={Array.isArray(d.hist) ? d.hist.map((p: any) => ({ verdict: p?.payload?.verdict ?? p?.verdict, price: p?.payload?.price ?? p?.price, ts: p?.recorded_at || p?.ts || p?.created_at })) : []} />
              {d.ts.narrative && <div style={{ margin: '8px 0', lineHeight: 1.6 }}>{d.ts.narrative}</div>}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(220px,1fr))', gap: 8 }}>
                {(d.ts.dimensions || []).map((dm: any) => (
                  <div key={dm.key} style={{ ...box, margin: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <b>{dm.label}</b>
                      <span className={sigCls(dm.signal)} style={{ fontWeight: 700 }}>{sigTxt(dm.signal)}</span>
                    </div>
                    {dm.headline && <div style={{ fontSize: 13, margin: '4px 0' }}><TermText text={dm.headline} /></div>}
                    {(dm.evidence || []).slice(0, 3).map((e: string, i: number) => (
                      <div key={i} style={{ ...dim, lineHeight: 1.5 }}>· <TermText text={e} /></div>
                    ))}
                  </div>
                ))}
              </div>
              {d.risk && (
                <div style={{ ...box }}>
                  <b>🛡 风险体检</b> <span style={dim}>（历史统计事实 + 纪律科普，不构成操作建议）</span>
                  <div style={{ fontSize: 13, marginTop: 4, lineHeight: 1.7 }}>
                    {d.risk.support != null && <span>距最近支撑位 <b>{d.risk.dist_support_pct}%</b>（{d.risk.support}）　</span>}
                    {d.risk.resistance != null && <span>距最近阻力位 <b>{d.risk.dist_resistance_pct}%</b>（{d.risk.resistance}）　</span>}
                    {d.risk.drawdown_from_52w_high_pct != null && <span>距 52 周高点回撤 <b>{d.risk.drawdown_from_52w_high_pct}%</b>　</span>}
                    {d.risk.market_regime?.note && <div style={dim}>大盘环境：{d.risk.market_regime.note}</div>}
                  </div>
                </div>
              )}
              <div style={dim}>内容由 AI 生成/确定性引擎聚合 · 仅供研究参考，不构成投资建议</div>
            </>
          ) : <div className="bbt-empty">速判卡暂时生成失败，稍后再试</div>}
        </div>
      )}

      {tab === 'lhb' && d !== undefined && (
        d ? (
          <div style={{ marginTop: 8 }}>
            <div><b>{d.date}</b> 上榜 <span style={dim}>{d.reason}</span></div>
            <div style={{ fontSize: 13, margin: '4px 0' }}>当日涨跌 {d.change_rate}% · 榜上净额 {(Number(d.net || 0) / 1e4).toFixed(0)} 万</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div><div style={dim}>买入席位 TOP</div>{(d.buy_seats || []).slice(0, 5).map((s: any, i: number) => <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>· {s.name}</div>)}</div>
              <div><div style={dim}>卖出席位 TOP</div>{(d.sell_seats || []).slice(0, 5).map((s: any, i: number) => <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>· {s.name}</div>)}</div>
            </div>
            <div style={dim}>交易所公开数据 · 席位信息不构成任何推荐</div>
          </div>
        ) : <div className="bbt-empty">近 30 天未上龙虎榜</div>
      )}

      {tab === 'consensus' && d !== undefined && (
        d ? (
          <div style={{ marginTop: 8 }}>
            {d.target_price != null && <div>一致目标价 <b>{d.target_price}</b>{d.upside_pct != null && <span style={dim}>（较现价 {d.upside_pct}%）</span>}</div>}
            {d.rating && <div style={{ margin: '4px 0' }}>评级共识：<b>{d.rating}</b>{d.rating_count != null && <span style={dim}> · {d.rating_count} 家机构</span>}</div>}
            {Array.isArray(d.items) && d.items.slice(0, 6).map((it: any, i: number) => (
              <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>· {it.org || it.name || ''} {it.rating || ''} {it.target_price ? `目标 ${it.target_price}` : ''}</div>
            ))}
            <div style={dim}>券商一致预期，非本站观点 · 不构成投资建议</div>
          </div>
        ) : <div className="bbt-empty">暂无一致预期数据（A股/港股覆盖为主）</div>
      )}

      {tab === 'dividends' && d !== undefined && (
        d && (Array.isArray(d.items) ? d.items.length : Array.isArray(d) ? d.length : 0) > 0 ? (
          <div style={{ marginTop: 8 }}>
            {((Array.isArray(d.items) ? d.items : d) as any[]).slice(0, 10).map((it: any, i: number) => (
              <div key={i} style={{ fontSize: 13, lineHeight: 1.8 }}>
                <b>{it.year || (it.ex_date || '').slice(0, 4)}</b>　{it.plan || it.desc || `每10股派 ${it.dividend ?? '-'}`}
                {it.ex_date && <span style={dim}>　除权除息 {String(it.ex_date).slice(0, 10)}</span>}
              </div>
            ))}
            <div style={dim}>历史分红为公开事实 · 过往分红不代表未来</div>
          </div>
        ) : <div className="bbt-empty">暂无分红记录</div>
      )}

      {tab === 'news' && d !== undefined && (
        d && Array.isArray(d.items || d) && (d.items || d).length > 0 ? (
          <div style={{ marginTop: 8 }}>
            {((d.items || d) as any[]).slice(0, 10).map((it: any, i: number) => (
              <div key={i} style={{ fontSize: 13, lineHeight: 1.7 }}>
                {it.url ? <a href={it.url} target="_blank" rel="noreferrer" style={{ color: 'inherit' }}>· {it.title}</a> : <span>· {it.title}</span>}
                <span style={dim}>　{String(it.date || it.time || '').slice(0, 10)}</span>
              </div>
            ))}
          </div>
        ) : <div className="bbt-empty">暂无个股新闻</div>
      )}
    </div>
  );
}
