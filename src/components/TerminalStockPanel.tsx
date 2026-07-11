import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet } from '../services/apiClient';
import { createCall, cancelCall, fetchMyCalls, StockCall, CallDirection } from '../services/authService';

// 战绩闭环白名单（单一真源，FinancialTerminal 的「我的战绩」菜单行/弹层同套引用——捕获与镜像同进退）：
// 前端只控可见性，后端 DEEPFOCUS_CALLS_ALLOWED_USERS 硬门 403。内测过线（表态率达标）再放量。
export const CALLS_USERS = new Set(['lx199710']);
export const callsUserAllowed = (username?: string | null) => !!username && CALLS_USERS.has(username.toLowerCase());

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
  username?: string | null;                            // 战绩闭环白名单判定用（未传/非白名单 → 「我的判断」tab 整体不渲染）
  onLog?: (action: string, target?: string) => void;   // 埋点（call_create / call_cancel），复用 FinancialTerminal.logAct
}

type TabKey = 'verdict' | 'lhb' | 'consensus' | 'dividends' | 'news' | 'calls';

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

// ===== 战绩闭环 · 我的判断（表态即笔记，按收盘价自动兑现打分） =====
const CALL_DIR_TXT: Record<CallDirection, string> = { bull: '▲ 看多', bear: '▼ 看空' };
const CALL_OUTCOME_TXT: Record<string, string> = { hit: '✅ 命中', miss: '❌ 未中', flat: '⚪ 持平' };
const fmtRet = (v?: number | null) => (v == null ? '' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
const fmtMD = (d?: string | null) => {
  const m = String(d || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${Number(m[2])}月${Number(m[3])}日` : (d || '');
};
// 北京时区今天（YYYY-MM-DD）：判断 entry 是否已起算 / 跟踪第几天
const bjToday = () => new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' });
const callStatusTxt = (c: StockCall) =>
  c.status === 'open' ? '跟踪中'
    : c.status === 'settled' ? `${CALL_OUTCOME_TXT[c.outcome || ''] || '已兑现'} ${fmtRet(c.ret_pct)}`
      : c.status === 'void' ? '⚫ 无效（起算日停牌）'
        : c.status === 'canceled' ? '已撤销'
          : '⚠️ 结算异常，重试中';

// 历次表态时间轴：改造自上面的评级演变 Sparkline——色块=方向（沿用绿涨红跌单一真源），
// 未中降透明度、open 描边，tooltip 带日期/方向/兑现结果。判断从瞬间变成可回看的轨迹。
function CallTimeline({ items }: { items: StockCall[] }) {
  if (!items.length) return null;
  const seq = items.slice().sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
  return (
    <div title="我的历次表态：由旧到新（已兑现结果不可篡改，全量可回看）" style={{ display: 'flex', gap: 2, alignItems: 'center', margin: '6px 0 2px' }}>
      <span style={dim}>历次表态</span>
      {seq.slice(-24).map((c, i) => (
        <span key={c.id ?? i}
          title={`${(c.entry_date || c.created_at || '').slice(0, 10)} ${CALL_DIR_TXT[c.direction] || c.direction} · ${callStatusTxt(c)}${c.note ? `\n${c.note}` : ''}`}
          style={{
            width: 8, height: 14, borderRadius: 2,
            background: c.direction === 'bull' ? 'var(--up,#10b981)' : 'var(--down,#ef4444)',
            opacity: c.status === 'canceled' || c.status === 'void' ? 0.25 : c.status === 'settled' && c.outcome === 'miss' ? 0.45 : c.status === 'open' ? 0.85 : 1,
            outline: c.status === 'open' ? '1px solid rgba(255,255,255,.55)' : 'none',
          }} />
      ))}
    </div>
  );
}

export default function TerminalStockPanel({ symbol, name, loggedIn, onRequireLogin, username, onLog }: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabKey>('verdict');
  const [busy, setBusy] = useState(false);
  const cache = useRef<Record<string, any>>({});   // symbol:tab → data（会话内缓存，防重复拉）
  const [, force] = useState(0);
  const bump = () => force(v => v + 1);

  // 「🎯 我的判断」tab 可见性：仅A股（HK/US 无结算日历，后端 422 的防呆前置）+ 白名单（后端同样硬门 403）
  const isCn = /^\d{6}$/.test((symbol || '').trim());
  const showCalls = isCn && callsUserAllowed(username);
  const [calls, setCalls] = useState<StockCall[] | null>(null);          // 本 symbol 我的历次表态；null=未加载
  const [callBusy, setCallBusy] = useState(false);
  const [callMsg, setCallMsg] = useState('');                            // 表态/撤销的行内反馈（含后端拒绝理由）
  const [justCreated, setJustCreated] = useState<StockCall | null>(null); // 刚表态成功 → 确认卡（即时反馈是这个入口的生死线）
  const [callHorizon, setCallHorizon] = useState<3 | 5 | 20>(5);
  const [callNote, setCallNote] = useState('');
  const [callMore, setCallMore] = useState(false);                       // 展开区：改期限 + 一句话理由（收起不增噪）

  useEffect(() => {   // 换股复位（含表态区状态；calls 置 null 触发重拉）
    setOpen(false); setTab('verdict');
    setCalls(null); setCallMsg(''); setJustCreated(null); setCallMore(false); setCallNote(''); setCallHorizon(5);
  }, [symbol]);

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

  // 我的表态（本 symbol）：不走 cache——表态/撤销后必须立刻反映，会话缓存会给用户看陈旧状态
  const loadCalls = useCallback(async () => {
    setBusy(true);
    try {
      const sym = (symbol || '').trim();
      const all = await fetchMyCalls();
      setCalls(all.filter(c => String(c.symbol || '').trim() === sym));
    } finally { setBusy(false); }
  }, [symbol]);

  const submitCall = useCallback(async (direction: CallDirection) => {
    if (callBusy) return;
    setCallBusy(true); setCallMsg('');
    try {
      const r = await createCall({ symbol: (symbol || '').trim(), direction, horizon_days: callHorizon, note: callNote.trim() || undefined });
      if (r.created) onLog?.('call_create', `${symbol} ${direction} ${callHorizon}d`);
      else setCallMsg(r.message || '已在跟踪（同一标的同时只跟踪一笔，可先撤销再改判）');   // 幂等命中已有单（含反向单）
      setJustCreated(r.created ? r.call : null);
      setCalls(prev => [r.call, ...(prev || []).filter(c => c.id !== r.call.id)]);
      setCallNote(''); setCallMore(false);
    } catch (e: any) {
      setCallMsg(e?.message || '表态失败，请稍后再试');   // 后端 detail 原样透出（已在跟踪/每日上限/非白名单等）
    } finally { setCallBusy(false); }
  }, [symbol, callBusy, callHorizon, callNote, onLog]);

  const doCancelCall = useCallback(async (id: number) => {
    if (callBusy) return;
    setCallBusy(true); setCallMsg('');
    try {
      await cancelCall(id);
      onLog?.('call_cancel', symbol);
      setJustCreated(null);
      setCalls(prev => (prev || []).map(c => (c.id === id ? { ...c, status: 'canceled' as const } : c)));
      setCallMsg('已撤销，这笔判断不会进入战绩');
    } catch (e: any) {
      // fail-closed：超时/价格异动/行情取数失败都拒绝撤销（防「不利即撤」），如实告知
      setCallMsg(e?.message || '撤销失败：超过 60 分钟、价格已异动（>1.5%）或行情暂不可用时不可撤销');
    } finally { setCallBusy(false); }
  }, [symbol, callBusy, onLog]);

  const switchTab = useCallback((t: TabKey) => {
    setTab(t);
    if (t === 'calls') { void loadCalls(); return; }
    void load(t);
  }, [load, loadCalls]);

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
        {(showCalls ? [...TABS, ['calls', '🎯 我的判断'] as [TabKey, string]] : TABS).map(([k, label]) => (
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

      {/* 🎯 我的判断：一键即完整 call（默认 5 个交易日/信念档默认档），按收盘价起算自动兑现。
          ⚠️ 全程不展示实时快照价——「入场价」只有 entry 交易日收盘价一个口径（客观兑现的信任底线）。 */}
      {tab === 'calls' && showCalls && (
        <div style={{ marginTop: 8 }}>
          {/* 加载态由上方通用 busy 指示器负责（calls 无会话缓存，d 恒 undefined 正好命中它） */}
          {calls === null ? null : (() => {
            const list = calls;
            const openCall = list.find(c => c.status === 'open') || null;
            const today = bjToday();
            const entryPassed = !!(openCall?.entry_date && openCall.entry_date <= today);
            const trackDay = openCall?.entry_date ? Math.max(1, Math.floor((Date.parse(today) - Date.parse(openCall.entry_date)) / 86400000) + 1) : 1;
            const cancelLeftMin = openCall?.created_at ? Math.max(0, Math.ceil((3600000 - (Date.now() - Date.parse(openCall.created_at))) / 60000)) : 0;
            const history = list.filter(c => c.status !== 'open')
              .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
            return (
              <>
                {openCall && justCreated && justCreated.id === openCall.id ? (
                  /* 表态成功确认卡：即时反馈——说清起算口径与兑现节拍（旧▲▼按钮零反馈被下线的教训） */
                  <div style={{ ...box, borderColor: 'rgba(255,176,46,.5)' }}>
                    <b>✅ 已记录你的判断：{name || symbol} <span className={openCall.direction === 'bull' ? 'bbt-up' : 'bbt-down'}>{CALL_DIR_TXT[openCall.direction]}</span></b>
                    <div style={{ fontSize: 13, margin: '6px 0 2px', lineHeight: 1.7 }}>
                      将按 <b>{fmtMD(openCall.entry_date)} 收盘价</b> 起算，<b>{openCall.horizon_days}</b> 个交易日后自动按收盘价兑现打分——结果好坏都会如实记入头像菜单「🎯 我的战绩」，不可修改。
                    </div>
                    {openCall.note && <div style={dim}>理由：{openCall.note}</div>}
                    {cancelLeftMin > 0 && (
                      <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <button className="bbt-clear" disabled={callBusy} onClick={() => void doCancelCall(openCall.id)}>撤销这笔判断</button>
                        <span style={dim}>60 分钟内可撤（剩约 {cancelLeftMin} 分钟）</span>
                      </div>
                    )}
                  </div>
                ) : openCall ? (
                  /* 跟踪中卡：第X天按自然日粗计（结算按交易日，以后端为准）；不显示任何盘中价 */
                  <div style={box}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                      <b className={openCall.direction === 'bull' ? 'bbt-up' : 'bbt-down'}>{CALL_DIR_TXT[openCall.direction]}</b>
                      <span style={{ fontSize: 13 }}>{entryPassed ? `跟踪中 · 第 ${trackDay} 天` : `待起算 · 将按 ${fmtMD(openCall.entry_date)} 收盘价起算`}</span>
                      <span style={dim}>{openCall.horizon_days} 个交易日后按收盘价自动兑现</span>
                    </div>
                    {openCall.note && <div style={{ ...dim, marginTop: 4 }}>理由：{openCall.note}</div>}
                    {cancelLeftMin > 0 && (
                      <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <button className="bbt-clear" disabled={callBusy} onClick={() => void doCancelCall(openCall.id)}>撤销这笔判断</button>
                        <span style={dim}>表态后 60 分钟内可撤（剩约 {cancelLeftMin} 分钟；价格异动超 1.5% 后不可撤）</span>
                      </div>
                    )}
                  </div>
                ) : (
                  /* 表态区：▲/▼ 大按钮一键即完整 call；期限/理由收在展开区不增噪 */
                  <div style={box}>
                    <div style={{ fontSize: 13, marginBottom: 8, lineHeight: 1.6 }}>
                      对 <b>{name || symbol}</b> 未来 <b>{callHorizon}</b> 个交易日怎么看？按 <b>收盘价</b> 起算、到期自动兑现打分，好坏都记档。
                    </div>
                    <div style={{ display: 'flex', gap: 10 }}>
                      <button disabled={callBusy} onClick={() => void submitCall('bull')}
                        style={{ flex: 1, fontFamily: 'inherit', fontSize: 15, fontWeight: 700, padding: '9px 0', borderRadius: 8, cursor: 'pointer', color: 'var(--up,#10b981)', background: 'rgba(16,185,129,.10)', border: '1px solid rgba(16,185,129,.35)' }}>▲ 看多</button>
                      <button disabled={callBusy} onClick={() => void submitCall('bear')}
                        style={{ flex: 1, fontFamily: 'inherit', fontSize: 15, fontWeight: 700, padding: '9px 0', borderRadius: 8, cursor: 'pointer', color: 'var(--down,#ef4444)', background: 'rgba(239,68,68,.10)', border: '1px solid rgba(239,68,68,.35)' }}>▼ 看空</button>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <button className="bbt-clear" onClick={() => setCallMore(v => !v)}>{callMore ? '收起 ▴' : '改期限 / 写理由 ▾'}</button>
                      {callMore && (
                        <div style={{ marginTop: 6 }}>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={dim}>兑现期限</span>
                            {([3, 5, 20] as const).map(h => (
                              <button key={h} className={'bbt-chip' + (callHorizon === h ? ' on' : '')} onClick={() => setCallHorizon(h)}>{h} 个交易日</button>
                            ))}
                          </div>
                          <input value={callNote} maxLength={140} onChange={e => setCallNote(e.target.value)}
                            placeholder="一句话理由（可选，≤140字）——表态即笔记，兑现时回看"
                            style={{ marginTop: 6, width: '100%', boxSizing: 'border-box', background: 'transparent', border: '1px solid var(--line-2,#233039)', borderRadius: 6, padding: '6px 9px', color: 'inherit', fontFamily: 'inherit', fontSize: 13 }} />
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {callMsg && <div style={{ fontSize: 13, color: 'var(--warn,#f0a020)', margin: '6px 0' }}>{callMsg}</div>}
                <CallTimeline items={list} />
                {history.slice(0, 8).map(c => (
                  <div key={c.id} style={{ fontSize: 13, lineHeight: 1.8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <span style={dim}>{(c.entry_date || c.created_at || '').slice(0, 10)}</span>
                    <span className={c.direction === 'bull' ? 'bbt-up' : 'bbt-down'}>{CALL_DIR_TXT[c.direction] || c.direction}</span>
                    <span>{callStatusTxt(c)}</span>
                    {c.note && <span style={dim} title={c.note}>「{c.note.slice(0, 24)}{c.note.length > 24 ? '…' : ''}」</span>}
                  </div>
                ))}
                <div style={dim}>个人判断记录（内测）· 按收盘价自动兑现，非实时盈亏 · 仅供自我复盘，不构成投资建议</div>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
