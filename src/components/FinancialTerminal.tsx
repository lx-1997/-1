import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiGet, apiPost } from '../services/apiClient';
import {
  listRealtimeMessages,
  getRealtimeMessageById,
  createRealtimeMessageStream,
  createReportShare,
  getReportShareById,
  RealtimeMessageRecord,
  RealtimeMessageSeverity,
  RealtimeMessageFilters,
  StreamConnectionStatus,
} from '../services/eventService';
import ShareButton from './common/ShareButton';
import ShareModal, { ShareTarget } from './ShareModal';
import * as authService from '../services/authService';
import { runToolResearch, sendAgentFeedback, startDeepResearch, pollDeepResearch, type ToolTraceItem, type DeepTask } from '../services/agentService';
import { fetchWatchlist, saveWatchlist } from '../services/watchlistService';
import { loadRecallPrefs, saveRecallPrefs, requestBrowserPermission, evaluateAndNotify, RECALL_PREFS_EVENT, subscribeWebPush, subscribeEmailRecall, getNotificationPermission } from '../utils/signalRecall';
import { copyText } from '../utils/clipboard';
import { shareImageNative } from '../utils/share';
import TerminalAuthModal from './TerminalAuthModal';
import TerminalOnboarding, { ONB_KEY } from './TerminalOnboarding';
import TerminalHelp from './TerminalHelp';
import TerminalReferral from './TerminalReferral';
import TerminalAiFund from './TerminalAiFund';
import TerminalCelebrityViews from './TerminalCelebrityViews';
import TerminalWeixinBind from './TerminalWeixinBind';
import TerminalKline from './TerminalKline';
import TerminalStockPanel from './TerminalStockPanel';
import { useTheme } from '../context/ThemeContext';
import './FinancialTerminal.css';

// ===== 市场交易时段（北京时间）+ 2026 节假日 =====
const HOLIDAYS_2026: Record<string, string[]> = {
  CN: ['2026-01-01', '2026-01-02', '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
    '2026-02-23', '2026-04-06', '2026-05-01', '2026-05-04', '2026-05-05', '2026-06-19', '2026-09-25',
    '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07'],
  HK: ['2026-01-01', '2026-02-17', '2026-02-18', '2026-02-19', '2026-04-03', '2026-04-06', '2026-04-07',
    '2026-05-01', '2026-05-25', '2026-06-19', '2026-07-01', '2026-09-26', '2026-10-01', '2026-10-19',
    '2026-12-25', '2026-12-26'],
  US: ['2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03',
    '2026-09-07', '2026-11-26', '2026-12-25'],
};
function beijingParts(d: Date) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', weekday: 'short', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit',
  });
  const p: Record<string, string> = {};
  fmt.formatToParts(d).forEach(part => { p[part.type] = part.value; });
  const wdMap: Record<string, number> = { Sun: 6, Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5 };
  const hour = parseInt(p.hour, 10) % 24;
  return { wd: wdMap[p.weekday], minutes: hour * 60 + parseInt(p.minute, 10), clock: `${String(hour).padStart(2, '0')}:${p.minute}:${p.second}`, dateStr: `${p.year}-${p.month}-${p.day}` };
}
function isMarketOpen(market: 'CN' | 'HK' | 'US', now: Date): boolean {
  const { wd, minutes, dateStr } = beijingParts(now);
  const hol = HOLIDAYS_2026[market] || [];
  if (market === 'CN') { if (wd >= 5 || hol.includes(dateStr)) return false; return (minutes >= 570 && minutes < 690) || (minutes >= 780 && minutes < 900); }
  if (market === 'HK') { if (wd >= 5 || hol.includes(dateStr)) return false; return (minutes >= 570 && minutes < 720) || (minutes >= 780 && minutes < 960); }
  if (minutes >= 1260) return wd <= 4 && !hol.includes(dateStr);
  if (minutes < 300) return wd >= 1 && wd <= 5;
  return false;
}

// 默认自选 + 名称 + 资讯匹配关键词（用户可自行增删，存 localStorage）
const DEFAULT_WATCHLIST = ['600519', '300750', '002594', '000858', '00700', '09988', '03690', 'NVDA', 'TSLA', 'AAPL', 'MSFT', 'GOOGL'];
// blob → data: URL（base64）。长按保存图片在微信内置浏览器 / App WebView 里只认 data:，blob: 常常长按无反应。
const blobToDataUrl = (blob: Blob): Promise<string> => new Promise((resolve, reject) => {
  const fr = new FileReader();
  fr.onload = () => resolve(String(fr.result || ''));
  fr.onerror = reject;
  fr.readAsDataURL(blob);
});
// Day-1 激活快选：跨市场高知名度标的，一键加自选(降低激活摩擦)。
// ⚠️刻意避开 DEFAULT_WATCHLIST 里已有的，确保每只都「可加」(否则显示成灰色已加、点不动)。
const ACTIVATE_PICKS: { code: string; name: string }[] = [
  { code: '688981', name: '中芯国际' }, { code: '601899', name: '紫金矿业' }, { code: '01810', name: '小米集团' },
  { code: '002475', name: '立讯精密' }, { code: '300760', name: '迈瑞医疗' }, { code: '600276', name: '恒瑞医药' },
  { code: '688256', name: '寒武纪' }, { code: '002415', name: '海康威视' }, { code: '601012', name: '隆基绿能' },
  { code: '600036', name: '招商银行' }, { code: 'AMD', name: '超威半导体' }, { code: 'META', name: 'Meta' },
];
const DEFAULT_NAMES: Record<string, string> = {
  '600519': '贵州茅台', '300750': '宁德时代', '002594': '比亚迪', '000858': '五粮液',
  '00700': '腾讯控股', '09988': '阿里巴巴', '03690': '美团',
  NVDA: '英伟达', TSLA: '特斯拉', AAPL: '苹果', MSFT: '微软', GOOGL: '谷歌',
};
const DEFAULT_SEARCH_KEYS: Record<string, string[]> = {
  '600519': ['茅台', '600519'], '300750': ['宁德', '300750'], '002594': ['比亚迪', '002594'], '000858': ['五粮液', '000858'],
  '00700': ['腾讯', '00700'], '09988': ['阿里', '09988'], '03690': ['美团', '03690'],
  NVDA: ['英伟达', 'NVDA', '黄仁勋'], TSLA: ['特斯拉', 'TSLA', '马斯克'], AAPL: ['苹果', 'AAPL', '库克'], MSFT: ['微软', 'MSFT'], GOOGL: ['谷歌', 'GOOGL', 'Alphabet'],
};

// ---- localStorage 持久化 ----
const LS = {
  read<T>(key: string, fallback: T): T {
    try { const raw = localStorage.getItem(key); return raw ? (JSON.parse(raw) as T) : fallback; } catch { return fallback; }
  },
  write(key: string, val: unknown) { try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* quota/private mode */ } },
};

interface Quote {
  symbol: string; price: number; change?: number | null; change_percent?: number | null;
  high?: number | null; low?: number | null; volume?: number | null; currency?: string; is_realtime?: boolean;
  open_price?: number | null; previous_close?: number | null;
  // iFinD 实时基本面（仅白名单 A股 quote 带；其他恒空）
  pe_ttm?: number | null; pb?: number | null; total_capital?: number | null; turnover_ratio?: number | null;
}
interface SearchCandidate { symbol: string; code: string; name: string; market?: string; exchange?: string; }
interface ResearchWireItem {
  id: string; title: string; org: string; date: string; created_at: string;
  filename: string; out: string; size: number; hashtag: string; download_count: number; preview_url: string;
  file_id?: string | null;
  instruments?: string[];   // 研报提及的标的（A/美/港股+黄金原油白银比特币），收报时预提取
  market?: string;          // 主要市场 A/HK/US（模型权威判定；空则前端按标题/标的启发式归类）
}
interface AiAnalysis {
  title: string; subject?: string; one_liner?: string; summary: string; core_logic?: string; takeaway?: string;
  df_take?: string;   // DeepFocus 视角点评：我方原创独立判断（转化创作，版权安全，盖 DeepFocus 水印）
  bullish?: string[]; bearish?: string[]; key_points?: string[]; risks?: string[];
  instruments?: string[]; market?: string;   // 原文提及的可交易标的（A/美/港股+黄金原油白银比特币）+ 主要市场
  rating?: string | null; target_price?: string | null; confidence?: number; pages_analyzed?: number; provider?: string;
  source_note?: string;   // 取料充分度：「已读取原文全文」/「⚠ 仅据标题概括」，诚实展示不误导
}

// 把研报 AI 解读拼成可分享的纯文本正文（落地页/深链阅读用；不带站点脚注，由分享文案/落地页自带品牌）。
// 分享的是我们自己的解读（增值内容），绝不含第三方研报原文/PDF。
function aiAnalysisToText(r: AiAnalysis, title: string): string {
  const bull = (r.bullish?.length ? r.bullish : r.key_points) || [];
  const bear = (r.bearish?.length ? r.bearish : r.risks) || [];
  const L: string[] = [];
  if (title) L.push(title);
  const meta = [r.subject && `标的 ${r.subject}`, r.rating && `评级 ${r.rating}`, r.target_price && `目标价 ${r.target_price}`].filter(Boolean) as string[];
  if (meta.length) { L.push(''); L.push(meta.join('  |  ')); }
  if (r.one_liner) { L.push(''); L.push(`💡 ${r.one_liner}`); }
  if (r.summary) { L.push('', '【摘要】', r.summary); }
  if (r.core_logic) { L.push('', '【投资逻辑】', r.core_logic); }
  if (bull.length) { L.push('', '【利好 · 看涨理由】', ...bull.map((b, i) => `${i + 1}. ${b}`)); }
  if (bear.length) { L.push('', '【利空 · 风险点】', ...bear.map((b, i) => `${i + 1}. ${b}`)); }
  if (r.takeaway) { L.push('', `📌 启示：${r.takeaway}`); }
  if (r.df_take) { L.push('', '【DeepFocus 视角 · 独家点评】', r.df_take); }
  return L.join('\n').trim();
}

const SEV_TAG: Record<RealtimeMessageSeverity, string> = { critical: '紧急', warning: '利空', success: '利好', info: '资讯' };
const STATUS_LABEL: Record<StreamConnectionStatus, string> = { connecting: 'CONNECTING', live: 'LIVE', reconnecting: 'RECONNECTING', closed: 'OFFLINE', error: 'ERROR' };
// 统一信息流：快讯 / 文章 走 DAO 推送；「研报」标签切到海外投行研报视图（在线搜索 + AI 总结）
const FEED_FILTERS = [{ key: 'all', label: '全部' }, { key: '自选', label: '★自选' }, { key: '快讯', label: '快讯' }, { key: '文章', label: '文章' }, { key: '研报', label: '研报' }];
// iFinD 专业数据：目前只对白名单账号开放（后端 DEEPFOCUS_IFIND_ALLOWED_USERS 同步硬控，前端只控入口可见性）
const IFIND_USERS = new Set(['lx199710']);

// 创始会员价限时档：每位访客「首次打开起 72 小时」滚动倒计时——永远在走、不会像固定日期那样过期哑火。
// 持久化到本地：窗口内复访继续倒计时；过期后下次打开顺延新的 72h（紧迫感长期有效）。
const FOUNDING_PROMO_END = (() => {
  const WINDOW_MS = 72 * 3600 * 1000;
  try {
    const k = 'df_promo_end_v1';
    const saved = Number(localStorage.getItem(k) || 0);
    if (saved && saved > Date.now()) return saved;     // 仍在 72h 窗口内 → 续用同一截止点
    const end = Date.now() + WINDOW_MS;                 // 新窗口：从现在起 72h
    localStorage.setItem(k, String(end));
    return end;
  } catch { return Date.now() + WINDOW_MS; }
})();
// 新人前 3 天加赠：按套餐 key 给额外天数（年卡 +1 个月、半年卡 +15 天）。
const NEW_USER_BONUS: Record<string, { days: number; label: string }> = {
  year: { days: 30, label: '新人 +1个月' },
  half: { days: 15, label: '新人 +15天' },
};
const NEW_USER_WINDOW_MS = 3 * 24 * 3600 * 1000;  // 注册后 3 天内算「新人」

// AI 助手用的工具→中文名(供对话/材料/分享复用)。
const TOOL_LABEL: Record<string, string> = {
  get_market_quote: '实时行情', get_valuation: '估值', get_financials: '财报',
  get_fund_flow: '资金流', get_analyst_consensus: '一致预期', get_stock_verdict: '速判卡',
  get_verdict_history: '历史判定', get_price_history: '价格走势', get_macro_environment: '宏观',
  get_options_signal: '期权情绪', search_our_content: '检索快讯/文章', get_stock_research: '检索券商研报', get_daily_review: '今日复盘',
};

// 安全 markdown-lite：仅支持 **加粗** / - · 列表 / 1. 有序 / 换行段落；纯 React 元素拼接，
// 绝不 dangerouslySetInnerHTML 灌 LLM 生文本（防 XSS）。
function mdInline(text: string, kp: string): React.ReactNode[] {
  const out: React.ReactNode[] = []; const re = /\*\*(.+?)\*\*/g;
  let last = 0, i = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<strong key={`${kp}-b${i++}`}>{m[1]}</strong>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length ? out : [text];
}
/** AI 问答等待假进度（对数曲线爬到 ~90% 等真结果）：静态文案干等 10-30 秒会让非会员
 * 中途关闭、白白烧掉当日免费额度；进度条 + 阶段文案让等待"看起来在干活"。 */
function AiFakeProgress() {
  const [pct, setPct] = useState(4);
  useEffect(() => {
    const t = window.setInterval(() => {
      setPct(p => (p >= 90 ? 90 : p + Math.max(0.5, (90 - p) * 0.06)));
    }, 350);
    return () => window.clearInterval(t);
  }, []);
  const stage = pct < 25 ? '正在取实时行情与估值…' : pct < 55 ? '检索我们的快讯 · 研报 · 复盘…' : pct < 80 ? '交叉核对数据、组织观点…' : '快好了，正在成稿…';
  return (
    <div className="bbt-empty" style={{ textAlign: 'left' }}>
      <div>🤖 {stage}（通常 10–30 秒）</div>
      <div style={{ height: 4, background: 'rgba(255,255,255,.08)', borderRadius: 2, marginTop: 8, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.round(pct)}%`, background: 'var(--amber,#f5b942)', transition: 'width .3s' }} />
      </div>
    </div>
  );
}

function Markdownlite({ text }: { text: string }) {
  const raw = (text || '').trim();
  if (!raw) return null;
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const L = list, k = blocks.length;
    blocks.push(L.ordered
      ? <ol key={`ol${k}`} className="bbt-md-ol">{L.items.map((it, j) => <li key={j}>{mdInline(it, `ol${k}-${j}`)}</li>)}</ol>
      : <ul key={`ul${k}`} className="bbt-md-ul">{L.items.map((it, j) => <li key={j}>{mdInline(it, `ul${k}-${j}`)}</li>)}</ul>);
    list = null;
  };
  for (const ln of raw.split('\n')) {
    const t = ln.trim();
    const ul = t.match(/^[-•·*]\s+(.*)$/);
    const ol = t.match(/^\d+[.)]\s+(.*)$/);
    if (ul) { if (!list || list.ordered) { flush(); list = { ordered: false, items: [] }; } list.items.push(ul[1]); continue; }
    if (ol) { if (!list || !list.ordered) { flush(); list = { ordered: true, items: [] }; } list.items.push(ol[1]); continue; }
    flush();
    if (t) { const k = blocks.length; blocks.push(<p key={`p${k}`} className="bbt-md-p">{mdInline(t, `p${k}`)}</p>); }
  }
  flush();
  return <div className="bbt-md">{blocks}</div>;
}
// 去 markdown 标记（出图/复制文字用）。
function stripMd(text: string): string {
  return (text || '').replace(/\*\*(.+?)\*\*/g, '$1').replace(/^[ \t]*[-•·*]\s+/gm, '· ');
}
// 「AI 看了哪些数据/材料」可折叠区：tool / 查询参数(args) / 取到的摘要(summary)。
function ToolTrace({ trace, label, defaultOpen }: { trace: any[]; label?: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(!!defaultOpen);
  if (!Array.isArray(trace) || trace.length === 0) return null;
  return (
    <div className="bbt-tt">
      <button className="bbt-tt-head" onClick={() => setOpen(o => !o)}>
        <span className="bbt-tt-caret">{open ? '▾' : '▸'}</span>
        🔎 {label || `AI 调用了 ${trace.length} 项数据 / 材料`}
      </button>
      {open && (
        <div className="bbt-tt-body">
          {trace.map((t, i) => {
            const tl = TOOL_LABEL[t.tool] || t.tool;
            const q = t && t.args ? Object.values(t.args).filter((v: any) => v != null && v !== '').map(String).join(' · ') : '';
            return (
              <div key={i} className={'bbt-tt-row' + (t.ok === false ? ' err' : '')}>
                <div className="bbt-tt-line"><span className="bbt-tt-tool">{t.ok === false ? '✕' : '✓'} {tl}</span>{q && <span className="bbt-tt-args">{q.slice(0, 48)}</span>}</div>
                {t.summary && <div className="bbt-tt-sum">{String(t.summary).slice(0, 200)}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const MARKETS: { key: 'CN' | 'HK' | 'US'; label: string }[] = [{ key: 'CN', label: 'SHA/SHE' }, { key: 'HK', label: 'HKG' }, { key: 'US', label: 'US' }];
type SortKey = 'pct' | 'price' | 'vol' | null;

// 原文是否为图片（不少文章源「原文」其实是整篇截图 PNG）→ 走站内查看器适配比例，而非裸开新标签页
function isImageUrl(url?: string | null): boolean {
  return !!url && /\.(png|jpe?g|gif|webp|bmp|avif)(\?|#|$)/i.test(url);
}
// 自有后端托管的「原文」（内部截图/PDF 域名）：不暴露原文入口、不在正文里露域名，只给 AI 解读
function isOwnHosted(m?: { url?: string | null; content?: string | null } | null): boolean {
  return /futoucaixin/i.test(m?.url || '') || /futoucaixin/i.test(m?.content || '');
}
// 清理展示用文案：去掉任意裸链接 + 收尾「请下载PDF查看」赘语 + 抹掉来源品牌字样（知识星球 / 水木调研纪要等）
function stripUrls(text?: string | null): string {
  return (text || '')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/\s*请下载\s*PDF\s*查看\s*[:：]?\s*$/i, '')
    .replace(/[【\[（(]?\s*(知识星球|水木调研纪要|水木调研|水木纪要|调研纪要)\s*[】\]）)]?[\s:：\-—|·]*/g, '')
    .replace(/[ \t　]{2,}/g, ' ')
    .trim();
}
// 文章「原文」链接：优先 url 字段；有些文章(如飞书纪要/外部 doc)把链接存在 content 正文里 → 兜底抽出首个链接。
function articleOriginalUrl(m?: { url?: string | null; content?: string | null } | null): string {
  if (!m) return '';
  if (m.url) return m.url;
  const mt = (m.content || '').match(/https?:\/\/\S+/i);
  return mt ? mt[0] : '';
}
function fmtTime(iso: string): string {
  try { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(iso)); } catch { return ''; }
}
// 智能时间：今天只显示时刻 HH:MM:SS；非今天前面带上 MM-DD（翻历史/自选时分得清是哪天）
function fmtTimeSmart(iso: string): string {
  try {
    const d = new Date(iso);
    const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' });
    const today = day.format(new Date());
    const that = day.format(d);  // YYYY-MM-DD（上海时区）
    const hm = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
    if (that === today) return fmtTime(iso);  // 今天：HH:MM:SS
    return `${that.slice(5)} ${hm}`;  // 非今天：MM-DD HH:MM
  } catch { return fmtTime(iso); }
}
function fmtMsgTime(iso: string): string {
  try { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso)); } catch { return ''; }
}
function fmtVol(v?: number | null): string {
  if (!v) return '-';
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万';
  return String(Math.round(v));
}
function fmtReportDate(iso: string): string {
  const s = (iso || '').slice(0, 10);
  return s ? s.slice(5) : '--'; // MM-DD
}
// 研报按日期分组:取归组键(YYYY-MM-DD,优先 r.date,回退 created_at)+ 日期块标题(今天/昨天/MM-DD 周X)
function resDayKey(r: ResearchWireItem): string {
  const d = (r.date || '').trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(d)) return d.slice(0, 10);
  const c = (r.created_at || '').slice(0, 10);
  return c || d || '其他';
}
function fmtResGroup(key: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key || '其他';
  const pad = (n: number) => String(n).padStart(2, '0');
  const t = new Date();
  const todayKey = `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
  const yd = new Date(t.getFullYear(), t.getMonth(), t.getDate() - 1);
  const yestKey = `${yd.getFullYear()}-${pad(yd.getMonth() + 1)}-${pad(yd.getDate())}`;
  const md = key.slice(5);
  const wd = '日一二三四五六'[new Date(`${key}T00:00:00`).getDay()];
  if (key === todayKey) return `今天 · ${md} 周${wd}`;
  if (key === yestKey) return `昨天 · ${md} 周${wd}`;
  return `${md} 周${wd}`;
}
const MAX_KEEP = 6000;   // 内存里保留的最大消息数（支持向历史翻页，不再丢历史）
const RES_RECENT_LIMIT = 250;  // 研报默认取最近一档(约覆盖最新 3 天完整;单日可达 80~90 篇,60 太小会把昨天挤成几篇);更早历史靠「加载全部历史」按需翻(归档 before=)。配合按日期手风琴折叠,默认仍只展开今天、界面干净
const EQ_MIN = 180, EQ_MAX = 560, EQ_NARROW = 360;   // 行情监视列宽拖拽：最小/最大/窄列阈值(px)
const PAGE_SIZES = [20, 30, 50];                          // 每页条数可选项
const isMobileView = () => typeof window !== 'undefined' && window.matchMedia('(max-width: 720px)').matches;
const defaultPageSize = () => {
  try { const v = Number(localStorage.getItem('df_pagesize')); if (PAGE_SIZES.includes(v)) return v; } catch { /* */ }
  return isMobileView() ? 15 : 30;   // 移动端默认更少、PC 默认 30
};

// 通用分页器：PC 显页码、移动端显「第 x/y 页」；带「N/页」选择 + 「共 N 条」
const Pager: React.FC<{ page: number; total: number; pageSize: number; onPage: (p: number) => void; onSize: (n: number) => void; busy?: boolean; hasMore?: boolean }> = ({ page, total, pageSize, onPage, onSize, busy, hasMore }) => {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const cur = Math.min(Math.max(1, page), pageCount);
  const win = 2;
  const nums: number[] = [];
  for (let i = Math.max(1, cur - win); i <= Math.min(pageCount, cur + win); i++) nums.push(i);
  if (total === 0 && !hasMore) return null;
  const nextDisabled = (cur >= pageCount && !hasMore) || !!busy;
  return (
    <div className="bbt-pager">
      <button className="bbt-pg-btn" disabled={cur <= 1 || busy} onClick={() => onPage(cur - 1)}>‹ 上一页</button>
      <span className="bbt-pg-nums">
        {nums[0] > 1 && <><button className="bbt-pg-n" onClick={() => onPage(1)}>1</button>{nums[0] > 2 && <span className="bbt-pg-ell">…</span>}</>}
        {nums.map(n => <button key={n} className={`bbt-pg-n${n === cur ? ' on' : ''}`} onClick={() => onPage(n)}>{n}</button>)}
        {nums[nums.length - 1] < pageCount && <>{nums[nums.length - 1] < pageCount - 1 && <span className="bbt-pg-ell">…</span>}<button className="bbt-pg-n" onClick={() => onPage(pageCount)}>{pageCount}</button></>}
        {hasMore && <span className="bbt-pg-ell">…</span>}
      </span>
      <span className="bbt-pg-mob">{busy ? '加载中…' : `第 ${cur}/${pageCount}${hasMore ? '+' : ''} 页`}</span>
      <button className="bbt-pg-btn" disabled={nextDisabled} onClick={() => onPage(cur + 1)}>下一页 ›</button>
      <span className="bbt-pg-meta">共 {total}{hasMore ? '+' : ''} 条</span>
      <select className="bbt-pg-size" value={pageSize} onChange={e => onSize(Number(e.target.value))} title="每页条数">
        {PAGE_SIZES.map(s => <option key={s} value={s}>{s}/页</option>)}
      </select>
    </div>
  );
};

// 注:研报「A股/港美股」分市场已取消(标题中英混杂、源头无市场字段→启发式误分多,与其分错不如不分)。
// 原 classifyMarket + RES_*_KW 关键词表随之移除;研报统一一个列表展示。

// 顶部时钟 + 市场开/收盘标签：独立组件自带每秒刷新，避免整个终端每秒全量重渲染（性能关键）。

// 资讯去重：新闻源常把同一条快讯/文章多次推送（不同 id/时间）→ 按标题(无标题用内容)归一去重。
// 入参已按时间倒序，保留最先出现的=最新那条；标题内容皆空者不参与去重、原样保留。
// 去重归一化：去掉空白、标点/符号、并小写。让「美军完成对伊朗的打击」与「美军完成了对伊朗的打击。」这类
// 仅差标点/语气词的近似重复落到同一比较基准上。
function _normKey(s: string): string {
  return (s || '')
    .replace(/[\s　]+/g, '')                          // 各类空白（含全角空格）
    // eslint-disable-next-line no-useless-escape
    .replace(/[，。、；：？！…—·「」『』（）()\[\]【】《》"'""''~`!@#$%^&*\-_=+/\\|,.<>?;:]/g, '')  // 中英标点/符号
    .toLowerCase();
}
// 带提前退出的有界编辑距离：一旦确定超过 max 立即返回 max+1（绝大多数“明显不同”的标题会在头几行就被剪掉）。
function _boundedLev(a: string, b: string, max: number): number {
  const la = a.length, lb = b.length;
  if (Math.abs(la - lb) > max) return max + 1;
  let prev = new Array(lb + 1);
  for (let j = 0; j <= lb; j++) prev[j] = j;
  for (let i = 1; i <= la; i++) {
    const cur = new Array(lb + 1);
    cur[0] = i;
    let rowMin = i;
    const ac = a.charCodeAt(i - 1);
    for (let j = 1; j <= lb; j++) {
      const cost = ac === b.charCodeAt(j - 1) ? 0 : 1;
      let v = prev[j] + 1;
      const ins = cur[j - 1] + 1; if (ins < v) v = ins;
      const sub = prev[j - 1] + cost; if (sub < v) v = sub;
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;   // 整行都超阈值 → 不可能再降回来
    prev = cur;
  }
  return prev[lb];
}
// 近似相同：长度接近且编辑距离占比 ≤14%（即 ≥86% 相似）。短标题至少允许 1 字差异（抓“了/的/标点”级别的重复）。
function _nearSame(a: string, b: string): boolean {
  if (a === b) return true;
  const len = Math.max(a.length, b.length);
  if (len < 4) return false;                              // 极短串不做模糊，避免误并
  // 数字序列不同 = 不同新闻：「沪指涨0.5%」vs「涨0.8%」这类模板化行情快讯只差一个数字，
  // 编辑距离极近但语义完全不同，绝不能当重复合并。
  const da = a.replace(/[^0-9]/g, ''), db = b.replace(/[^0-9]/g, '');
  if (da !== db) return false;
  // 二元组相似度兜底：不同稿源同义改写（称/说、袭击/打击、语序调整）编辑距离抓不住；
  // 列表里误并真新闻代价高 → 阈值取保守的 0.5（头条位由后端用更宽的 0.4 拦）。
  const ga = new Set<string>(), gb = new Set<string>();
  for (let i = 0; i < a.length - 1; i++) ga.add(a.slice(i, i + 2));
  for (let i = 0; i < b.length - 1; i++) gb.add(b.slice(i, i + 2));
  if (ga.size && gb.size) {
    let inter = 0;
    ga.forEach(x => { if (gb.has(x)) inter++; });
    if (inter / (ga.size + gb.size - inter) >= 0.5) return true;
  }
  const max = Math.max(1, Math.floor(len * 0.14));
  return _boundedLev(a, b, max) <= max;
}
function dedupeMessages(list: RealtimeMessageRecord[]): RealtimeMessageRecord[] {
  const out: RealtimeMessageRecord[] = [];
  const keptKeys: string[] = [];      // 已保留条目的归一化标题，按出现顺序
  const exact = new Set<string>();    // 完全一致快速命中
  const WINDOW = 80;                  // 仅与最近 80 条已留标题做模糊比对（重复通常时间相邻），控成本
  for (const m of list) {
    const key = _normKey((m.title || m.content || '') as string);
    if (!key) { out.push(m); continue; }
    if (exact.has(key)) continue;                         // 归一化后完全相同
    let dup = false;
    for (let i = keptKeys.length - 1, n = 0; i >= 0 && n < WINDOW; i--, n++) {
      if (_nearSame(key, keptKeys[i])) { dup = true; break; }
    }
    if (dup) continue;
    exact.add(key);
    keptKeys.push(key);
    out.push(m);
  }
  return out;
}

const FinancialTerminal: React.FC<{ appState?: any }> = () => {
  const { theme, toggleTheme } = useTheme();   // 深/浅色主题切换（持久化在 localStorage，由 ThemeProvider 写 <html data-theme>）
  // 新手引导显隐状态（自动触发逻辑在各弹窗 state 声明之后，避免 TDZ）
  const [showOnb, setShowOnb] = useState(false);
  const [showHelp, setShowHelp] = useState(false);  // 产品说明书弹层
  const [showReferral, setShowReferral] = useState(false);  // 邀请得会员弹层
  const [showAiFund, setShowAiFund] = useState(false);      // AI 模拟盘弹层
  const [showWeixinBind, setShowWeixinBind] = useState(false);  // 微信扫码绑定（扫码即问 DeepFocus）
  // 账号菜单可发现性：首次登录给一次性气泡指向头像，告知里面有会员/绑定/邀请等功能（看过即不再弹）
  const [showAcctHint, setShowAcctHint] = useState(false);
  const dismissAcctHint = useCallback(() => { setShowAcctHint(false); try { localStorage.setItem('bbt_acct_hint_v1', '1'); } catch { /* */ } }, []);
  // 操作流水打点（早定义，供下方所有 handler 复用）：登录账号精确到人(apiClient 带 JWT)，匿名按本地会话id+IP 归并
  const logAct = useCallback((action: string, target?: string) => {
    let sess = '';
    try {
      sess = localStorage.getItem('df_sess') || '';
      if (!sess) { sess = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem('df_sess', sess); }
    } catch { /* 隐私模式无 localStorage */ }
    apiPost('/api/activity', { action, target: (target || '').slice(0, 180), session: sess }).catch(() => { /* 失败忽略 */ });
  }, []);
  // 邀请入口「醒目→点开即安静」：点开过一次写 localStorage，跨刷新不再骚扰；目的=拉来第一次点击
  const [refOpened, setRefOpened] = useState<boolean>(() => { try { return localStorage.getItem('bbt_ref_opened') === '1'; } catch { return false; } });
  const openReferral = useCallback(() => {
    setShowReferral(true);
    setRefOpened(true);
    try { localStorage.setItem('bbt_ref_opened', '1'); } catch { /* 隐私模式忽略 */ }
  }, []);
  // 用户交流群（面向【所有用户】，含未登录 / 非会员）：入口按钮 + 弹层 + 首次一次性发现气泡。
  // 配置走公开端点（活码：群码每周后台换、客服名片码兜底），不入鉴权链路。
  const [groupCfg, setGroupCfg] = useState<any>(null);   // 后端 /api/community/group 配置（含 enabled / 文案 / 失效日 / 客服号）
  const [groupOpen, setGroupOpen] = useState(false);
  // 发现气泡 + 入口高亮：让「每个用户都注意到并想进群」。两级关闭——
  //  · groupSeen(永久,localStorage)：真点开过弹层才置位 → 入口冷却、气泡永不再弹；
  //  · groupHintSess(本会话,sessionStorage)：点气泡✕只本次隐藏，下次访问继续温和提醒（没进群就一直提醒）。
  const [groupSeen, setGroupSeen] = useState<boolean>(() => { try { return localStorage.getItem('df_group_seen') === '1'; } catch { return true; } });
  const [groupHintSess, setGroupHintSess] = useState<boolean>(() => { try { return sessionStorage.getItem('df_group_hint_sess') === '1'; } catch { return false; } });
  const dismissGroupHint = useCallback(() => { setGroupHintSess(true); try { sessionStorage.setItem('df_group_hint_sess', '1'); } catch { /* */ } }, []);
  const openGroup = useCallback(() => {
    setGroupOpen(true);
    setGroupSeen(true);
    try { localStorage.setItem('df_group_seen', '1'); } catch { /* */ }
    logAct('open_community', '用户交流群');
  }, [logAct]);
  useEffect(() => {  // 挂载即拉群配置（公开、轻量）：决定是否显示入口 + 一次性气泡
    apiGet<any>('/api/community/group').then(setGroupCfg).catch(() => { /* 失败静默：入口不显示 */ });
  }, []);
  // A股收盘复盘
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewData, setReviewData] = useState<any>(null);       // 当前展示的复盘
  const [reviewList, setReviewList] = useState<any[]>([]);       // 历史复盘摘要
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState(false);   // 复盘加载失败(网络/500) → 显重试，区别于「今日尚未生成」
  const reviewRetryRef = useRef<string | undefined>(undefined);  // 失败时记住该重试哪一天
  const [reviewToday, setReviewToday] = useState<any>(null);     // 首页置顶卡片用（今日复盘）
  const [trackRecord, setTrackRecord] = useState<authService.TrackRecord | null>(null);  // 「我们提前发现的」量化战绩
  // Day-1 激活：引导新用户「选自选 + 开盯盘」，建立回访触发器(留存核心)。
  // ⭐已开盯盘→永不再显;否则「稍后」只是软关闭、下次再提示,累计 3 次才彻底不显(点一次就永久消失=白白流失订阅)。
  const [activateDone, setActivateDone] = useState<boolean>(() => {
    try { return loadRecallPrefs().browserEnabled || Number(localStorage.getItem('dfx_activate_seen') || '0') >= 3; } catch { return false; }
  });
  // 加自选后的「开盯盘」上下文提示——高意向时刻顺势捕获推送订阅;每会话至多一次,不打扰。
  const [pushNudge, setPushNudge] = useState(false);
  const pushNudgeRef = useRef(false);
  // 连续看复盘签到：打开复盘即签到（登录用户），连续天数到里程碑送会员。
  // 类型放宽为 Partial<CheckinStatus>：页面加载即拉全量状态（头部常驻火焰徽章），签到 POST 只回三个字段。
  const [checkin, setCheckin] = useState<(Partial<authService.CheckinStatus> & { streak: number }) | null>(null);
  const checkinDayRef = useRef('');  // 本会话今天已签过（避免每次开弹层都重复 POST）
  const [refAvail, setRefAvail] = useState(0);  // 可兑换奖励卡总数（醒目角标）
  // 轻互动：收藏（看多/看空表态已下线——AI 情绪标签替代）
  const [bookmarks, setBookmarks] = useState<Set<string>>(new Set());
  const [bookmarksOpen, setBookmarksOpen] = useState(false);
  const [bookmarkList, setBookmarkList] = useState<authService.BookmarkItem[]>([]);
  const onbShownRef = useRef(false);
  const [onbTick, setOnbTick] = useState(0);
  const [messages, setMessages] = useState<RealtimeMessageRecord[]>([]);
  const [feedBooted, setFeedBooted] = useState(false);  // 快讯首批是否已拉完（之前显示骨架屏而非"暂无"）
  const [feedLoadError, setFeedLoadError] = useState(false);  // 首批拉取失败 → 区分「失败可重试」与「没消息」
  // ===== 快讯语音播报（Web Speech API，纯前端 TTS）=====
  // opt-in 规避浏览器自动播放限制：点击开关=用户手势=解锁音频；只读开启后新到的快讯、按标题去重、防刷屏。
  const ttsSupported = typeof window !== 'undefined' && 'speechSynthesis' in window;
  const [ttsOn, setTtsOn] = useState<boolean>(() => { try { return localStorage.getItem('bbt.tts') === '1'; } catch { return false; } });
  const ttsVoiceRef = useRef<SpeechSynthesisVoice | null>(null);
  const ttsSinceRef = useRef<string>(new Date().toISOString());   // 基准时刻：只读此后新到的快讯（不补读历史）
  const ttsSpokenRef = useRef<Set<string>>(new Set());            // 已读标题（去重，不重复念）
  useEffect(() => {
    if (!ttsSupported) return;
    const pick = () => {
      const vs = window.speechSynthesis.getVoices() || [];
      ttsVoiceRef.current = vs.find(v => /zh[-_]?CN/i.test(v.lang)) || vs.find(v => /zh/i.test(v.lang)) || null;
    };
    pick();
    window.speechSynthesis.addEventListener?.('voiceschanged', pick);
    return () => {
      window.speechSynthesis.removeEventListener?.('voiceschanged', pick);
      try { window.speechSynthesis.cancel(); } catch { /* 卸载时停掉排队/进行中的朗读，避免组件走后仍在念 */ }
    };
  }, [ttsSupported]);
  const ttsSpeak = useCallback((text: string) => {
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'zh-CN'; u.rate = 1.06; u.pitch = 1;
      if (ttsVoiceRef.current) u.voice = ttsVoiceRef.current;
      window.speechSynthesis.speak(u);
    } catch { /* */ }
  }, []);
  const toggleTts = useCallback(() => {
    setTtsOn(prev => {
      const next = !prev;
      try { localStorage.setItem('bbt.tts', next ? '1' : '0'); } catch { /* */ }
      if (next) { ttsSinceRef.current = new Date().toISOString(); ttsSpeak('快讯语音播报已开启'); }
      else { try { window.speechSynthesis.cancel(); } catch { /* */ } }
      logAct('tts', next ? 'on' : 'off');
      return next;
    });
  }, [ttsSpeak, logAct]);
  // 新快讯到达 → 朗读（仅开启后、按标题去重、一次最多读 4 条防刷屏）
  useEffect(() => {
    if (!ttsOn || !ttsSupported) return;
    const since = ttsSinceRef.current;
    if (ttsSpokenRef.current.size > 800) ttsSpokenRef.current.clear();
    // 用去重后的列表(消同一快讯多次推送)，并同时按 id+标题双重去重，杜绝重复朗读(之前"读三次"根因)
    const fresh = dedupeMessages(messages)
      .filter(m => (m.topic || '') === '快讯' && (m.created_at || '') > since)
      .sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
    const batch: RealtimeMessageRecord[] = [];
    for (const m of fresh) {
      const title = (m.title || '').trim();
      const idKey = (m.id || '').trim();
      if (!title) continue;
      if ((idKey && ttsSpokenRef.current.has(idKey)) || ttsSpokenRef.current.has(title)) continue;
      if (idKey) ttsSpokenRef.current.add(idKey);
      ttsSpokenRef.current.add(title);
      batch.push(m);
    }
    batch.slice(-4).forEach(m => ttsSpeak(`${SEV_TAG[m.severity] || '快讯'}：${m.title}`));
  }, [messages, ttsOn, ttsSupported, ttsSpeak]);
  const [pageSize, setPageSizeRaw] = useState<number>(defaultPageSize);  // 每页条数（PC/移动默认不同，可调，记 localStorage）
  const setPageSize = useCallback((n: number) => { setPageSizeRaw(n); try { localStorage.setItem('df_pagesize', String(n)); } catch { /* */ } }, []);
  const [newsPage, setNewsPage] = useState(1);  // 资讯当前页
  // 翻页时间锚点：实时流(全部/快讯)翻到第 2 页起冻结此刻最新时间，新到的快讯(比锚点新)不再插进翻页视图、
  // 避免下标分页被前移打乱(第 2 页内容错位/与第 1 页重复)。回到第 1 页清空 → 恢复实时。
  const [feedAnchor, setFeedAnchor] = useState<string | null>(null);
  const [resPage, setResPage] = useState(1);    // 研报当前页（仅「全部」搜索区的研报小节用；研报标签本身改日期手风琴、不分页）
  // 研报日期手风琴：默认仅最新一天展开。openDays=用户显式展开的旧日期；closedDays=用户显式收起的(含最新天)。
  const [resOpenDays, setResOpenDays] = useState<Set<string>>(new Set());
  const [resClosedDays, setResClosedDays] = useState<Set<string>>(new Set());
  const [resDayFull, setResDayFull] = useState<Set<string>>(new Set());   // 用户点「展开本日剩余」的日期(突破单日初始渲染上限)
  const [histLoading, setHistLoading] = useState(false);        // 正在拉取更旧历史
  const [histDone, setHistDone] = useState(false);              // 已无更多历史
  const [searchMsgs, setSearchMsgs] = useState<RealtimeMessageRecord[]>([]);  // 选股/搜索时：服务端全量历史检索结果
  const [searchLoading, setSearchLoading] = useState(false);
  const [status, setStatus] = useState<StreamConnectionStatus>('connecting');
  // 回访恢复上次的浏览视角（「自选」tab 用户天天手动点一次=浪费已投入的自选沉没成本）；写入见下方 effect
  const [feedFilter, setFeedFilter] = useState<string>(() => { try { return LS.read('bbt.feedFilter', 'all'); } catch { return 'all'; } });
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [flash, setFlash] = useState<Record<string, 'up' | 'down'>>({});
  const [active, setActive] = useState<string | null>(null);
  const [maxed, setMaxed] = useState<'eq' | 'news' | 'res' | 'sig' | null>(null);
  // 面板折叠（移动端「行情监视」默认收起，腾出资讯/研报空间）
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(
    (): Record<string, boolean> => (typeof window !== 'undefined' && window.innerWidth <= 820) ? { eq: true } : {}
  );
  const toggleCollapse = useCallback((k: string) => setCollapsed(p => ({ ...p, [k]: !p[k] })), []);
  const [headsHidden, setHeadsHidden] = useState<boolean>(() => LS.read('bbt.heads_hidden', false));  // 头条折叠态：默认展开
  useEffect(() => { LS.write('bbt.heads_hidden', headsHidden); }, [headsHidden]);
  // 行情监视列宽（px）可拖拽，记 localStorage；过窄自动隐藏附加列
  const [eqW, setEqW] = useState<number>(() => Math.max(EQ_MIN, Math.min(EQ_MAX, LS.read('bbt.eqw', 330))));
  useEffect(() => { LS.write('bbt.eqw', eqW); }, [eqW]);
  const eqNarrow = eqW < EQ_NARROW;
  const gridRef = useRef<HTMLDivElement>(null);
  const startEqDrag = useCallback((e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault();
    const apply = (clientX: number) => {
      const rect = gridRef.current?.getBoundingClientRect();
      if (rect) setEqW(Math.max(EQ_MIN, Math.min(EQ_MAX, clientX - rect.left)));
    };
    const onMove = (ev: MouseEvent) => apply(ev.clientX);
    const onTouch = (ev: TouchEvent) => { if (ev.touches[0]) { ev.preventDefault(); apply(ev.touches[0].clientX); } };
    const stop = () => {
      window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', stop);
      window.removeEventListener('touchmove', onTouch); window.removeEventListener('touchend', stop);
      document.body.style.userSelect = '';
    };
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove); window.addEventListener('mouseup', stop);
    window.addEventListener('touchmove', onTouch, { passive: false }); window.addEventListener('touchend', stop);
  }, []);

  // 选股三列(快讯/文章/研报)列宽可拖拽（px；未拖则用 fr 默认）
  const [scW, setScW] = useState<{ kx?: number; wz?: number }>(() => LS.read('bbt.scw', {} as { kx?: number; wz?: number }));
  useEffect(() => { LS.write('bbt.scw', scW); }, [scW]);
  const startScDrag = useCallback((key: 'kx' | 'wz') => (e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault();
    const apply = (clientX: number) => {
      const colEl = document.querySelector(`.bbt-stockcol--${key}`);
      if (colEl) setScW(prev => ({ ...prev, [key]: Math.max(150, Math.min(720, clientX - colEl.getBoundingClientRect().left)) }));
    };
    const onMove = (ev: MouseEvent) => apply(ev.clientX);
    const onTouch = (ev: TouchEvent) => { if (ev.touches[0]) { ev.preventDefault(); apply(ev.touches[0].clientX); } };
    const stop = () => {
      window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', stop);
      window.removeEventListener('touchmove', onTouch); window.removeEventListener('touchend', stop);
      document.body.style.userSelect = '';
    };
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove); window.addEventListener('mouseup', stop);
    window.addEventListener('touchmove', onTouch, { passive: false }); window.addEventListener('touchend', stop);
  }, []);
  const [reports, setReports] = useState<ResearchWireItem[]>([]);
  const [reportDq, setReportDq] = useState<{ level?: string; label?: string; detail?: string } | null>(null);
  const [resQuery, setResQuery] = useState('');           // 研报在线全局搜索关键词
  const [resLoading, setResLoading] = useState(false);
  // 研报默认只展示最近一档(干净);更早历史靠底部「加载更早」按需翻(归档 before= 分页,历史一条不丢)
  const [resHistDone, setResHistDone] = useState(false);  // 已无更早历史
  const [resMoreLoading, setResMoreLoading] = useState(false);
  const resHistLoadedRef = useRef(false);                 // 用户已翻过历史 → 暂停自动刷新(免得把展开的历史收回去)
  const [resSyncedAt, setResSyncedAt] = useState<Date | null>(null);  // 研报最近一次成功同步时刻
  const [newsPreview, setNewsPreview] = useState<RealtimeMessageRecord | null>(null);  // 文章在线预览
  const [aiReport, setAiReport] = useState<{ title?: string; date?: string } | null>(null);  // AI 解读对象（研报或文章）
  // 研报解读分享：非空=当前 AI 解读是「研报」（带机构/标的 + 原文 preview_url，可生成分享落地页）；null=文章解读
  const [aiReportMeta, setAiReportMeta] = useState<{ org?: string; symbol?: string; preview_url?: string } | null>(null);
  const [pdfLoadingUrl, setPdfLoadingUrl] = useState<string | null>(null);  // 正在加载的研报原文 URL
  const [reportShareBusy, setReportShareBusy] = useState(false);
  const [shareModal, setShareModal] = useState<{ open: boolean; target: ShareTarget | null }>({ open: false, target: null });
  const aiRetryRef = useRef<null | (() => void)>(null);
  const [shareImgUrl, setShareImgUrl] = useState<string>('');  // 出图兜底预览（长按保存）
  const [aiResult, setAiResult] = useState<AiAnalysis | null>(null);
  const [dfExpanded, setDfExpanded] = useState(false);  // DeepFocus 视角深度点评：长文默认收起，点「展开全文」看全
  const [aiLoading, setAiLoading] = useState(false);
  const [aiProgress, setAiProgress] = useState(0);  // AI 解读进度条（按耗时渐近爬升，完成即收）
  const [aiError, setAiError] = useState('');
  const [upgradeOpen, setUpgradeOpen] = useState(false);   // 开通会员引导弹层
  const [upgradeReason, setUpgradeReason] = useState('');
  const [aiCopied, setAiCopied] = useState(false);
  const [aiTextCopied, setAiTextCopied] = useState(false);
  const [copiedNewsId, setCopiedNewsId] = useState('');
  const [picks, setPicks] = useState<{ kx?: any; wz?: any; yb?: any } | null>(null);  // AI 评选的头条
  const [newsQuery, setNewsQuery] = useState('');  // 快讯/文章 模糊搜索
  const [toast, setToast] = useState('');
  const [shareImgNote, setShareImgNote] = useState('');
  const [shareImgCoarse, setShareImgCoarse] = useState(false);  // 移动端（触屏）：只引导长按
  const showToast = useCallback((msg: string) => { setToast(msg); window.setTimeout(() => setToast(''), 2800); }, []);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [pq, setPq] = useState('');
  const [paletteActive, setPaletteActive] = useState(0);   // 命令面板当前高亮项（↑↓ 导航）
  const [paletteLoading, setPaletteLoading] = useState(false);
  const paletteInput = useRef<HTMLInputElement>(null);
  const paletteActiveRef = useRef<HTMLDivElement>(null);
  const seen = useRef<Set<string>>(new Set());
  const prevPrice = useRef<Record<string, number>>({});
  const latestTsRef = useRef<string>('');  // 已加载消息的最新时间戳（增量轮询用）

  // 自选股 + 名称(可编辑、持久化)
  const [watchlist, setWatchlist] = useState<string[]>(() => LS.read('bbt.watchlist', DEFAULT_WATCHLIST));
  const [names, setNames] = useState<Record<string, string>>(() => ({ ...DEFAULT_NAMES, ...LS.read('bbt.names', {} as Record<string, string>) }));
  const [sortKey, setSortKey] = useState<SortKey>(null);
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  // 命令面板远程搜索结果
  const [remoteHits, setRemoteHits] = useState<SearchCandidate[]>([]);

  const nameOf = useCallback((sym: string) => names[sym] || sym, [names]);
  const keysOf = useCallback((sym: string) => DEFAULT_SEARCH_KEYS[sym] || [sym, names[sym]].filter(Boolean) as string[], [names]);
  // 公司简称：去掉括号/后缀(控股/集团/股份/科技…)，提升召回（腾讯控股→腾讯、迈威尔科技→迈威尔）
  const stockShort = useCallback((sym: string) => {
    const full = (names[sym] || sym);
    return (full.replace(/[-（(].*$/, '').replace(/(控股|集团|股份|科技|有限公司|公司|银行|证券|保险|国际|实业|发展|半导体)$/, '').trim() || full);
  }, [names]);
  const isUsTicker = (s: string) => /^[A-Za-z.]{1,7}$/.test(s);
  // 研报在线检索关键词【序列】：中文优先(中文简称→中文全名)，美股再补英文代码兜底。
  // 研报源(含海外投行)多数按中文公司名收录标题(实测「台积电」30 篇、「TSM/TSMC」0 篇)，
  // 但知识星球里也有英文标题的投行原文(如 TSMC Q3 preview)——故按此序逐个检索、去重累加，召回最大。
  const researchKws = useCallback((sym: string): string[] => {
    const short = stockShort(sym);
    const full = (names[sym] || '').trim();
    const out = [short];                       // ① 中文简称(召回最高)
    if (full && full !== short) out.push(full); // ② 中文全名
    if (isUsTicker(sym)) out.push(sym);         // ③ 英文代码(英文标题原文兜底)
    return Array.from(new Set(out.filter(Boolean)));
  }, [stockShort, names]);
  // 快讯/文章多别名(OR命中)：简称+全名+(美股)代码，逗号分隔传后端
  const stockAliases = useCallback((sym: string): string[] => {
    const full = (names[sym] || '').trim();
    const arr = [stockShort(sym), full];
    if (isUsTicker(sym)) arr.push(sym);
    return Array.from(new Set(arr.filter(Boolean)));
  }, [names, stockShort]);
  // 研报有效检索词【序列】：手输优先(单词)，否则选中个股的多关键词序列(中文→英文，去重累加)
  const resSearchKws = useMemo<string[]>(() => {
    const manual = resQuery.trim();
    if (manual) return [manual];
    return active ? researchKws(active) : [];
  }, [resQuery, active, researchKws]);
  // 首词：用于展示文案与真值判断(有无在线检索)；下游 if(resSearchKw)/!!resSearchKw 仍可用
  const resSearchKw = useMemo(() => resSearchKws[0] || '', [resSearchKws]);
  // 资讯检索：手输关键词(AND) 或 选中个股(多别名OR)
  const newsManual = newsQuery.trim();
  const newsAliases = useMemo(() => (active ? stockAliases(active) : []), [active, stockAliases]);
  const newsSearching = !!newsManual || !!active;
  // 选中个股：切到 ALL、清掉手输搜索，让该股驱动 快讯/文章/研报 的全量历史检索；再点同一支=取消
  const selectStock = useCallback((sym: string) => {
    setActive(prev => { if (prev !== sym) logAct('select_stock', sym); return prev === sym ? null : sym; });
    setFeedFilter('all'); setNewsQuery(''); setResQuery('');
  }, [logAct]);

  useEffect(() => { LS.write('bbt.feedFilter', feedFilter); }, [feedFilter]);

  const addSymbol = useCallback(async (code: string, name?: string, activate = true) => {
    const sym = (code || '').trim();
    if (!sym) return;
    // 加之前先校验行情：拿不到价 = 无效代码/乱输入 → 不加，给提示（挡掉「MUXIGUFEN」这类垃圾）
    try {
      const resp = await apiGet<{ quotes: Quote[] }>('/api/market/quotes', { params: { symbols: sym } });
      const q = (resp.quotes || [])[0];
      if (!q || q.price == null) { showToast(`未找到「${name || sym}」的行情，未添加`); return; }
    } catch { showToast('行情校验失败，请稍后再试'); return; }
    if (name) setNames(prev => ({ ...prev, [sym]: name }));
    setWatchlist(prev => (prev.includes(sym) ? prev : [...prev, sym]));
    if (activate) setActive(sym);  // 命令面板加股=下钻该标的；激活卡加股传 false，只悄悄入自选、不跳转
    logAct('watch_add', name ? `${sym} ${name}` : sym);
    showToast(`已添加自选 ${name || sym}`);
    // 高意向时刻:刚加自选 → 若还没开盯盘且通知未被拒,弹一次「开盯盘」提示把这份意向变成离线订阅(每会话至多一次)
    try {
      if (!pushNudgeRef.current && !loadRecallPrefs().browserEnabled && getNotificationPermission() !== 'denied') {
        pushNudgeRef.current = true; setPushNudge(true);
      }
    } catch { /* */ }
  }, [showToast, logAct]);
  const removeSymbol = useCallback((code: string) => {
    setWatchlist(prev => prev.filter(s => s !== code));
    setActive(prev => (prev === code ? null : prev));
    logAct('watch_remove', code);
  }, [logAct]);

  // Day-1 激活：开启盯盘提醒（自选出快讯/异动把用户叫回来 = 回访触发器）
  const armRecall = useCallback(async () => {
    let perm: string = 'default';
    try { perm = await requestBrowserPermission(); } catch { /* */ }
    try {
      saveRecallPrefs({ ...loadRecallPrefs(), browserEnabled: true });
      window.dispatchEvent(new Event(RECALL_PREFS_EVENT));
      localStorage.setItem('dfx_activate_seen', '3');  // 已开启→彻底不再提示
    } catch { /* */ }
    // 关页也能被叫回:注册 Web Push 离线订阅(仅授权后有意义;best-effort 内部已吞错,不阻塞)
    if (perm === 'granted') { try { void subscribeWebPush({ symbols: watchlist }); } catch { /* */ } }
    // 邮箱兜底订阅:国内 Chrome 推送端点在 Google FCM 送不到,账号留了邮箱就顺手建邮件召回(服务端直取,无邮箱静默)
    try { void subscribeEmailRecall(); } catch { /* */ }
    setActivateDone(true); setPushNudge(false);
    showToast(perm === 'granted'
      ? '🔔 盯盘已开启 · 自选有快讯/异动第一时间通知你(关掉页面也能收到)'
      : '✅ 盯盘已开启 · 浏览器通知未授权可在系统设置允许，以便离开页面也能收到提醒');
  }, [showToast, watchlist]);
  const dismissActivate = useCallback(() => {
    // 软关闭:累计次数,本次会话先收起,下次仍提示,满 3 次才彻底不显(避免点一次就永久流失订阅)
    try { localStorage.setItem('dfx_activate_seen', String(Number(localStorage.getItem('dfx_activate_seen') || '0') + 1)); } catch { /* */ }
    setActivateDone(true);
  }, []);

  // 页面访问打点（每次加载记一次；ref 防 StrictMode 双触发）
  const pageviewSent = useRef(false);
  useEffect(() => {
    if (pageviewSent.current) return;
    pageviewSent.current = true;
    apiPost('/api/metrics/pageview').catch(() => { /* 打点失败不影响使用 */ });
  }, []);

  useEffect(() => {  // 进入页面记一条，target 带来源（referrer + utm），用于拉新脉冲归因
    let src = '';
    try {
      const q = new URLSearchParams(window.location.search);
      const utm = ['utm_source', 'utm_medium', 'utm_campaign'].map(k => q.get(k)).filter(Boolean).join('/');
      const ref = document.referrer && !document.referrer.includes(window.location.hostname) ? new URL(document.referrer).hostname : '';
      src = [utm, ref].filter(Boolean).join(' · ');
    } catch { /* 来源解析失败不影响打点 */ }
    logAct('pageview', src);
  }, [logAct]);

  // AI 头条评选（华尔街视角）：加载即拉 + 每 3 分钟刷新
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try { const d = await apiGet<any>('/api/headlines'); if (!cancelled) setPicks(d || null); } catch { /* 回退本地规则 */ }
    };
    load();
    const t = window.setInterval(load, 180000);
    return () => { cancelled = true; window.clearInterval(t); };
  }, []);

  // 命令面板：点击命令栏唤起（⌘K 留给 App 全局面板）；Esc 关 + 清选中；↑/↓ 扫描自选股
  const navRef = useRef<string[]>(watchlist);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setPaletteOpen(false); setMaxed(null); setActive(null); setNewsPreview(null); setAiReport(null); return; }
      if (paletteOpen) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === '/' || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k')) { e.preventDefault(); setPaletteOpen(true); setPq(''); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const list = navRef.current;
        if (!list.length) return;
        const dir = e.key === 'ArrowDown' ? 1 : -1;
        setActive(prev => {
          if (!prev) return dir > 0 ? list[0] : list[list.length - 1];
          const i = list.indexOf(prev);
          if (i < 0) return list[0];
          return list[(i + dir + list.length) % list.length];
        });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen]);
  useEffect(() => { if (paletteOpen) setTimeout(() => paletteInput.current?.focus(), 30); }, [paletteOpen]);
  useEffect(() => { paletteActiveRef.current?.scrollIntoView({ block: 'nearest' }); }, [paletteActive]);

  const [quotesError, setQuotesError] = useState(false);  // 行情整体拉取失败（仅零报价时提示）
  const quoteAtRef = useRef<Record<string, number>>({});  // 每只股最近一次「真拿到价」的时刻——新鲜度标注用（防静默陈旧）
  const loadQuotes = useCallback(async () => {
    // 轮询集合 = 自选 ∪ 当前下钻标的：搜索下钻的新股（未加自选）也要有实时价，匿名试看才成立
    const polled = active && !watchlist.includes(active) ? [...watchlist, active] : watchlist;
    if (!polled.length) { setQuotes({}); return; }
    try {
      const resp = await apiGet<{ quotes: Quote[] }>('/api/market/quotes', { params: { symbols: polled.join(',') } });
      const map: Record<string, Quote> = {}; const flashes: Record<string, 'up' | 'down'> = {};
      (resp.quotes || []).forEach(q => {
        map[q.symbol] = q;
        quoteAtRef.current[q.symbol] = Date.now();  // 真拿到价才刷新时间戳；沿用旧值的不刷新 → 可识别陈旧
        const prev = prevPrice.current[q.symbol];
        if (prev != null && q.price !== prev) flashes[q.symbol] = q.price > prev ? 'up' : 'down';
        prevPrice.current[q.symbol] = q.price;
      });
      setQuotes(prev => {
        // 免费行情源偶发漏返某只 → 沿用上一次报价，避免该行瞬间变「—」（仅保留仍在轮询集合里的）
        polled.forEach(sym => { if (!map[sym] && prev[sym]) map[sym] = prev[sym]; });
        return map;
      });
      if (Object.keys(flashes).length) { setFlash(flashes); window.setTimeout(() => setFlash({}), 900); }
      setQuotesError(false);
    } catch { setQuotesError(true); }  // 整体失败 → 仅在零报价时提示，漏价沿用旧值不算失败
  }, [watchlist, active]);
  // 自适应刷新：有市场开盘时 5s 一刷（更跟手），全部休市降到 30s（省请求、休市价不动）。
  // 用「拉完再排下一次」避免源慢时请求重叠。
  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    const tick = async () => {
      await loadQuotes();
      if (stopped) return;
      const anyOpen = MARKETS.some(m => isMarketOpen(m.key, new Date()));
      timer = window.setTimeout(tick, anyOpen ? 5000 : 30000);
    };
    tick();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [loadQuotes]);

  // ---- 宏观/大盘总览（VIX/利率/汇率/商品/加密/指数）----
  const [macro, setMacro] = useState<Record<string, any>>({});
  // 宏观条手机默认收起（首屏寸土寸金），点 MACRO 标签展开；桌面默认展开
  const [macroOpen, setMacroOpen] = useState<boolean>(() => typeof window === 'undefined' || window.innerWidth > 820);
  const [macroFailed, setMacroFailed] = useState(false);  // 宏观拉取失败 → 显「暂不可用」而非永久假「加载中」
  const loadMacro = useCallback(async () => {
    try {
      const d = await apiGet<{ categories: { indicators: any[] }[] }>('/api/market-dashboard');
      const map: Record<string, any> = {};
      (d.categories || []).forEach(cat => (cat.indicators || []).forEach(ind => { if (ind && ind.key) map[ind.key] = ind; }));
      setMacro(map); setMacroFailed(false);
    } catch { setMacroFailed(true); }  // 慢数据，下轮自动重试
  }, []);
  useEffect(() => { loadMacro(); const t = window.setInterval(loadMacro, 60000); return () => window.clearInterval(t); }, [loadMacro]);

  // ---- 研报流（海外投行报告）：空关键词=最新，带关键词=在线全局检索 ----
  const loadReports = useCallback(async (q: string | string[] = '') => {
    setResLoading(true);
    try {
      const kws = (Array.isArray(q) ? q : [q]).map(s => s.trim()).filter(Boolean);
      if (kws.length <= 1) {
        // 默认(空 q)只取最新一档,界面干净;搜索仍走全量历史归档(400)。更早历史靠底部「加载更早」按需翻(before=)。
        const params: Record<string, any> = { limit: kws[0] ? 400 : RES_RECENT_LIMIT };
        if (kws[0]) params.q = kws[0];
        const d = await apiGet<{ items: ResearchWireItem[]; data_quality?: any }>('/api/research/wire', { params });
        setReports(d.items || []);
        setReportDq(d.data_quality || null);
        setResHistDone((d.items || []).length < RES_RECENT_LIMIT);  // 不足一档=没更早了
        resHistLoadedRef.current = false;  // 回到最新视图,恢复自动刷新
      } else {
        // 多关键词(中文→英文)并发检索，按序累加去重——中文命中在前，英文标题原文补在后
        const ds = await Promise.all(kws.map(kw =>
          apiGet<{ items: ResearchWireItem[]; data_quality?: any }>('/api/research/wire', { params: { limit: 400, q: kw } })
            .catch(() => ({ items: [] as ResearchWireItem[], data_quality: null }))
        ));
        const seen = new Set<string>();
        const merged: ResearchWireItem[] = [];
        ds.forEach(d => (d.items || []).forEach(it => {
          const key = (it.id || it.file_id || it.preview_url || it.title || '').trim().toLowerCase();
          if (key && !seen.has(key)) { seen.add(key); merged.push(it); }
        }));
        setReports(merged);
        setReportDq(ds.find(d => d.data_quality)?.data_quality || null);
        setResHistDone(true);              // 搜索已是全量归档,无「加载更早」
        resHistLoadedRef.current = false;
      }
      setResSyncedAt(new Date());
    } catch {
      // 首次加载即失败时 reportDq 仍为空 → 给明确错误态，避免永久假「加载中」死胡同
      setReportDq(prev => prev || { level: 'error', detail: '研报同步失败，可能是源不稳，点右上角 ⟳ 重试' });
    } finally { setResLoading(false); }
  }, []);
  // 一次性加载全部更早历史:循环用归档 before= 往回翻到底(渐进填充,边翻边显),不用反复点。历史一条不丢。
  const loadMoreReports = useCallback(async () => {
    if (resMoreLoading || resHistDone) return;
    setResMoreLoading(true);
    resHistLoadedRef.current = true;       // 翻历史中 → 暂停自动刷新,不收回已展开的历史
    const keyOf = (it: ResearchWireItem) => (it.id || it.file_id || it.title || '').trim().toLowerCase();
    try {
      let cur = reports;
      for (let guard = 0; guard < 40; guard++) {   // 守护上限:40×60=2400 条,远超归档量,防异常死循环
        const oldest = cur.length ? (cur[cur.length - 1].date || '').slice(0, 10) : '';
        if (!oldest) break;
        const d = await apiGet<{ items: ResearchWireItem[] }>('/api/research/wire', { params: { limit: RES_RECENT_LIMIT, before: oldest } });
        const older = d.items || [];
        const seen = new Set(cur.map(keyOf));
        const add = older.filter(it => { const k = keyOf(it); return k && !seen.has(k); });
        if (add.length) { cur = [...cur, ...add]; setReports(cur); }   // 渐进更新:列表边翻边变长
        if (!add.length || older.length < RES_RECENT_LIMIT) { setResHistDone(true); break; }
      }
    } catch { /* 下次再试 */ } finally { setResMoreLoading(false); }
  }, [reports, resMoreLoading, resHistDone]);
  // 关键词去抖在线检索；空关键词回到最新流并每分钟自动同步知识星球
  useEffect(() => {
    const kws = resSearchKws;   // 手输研报搜索 或 选中个股(中文→英文序列) → 在线全量检索历史研报
    if (!kws.length) {
      loadReports('');
      // 自动同步最新;但用户正在翻历史(resHistLoadedRef)时跳过,免得把展开的更早研报收回去
      const t = window.setInterval(() => { if (!resHistLoadedRef.current) loadReports(''); }, 60000);
      return () => window.clearInterval(t);
    }
    const t = window.setTimeout(() => { loadReports(kws); logAct('search', '研报:' + kws.join('+')); }, 350);
    return () => window.clearTimeout(t);
  }, [resSearchKws, loadReports, logAct]);

  // ---- 账号登录态（终端独占页：AI 解读 / 查看原文 = 登录网关，行情与资讯免费）----
  const [authUser, setAuthUser] = useState<string | null>(null);
  const authUserRef = useRef<string | null>(null);
  const refreshMembershipRef = useRef<(() => void) | null>(null);  // 解决定义顺序：签到发奖后刷新会员态
  // 已登录用户的专属邀请码——用于让所有分享卡的二维码带 ?ref=，扫码注册即归到分享者名下（拉新闭环）。
  const inviteCodeRef = useRef<string>('');
  const [membership, setMembership] = useState<authService.Membership | null>(null);  // 会员状态：体验期/尊享会员
  const [isAdmin, setIsAdmin] = useState(false);  // 后端角色=管理员（决定是否显示「管理员」标签，不再写死「站长」）
  const [trialClaimable, setTrialClaimable] = useState(false);  // 可领「登录送 3 天体验会员」
  const [joinedAt, setJoinedAt] = useState('');  // 注册时间(account.created_at)，判「新人前三天」福利
  const [expiryDismissed, setExpiryDismissed] = useState(false);  // 本次会话关闭到期续费条
  const [trialClaiming, setTrialClaiming] = useState(false);    // 领取请求进行中
  const [acctOpen, setAcctOpen] = useState(false);  // 头像下拉（账号 + 会员 + 邀请 + 登出）
  // 兑换会员码
  const [redeemOpen, setRedeemOpen] = useState(false);
  const [redeemInput, setRedeemInput] = useState('');
  const [redeemBusy, setRedeemBusy] = useState(false);
  const redeemBusyRef = useRef(false);  // 同步防重复提交：状态更新是异步的，连点两下会都读到旧值，必须用 ref 拦
  // iFinD 专业数据面板（白名单账号）
  const [ifindOpen, setIfindOpen] = useState(false);
  const [ifindInput, setIfindInput] = useState('600519,300750,000858');
  const [ifindRows, setIfindRows] = useState<authService.IfindRow[]>([]);
  const [ifindBusy, setIfindBusy] = useState(false);
  const [ifindErr, setIfindErr] = useState('');
  const runIfind = useCallback(async (codes: string) => {
    const q = codes.trim(); if (!q) return;
    setIfindBusy(true); setIfindErr('');
    const r = await authService.fetchIfindQuote(q);
    if (r.ok) { setIfindRows(r.rows || []); if (!(r.rows || []).length) setIfindErr('无数据（仅支持 A 股代码）'); }
    else { setIfindRows([]); setIfindErr(r.error || '查询失败'); }
    setIfindBusy(false);
  }, []);
  const openIfind = useCallback(() => { setIfindOpen(true); logAct('tab', 'ifind'); runIfind(ifindInput); }, [ifindInput, runIfind, logAct]);
  // AI 对话（tool-use agent：行情/估值/iFinD + 搜我们的快讯/研报/复盘）。灰度白名单账号。
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInput, setAiInput] = useState('');
  const [aiBusy, setAiBusy] = useState(false);
  const [aiTools, setAiTools] = useState<ToolTraceItem[]>([]);
  const [aiAnswer, setAiAnswer] = useState('');
  const [aiQuestion, setAiQuestion] = useState('');   // 已作答的问题（对话回显）
  const [aiErr, setAiErr] = useState('');
  const [aiSugg, setAiSugg] = useState<string[]>([]);            // 答案后的确定性追问建议（后端按工具轨迹反推）
  const [aiQuotaLeft, setAiQuotaLeft] = useState<number | null>(null);  // 剩余免费次数；null=不限/未知
  const [aiFeedback, setAiFeedback] = useState<'' | 'up' | 'down'>(''); // 本条答案已投的反馈
  const aiHistoryRef = useRef<Array<[string, string]>>([]);      // 最近几轮 [问,答]——多轮追问记忆（会话内）
  const askAi = useCallback(async (q?: string) => {
    const msg = (q ?? aiInput).trim();
    if (!msg || aiBusy) return;
    setAiBusy(true); setAiErr(''); setAiAnswer(''); setAiTools([]); setAiQuestion(msg); setAiSugg([]); setAiFeedback('');
    logAct('ai_chat', msg.slice(0, 60));
    const r = await runToolResearch(msg, '', '', aiHistoryRef.current);
    if (!r.ok && r.status === 402) {  // 非会员今日免费额度用完 → 升级弹窗（绑当下问题）
      setUpgradeReason(r.error || '今日免费 AI 问答已用完，开通会员畅享无限');
      setUpgradeOpen(true); setAiBusy(false); return;
    }
    if (!r.ok && r.status === 403) {  // 匿名 → 引导登录（送 3 天）
      setAiErr(r.error || '登录即可继续用 AI 问答，还送 3 天尊享会员 🎁'); setAiBusy(false); return;
    }
    if (r.ok && r.answer) {
      setAiAnswer(r.answer); setAiTools(r.tool_trace || []);
      setAiSugg(r.suggestions || []);
      setAiQuotaLeft(typeof r.quota_left === 'number' ? r.quota_left : null);
      // 会话记忆：记最近 3 轮，追问『那估值呢』才接得上（此前 web 端每问都失忆）
      aiHistoryRef.current = [...aiHistoryRef.current.slice(-2), [msg, r.answer.slice(0, 300)]];
    }
    else setAiErr(r.error || r.reason || '暂时没有得出结论，换个问法或稍后再试');
    setAiBusy(false);
  }, [aiInput, aiBusy, logAct]);
  const openAi = useCallback(() => { setAiOpen(true); logAct('tab', 'ai_chat'); }, [logAct]);
  // 深度研判（多智能体辩论：取证→多空立论→交叉反驳→风控→投委会裁决）。纯轮询，灰度白名单。
  const [deepMode, setDeepMode] = useState(false);
  const [deepSymbol, setDeepSymbol] = useState('');
  const [deepTask, setDeepTask] = useState<DeepTask | null>(null);
  const [deepBusy, setDeepBusy] = useState(false);
  const [deepErr, setDeepErr] = useState('');
  const deepPollRef = useRef<number | null>(null);
  const deepPollsRef = useRef(0);
  const deepInFlightRef = useRef(false);   // 单次轮询在途标记：上次没回来就跳过本次，杜绝 2s 间隔下请求叠加（曾因轮询叠加吃过 429）
  const deepStartingRef = useRef(false);    // 发起研判同步锁：防极快连点在 setDeepBusy 生效前并发建出两个轮询
  const stopDeepPoll = useCallback(() => {
    if (deepPollRef.current) { window.clearInterval(deepPollRef.current); deepPollRef.current = null; }
    deepInFlightRef.current = false;
  }, []);
  const startDeep = useCallback(async (force = false) => {
    const sym = deepSymbol.trim();
    if (!sym || deepBusy || deepStartingRef.current) return;
    deepStartingRef.current = true;
    setDeepBusy(true); setDeepErr(''); setDeepTask(null); deepPollsRef.current = 0;
    logAct(force ? 'deep_research_redo' : 'deep_research_start', sym);
    try {
      const nm = (namesRef.current[sym] || '').trim();  // 名称自动从自选名称表带上；取不到就空，agent 会从行情取真名
      const { task_id } = await startDeepResearch(sym, nm, 'CN', force);
      stopDeepPoll();
      deepPollRef.current = window.setInterval(async () => {
        if (deepInFlightRef.current) return;  // 上一次轮询还没回来 → 跳过本次，避免请求叠加
        deepPollsRef.current += 1;
        if (deepPollsRef.current > 90) { stopDeepPoll(); setDeepBusy(false); setDeepErr('研判超时，请稍后重试'); return; }
        deepInFlightRef.current = true;
        try {
          const t = await pollDeepResearch(task_id);
          setDeepTask(t);
          if (t.status === 'done' || t.status === 'error') {
            stopDeepPoll(); setDeepBusy(false);
            if (t.status === 'error') setDeepErr(t.error || '研判失败，请重试');
            else logAct('deep_research_done', sym);
          }
        } catch { /* 单次轮询失败不致命，继续；maxPolls 兜底 */ }
        finally { deepInFlightRef.current = false; }
      }, 2000);
    } catch (e: any) {
      setDeepBusy(false);
      setDeepErr(e?.response?.data?.detail || e?.message || '发起失败，请稍后重试');
    } finally {
      deepStartingRef.current = false;
    }
  }, [deepSymbol, deepBusy, logAct, stopDeepPoll]);
  const enterDeepMode = useCallback(() => {
    setDeepMode(true);
    setDeepSymbol(prev => prev || watchlistRef.current[0] || '');  // 默认带入第一只自选股
  }, []);
  // 关弹窗 / 卸载 → 停轮询（避免后台空转）
  useEffect(() => { if (!aiOpen) stopDeepPoll(); }, [aiOpen, stopDeepPoll]);
  useEffect(() => () => stopDeepPoll(), [stopDeepPoll]);
  // 开通会员（购买页：收款码 + 套餐 + 一键私信）
  const [buyOpen, setBuyOpen] = useState(false);
  const [buyPaid, setBuyPaid] = useState(false);   // 两步走：用户点「我已完成付款」后才揭示"发凭证给管理员开通"这步
  const buyOpenAtRef = useRef(0);            // 弹窗打开时刻：buy_close 带停留时长
  const buyQrViewedRef = useRef(false);      // 本次打开是否已记过 buy_qr_view（看码≥5s 只记一次）
  const buyOutcomeRef = useRef('');          // 已转化标记(我已付款/领体验卡)：转化后关闭不再记 buy_close
  const [payCfg, setPayCfg] = useState<authService.PaymentConfig | null>(null);
  const [buyPkg, setBuyPkg] = useState('');
  // 后台私信（联系管理员）
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportMsgs, setSupportMsgs] = useState<authService.SupportMessage[]>([]);
  const [supportText, setSupportText] = useState('');
  const [supportSending, setSupportSending] = useState(false);
  const [supportUnread, setSupportUnread] = useState(0);
  const [adminUnread, setAdminUnread] = useState(0);   // 管理员侧：用户发来的未读私信总数（仅管理员主页提醒）
  const supportUnreadRef = useRef(0);  // 未读基准：只有"变多"(新回复到达)才弹提示，避免每轮/每次进页面都弹
  const applySupportUnread = useCallback((n: number, notify: boolean) => {
    if (notify && n > supportUnreadRef.current) showToast('💬 管理员回复了你的私信 · 点右上角头像查看');
    supportUnreadRef.current = n;
    setSupportUnread(n);
  }, [showToast]);
  // A股复盘：打开弹层（拉今日 + 历史），以及首页置顶卡片的今日复盘
  const openReview = useCallback(async (date?: string) => {
    setReviewOpen(true); setReviewLoading(true); setReviewError(false); reviewRetryRef.current = date;
    authService.fetchTrackRecord().then(tr => setTrackRecord(tr)).catch(() => {});  // 「我们提前发现的」战绩
    try {
      if (date) {
        const r = await apiGet<{ review: any }>(`/api/review/${date}`);
        setReviewData(r.review);
      } else {
        const [t, l] = await Promise.all([
          apiGet<{ exists: boolean; review?: any }>('/api/review/today'),
          apiGet<{ items: any[] }>('/api/review/list', { params: { limit: 30 } }),
        ]);
        setReviewData(t.exists ? t.review : null);
        setReviewList(l.items || []);
      }
      logAct('open_review', date || 'today');
    } catch {
      setReviewError(true);  // 区分「加载失败」与「今日尚未生成」：失败给重试，不误导成没复盘
    } finally { setReviewLoading(false); }
    try {
      // 连续看复盘签到：登录用户打开复盘即签到（每天一次，里程碑送会员）
      const todayKey = new Date().toDateString();
      if (authUserRef.current && checkinDayRef.current !== todayKey) {
        checkinDayRef.current = todayKey;
        const r = await authService.checkin();
        if (r?.ok) {
          setCheckin(prev => ({ ...(prev || {}), streak: r.streak, total: r.total, longest: r.longest, checked_today: true }));
          const ms = r.milestone;
          if (ms) {  // milestone 非空 = 服务端本次新发的里程碑（已去重），无论是否今日首签都要庆祝
            if (ms.reward_days > 0) {
              showToast(`🎉 连续看盘 ${ms.days} 天！已送你 ${ms.reward_days} 天尊享会员`);
              refreshMembershipRef.current?.();
            } else {
              showToast(`🔥 已连续看盘 ${ms.days} 天，继续保持解锁会员奖励！`);
            }
          } else if (r.first_today && r.streak > 1) {  // 普通连续仅今日首签提示，避免重复开弹层刷屏
            showToast(`🔥 连续看盘第 ${r.streak} 天，已签到`);
          }
        }
      }
    } catch { /* 签到失败静默，不影响复盘展示 */ }
  }, [logAct, showToast]);
  useEffect(() => {  // 首页置顶卡片：进页面拉一次今日复盘
    apiGet<{ exists: boolean; review?: any }>('/api/review/today')
      .then(r => { if (r.exists) setReviewToday(r.review); }).catch(() => {});
  }, []);
  const refreshMembership = useCallback(() => {
    authService.fetchAccount().then(u => { if (u) { setMembership(u.membership ?? null); setIsAdmin(u.role === 'admin'); setTrialClaimable(!!u.trial_claimable); setJoinedAt(u.created_at || ''); } }).catch(() => {});
    authService.fetchSupportUnread().then(n => applySupportUnread(n, false)).catch(() => {});  // 进页面只对齐红点、不弹
    if (authService.getStoredToken()) {  // 邀请奖励可兑换卡数（醒目角标）
      authService.fetchReferral().then(d => setRefAvail((d.available.month || 0) + (d.available.quarter || 0) + (d.available.year || 0))).catch(() => {});
    }
  }, [applySupportUnread]);
  refreshMembershipRef.current = refreshMembership;  // 供 openReview 等先定义的回调延迟调用
  const openSupport = useCallback(async () => {
    setAcctOpen(false); setSupportOpen(true);
    const msgs = await authService.fetchSupportThread();
    setSupportMsgs(msgs); supportUnreadRef.current = 0; setSupportUnread(0);
  }, []);
  const submitRedeem = useCallback(async () => {
    const code = redeemInput.trim();
    if (!code || redeemBusyRef.current) return;  // ref 同步拦截连点，避免「第一次成功、第二次报已使用」
    redeemBusyRef.current = true;
    setRedeemBusy(true);
    logAct('redeem', code.slice(0, 24));
    try {
      const r = await authService.redeemCode(code);
      setMembership(r.membership ?? null);
      setRedeemOpen(false); setRedeemInput('');
      if (r.already) {
        showToast('✅ ' + (r.message || '此兑换码你已成功兑换过，会员已到账'));
      } else {
        const label = r.trial ? `体验会员 ${r.days} 天` : (r.tier === 'lifetime' ? '永久会员' : `尊享会员 ${r.days} 天`);
        showToast(`🎉 兑换成功！已开通 ${label}`);
      }
      refreshMembership();
    } catch (e: any) {
      showToast('❌ ' + (e?.message || '兑换失败，请检查兑换码'));
    } finally { redeemBusyRef.current = false; setRedeemBusy(false); }
  }, [redeemInput, showToast, refreshMembership, logAct]);
  const openBuy = useCallback(async () => {
    setAcctOpen(false);
    logAct('open_buy', '开通/续费会员');   // 购买意向：谁点开了购买页（看板可见）
    buyOpenAtRef.current = Date.now(); buyQrViewedRef.current = false; buyOutcomeRef.current = ''; setBuyPaid(false);
    const c = await authService.fetchPaymentConfig();
    // 锚定方向修正：默认选中「每天均价最低」的套餐（通常是年卡）而非最便宜的月卡——
    // 默认项是零成本的选择架构，原「月卡打头+默认月卡」系统性把用户往最低客单价推。
    const pkgs = c?.packages || [];
    const bestKey = pkgs.length ? pkgs.reduce((a, b) => (a.price / a.days <= b.price / b.days ? a : b)).key : '';
    setPayCfg(c); setBuyPkg(bestKey || c?.packages?.[0]?.key || ''); setBuyOpen(true);
  }, [logAct]);
  // 关闭购买弹窗：未转化(没点「我已付款」/没去领体验卡)才记 buy_close，带停留秒数定位流失环节
  const closeBuy = useCallback((outcome?: string) => {
    setBuyOpen(false); setBuyPaid(false);
    if (!buyOutcomeRef.current) {
      const secs = Math.round((Date.now() - buyOpenAtRef.current) / 1000);
      logAct('buy_close', `${outcome || '直接关闭'} · 停留${secs}s`);
    }
  }, [logAct]);
  // 当前选中的套餐（个人收款码不含金额，需用户手动输入——把应付金额醒目展示）
  const buySel = useMemo(() => (payCfg?.packages || []).find(p => p.key === buyPkg) || payCfg?.packages?.[0] || null, [payCfg, buyPkg]);
  // 「限时一周」倒计时：仅购买弹窗打开时每秒走表（关着不空转）
  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    if (!buyOpen) return;
    setNowTs(Date.now());
    const id = window.setInterval(() => setNowTs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [buyOpen]);
  const promoLeftMs = FOUNDING_PROMO_END - nowTs;            // >0 表示活动进行中
  // 新人前 3 天：可享加赠（年卡 +1月、半年卡 +15天）
  const isNewUser = useMemo(() => {
    if (!joinedAt) return false;
    const t = new Date(joinedAt).getTime();
    return Number.isFinite(t) && (Date.now() - t) < NEW_USER_WINDOW_MS;
  }, [joinedAt]);
  const selBonus = (isNewUser && buySel) ? NEW_USER_BONUS[buySel.key] : undefined;  // 当前选中套餐的新人加赠
  // 该套餐有无「固定金额收款码」：有则扫码自动带金额、用各自的码；无则回退通用码 + 手动输入金额
  const buyQr = useMemo(() => {
    const key = buySel?.key;
    const has = (prov: 'wechat' | 'alipay') => !!(key && payCfg?.pkg_qr?.[key]?.[prov]);
    return {
      wechatSrc: has('wechat') ? `/api/payment-qr/wechat_${key}` : '/api/payment-qr/wechat',
      alipaySrc: has('alipay') ? `/api/payment-qr/alipay_${key}` : '/api/payment-qr/alipay',
      wechatFixed: has('wechat'),
      alipayFixed: has('alipay'),
      anyFixed: has('wechat') || has('alipay'),
    };
  }, [buySel, payCfg]);
  // 弹窗开着且收款码已配置：停留满 5s 记一次 buy_qr_view（区分「询价即走」和「认真看了码没付」）
  useEffect(() => {
    if (!buyOpen || buyQrViewedRef.current || !(payCfg?.wechat || payCfg?.alipay)) return;
    const id = window.setTimeout(() => {
      buyQrViewedRef.current = true;
      logAct('buy_qr_view', buySel ? `${buySel.label} ¥${buySel.price}` : '未选套餐');
    }, 5000);
    return () => window.clearTimeout(id);
  }, [buyOpen, payCfg, buySel, logAct]);
  // 第一步「我已完成付款」：先确认收到，进入第二步才揭示"发凭证给管理员"——付款前不提管理员，降低劝退
  const markPaid = useCallback(() => {
    const pkg = (payCfg?.packages || []).find(p => p.key === buyPkg) || payCfg?.packages?.[0];
    buyOutcomeRef.current = 'paid';
    logAct('buy_paid_click', pkg ? `${pkg.label} ¥${pkg.price}` : '未选套餐');  // 付款转化点（点了"我已完成付款"）
    setBuyPaid(true);
    // 自动把用户名复制到剪贴板：用户切到微信付款时直接粘进备注（人工开通靠备注匹配用户名，手打易错→开不通→流失）
    if (authUser) { try { navigator.clipboard?.writeText?.(authUser); showToast('📋 已复制用户名，粘到微信付款备注即可'); } catch { /* 忽略剪贴板失败 */ } }
  }, [payCfg, buyPkg, logAct, authUser, showToast]);
  // 第二步「发凭证给管理员」→ 打开私信并预填套餐 + 用户名，方便管理员核对开通
  const buyContactAdmin = useCallback(async () => {
    const pkg = (payCfg?.packages || []).find(p => p.key === buyPkg) || payCfg?.packages?.[0];
    buyOutcomeRef.current = 'contact';
    logAct('buy_contact', pkg ? `${pkg.label} ¥${pkg.price}` : '未选套餐');   // 付款转化：点了「我已付款」+ 哪个套餐
    const hasQr = !!(payCfg?.wechat || payCfg?.alipay);
    const bonus = (isNewUser && pkg) ? NEW_USER_BONUS[pkg.key] : undefined;   // 新人加赠 → 写进给管理员的消息，提醒额外赠天数
    const bonusNote = bonus ? `（新人专享，请额外赠送 ${bonus.days} 天，合计 ${pkg!.days + bonus.days} 天）` : '';
    const text = pkg
      ? (hasQr
        ? `我已购买【${pkg.label} ¥${pkg.price}】，用户名：${authUser || ''}，已扫码付款，请帮我开通会员 🙏${bonusNote}（付款备注/凭证：）`
        : `想购买【${pkg.label} ¥${pkg.price}】会员，用户名：${authUser || ''}，请问如何付款开通？🙏${bonusNote}`)
      : '';
    setBuyOpen(false); setSupportOpen(true);
    const msgs = await authService.fetchSupportThread();
    setSupportMsgs(msgs); supportUnreadRef.current = 0; setSupportUnread(0);
    if (text) setSupportText(text);
  }, [payCfg, buyPkg, authUser, logAct, isNewUser]);
  const sendSupportMsg = useCallback(async () => {
    const text = supportText.trim();
    if (!text || supportSending) return;
    setSupportSending(true);
    const msg = await authService.sendSupport(text);
    if (msg) { setSupportMsgs(prev => [...prev, msg]); setSupportText(''); logAct('support_msg', text.slice(0, 60)); }
    else showToast('发送失败，请稍后再试');
    setSupportSending(false);
  }, [supportText, supportSending, showToast, logAct]);
  // 登录后轮询管理员回复：弹窗开着→刷新对话(并标已读)；关着→只更新未读红点。每 45s。
  useEffect(() => {
    if (!authUser) return;
    const tick = () => {
      if (supportOpen) authService.fetchSupportThread().then(m => { setSupportMsgs(m); supportUnreadRef.current = 0; setSupportUnread(0); }).catch(() => {});
      else authService.fetchSupportUnread().then(n => applySupportUnread(n, true)).catch(() => {});  // 新回复到达 → 弹提示
    };
    const id = window.setInterval(tick, 45000);
    return () => window.clearInterval(id);
  }, [authUser, supportOpen, applySupportUnread]);
  // 管理员：轮询「用户发来的未读私信数」→ 主页醒目提醒（立即一次 + 每 45s）
  useEffect(() => {
    if (!authUser || !isAdmin) { setAdminUnread(0); return; }
    let alive = true;
    const tick = () => apiGet<{ unread: number }>('/api/admin/support/unread-count')
      .then(r => { if (alive) setAdminUnread((r && r.unread) || 0); }).catch(() => {});
    tick();
    const id = window.setInterval(tick, 45000);
    return () => { alive = false; window.clearInterval(id); };
  }, [authUser, isAdmin]);
  // 尊享会员剩余天数：从到期时间实时算（自然每天递减），回退后端 days_left
  const memDaysLeft = useMemo(() => {
    if (!membership || membership.tier !== 'premium') return null;
    if (membership.expires_at) { const ms = new Date(membership.expires_at).getTime() - Date.now(); return ms > 0 ? Math.ceil(ms / 86400000) : 0; }
    return membership.days_left ?? null;
  }, [membership]);
  // AI 投研问答入口可见性：对所有登录用户开放（让 90% 非会员也能先尝到旗舰「哇时刻」→ 再在超额时转化）。
  // 后端 /api/agents/tool-research 本就不门控（深度研判更重、仍仅白名单，见 canDeep）；后端轻量日额度为待办的护栏。
  const canAskAi = (
    !!authUser
    || IFIND_USERS.has((authUser || '').toLowerCase())
    || membership?.tier === 'premium' || membership?.tier === 'lifetime'
    || isAdmin
  );
  // 组件级会员真值（账号弹层里的 isVip 是块级作用域，别处引用会 TS2304）
  const isMemberVip = membership?.tier === 'premium' || membership?.tier === 'lifetime';
  // 签到 streak：页面加载即拉全量状态（此前 fetchCheckinStatus 全前端零调用点，头部火焰徽章白建）
  useEffect(() => {
    if (!authUser) { setCheckin(null); return; }
    let dead = false;
    (async () => { const s = await authService.fetchCheckinStatus(); if (!dead && s) setCheckin(s); })();
    return () => { dead = true; };
  }, [authUser]);
  // 研报「原文」PDF 入口：仅白名单账号(lx199710)可见可用；其余账号一律隐藏入口。
  // 复用 IFIND_USERS 单一真源（后端 wire-file 亦硬门 403，见 main.py：前端只控可见性、后端硬控）。
  const canViewResearchOriginal = IFIND_USERS.has((authUser || '').toLowerCase());
  const [authOpen, setAuthOpen] = useState(false);
  const [authReason, setAuthReason] = useState('AI 解读');
  const pendingActionRef = useRef<null | (() => void)>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteData, setInviteData] = useState<authService.InviteOverview | null>(null);
  const [inviteCopied, setInviteCopied] = useState('');
  useEffect(() => { authUserRef.current = authUser; }, [authUser]);
  useEffect(() => {
    if (!authUser) { setShowAcctHint(false); return; }
    try { if (!localStorage.getItem('bbt_acct_hint_v1')) setShowAcctHint(true); } catch { /* */ }
  }, [authUser]);
  // 分享/邀请链接 ?ref=CODE → 存本地；未登录则直接弹注册框（模态读 df_ref 自动切注册+预填邀请码）
  useEffect(() => {
    try {
      const url = new URL(window.location.href);
      const ref = url.searchParams.get('ref');
      if (!ref) return;
      localStorage.setItem('df_ref', ref.trim().toUpperCase().slice(0, 16));
      // 清掉 URL 里的 ref，避免刷新反复弹窗（df_ref 已留在本地，注册时仍会带上）
      url.searchParams.delete('ref');
      window.history.replaceState({}, '', url.pathname + url.search + url.hash);
      if (!authService.getStoredToken()) { setAuthReason('邀请注册'); setAuthOpen(true); }
    } catch { /* */ }
  }, []);
  // 新手引导自动触发：仅在「无任何弹窗/任务进行中 + 页面可见」时弹一次；
  // 有弹窗（尤其 ?ref 邀请注册框）则等关闭后自动重算，绝不盖住登录/注册/命令面板/AI 浮层。
  useEffect(() => {
    if (onbShownRef.current) return;
    let onboarded = false;
    try { onboarded = !!localStorage.getItem(ONB_KEY); } catch { /* */ }
    if (onboarded) return;
    const busy = authOpen || inviteOpen || paletteOpen || !!newsPreview || !!aiReport || !!shareImgUrl;
    if (busy) return;
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      const onVis = () => { if (document.visibilityState === 'visible') setOnbTick(t => t + 1); };
      document.addEventListener('visibilitychange', onVis);
      return () => document.removeEventListener('visibilitychange', onVis);
    }
    // 延后到 8s：1.2s 就盖脸打断用户的第一眼探索，先让首屏叙事说话；用户先自己点了什么就更不该打断
    const t = window.setTimeout(() => { onbShownRef.current = true; setShowOnb(true); }, 8000);
    return () => window.clearTimeout(t);
  }, [authOpen, inviteOpen, paletteOpen, newsPreview, aiReport, shareImgUrl, onbTick]);
  const openInvite = useCallback(async () => {
    setInviteOpen(true); setInviteData(null);
    try { setInviteData(await authService.fetchInvite()); } catch { /* */ }
  }, []);
  const openBookmarks = useCallback(async () => {
    setAcctOpen(false); setBookmarksOpen(true);
    try { const items = await authService.fetchBookmarks(); setBookmarkList(items); setBookmarks(new Set(items.map(i => i.message_id))); } catch { /* */ }
  }, []);
  // 站长内置看板：按登录态向后端取看板直达 URL（令牌不入前端包），新标签打开运营看板
  const openDashboard = useCallback(async () => {
    setAcctOpen(false);
    try {
      const r = await apiGet<{ url: string }>('/api/admin/metrics-token');
      const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
      window.open(r.url.startsWith('http') ? r.url : site + r.url, '_blank', 'noopener');
    } catch { showToast('⚠️ 无权访问看板或登录已过期'); }
  }, [showToast]);
  // 微信推送台：取同款管理令牌，新标签打开独立推送台页（仅 lx199710/管理员）
  const openWeixinConsole = useCallback(async () => {
    setAcctOpen(false);
    try {
      const r = await apiGet<{ token: string; url: string }>('/api/admin/metrics-token');
      const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
      window.open(site + '/api/weixin/console?token=' + encodeURIComponent(r.token), '_blank', 'noopener');
    } catch { showToast('⚠️ 无权访问或登录已过期'); }
  }, [showToast]);
  // 进入页面用已存令牌验证一次：有效则恢复登录态（一次 /auth/me 同时拿到用户名+会员+角色，避免重复请求）。
  // token 失效(401)由拦截器清除；网络瞬断等其他失败不清 token，下次进页面再试，避免误登出。
  useEffect(() => {
    if (!authService.getStoredToken()) return;
    let cancelled = false;
    authService.fetchAccount().then(u => {
      if (cancelled || !u) return;
      setAuthUser(u.username);
      setMembership(u.membership ?? null); setIsAdmin(u.role === 'admin'); setTrialClaimable(!!u.trial_claimable); setJoinedAt(u.created_at || '');
      authService.fetchSupportUnread().then(n => applySupportUnread(n, false)).catch(() => {});
      authService.fetchReferral().then(d => setRefAvail((d.available.month || 0) + (d.available.quarter || 0) + (d.available.year || 0))).catch(() => {});
      authService.fetchInvite().then(o => { inviteCodeRef.current = o.code || ''; }).catch(() => {});
    });
    return () => { cancelled = true; };
  }, [applySupportUnread]);
  // 网关：已登录直接执行；否则记住意图、弹登录框，登录成功后自动续做那一步。
  const requireLogin = useCallback((run: () => void, reason: string) => {
    if (authUserRef.current) { run(); return; }
    pendingActionRef.current = run;
    setAuthReason(reason);
    setAuthOpen(true);
  }, []);
  // 会员网关：未登录先登录；已登录但非会员(体验期)→ 弹「开通会员」引导（付费/邀请），不执行该动作。
  const requireMember = useCallback((run: () => void, reason: string) => {
    if (!authUserRef.current) { requireLogin(run, reason); return; }
    if (membership?.tier === 'premium' || membership?.tier === 'lifetime') { run(); return; }
    setUpgradeReason(reason); setUpgradeOpen(true);
  }, [requireLogin, membership]);
  // 收藏 id：研报头条可能只有 file_id/filename，统一解析
  const bmId = (m: any): string => String(m?.id || m?.file_id || m?.filename || '');
  // 收藏（登录态，头像菜单「我的收藏」可看）；topicOverride 用于头条卡（m.topic 可能缺）
  const toggleBookmark = useCallback((m: any, topicOverride?: string) => {
    const id = bmId(m);
    if (!id) return;
    requireLogin(async () => {
      try {
        const r = await authService.toggleBookmark({ message_id: id, title: m.title || '', topic: topicOverride || m.topic || '', url: m.url || '', symbol: m.symbol || '' });
        setBookmarks(prev => { const n = new Set(prev); if (r.bookmarked) n.add(id); else n.delete(id); return n; });
        logAct(r.bookmarked ? 'bookmark' : 'unbookmark', (topicOverride || m.topic || '') + ':' + (m.title || '').slice(0, 60));
        showToast(r.bookmarked ? '⭐ 已收藏 · 点头像「我的收藏」查看' : '已取消收藏');
      } catch { /* */ }
    }, '收藏资讯');
  }, [requireLogin, showToast, logAct]);
  // 登录态变化：拉本账号收藏集合
  useEffect(() => {
    if (authUser) { authService.fetchBookmarks().then(items => setBookmarks(new Set(items.map(i => i.message_id)))).catch(() => {}); }
    else { setBookmarks(new Set()); }
  }, [authUser]);
  const onAuthed = useCallback((username: string, isNew?: boolean) => {
    setAuthUser(username);
    authUserRef.current = username;
    refreshMembership();  // 登录后拉会员状态
    authService.fetchInvite().then(o => { inviteCodeRef.current = o.code || ''; }).catch(() => {});
    const next = pendingActionRef.current;
    pendingActionRef.current = null;
    if (next) { next(); if (isNew) logAct('signup', '注册(带意图直达)'); return; }
    if (isNew) {  // T+0 激活：新用户注册即喂一口「今天就能用」的内容——当日 A 股收盘复盘
      logAct('signup', '注册→推送今日复盘');
      showToast('🎉 注册成功！先看看今天的 A 股收盘复盘');
      openReview();
    }
  }, [refreshMembership, logAct, showToast, openReview]);

  // 文章分享深链：?article={id} → 拉取该文章，未登录先弹登录（登录即解锁这一篇，不卡会员墙，契合「分享链接登录就能看原文」），
  // 登录后打开站内全文阅读器。只跑一次：抓到参数即清掉 URL，避免重复触发。
  // ⭐先展示后要账：分享回流的最后一米。原来「一进站弹登录墙 + 立刻抹掉 URL 参数」——
  // 匿名回流者在看到任何内容之前先撞墙，点掉登录框内容永久丢失、中途刷新参数已焚。
  // 现在：先打开阅读器（匿名给 ~120 字软墙导语，镜像服务端 /article/{id} 的分寸），
  // 参数清理挪到内容成功展示之后；登录弹窗盖在预览之上，关掉弹窗内容仍在。
  const articleDeepLinkDone = useRef(false);
  useEffect(() => {
    if (articleDeepLinkDone.current) return;
    let articleId = '';
    try { articleId = new URLSearchParams(window.location.search).get('article') || ''; } catch { /* */ }
    if (!articleId) return;
    articleDeepLinkDone.current = true;
    (async () => {
      const article = await getRealtimeMessageById(articleId);
      if (!article) { showToast('该文章不存在或已下线'); return; }
      try { window.history.replaceState({}, '', window.location.pathname + window.location.hash); } catch { /* */ }
      logAct('open_news', `深链·${article.title}`);
      if (authUser) { setNewsPreview(article); return; }
      // 匿名软墙预览：第三方全文绝不出现在登录前视图（合规红线），只给导语+登录引导
      const teaser = {
        ...article,
        content: `${(article.content || '').slice(0, 120)}……\n\n—— 🔓 登录即可阅读本篇全文（用户名+密码即可注册）——`,
      } as RealtimeMessageRecord;
      setNewsPreview(teaser);
      requireLogin(() => setNewsPreview(article), '登录查看文章全文');
    })();
  }, [requireLogin, logAct, showToast, authUser]);
  // 研报解读分享深链：?report={id} → 同款「先展示后要账」；解读是我方 AI 原创内容，匿名同样只给导语。
  const reportDeepLinkDone = useRef(false);
  useEffect(() => {
    if (reportDeepLinkDone.current) return;
    let reportId = '';
    try { reportId = new URLSearchParams(window.location.search).get('report') || ''; } catch { /* */ }
    if (!reportId) return;
    reportDeepLinkDone.current = true;
    (async () => {
      const rec = await getReportShareById(reportId);
      if (!rec) { showToast('该研报解读不存在或已下线'); return; }
      try { window.history.replaceState({}, '', window.location.pathname + window.location.hash); } catch { /* */ }
      // 用站内阅读器展示完整 AI 解读（合成一条只读消息：content=我们的解读正文）
      const view = { id: rec.id, title: rec.title, content: rec.summary, topic: '研报', source_name: rec.source_name || '', created_at: rec.created_at || '' } as unknown as RealtimeMessageRecord;
      logAct('open_news', `研报深链·${rec.title}`);
      if (authUser) { setNewsPreview(view); return; }
      const teaser = { ...view, content: `${(rec.summary || '').slice(0, 120)}……\n\n—— 🔓 登录即可查看完整 AI 解读（用户名+密码即可注册）——` } as RealtimeMessageRecord;
      setNewsPreview(teaser);
      requireLogin(() => setNewsPreview(view), '登录查看完整研报解读');
    })();
  }, [requireLogin, logAct, showToast, authUser]);
  // 晨报深链 ?briefing={date}：微信/推送召回点击直达晨报内容（此前 url 是裸首页，回流落点浪费）
  const briefingDeepDone = useRef(false);
  useEffect(() => {
    if (briefingDeepDone.current) return;
    let bday = '';
    try { bday = new URLSearchParams(window.location.search).get('briefing') || ''; } catch { /* */ }
    if (!bday) return;
    briefingDeepDone.current = true;
    (async () => {
      try {
        const d = await apiGet<any>('/api/briefing/today');
        try { window.history.replaceState({}, '', window.location.pathname + window.location.hash); } catch { /* */ }
        const bits = [d?.headline, d?.macro_verdict ? `宏观风险偏好：${d.macro_verdict}` : '', d?.portfolio_verdict ? `组合状态：${d.portfolio_verdict}` : '', d?.disclaimer].filter(Boolean);
        if (bits.length) {
          const view = { id: `briefing-${bday}`, title: `🌅 投研晨报 · ${bday}`, content: bits.join('\n\n'), topic: '晨报', source_name: 'DeepFocus 投研晨报', created_at: '' } as unknown as RealtimeMessageRecord;
          setNewsPreview(view);
        }
      } catch { /* 晨报拉取失败就落在首页，与旧行为一致 */ }
    })();
  }, []);
  // 领取「登录送 3 天体验会员」：未登录先弹登录，登录后自动领取；每账号仅一次
  const onClaimTrial = useCallback(() => {
    requireLogin(async () => {
      setTrialClaiming(true);
      try {
        const r = await authService.claimTrial();
        setMembership(r.membership ?? null);
        setTrialClaimable(false);
        logAct('claim_trial', `${r.days}天体验会员`);
        showToast(`🎉 已领取 ${r.days} 天体验会员，已解锁全部功能`);
      } catch (e: any) {
        setTrialClaimable(false);  // 后端判定已领/已是会员 → 收起入口
        showToast('❌ ' + (e?.message || '领取失败，请稍后再试'));
      } finally { setTrialClaiming(false); }
    }, '领取 3 天体验会员');
  }, [requireLogin, showToast]);
  const onLogout = useCallback(() => {
    authService.logout();
    setAuthUser(null);
    authUserRef.current = null;
    inviteCodeRef.current = '';
    pendingActionRef.current = null;
    setMembership(null); setIsAdmin(false); setTrialClaimable(false); setSupportUnread(0); setSupportOpen(false); setSupportMsgs([]); setRedeemOpen(false); setBuyOpen(false);
    setAcctOpen(false);
    showToast('已退出登录');
  }, [showToast]);

  // 单设备登录：账号在其他设备登录后，本端被挤下线（apiClient 拦截器捕获 401 标记后广播）→ 清态 + 明确提示
  useEffect(() => {
    const onKicked = (e: Event) => {
      authService.logout();
      setAuthUser(null);
      authUserRef.current = null;
      pendingActionRef.current = null;
      setMembership(null); setIsAdmin(false); setTrialClaimable(false); setSupportUnread(0); setSupportOpen(false); setSupportMsgs([]); setRedeemOpen(false); setBuyOpen(false);
      setAcctOpen(false);
      const msg = ((e as CustomEvent)?.detail as string) || '账号已在其他设备登录，你已被挤下线';
      showToast('⚠️ ' + msg);
    };
    window.addEventListener('df:auth-kicked', onKicked);
    return () => window.removeEventListener('df:auth-kicked', onKicked);
  }, [showToast]);

  // ---- 自选股按账号同步：未登录走 localStorage（仅本地、互不影响）；登录后绑定账号、跨设备一致 ----
  const watchlistRef = useRef(watchlist);
  const namesRef = useRef(names);
  const wlHydrating = useRef(false);   // 程序化加载期间抑制回存，避免「加载即回存」抖动
  const wlSaveTimer = useRef<number | undefined>(undefined);
  const scheduleWatchlistSave = useCallback(() => {
    window.clearTimeout(wlSaveTimer.current);
    wlSaveTimer.current = window.setTimeout(() => {
      saveWatchlist(watchlistRef.current, namesRef.current).catch(() => { /* 同步失败不打扰用户，下次改动再试 */ });
      // 推送订阅跟着自选走：否则订阅行里的 symbols 是建行时快照，之后加的自选股永远收不到召回
      if (getNotificationPermission() === 'granted' && loadRecallPrefs().browserEnabled) {
        void subscribeWebPush({ symbols: watchlistRef.current });
      }
    }, 800);
  }, []);
  // 自选股变化 → 登录态防抖存服务器；未登录存本地
  useEffect(() => {
    watchlistRef.current = watchlist;
    if (wlHydrating.current) return;
    if (authUserRef.current) scheduleWatchlistSave();
    else LS.write('bbt.watchlist', watchlist);
  }, [watchlist, scheduleWatchlistSave]);
  // 名称变化 → 同上
  useEffect(() => {
    namesRef.current = names;
    if (wlHydrating.current) return;
    if (authUserRef.current) scheduleWatchlistSave();
    else LS.write('bbt.names', names);
  }, [names, scheduleWatchlistSave]);
  // 登录态切换：登录→拉账号自选（无则用当前列表做种子）；登出→回退到本地游客列表
  useEffect(() => {
    let cancelled = false;
    if (authUser) {
      wlHydrating.current = true;
      fetchWatchlist().then(data => {
        if (cancelled) return;
        if (data && Array.isArray(data.symbols) && data.symbols.length) {
          setWatchlist(data.symbols);
          setNames({ ...DEFAULT_NAMES, ...(data.names || {}) });
          window.setTimeout(() => { wlHydrating.current = false; }, 0);  // 等状态生效后再放开回存
        } else {
          // 该账号首次：用当前（游客）列表做种子并落库，之后即绑定账号
          wlHydrating.current = false;
          saveWatchlist(watchlistRef.current, namesRef.current).catch(() => { /* 忽略 */ });
        }
      }).catch(() => { if (!cancelled) wlHydrating.current = false; });
    } else {
      // 登出：恢复本地游客自选股
      wlHydrating.current = true;
      setWatchlist(LS.read('bbt.watchlist', DEFAULT_WATCHLIST));
      setNames({ ...DEFAULT_NAMES, ...LS.read('bbt.names', {} as Record<string, string>) });
      window.setTimeout(() => { wlHydrating.current = false; }, 0);
    }
    return () => { cancelled = true; };
  }, [authUser]);

  // ---- 研报一键 AI 分析（多模态解读：在线 file_id 或本地文件）----
  // 匿名免费体验额度：每天 1 次（与后端按 IP 每日限额对齐）。本机存"今天用过没"，跨天自动恢复。
  const aiFreeUsed = useCallback(() => { try { return localStorage.getItem('df_ai_free_day') === new Date().toLocaleDateString('en-CA'); } catch { return false; } }, []);
  const markAiFreeUsed = useCallback(() => { try { localStorage.setItem('df_ai_free_day', new Date().toLocaleDateString('en-CA')); } catch { /* */ } }, []);

  const runAiAnalysis = useCallback(async (r: ResearchWireItem) => {
    // 匿名用户：免费体验「一次」AI 解读，尝到甜头后第二次起引导登录（登录再送 3 天尊享会员）。
    if (!authUserRef.current && aiFreeUsed()) {
      showToast('💡 体验不错？登录即可继续解读，还送 3 天尊享会员 🎁');
      requireLogin(() => runAiAnalysis(r), 'AI 解读'); return;
    }
    logAct('ai_report', r.title);
    setAiReport(r); setAiResult(null); setAiError(''); setAiLoading(true);
    setAiReportMeta({ org: (r as any).org || '', symbol: (r.instruments && r.instruments[0]) || '', preview_url: r.preview_url || '' });  // 研报解读→可分享落地页 + 原文按钮
    aiRetryRef.current = () => runAiAnalysis(r);
    try {
      const body: Record<string, any> = { title: r.title, max_pages: 4 };
      const fid = r.file_id || (r.preview_url.includes('wire-file') ? new URLSearchParams(r.preview_url.split('?')[1] || '').get('file_id') : '');
      if (fid) { body.file_id = fid; body.filename = r.filename; }
      else { body.workbench_filename = r.filename; body.workbench_out = r.out || 'downloads/海外投行报告'; }
      // 图片型研报首次需多模态解读，30–60s 起；给足超时，避免默认 20s 误判失败
      const res = await apiPost<AiAnalysis>('/api/research/vision-analyze', body, { timeout: 150000 });
      setAiResult(res);
      if (!authUserRef.current) markAiFreeUsed();  // 匿名免费体验已消费 → 下次起需登录
    } catch (e: any) {
      const status = e?.response?.status ?? e?.status; const detail = e?.response?.data?.detail ?? e?.detail;
      // 402：登录非会员（未缓存 / 今日免费已用完）→ 升级弹窗
      if (status === 402) { setAiReport(null); setUpgradeReason(detail || 'AI 解读是会员功能，开通即可无限解读'); setUpgradeOpen(true); return; }
      // 403：匿名（该研报未生成解读 / 今日免费已用完）→ 直接弹注册/登录框（首登用户默认注册+送3天会员），不再只甩一行死提示
      if (status === 403) {
        setAiReport(null);
        requireLogin(() => runAiAnalysis(r), '登录解读 · 送 3 天尊享会员 🎁'); return;
      }
      const code = e?.code;
      if (code === 'ECONNABORTED' || /timeout/i.test(e?.message || '')) {
        setAiError('解读超时了，该研报可能较长。请稍后再点一次（后台仍在解读，通常重试即秒出）。');
      } else {
        setAiError(e?.response?.data?.detail || e?.message || 'AI 解读失败，请稍后重试');
      }
    } finally { setAiLoading(false); }
  }, [requireLogin, logAct, showToast, aiFreeUsed, markAiFreeUsed]);

  const runNewsAi = useCallback(async (m: RealtimeMessageRecord) => {
    if (!authUserRef.current && aiFreeUsed()) {
      showToast('💡 体验不错？登录即可继续解读，还送 3 天尊享会员 🎁');
      requireLogin(() => runNewsAi(m), 'AI 解读'); return;
    }
    logAct('ai_news', m.title);
    setAiReport({ title: m.title, date: (m.created_at || '').slice(0, 10) }); setAiReportMeta(null); setAiResult(null); setAiError(''); setAiCopied(false); setAiLoading(true);
    aiRetryRef.current = () => runNewsAi(m);
    try {
      const res = await apiPost<AiAnalysis>('/api/news/ai-analyze',
        { title: m.title, content: m.content || '', url: m.url || '' }, { timeout: 120000 });
      setAiResult(res);
      if (!authUserRef.current) markAiFreeUsed();
    } catch (e: any) {
      const status = e?.response?.status ?? e?.status; const detail = e?.response?.data?.detail ?? e?.detail;
      if (status === 402) { setAiReport(null); setUpgradeReason(detail || 'AI 解读是会员功能，开通即可无限解读'); setUpgradeOpen(true); return; }
      if (status === 403) {
        setAiReport(null);
        requireLogin(() => runNewsAi(m), '登录解读 · 送 3 天尊享会员 🎁'); return;
      }
      if (e?.code === 'ECONNABORTED' || /timeout/i.test(e?.message || '')) setAiError('解读超时了，请重试。');
      else setAiError(e?.response?.data?.detail || e?.message || 'AI 解读失败，请稍后重试');
    } finally { setAiLoading(false); }
  }, [requireLogin, logAct, showToast, aiFreeUsed, markAiFreeUsed]);

  // AI 解读进度条：拿不到流式进度，按耗时渐近爬升到 ~94%（文字快、图片慢），完成时面板切走即收。
  useEffect(() => {
    if (!aiLoading) return;
    setAiProgress(6);
    const start = Date.now();
    const tau = 16000;  // 时间常数：~16s 到 ~61%，~40s 到 ~87%，渐近 94%
    const t = window.setInterval(() => {
      const elapsed = Date.now() - start;
      setAiProgress(Math.min(94, 6 + 88 * (1 - Math.exp(-elapsed / tau))));
    }, 250);
    return () => window.clearInterval(t);
  }, [aiLoading]);

  // 交互埋点（白名单事件：copy_text / copy_image / copy_news）
  const pingMetric = useCallback((name: string, title?: string) => {
    const params: Record<string, string> = { name };
    if (title) params.title = title.slice(0, 120);
    apiPost('/api/metrics/event', undefined, { params }).catch(() => { /* 埋点失败忽略 */ });
  }, []);


  // 快讯复制(金十式·固定规则·零大模型)：头条用「DeepFocus快讯丨X月X日讯，」通讯体起手 + 原文事实(逐字忠实、不改不编) + 引流链接；
  // 原文链接保留(竞品域名除外)。即时、确定、零 token——固定格式没必要叫大模型。
  const copyNews = useCallback(async (m: RealtimeMessageRecord) => {
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    const dateLabel = (() => {  // 通讯体起手的「X月X日」(北京时间)
      try { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' }).format(new Date(m.created_at)); }
      catch { return ''; }
    })();
    const headline = (m.title || '').trim();
    // 头条:「DeepFocus快讯丨6月24日讯，<原文标题>。」标题已带句末标点则不再补
    const lead = `DeepFocus快讯${dateLabel ? `丨${dateLabel}讯，` : '丨'}${headline}${/[。！？!?…」』）)\.]$/.test(headline) ? '' : '。'}`;
    const parts: string[] = [lead];
    if (m.content && m.content !== m.title) parts.push('', m.content);   // 正文(有更详内容才带,空行分隔)
    if (m.url && !isOwnHosted(m)) parts.push('', `原文：${m.url}`);        // 竞品域名(futoucaixin)原文链接不外泄
    // 引流链接带追踪：已登录用户带本人邀请码(?ref=)→ 复制传播即计入邀请归因。
    // ⭐链接从裸首页改为【这条快讯的落地页】：复制文本已含全文，收信人需要一个文本里没有的东西
    // （这条快讯的 AI 解读与后续追踪）才有点击理由——1900 次/月的复制流是全站最大自然分发行为。
    const refCode = inviteCodeRef.current;
    const q = `${refCode ? `ref=${encodeURIComponent(refCode)}&` : ''}utm_source=copy`;
    const link = m.id ? `${site}/article/${encodeURIComponent(m.id)}?${q}` : `${site}/?${q}`;
    parts.push('', `🔗 看这条快讯的 AI 解读与后续追踪 → ${link}`);
    if (refCode) parts.push('注册即领体验会员，解锁 AI 完整解读');         // 带邀请码 → 来访者钩子，转化+归因双赢
    const text = parts.join('\n');
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); }
      setCopiedNewsId(m.id); window.setTimeout(() => setCopiedNewsId(''), 1600);
      pingMetric('copy_news', m.title); logAct('copy', m.title); showToast('✅ 已复制');
    } catch { showToast('⚠️ 复制失败，请重试'); }
  }, [pingMetric, logAct, showToast]);

  const closeAi = useCallback(() => { setAiReport(null); setAiReportMeta(null); setAiResult(null); setAiError(''); setAiCopied(false); setDfExpanded(false); }, []);

  // 研报「原文」：带 JWT 头 fetch 取 PDF Blob → createObjectURL → 新标签打开（会员专享，402 → 升级弹窗）
  const openResearchOriginal = useCallback(async (preview_url: string) => {
    if (!canViewResearchOriginal) return;   // 白名单外不发起请求（入口本已隐藏，双保险）
    if (!preview_url || pdfLoadingUrl) return;
    setPdfLoadingUrl(preview_url);
    try {
      const blob = await apiGet<Blob>(preview_url, { responseType: 'blob', timeout: 90000 });
      const blobUrl = URL.createObjectURL(blob);
      window.open(blobUrl, '_blank', 'noopener,noreferrer');
      // 不主动 revoke：新标签页的 PDF 「查看 + 下载」都依赖该 blob 存活。过早 revoke（原 60s）会让用户
      // 看一会儿再点下载时 blob 已失效 →「下载失败/请检查互联网连接」。blob 在该标签关闭时由浏览器自动回收。
    } catch (e: any) {
      const status = e?.status ?? e?.response?.status;
      const detail = e?.detail ?? e?.response?.data?.detail;
      if (status === 402) {
        setUpgradeReason(detail || '该功能为会员功能，开通会员即可使用');
        setUpgradeOpen(true);
      } else if (status === 403) {
        // 版权合规：原文仅白名单开放，绝不能用「开通会员即可阅读」招揽（买了也解不开=虚假宣传）
        showToast(detail || '应版权合规要求，研报原文暂不开放——可查看 AI 解读');
      } else if (status === 401) {
        showToast('请先登录再查看研报原文');
      } else {
        showToast('原文加载失败，请稍后重试');
      }
    } finally {
      setPdfLoadingUrl(null);
    }
  }, [showToast, pdfLoadingUrl, canViewResearchOriginal]);

  // 研报解读「分享」：把当前 AI 解读存成公开软墙落地页 → 拿到 URL → 打开极简分享弹窗（落地页登录看完整解读，引流转化）。
  const shareReportInsight = useCallback(async () => {
    const r = aiResult;
    if (!r || reportShareBusy) return;
    setReportShareBusy(true);
    try {
      const title = (aiReport?.title || r.title || '研报解读').trim();
      const summary = aiAnalysisToText(r, '');  // 正文不含标题（标题已单列），交给落地页/文案承载
      const { url } = await createReportShare({
        title,
        summary,
        source_name: aiReportMeta?.org || '',
        symbol: aiReportMeta?.symbol || r.subject || '',
      });
      logAct('share_click', '研报解读分享');
      setShareModal({ open: true, target: { kind: 'report', title, summary: (r.one_liner || r.summary || '').slice(0, 80), url } });
    } catch (e: any) {
      showToast('⚠️ ' + (e?.response?.data?.detail || e?.message || '生成分享链接失败，请稍后再试'));
    } finally {
      setReportShareBusy(false);
    }
  }, [aiResult, aiReport, aiReportMeta, reportShareBusy, logAct, showToast]);

  // 分享卡二维码目标：已登录则带上分享者的 ?ref=邀请码（扫码注册归到他名下，拉新闭环）；未登录回退裸站点。
  const qrShareTarget = useCallback((site: string) => (inviteCodeRef.current ? `${site}/?ref=${inviteCodeRef.current}` : site), []);

  // 把解读渲染成分享图片（中文走浏览器字体；右下角二维码 = 可扫的站点链接）
  const drawShareCard = useCallback(async (): Promise<Blob | null> => {
    const r = aiResult;
    if (!r) return null;
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    let qr: { size: number; matrix: number[][] } | null = null;
    try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: qrShareTarget(site) } }); } catch { qr = null; }
    const bull = (r.bullish?.length ? r.bullish : r.key_points) || [];
    const bear = (r.bearish?.length ? r.bearish : r.risks) || [];

    const cv = document.createElement('canvas');
    const ctx = cv.getContext('2d');
    if (!ctx) return null;
    const SC = 2, W = 760, PAD = 40, maxW = W - PAD * 2;
    const F = (size: number, weight = '400') => `${weight} ${size}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
    const wrap = (text: string, font: string, mw: number): string[] => {
      ctx.font = font; const out: string[] = []; let line = '';
      for (const ch of String(text || '')) {
        if (ch === '\n') { out.push(line); line = ''; continue; }
        if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch;
      }
      if (line) out.push(line); return out;
    };
    type Block = { lines: string[]; font: string; lh: number; color: string; mt: number; bullet?: string };
    const items: Block[] = [];
    let h = PAD;
    const push = (lines: string[], font: string, lh: number, color: string, mt = 0, bullet?: string) => {
      items.push({ lines, font, lh, color, mt, bullet }); h += mt + lines.length * lh;
    };
    push(['DEEPFOCUS 金融终端 · AI 速读'], F(15, '700'), 24, '#ffb000');
    push(wrap(aiReport?.title || r.title || '', F(21, '700'), maxW), F(21, '700'), 31, '#f4ecd6', 8);
    if (aiReport?.date) push([`🗓 ${aiReport.date}`], F(13, '400'), 20, '#8a8463', 4);
    const chips = [r.subject && `标的 ${r.subject}`, r.rating && `评级 ${r.rating}`, r.target_price && `目标价 ${r.target_price}`].filter(Boolean).join('　｜　');
    if (chips) push(wrap(chips, F(15, '700'), maxW), F(15, '700'), 25, '#9fc0ff', 12);
    if (r.one_liner) push(wrap('💡 ' + r.one_liner, F(17, '700'), maxW), F(17, '700'), 28, '#ffd980', 14);
    if (r.summary) { push(['一句话看懂'], F(13, '700'), 22, '#a78bfa', 16); push(wrap(r.summary, F(15), maxW), F(15), 26, '#e2e5ea', 4); }
    if (r.core_logic) { push(['🔑 投资逻辑'], F(13, '700'), 22, '#a78bfa', 16); push(wrap(r.core_logic, F(15), maxW), F(15), 26, '#e2e5ea', 4); }
    if (bull.length) { push(['✅ 利好'], F(14, '700'), 23, '#5fe39a', 16); bull.forEach(b => push(wrap(b, F(15), maxW - 18), F(15), 25, '#d6f0e0', 3, '▲')); }
    if (bear.length) { push(['⚠️ 利空'], F(14, '700'), 23, '#ff8a8a', 16); bear.forEach(b => push(wrap(b, F(15), maxW - 18), F(15), 25, '#f0d0d0', 3, '▼')); }
    if (r.takeaway) { push(wrap('📌 启示：' + r.takeaway, F(15, '700'), maxW), F(15, '700'), 26, '#ffd980', 16); }
    // DeepFocus 视角点评：我方原创独立判断（盖品牌、做厚价值）——视觉上用品牌琥珀醒目区分
    if (r.df_take) { push(['◆ DeepFocus 视角 · 独家点评'], F(14, '700'), 23, '#ffb000', 18); push(wrap(r.df_take, F(15), maxW), F(15), 26, '#ffe7b0', 4); }
    const footTop = h + 18;
    const qrSize = qr ? 100 : 0;
    h = footTop + Math.max(qrSize, 76) + PAD;

    cv.width = W * SC; cv.height = Math.ceil(h) * SC; ctx.scale(SC, SC);
    ctx.fillStyle = '#0a0d12'; ctx.fillRect(0, 0, W, h);
    ctx.fillStyle = '#ffb000'; ctx.fillRect(0, 0, W, 4);
    // 品牌水印：斜向平铺半透明「DEEPFOCUS」铺满全卡，明确标注为我方原创解读（防盗用、立品牌）
    ctx.save();
    ctx.globalAlpha = 0.04; ctx.fillStyle = '#ffb000'; ctx.font = F(34, '800');
    ctx.translate(W / 2, h / 2); ctx.rotate(-Math.PI / 7);
    for (let wy = -h; wy < h; wy += 120) {
      for (let wx = -W; wx < W; wx += 360) ctx.fillText('DEEPFOCUS', wx, wy);
    }
    ctx.restore();
    ctx.textBaseline = 'top';
    let y = PAD;
    for (const it of items) {
      y += it.mt;
      for (const ln of it.lines) {
        if (it.bullet) {
          ctx.font = F(10); ctx.fillStyle = it.bullet === '▲' ? '#2bd96a' : '#ff6b6b'; ctx.fillText(it.bullet, PAD, y + 4);
          ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD + 18, y);
        } else { ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD, y); }
        y += it.lh;
      }
    }
    ctx.strokeStyle = '#1c2530'; ctx.beginPath(); ctx.moveTo(PAD, footTop); ctx.lineTo(W - PAD, footTop); ctx.stroke();
    if (qr) {
      const n = qr.size, cell = qrSize / n, qx = W - PAD - qrSize, qy = footTop + 16;
      ctx.fillStyle = '#fff'; ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12);
      ctx.fillStyle = '#000';
      for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6);
    }
    // 页脚：品牌为主、域名为辅（不再用大蓝裸域名，显得更克制）
    ctx.font = F(16, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('DEEPFOCUS 金融终端', PAD, footTop + 14);
    ctx.font = F(12); ctx.fillStyle = '#8a93a0'; ctx.fillText('扫码访问 · ' + site.replace(/^https?:\/\//, ''), PAD, footTop + 40);
    ctx.font = F(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText('AI 自动解读 · 仅供参考，非投资建议', PAD, footTop + 60);
    return await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
  }, [aiResult, aiReport, qrShareTarget]);

  const [aiImgBusy, setAiImgBusy] = useState(false);
  const shareAiImage = useCallback(async () => {
    setAiImgBusy(true);
    try {
      const blob = await drawShareCard();
      if (!blob) { showToast('⚠️ 图片生成失败，请重试'); return; }
      pingMetric('copy_image');
      const coarse = typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
      // 移动端优先原生分享面板（可直接发朋友圈/好友/存图）；不支持/失败再回退长按弹图
      if (coarse) {
        const r = await shareImageNative(blob, { filename: 'DeepFocus.png', title: 'DeepFocus 金融终端', text: aiReport?.title || 'DeepFocus · AI 解读' });
        if (r === 'shared') return;
      }
      // 桌面：尝试直接复制图片到剪贴板（部分浏览器如 Firefox 不支持 → 抛错走弹图）
      let clipboardOk = false;
      try {
        const Cl = (window as any).ClipboardItem;
        if (!coarse && navigator.clipboard && Cl) {
          await navigator.clipboard.write([new Cl({ 'image/png': blob })]);
          clipboardOk = true;
        }
      } catch { clipboardOk = false; }
      if (clipboardOk) {
        setAiCopied(true); window.setTimeout(() => setAiCopied(false), 1800);
        showToast('✅ 图片已复制到剪贴板，可直接粘贴（微信/文档等）');
        return;
      }
      // 不支持复制图片：弹出大图，引导保存（移动端只长按、桌面右键/下载）。用 data: URL 保证长按可存
      setShareImgCoarse(coarse);
      setShareImgUrl(await blobToDataUrl(blob));
      setShareImgNote(coarse
        ? '👇 长按下方图片，选择「存储图像 / 保存到相册」或「分享」'
        : '右键图片选择「图片另存为」，或点下方「下载图片」');
    } finally { setAiImgBusy(false); }
  }, [drawShareCard, pingMetric, showToast]);

  // 把图片呈现给用户：桌面复制剪贴板，移动端/不支持则弹图长按保存
  const presentImage = useCallback(async (blob: Blob, okToast: string) => {
    const coarse = typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    // 移动端优先原生分享面板（可直接发朋友圈/好友/存图）；不支持/失败再回退长按弹图
    if (coarse) {
      const r = await shareImageNative(blob, { filename: 'DeepFocus.png', title: 'DeepFocus 金融终端', text: 'DeepFocus · 实时资讯，提前发现' });
      if (r === 'shared') return;
    }
    try {
      const Cl = (window as any).ClipboardItem;
      if (!coarse && navigator.clipboard && Cl) {
        await navigator.clipboard.write([new Cl({ 'image/png': blob })]);
        showToast(okToast); return;
      }
    } catch { /* 落到弹图 */ }
    setShareImgCoarse(coarse);
    setShareImgUrl(await blobToDataUrl(blob));  // data: URL，长按保存在微信/WebView 里才生效
    setShareImgNote(coarse ? '👇 长按下方图片，选择「存储图像 / 保存到相册」或「分享」' : '右键图片选择「图片另存为」，或点下方「下载图片」');
  }, [showToast]);

  // 复盘 → 分享长图（站点二维码 + 品牌水印，自带网站宣传）
  const [reviewImgBusy, setReviewImgBusy] = useState(false);
  const drawReviewCard = useCallback(async (): Promise<Blob | null> => {
    const r = reviewData; if (!r) return null;
    const nar = r.narrative || {};
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    let qr: { size: number; matrix: number[][] } | null = null;
    try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: qrShareTarget(site) } }); } catch { qr = null; }
    const cv = document.createElement('canvas'); const ctx = cv.getContext('2d'); if (!ctx) return null;
    const SC = 2, W = 760, PAD = 40, maxW = W - PAD * 2;
    const F = (s: number, w = '400') => `${w} ${s}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
    const wrap = (text: string, font: string, mw: number): string[] => {
      ctx.font = font; const out: string[] = []; let line = '';
      for (const ch of String(text || '')) { if (ch === '\n') { out.push(line); line = ''; continue; } if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch; }
      if (line) out.push(line); return out;
    };
    type Block = { lines: string[]; font: string; lh: number; color: string; mt: number; bullet?: string };
    const items: Block[] = []; let h = PAD;
    const push = (lines: string[], font: string, lh: number, color: string, mt = 0, bullet?: string) => { items.push({ lines, font, lh, color, mt, bullet }); h += mt + lines.length * lh; };
    const sess = r.session_label || '收盘复盘';
    push([`DEEPFOCUS 金融终端 · A股${sess}`], F(15, '700'), 24, '#ffb000');
    push([`🗓 ${r.date || ''}${r.session === 'midday' ? ' · 盘中半日' : ' · 全天定稿'}`], F(13, '400'), 20, '#8a8463', 4);
    if (nar.one_liner) push(wrap('「' + nar.one_liner + '」', F(19, '700'), maxW), F(19, '700'), 29, '#ffd980', 12);
    // 指数行
    const idxTxt = (r.indices || []).filter((i: any) => typeof i.pct === 'number').map((i: any) => `${i.name} ${i.close}(${i.pct > 0 ? '+' : ''}${i.pct.toFixed(2)}%)`).join('　');
    if (idxTxt) push(wrap(idxTxt, F(13, '700'), maxW), F(13, '700'), 22, '#9fc0ff', 12);
    const sec = (label: string, txt: string, color: string, head: string) => { if (!txt) return; push([head], F(14, '700'), 23, color, 16); push(wrap(txt, F(15), maxW), F(15), 25, '#e2e5ea', 3); };
    sec('plain', nar.plain, '#6ab0ff', '💡 导读');
    sec('market', nar.market, '#5fe39a', '📈 大盘');
    sec('sectors', nar.sectors, '#c4b5fd', '🧩 板块');
    sec('funds', nar.funds, '#9fc0ff', '💰 资金面');
    // ⭐ 我们提前发现的
    if (nar.our_value || (r.our_edge || []).length) {
      push(['⭐ DeepFocus 提前发现'], F(15, '800'), 25, '#ffb000', 18);
      if (nar.our_value) push(wrap(nar.our_value, F(15), maxW), F(15), 25, '#f0e3c8', 3);
      (r.our_edge || []).slice(0, 5).forEach((e: any) => {
        const lead = (typeof e.lead_hours === 'number' && e.lead_hours >= 1) ? (e.lead_hours >= 24 ? `领先约${(e.lead_hours / 24).toFixed(0)}天` : `领先约${e.lead_hours.toFixed(0)}小时`) : '同日捕捉';
        const tag = e.kind === 'stock' ? '个股' : '板块';
        const pct = typeof e.pct === 'number' ? `${e.pct > 0 ? '+' : ''}${e.pct.toFixed(2)}%` : '';
        push(wrap(`${tag} ${e.name} ${pct}　⚡${lead}　${e.evidence || ''}条佐证`, F(13.5, '700'), maxW - 18), F(13.5, '700'), 22, '#ffcf72', 6, '◆');
      });
    }
    if (nar.tomorrow) sec('tomorrow', nar.tomorrow, '#9fc0ff', '🔭 下一交易日');
    const footTop = h + 18; const qrSize = qr ? 100 : 0; h = footTop + Math.max(qrSize, 80) + PAD;
    cv.width = W * SC; cv.height = Math.ceil(h) * SC; ctx.scale(SC, SC);
    ctx.fillStyle = '#0a0d12'; ctx.fillRect(0, 0, W, h); ctx.fillStyle = '#ffb000'; ctx.fillRect(0, 0, W, 4);
    ctx.textBaseline = 'top'; let y = PAD;
    for (const it of items) { y += it.mt; for (const ln of it.lines) { if (it.bullet) { ctx.font = F(11); ctx.fillStyle = '#ffb000'; ctx.fillText(it.bullet, PAD, y + 3); ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD + 18, y); } else { ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD, y); } y += it.lh; } }
    ctx.strokeStyle = '#1c2530'; ctx.beginPath(); ctx.moveTo(PAD, footTop); ctx.lineTo(W - PAD, footTop); ctx.stroke();
    if (qr) { const n = qr.size, cell = qrSize / n, qx = W - PAD - qrSize, qy = footTop + 16; ctx.fillStyle = '#fff'; ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12); ctx.fillStyle = '#000'; for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6); }
    ctx.font = F(16, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('DeepFocus · 提前发现', PAD, footTop + 16);
    ctx.font = F(12); ctx.fillStyle = '#8a93a0'; ctx.fillText('扫码看实时资讯 · ' + site.replace(/^https?:\/\//, ''), PAD, footTop + 42);
    ctx.font = F(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText('数据 + AI 综述 · 仅供研究参考，不构成投资建议', PAD, footTop + 62);
    return await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
  }, [reviewData, qrShareTarget]);

  const shareReviewImage = useCallback(async () => {
    setReviewImgBusy(true);
    try { const blob = await drawReviewCard(); if (!blob) { showToast('⚠️ 图片生成失败，请重试'); return; } pingMetric('copy_image'); await presentImage(blob, '✅ 复盘图已复制，可直接粘贴到微信/朋友圈'); }
    finally { setReviewImgBusy(false); }
  }, [drawReviewCard, presentImage, pingMetric, showToast]);

  // ⭐先知卡：把「我们提前发现的」做成用户的炫耀战绩图（提前 X 小时 + 品牌 + 二维码）——自发传播引擎
  const _leadLabel = (h: any): string => (typeof h === 'number' && h >= 1) ? (h >= 24 ? `${(h / 24).toFixed(0)} 天` : `${h.toFixed(0)} 小时`) : '';
  const drawForesightCard = useCallback(async (): Promise<Blob | null> => {
    const r = reviewData; if (!r) return null;
    const edges = ((r.our_edge || []) as any[]).filter(e => typeof e.pct === 'number' && typeof e.lead_hours === 'number' && e.lead_hours >= 1)
      .sort((a, b) => (b.lead_hours || 0) - (a.lead_hours || 0));
    const hero = edges[0]; if (!hero) return null;
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    let qr: { size: number; matrix: number[][] } | null = null;
    try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: qrShareTarget(site) } }); } catch { qr = null; }
    const cv = document.createElement('canvas'); const ctx = cv.getContext('2d'); if (!ctx) return null;
    const SC = 2, W = 720, PAD = 44, maxW = W - PAD * 2;
    const F = (s: number, w = '400') => `${w} ${s}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
    const wrap = (text: string, font: string, mw: number): string[] => {
      ctx.font = font; const out: string[] = []; let line = '';
      for (const ch of String(text || '')) { if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch; }
      if (line) out.push(line); return out;
    };
    type Block = { lines: string[]; font: string; lh: number; color: string; mt: number; bullet?: string };
    const items: Block[] = []; let h = PAD;
    const push = (lines: string[], font: string, lh: number, color: string, mt = 0, bullet?: string) => { items.push({ lines, font, lh, color, mt, bullet }); h += mt + lines.length * lh; };
    const tag = (e: any) => e.kind === 'stock' ? '个股' : '板块';
    const pctOf = (e: any) => `${e.pct > 0 ? '+' : ''}${e.pct.toFixed(2)}%`;
    push(['DeepFocus · 信息领先 📈'], F(16, '800'), 26, '#ffb000');
    push(['🎯 我的先知战绩'], F(23, '800'), 38, '#ffffff', 8);
    push([`提前 ${_leadLabel(hero.lead_hours)}`], F(50, '900'), 60, '#ffd166', 6);
    push(['我在 DeepFocus 比市场早看到了 ↓'], F(15, '600'), 24, '#cfd6e0', 2);
    const sigTitle = (hero.signals && hero.signals[0] && hero.signals[0].title) || hero.name;
    push(wrap(`【${sigTitle}】`, F(18, '700'), maxW), F(18, '700'), 28, '#ffe6a3', 18);
    push([`${tag(hero)} ${hero.name}　今日 ${pctOf(hero)}　⚡领先约 ${_leadLabel(hero.lead_hours)}`], F(14, '700'), 24, '#ffcf72', 6);
    if (edges.length > 1) {
      push(['还提前发现 ——'], F(14, '700'), 22, '#9fc0ff', 18);
      edges.slice(1, 4).forEach((e: any) => push(wrap(`${tag(e)} ${e.name} ${pctOf(e)} · 领先约${_leadLabel(e.lead_hours)}`, F(13.5, '600'), maxW - 18), F(13.5, '600'), 21, '#cdd3dc', 4, '◆'));
    }
    const footTop = h + 20; const qrSize = qr ? 104 : 0; h = footTop + Math.max(qrSize, 84) + PAD;
    cv.width = W * SC; cv.height = Math.ceil(h) * SC; ctx.scale(SC, SC);
    ctx.fillStyle = '#0a0d12'; ctx.fillRect(0, 0, W, h); ctx.fillStyle = '#ffb000'; ctx.fillRect(0, 0, W, 5);
    ctx.textBaseline = 'top'; let y = PAD;
    for (const it of items) { y += it.mt; for (const ln of it.lines) { if (it.bullet) { ctx.font = F(11); ctx.fillStyle = '#ffb000'; ctx.fillText(it.bullet, PAD, y + 3); ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD + 18, y); } else { ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD, y); } y += it.lh; } }
    ctx.strokeStyle = '#1c2530'; ctx.beginPath(); ctx.moveTo(PAD, footTop); ctx.lineTo(W - PAD, footTop); ctx.stroke();
    if (qr) { const n = qr.size, cell = qrSize / n, qx = W - PAD - qrSize, qy = footTop + 16; ctx.fillStyle = '#fff'; ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12); ctx.fillStyle = '#000'; for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6); }
    ctx.font = F(19, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('比别人早一步看懂行情', PAD, footTop + 18);
    ctx.font = F(13); ctx.fillStyle = '#cfd6e0'; ctx.fillText('扫码也当「先知」· ' + site.replace(/^https?:\/\//, ''), PAD, footTop + 48);
    ctx.font = F(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText('数据 + AI · 仅供研究参考，不构成投资建议', PAD, footTop + 70);
    return await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
  }, [reviewData, qrShareTarget]);

  const shareForesight = useCallback(async () => {
    setReviewImgBusy(true);
    try { const blob = await drawForesightCard(); if (!blob) { showToast('⚠️ 暂无可炫耀的先知战绩'); return; } pingMetric('copy_image'); logAct('share_foresight', reviewData?.date || ''); await presentImage(blob, '✅ 先知战绩图已生成，晒到微信/朋友圈吧'); }
    finally { setReviewImgBusy(false); }
  }, [drawForesightCard, presentImage, pingMetric, showToast, logAct, reviewData]);

  const copyReviewText = useCallback(async () => {
    const r = reviewData; if (!r) return; const nar = r.narrative || {};
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    const L: string[] = [`📊 A股${r.session_label || '复盘'} · ${r.date || ''}`];
    if (nar.one_liner) L.push(`「${nar.one_liner}」`);
    const idx = (r.indices || []).filter((i: any) => typeof i.pct === 'number').map((i: any) => `${i.name} ${i.close}(${i.pct > 0 ? '+' : ''}${i.pct.toFixed(2)}%)`).join('  ');
    if (idx) L.push('', idx);
    const seg = (h: string, t: string) => { if (t) L.push('', `【${h}】${t}`); };
    seg('导读', nar.plain); seg('大盘', nar.market); seg('板块', nar.sectors); seg('资金面', nar.funds);
    if (nar.our_value || (r.our_edge || []).length) {
      L.push('', `【⭐ DeepFocus 提前发现】${nar.our_value || ''}`);
      (r.our_edge || []).slice(0, 5).forEach((e: any) => {
        const lead = (typeof e.lead_hours === 'number' && e.lead_hours >= 1) ? (e.lead_hours >= 24 ? `领先约${(e.lead_hours / 24).toFixed(0)}天` : `领先约${e.lead_hours.toFixed(0)}小时`) : '同日捕捉';
        L.push(`  · ${e.kind === 'stock' ? '个股' : '板块'} ${e.name}${typeof e.pct === 'number' ? ` ${e.pct > 0 ? '+' : ''}${e.pct.toFixed(2)}%` : ''}  ⚡${lead}${e.evidence ? `（${e.evidence}条佐证）` : ''}`);
      });
    }
    seg('下一交易日', nar.tomorrow);
    L.push('', `—— DeepFocus 终端 · 实时资讯，提前发现`, site.replace(/^https?:\/\//, ''));
    const text = L.join('\n');
    if (await copyText(text)) { pingMetric('copy_text'); showToast('✅ 复盘已复制，可直接粘贴分享'); }
    else showToast('⚠️ 复制失败，请长按文本手动复制');
  }, [reviewData, pingMetric, showToast]);

  // ===== AI 助手分享(快速问答 / 深度研判)——仿复盘卡:画布出图 + QR + 品牌水印 + 强制免责 =====
  // ⭐合规:只走本地出图/复制文字(无公开 hosted 链接/SEO);深度研判只画叙述结论,绝不含 iFinD 原始数据表;args 不入分享物。
  const [aiShareBusy, setAiShareBusy] = useState(false);
  const _cardFromBlocks = useCallback(async (
    build: (
      push: (lines: string[], font: string, lh: number, color: string, mt?: number, bullet?: string) => void,
      wrap: (t: string, f: string, mw: number) => string[],
      F: (s: number, w?: string) => string,
      maxW: number,
    ) => void,
    footer: { title: string; sub: string; disc: string },
  ): Promise<Blob | null> => {
    let qr: { size: number; matrix: number[][] } | null = null;
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: qrShareTarget(site) } }); } catch { qr = null; }
    const cv = document.createElement('canvas'); const ctx = cv.getContext('2d'); if (!ctx) return null;
    const SC = 2, W = 760, PAD = 40, maxW = W - PAD * 2;
    const F = (s: number, w = '400') => `${w} ${s}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
    const wrap = (text: string, font: string, mw: number): string[] => {
      ctx.font = font; const out: string[] = []; let line = '';
      for (const ch of String(text || '')) { if (ch === '\n') { out.push(line); line = ''; continue; } if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch; }
      if (line) out.push(line); return out;
    };
    type Block = { lines: string[]; font: string; lh: number; color: string; mt: number; bullet?: string };
    const items: Block[] = []; let h = PAD;
    const push = (lines: string[], font: string, lh: number, color: string, mt = 0, bullet?: string) => { items.push({ lines, font, lh, color, mt, bullet }); h += mt + lines.length * lh; };
    build(push, wrap, F, maxW);
    const footTop = h + 18; const qrSize = qr ? 100 : 0; h = footTop + Math.max(qrSize, 80) + PAD;
    cv.width = W * SC; cv.height = Math.ceil(h) * SC; ctx.scale(SC, SC);
    ctx.fillStyle = '#0a0d12'; ctx.fillRect(0, 0, W, h); ctx.fillStyle = '#ffb000'; ctx.fillRect(0, 0, W, 4);
    ctx.textBaseline = 'top'; let y = PAD;
    for (const it of items) { y += it.mt; for (const ln of it.lines) { if (it.bullet) { ctx.font = F(11); ctx.fillStyle = '#ffb000'; ctx.fillText(it.bullet, PAD, y + 3); ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD + 18, y); } else { ctx.font = it.font; ctx.fillStyle = it.color; ctx.fillText(ln, PAD, y); } y += it.lh; } }
    ctx.strokeStyle = '#1c2530'; ctx.beginPath(); ctx.moveTo(PAD, footTop); ctx.lineTo(W - PAD, footTop); ctx.stroke();
    if (qr) { const n = qr.size, cell = qrSize / n, qx = W - PAD - qrSize, qy = footTop + 16; ctx.fillStyle = '#fff'; ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12); ctx.fillStyle = '#000'; for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6); }
    ctx.font = F(16, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText(footer.title, PAD, footTop + 16);
    ctx.font = F(12); ctx.fillStyle = '#8a93a0'; ctx.fillText(footer.sub, PAD, footTop + 42);
    ctx.font = F(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText(footer.disc, PAD, footTop + 62);
    return await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
  }, [qrShareTarget]);

  const drawDeepCard = useCallback((): Promise<Blob | null> => {
    const t = deepTask; const dv = t && t.result; if (!t || !dv) return Promise.resolve(null);
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    return _cardFromBlocks((push, wrap, F, maxW) => {
      push(['DEEPFOCUS · 多智能体深度研判'], F(15, '700'), 24, '#ffb000');
      push([`${`${t.name || ''} ${t.symbol}`.trim()}　·　取证→多空辩论→风控→裁决`], F(13), 20, '#8a8463', 4);
      const dir = String(dv.direction || '中性');
      const dc = dir.indexOf('看多') >= 0 ? '#2bd96a' : dir.indexOf('看空') >= 0 ? '#ff5a52' : '#aeb6c2';
      const conf = typeof dv.confidence === 'number' ? `　置信 ${Math.round(dv.confidence * 100)}%` : '';
      push([`研判方向：${dir}${conf}`], F(20, '800'), 30, dc, 14);
      if (dv.thesis) push(wrap(stripMd(dv.thesis), F(15), maxW), F(15), 25, '#e2e5ea', 8);
      if (Array.isArray(dv.core_evidence) && dv.core_evidence.length) { push(['🔑 核心依据'], F(14, '700'), 23, '#9fc0ff', 16); dv.core_evidence.slice(0, 5).forEach((c: any) => push(wrap(`${c.point || ''}${c.evidence_ref ? `（${c.evidence_ref}）` : ''}`, F(13.5), maxW - 18), F(13.5), 22, '#dfe4ea', 4, '◆')); }
      if (Array.isArray(dv.key_risks) && dv.key_risks.length) { push(['⚠️ 关键风险'], F(14, '700'), 23, '#ffb0a8', 16); dv.key_risks.slice(0, 4).forEach((r: any) => push(wrap(`${r.risk || ''}${r.severity ? `（${r.severity}）` : ''}`, F(13.5), maxW - 18), F(13.5), 22, '#f0d2cd', 4, '◆')); }
      const wl = dv.watch_levels;
      if (wl && (wl.support || wl.resistance || wl.note)) { push(['📐 观察位（非买卖指令）'], F(14, '700'), 23, '#c4b5fd', 16); const lv = [wl.support ? `支撑 ${wl.support}` : '', wl.resistance ? `压力 ${wl.resistance}` : ''].filter(Boolean).join('　'); if (lv) push([lv], F(14), 23, '#e2e5ea', 2); if (wl.note) push(wrap(stripMd(wl.note), F(13), maxW), F(13), 21, '#b9c0cc', 2); }
      if (dv.debate_synthesis) { push(['⚖️ 多空交锋'], F(14, '700'), 23, '#9fc0ff', 16); push(wrap(stripMd(dv.debate_synthesis), F(14), maxW), F(14), 24, '#dfe4ea', 2); }
    }, { title: 'DeepFocus · AI 深度研判', sub: '扫码用 AI 研判个股 · ' + site.replace(/^https?:\/\//, ''), disc: '⚠ AI 多角色综合生成 · 仅供研究参考，不构成投资建议' });
  }, [deepTask, _cardFromBlocks]);

  const shareDeepImage = useCallback(async () => {
    setAiShareBusy(true);
    try { const blob = await drawDeepCard(); if (!blob) { showToast('⚠️ 图片生成失败，请重试'); return; } pingMetric('copy_image'); logAct('deep_share_img', deepTask?.symbol || ''); await presentImage(blob, '✅ 研判图已复制，可粘贴到微信/文档'); }
    finally { setAiShareBusy(false); }
  }, [drawDeepCard, presentImage, pingMetric, showToast, logAct, deepTask]);

  const copyDeepText = useCallback(async () => {
    const t = deepTask; const dv = t && t.result; if (!t || !dv) return;
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    const L: string[] = [`🔬 DeepFocus AI 深度研判 · ${`${t.name || ''} ${t.symbol}`.trim()}`];
    L.push(`研判方向：${dv.direction || '中性'}${typeof dv.confidence === 'number' ? ` · 置信 ${Math.round(dv.confidence * 100)}%` : ''}`);
    if (dv.thesis) L.push('', stripMd(dv.thesis));
    if (Array.isArray(dv.core_evidence) && dv.core_evidence.length) { L.push('', '【核心依据】'); dv.core_evidence.slice(0, 5).forEach((c: any) => L.push(`· ${c.point || ''}${c.evidence_ref ? `（${c.evidence_ref}）` : ''}`)); }
    if (Array.isArray(dv.key_risks) && dv.key_risks.length) { L.push('', '【关键风险】'); dv.key_risks.slice(0, 4).forEach((r: any) => L.push(`· ${r.risk || ''}${r.severity ? `（${r.severity}）` : ''}`)); }
    const wl = dv.watch_levels;
    if (wl && (wl.support || wl.resistance || wl.note)) { L.push('', `【观察位（非买卖指令）】${[wl.support ? `支撑 ${wl.support}` : '', wl.resistance ? `压力 ${wl.resistance}` : ''].filter(Boolean).join('  ')}`); if (wl.note) L.push(stripMd(wl.note)); }
    if (dv.debate_synthesis) L.push('', `【多空交锋】${stripMd(dv.debate_synthesis)}`);
    L.push('', stripMd(dv.disclaimer || 'AI 多角色综合生成，仅供研究参考，不构成投资建议。'), `—— DeepFocus 终端 · ${site.replace(/^https?:\/\//, '')}`);
    if (await copyText(L.join('\n'))) { pingMetric('copy_text'); logAct('deep_share_text', t.symbol || ''); showToast('✅ 研判已复制，可直接粘贴'); }
    else showToast('⚠️ 复制失败，请长按手动复制');
  }, [deepTask, pingMetric, showToast, logAct]);

  const drawQaCard = useCallback((): Promise<Blob | null> => {
    const q = aiQuestion.trim(); const a = aiAnswer.trim(); if (!a) return Promise.resolve(null);
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    return _cardFromBlocks((push, wrap, F, maxW) => {
      push(['DEEPFOCUS · AI 投研问答'], F(15, '700'), 24, '#ffb000');
      if (q) push(wrap('问：' + q, F(15, '700'), maxW), F(15, '700'), 25, '#ffd980', 8);
      push(wrap(stripMd(a), F(15), maxW), F(15), 26, '#e2e5ea', 10);
    }, { title: 'DeepFocus · AI 投研问答', sub: '扫码自己问 · ' + site.replace(/^https?:\/\//, ''), disc: '⚠ AI 综合平台数据生成 · 仅供研究参考，不构成投资建议' });
  }, [aiQuestion, aiAnswer, _cardFromBlocks]);

  const shareQaImage = useCallback(async () => {
    setAiShareBusy(true);
    try { const blob = await drawQaCard(); if (!blob) { showToast('⚠️ 图片生成失败，请重试'); return; } pingMetric('copy_image'); logAct('ai_share_img', aiQuestion.slice(0, 40)); await presentImage(blob, '✅ 已复制为图片，可粘贴到微信/文档'); }
    finally { setAiShareBusy(false); }
  }, [drawQaCard, presentImage, pingMetric, showToast, logAct, aiQuestion]);

  const copyQaText = useCallback(async () => {
    const a = aiAnswer.trim(); if (!a) return;
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    const L: string[] = [];
    if (aiQuestion.trim()) L.push(`问：${aiQuestion.trim()}`, '');
    L.push(stripMd(a), '', '⚠ 仅供研究参考，不构成投资建议。', `—— DeepFocus 终端 · ${site.replace(/^https?:\/\//, '')}`);
    if (await copyText(L.join('\n'))) { pingMetric('copy_text'); showToast('✅ 已复制为文字'); }
    else showToast('⚠️ 复制失败，请长按手动复制');
  }, [aiAnswer, aiQuestion, pingMetric, showToast]);

  // ⭐快讯图片卡（金十式）：微信群里真正流通的是「截图 + 二维码」不是链接（复制 1900 次/月 vs 分享 27 次）——
  // 卡片自带品牌水印与二维码回流入口，收信人长按识别直达该条快讯落地页。纯事实转述，不带 AI 结论。
  const saveNewsImage = useCallback(async (m: RealtimeMessageRecord) => {
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    const refCode = inviteCodeRef.current;
    const link = m.id ? `${site}/article/${encodeURIComponent(m.id)}?utm_source=img${refCode ? `&ref=${encodeURIComponent(refCode)}` : ''}` : site;
    let qr: { size: number; matrix: number[][] } | null = null;
    try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: link } }); } catch { qr = null; }
    const cv = document.createElement('canvas');
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    const SC = 2, W = 680, PAD = 36, maxW = W - PAD * 2;
    const F = (size: number, weight = '400') => `${weight} ${size}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
    const wrap = (text: string, font: string, mw: number): string[] => {
      ctx.font = font; const out: string[] = []; let line = '';
      for (const ch of String(text || '')) {
        if (ch === '\n') { out.push(line); line = ''; continue; }
        if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch;
      }
      if (line) out.push(line); return out;
    };
    const when = (() => {
      try { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(m.created_at)); }
      catch { return ''; }
    })();
    const titleLines = wrap(m.title || '', F(20, '700'), maxW);
    const bodyRaw = (m.content && m.content !== m.title) ? m.content : '';
    const bodyLines = bodyRaw ? wrap(bodyRaw, F(15), maxW).slice(0, 16) : [];
    const qrSize = qr ? 92 : 0;
    let h = PAD + 26 + 10 + titleLines.length * 30 + (bodyLines.length ? 10 + bodyLines.length * 25 : 0);
    const footTop = h + 16;
    h = footTop + Math.max(qrSize + 14, 74) + PAD - 8;
    cv.width = W * SC; cv.height = Math.ceil(h) * SC; ctx.scale(SC, SC);
    ctx.fillStyle = '#0a0d12'; ctx.fillRect(0, 0, W, h);
    ctx.fillStyle = '#ffb000'; ctx.fillRect(0, 0, W, 4);
    ctx.save();  // 品牌水印
    ctx.globalAlpha = 0.045; ctx.fillStyle = '#ffb000'; ctx.font = F(32, '800');
    ctx.translate(W / 2, h / 2); ctx.rotate(-Math.PI / 7);
    for (let wy = -h; wy < h; wy += 110) for (let wx = -W; wx < W; wx += 340) ctx.fillText('DEEPFOCUS', wx, wy);
    ctx.restore();
    ctx.textBaseline = 'top';
    let y = PAD;
    ctx.font = F(14, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('DEEPFOCUS 快讯', PAD, y);
    if (when) { ctx.font = F(13); ctx.fillStyle = '#8a93a0'; ctx.textAlign = 'right'; ctx.fillText(`${when} · 北京时间`, W - PAD, y + 1); ctx.textAlign = 'left'; }
    y += 26 + 10;
    ctx.font = F(20, '700'); ctx.fillStyle = '#f4ecd6';
    for (const ln of titleLines) { ctx.fillText(ln, PAD, y); y += 30; }
    if (bodyLines.length) {
      y += 10; ctx.font = F(15); ctx.fillStyle = '#c9cfd6';
      for (const ln of bodyLines) { ctx.fillText(ln, PAD, y); y += 25; }
    }
    ctx.strokeStyle = '#1c2530'; ctx.beginPath(); ctx.moveTo(PAD, footTop); ctx.lineTo(W - PAD, footTop); ctx.stroke();
    if (qr) {
      const n = qr.size, cell = qrSize / n, qx = W - PAD - qrSize, qy = footTop + 12;
      ctx.fillStyle = '#fff'; ctx.fillRect(qx - 5, qy - 5, qrSize + 10, qrSize + 10);
      ctx.fillStyle = '#000';
      for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6);
    }
    ctx.font = F(15, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('DEEPFOCUS 金融终端', PAD, footTop + 14);
    ctx.font = F(12); ctx.fillStyle = '#8a93a0'; ctx.fillText('长按识别二维码 · 看 AI 解读与后续追踪', PAD, footTop + 38);
    ctx.font = F(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText('快讯为事实转述 · 不构成投资建议', PAD, footTop + 58);
    const blob = await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
    if (!blob) { showToast('⚠️ 图片生成失败，请重试'); return; }
    pingMetric('news_img', m.title); logAct('share_img', `快讯卡·${(m.title || '').slice(0, 30)}`);
    await presentImage(blob, '✅ 快讯卡已复制，可直接粘贴到微信群');
  }, [pingMetric, logAct, showToast, presentImage]);

  // 全实时流去重后的版本：派生列表（头条/自选相关/资讯流/角标）统一用它，避免同一条重复推送在各处重复出现
  const dedupedMessages = useMemo(() => dedupeMessages(messages), [messages]);
  // 各模块「头条」：优先用 AI 评选(机构交易台标准，每类最多3条、带"为何重要")；拉不到时回退本地规则
  const heads = useMemo(() => {
    const asArr = (v: any): any[] => Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];
    if (picks && (asArr(picks.kx).length || asArr(picks.wz).length || asArr(picks.yb).length)) {
      return { kx: asArr(picks.kx), wz: asArr(picks.wz), yb: asArr(picks.yb) };
    }
    const ns = dedupedMessages.filter(m => m.topic !== '信号' && m.source_type !== 'dao-signal');
    const top = (arr: RealtimeMessageRecord[]): RealtimeMessageRecord | null => arr.find(m => m.severity === 'critical') || arr[0] || null;
    const kx = top(ns.filter(m => (m.topic || '') === '快讯'));
    let wz: RealtimeMessageRecord | null = top(ns.filter(m => (m.topic || '') === '文章'));
    if (kx && wz && (wz.title.includes(kx.title.slice(0, 12)) || kx.title.includes(wz.title.slice(0, 12)))) wz = null;
    // 研报兜底：只取「够格」(宏观/策略/行业/龙头权重股)报告，避免中小盘个股标题硬上头条
    const ybKw = ['宏观', '策略', '经济', '市场', '大盘', '大类', '配置', '行业', '板块', '周观点', '复盘', '政策', '展望', '周期', '估值', '流动性', '通胀', '美联储', '降息', '加息', '专题', '周度', '月度', '利率', '汇率', '商品', '主题', '景气', '拐点', '方向', '供给', '需求', '信用', '资产', '如何看', '如何评估', '怎么看', '怎么配', '再平衡'];
    const ybLeaders = ['贵州茅台', '茅台', '腾讯', '英伟达', '苹果', '台积电', '宁德时代', '比亚迪', '阿里', '美的'];
    const yb = reports.find(r => {
      const t = r.title || '';
      if (ybKw.some(k => t.includes(k)) || ybLeaders.some(k => t.includes(k))) return true;
      return t.replace(/[-\s]*\d{6,8}$/, '').trim().length >= 9;
    }) || null;
    return { kx: kx ? [kx] : [], wz: wz ? [wz] : [], yb: yb ? [yb] : [] };
  }, [picks, dedupedMessages, reports]);
  const pinnedIds = useMemo(() => new Set([...heads.kx, ...heads.wz].map((m: any) => m?.id).filter(Boolean) as string[]), [heads]);
  const ybHeadKeys = useMemo(() => new Set(heads.yb.map((y: any) => y.file_id || y.id).filter(Boolean) as string[]), [heads]);

  // 自选相关·今日：当天 + 命中任一自选股关键词的快讯/文章，置顶到头条下方高亮（去掉已在头条的）
  // 「自选」tab 数据源：所有匹配自选股关键词的快讯/文章，时间从新到旧（dedupedMessages 已倒序）
  // 早报/综述/盘前盘后类「大盘汇总」文章：正文海量、顺带提及一堆个股，命中即标会变噪音
  //（如「彭博财经早报」正文提到比亚迪→被误标“自选 比亚迪”）。此类只按标题匹配，正文提及不算。
  const isDigest = (m: RealtimeMessageRecord) =>
    /(早报|晚报|午报|早餐|早盘|盘前|盘后|收盘|复盘|快报|简报|市况|要闻|汇总|综述|日报|周报|月报|全球市场|财经早|宏观日)/.test(m.title || '');
  // 命中自选股的快讯/文章 + 各自命中的股票（综述类仅按标题匹配，避免正文顺带提及的噪音）
  const matchWatchlist = useCallback((msgs: RealtimeMessageRecord[]) => {
    if (!watchlist.length) return [] as { m: RealtimeMessageRecord; syms: string[] }[];
    const out: { m: RealtimeMessageRecord; syms: string[] }[] = [];
    for (const m of msgs) {
      if (m.topic !== '快讯' && m.topic !== '文章') continue;
      // 综述类：仅标题命中才算「相关」；其余正文+标题一起匹配（召回更全）
      const hay = isDigest(m) ? (m.title || '') : `${m.title} ${m.content || ''}`;
      const syms = watchlist.filter(sym => keysOf(sym).some(k => k && hay.includes(k)));
      if (syms.length) out.push({ m, syms });
      if (out.length >= 400) break;
    }
    return out;
  }, [watchlist, keysOf]);
  // 自选股全别名（OR 检索词，喂服务端 anyq 拉取全量历史）
  const watchlistAliasKey = useMemo(() => Array.from(new Set(watchlist.flatMap(s => stockAliases(s)))).join(','), [watchlist, stockAliases]);
  // 计数角标 & 兜底：本地实时流命中（任意标签页可用）
  const watchlistNews = useMemo(() => matchWatchlist(dedupedMessages), [matchWatchlist, dedupedMessages]);
  // 角标全量：独立常驻按自选别名向服务端拉一次（不依赖当前在哪个 tab）→ 任意页角标都反映真实自选条数
  const [wlAllMsgs, setWlAllMsgs] = useState<RealtimeMessageRecord[]>([]);
  useEffect(() => {
    if (!watchlistAliasKey) { setWlAllMsgs([]); return undefined; }
    let cancelled = false;
    const loadWl = async () => {
      try { const r = await listRealtimeMessages({ anyq: watchlistAliasKey, limit: 400 }); if (!cancelled) setWlAllMsgs(r); } catch { /* 拉取失败保留上次，不影响使用 */ }
    };
    loadWl();
    const t = window.setInterval(loadWl, 120000);
    return () => { cancelled = true; window.clearInterval(t); };
  }, [watchlistAliasKey]);
  const watchlistAll = useMemo(() => matchWatchlist(dedupeMessages(wlAllMsgs)), [matchWatchlist, wlAllMsgs]);
  // 自选 tab 实际数据源：服务端 anyq 历史检索（命中更全）→ 独立全量兜底 → 本地实时流
  const watchlistServer = useMemo(() => {
    const server = matchWatchlist(dedupeMessages(searchMsgs));
    if (server.length) return server;
    if (watchlistAll.length) return watchlistAll;
    return watchlistNews;
  }, [matchWatchlist, searchMsgs, watchlistAll, watchlistNews]);
  const watchlistFeed = useMemo(() => watchlistServer.map(x => x.m), [watchlistServer]);  // 仅消息列表（喂给 feed/分页）
  const wlSymsMap = useMemo(() => { const map = new Map<string, string[]>(); watchlistServer.forEach(x => map.set(x.m.id, x.syms)); return map; }, [watchlistServer]);  // id→命中的自选股（行内徽标）
  // 「自选」tab 按个股分组：每只股票一个专区（最新动态的股票排前），跨多股的消息在每个相关分组都出现；
  // 组内再按「快讯 / 文章 / 研报」分节——研报按标题+预提取标的匹配该股别名。
  const wlGroups = useMemo(() => {
    if (!watchlist.length) return [] as { sym: string; kx: RealtimeMessageRecord[]; wz: RealtimeMessageRecord[]; yb: ResearchWireItem[]; latest: string; total: number }[];
    const by = new Map<string, { kx: RealtimeMessageRecord[]; wz: RealtimeMessageRecord[] }>(watchlist.map(s => [s, { kx: [], wz: [] }]));
    watchlistServer.forEach(({ m, syms }) => syms.forEach(s => {
      const g = by.get(s); if (!g) return;
      ((m.topic || '') === '文章' ? g.wz : g.kx).push(m);
    }));
    // 研报暂不在自选展示（用户拍板 2026-06-10）；匹配逻辑保留在 git 历史，要恢复时把这里改回按
    // title+instruments 匹配 keysOf 别名即可。
    const ybBy = new Map<string, ResearchWireItem[]>(watchlist.map(s => [s, []]));
    return Array.from(by.entries())
      .map(([sym, g]) => {
        const yb = ybBy.get(sym) || [];
        const latest = [g.kx[0]?.created_at, g.wz[0]?.created_at].filter(Boolean).sort().pop() || '';
        return { sym, kx: g.kx, wz: g.wz, yb, latest, total: g.kx.length + g.wz.length + yb.length };
      })
      .sort((a, b) => {  // 有动态的在前、按最新消息时间倒序；只有研报的次之；全空的沉底（保持自选原顺序）
        if (a.latest !== b.latest) { if (!a.latest) return 1; if (!b.latest) return -1; return a.latest < b.latest ? 1 : -1; }
        return (b.total ? 1 : 0) - (a.total ? 1 : 0);
      });
  }, [watchlist, watchlistServer]);
  const [wlCollapsed, setWlCollapsed] = useState<Record<string, boolean>>({});  // 分组折叠
  const [wlShowAll, setWlShowAll] = useState<Record<string, boolean>>({});      // 分节展开全部（key=`sym#节`）
  const [wlSecClosed, setWlSecClosed] = useState<Record<string, boolean>>({});  // 分节整节收起（key=`sym#节`）
  // ===== 自选未读标记：localStorage 记每只股「上次看到的最新消息时刻」，比它新的 = 未读（红色 N 新）。
  // 进入自选可看到积累的未读；离开自选 tab 时整体置为已读。首次使用以当前最新为基线，避免全屏爆"新"。
  const [wlSeen, setWlSeen] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem('bbt.wlseen') || '{}'); } catch { return {}; }
  });
  useEffect(() => { try { localStorage.setItem('bbt.wlseen', JSON.stringify(wlSeen)); } catch { /* */ } }, [wlSeen]);
  const wlMarkAllRead = useCallback(() => {
    setWlSeen(prev => {
      const next = { ...prev };
      wlGroups.forEach(g => { if (g.latest) next[g.sym] = g.latest; });
      return next;
    });
  }, [wlGroups]);
  const wlSeenInited = useRef(false);
  useEffect(() => {  // 首次（无历史基线）：当前最新即基线
    if (wlSeenInited.current || !wlGroups.some(g => g.total > 0)) return;
    wlSeenInited.current = true;
    if (Object.keys(wlSeen).length === 0) wlMarkAllRead();
  }, [wlGroups, wlSeen, wlMarkAllRead]);
  const prevFeedFilterRef = useRef(feedFilter);
  useEffect(() => {  // 离开「自选」tab → 本次看过的都置为已读
    if (prevFeedFilterRef.current === '自选' && feedFilter !== '自选') wlMarkAllRead();
    prevFeedFilterRef.current = feedFilter;
  }, [feedFilter, wlMarkAllRead]);

  // 今日市场早报：关键指标 + AI 头条 → 一张可转发的图（带二维码）
  const [briefBusy, setBriefBusy] = useState(false);
  const drawBriefCard = useCallback(async (): Promise<Blob | null> => {
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    let qr: { size: number; matrix: number[][] } | null = null;
    try { qr = await apiGet<{ size: number; matrix: number[][] }>('/api/qr', { params: { data: qrShareTarget(site) } }); } catch { qr = null; }
    const cv = document.createElement('canvas'); const ctx = cv.getContext('2d'); if (!ctx) return null;
    const SC = 2, W = 760, PAD = 40, maxW = W - PAD * 2;
    const F = (s: number, w = '400') => `${w} ${s}px "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif`;
    const wrap = (text: string, font: string, mw: number): string[] => {
      ctx.font = font; const out: string[] = []; let line = '';
      for (const ch of String(text || '')) { if (ch === '\n') { out.push(line); line = ''; continue; } if (line && ctx.measureText(line + ch).width > mw) { out.push(line); line = ch; } else line += ch; }
      if (line) out.push(line); return out;
    };
    const dateStr = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'long' }).format(new Date());
    type Op = () => void;
    const ops: Op[] = []; let h = PAD;
    const addText = (lines: string[], font: string, lh: number, color: string, mt = 0, x = PAD) => {
      h += mt; const y0 = h;
      ops.push(() => { ctx.font = font; ctx.fillStyle = color; ctx.textBaseline = 'top'; lines.forEach((ln, i) => ctx.fillText(ln, x, y0 + i * lh)); });
      h += lines.length * lh;
    };
    addText(['DEEPFOCUS 金融终端 · 今日市场早报'], F(19, '700'), 27, '#ffb000');
    addText([dateStr], F(13), 20, '#8a8463', 2);
    // 关键指标
    const ind = (k: string) => macro[k];
    const macroList = [{ k: 'vix', l: 'VIX' }, { k: 'spx', l: '标普' }, { k: 'ten_year', l: 'US10Y' }, { k: 'gold', l: '黄金' }, { k: 'oil', l: 'WTI' }, { k: 'bitcoin', l: 'BTC' }]
      .map(m => ({ ...m, d: ind(m.k) })).filter(m => m.d && m.d.value != null);
    if (macroList.length) {
      addText(['关键指标'], F(13, '700'), 22, '#6ab0ff', 16);
      const colW = maxW / 2; const rowH = 24; const startY = h + 4;
      macroList.forEach((m, i) => {
        const col = i % 2, row = Math.floor(i / 2);
        const x = PAD + col * colW, y = startY + row * rowH;
        const pc = Number(m.d.change_pct || 0); const cc = pc > 0 ? '#2bd96a' : pc < 0 ? '#ff5a52' : '#9aa6b2';
        const lv = `${m.l} ${m.d.value}${m.d.unit === '%' ? '%' : ''}`;
        ops.push(() => {
          ctx.textBaseline = 'top'; ctx.font = F(14, '600'); ctx.fillStyle = '#e6ebf2'; ctx.fillText(lv, x, y);
          if (m.d.change_pct != null) { const w = ctx.measureText(lv + '  ').width; ctx.font = F(13, '600'); ctx.fillStyle = cc; ctx.fillText(`${pc > 0 ? '+' : ''}${pc.toFixed(2)}%`, x + w, y); }
        });
      });
      h = startY + Math.ceil(macroList.length / 2) * rowH;
    }
    // 今日头条
    addText(['今日头条 · AI 精选'], F(13, '700'), 22, '#ffce72', 18);
    const hd = [['快讯', '#ff7a72', heads.kx[0]], ['文章', '#6ab0ff', heads.wz[0]], ['研报', '#c4b5fd', heads.yb[0]]] as const;
    let any = false;
    hd.forEach(([tag, color, item]) => {
      if (!item) return; any = true;
      const titleLines = wrap(`【${tag}】${item.title}`, F(15, '700'), maxW);
      addText(titleLines, F(15, '700'), 22, color, 10);
      if (item.why) addText(wrap('💡 ' + item.why, F(13), maxW), F(13), 19, '#cbb88a', 3);
    });
    if (!any) addText(['暂无重大头条'], F(13), 20, '#8a8463', 8);
    // 页脚 + 二维码
    const footTop = h + 20; const qrSize = qr ? 92 : 0;
    h = footTop + Math.max(qrSize, 72) + PAD;
    cv.width = W * SC; cv.height = Math.ceil(h) * SC; ctx.scale(SC, SC);
    ctx.fillStyle = '#0a0d12'; ctx.fillRect(0, 0, W, h); ctx.fillStyle = '#ffb000'; ctx.fillRect(0, 0, W, 4);
    ops.forEach(op => op());
    ctx.strokeStyle = '#1c2530'; ctx.beginPath(); ctx.moveTo(PAD, footTop); ctx.lineTo(W - PAD, footTop); ctx.stroke();
    if (qr) {
      const n = qr.size, cell = qrSize / n, qx = W - PAD - qrSize, qy = footTop + 14;
      ctx.fillStyle = '#fff'; ctx.fillRect(qx - 6, qy - 6, qrSize + 12, qrSize + 12); ctx.fillStyle = '#000';
      for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (qr.matrix[i][j]) ctx.fillRect(qx + j * cell, qy + i * cell, cell + 0.6, cell + 0.6);
    }
    ctx.textBaseline = 'top';
    ctx.font = F(16, '800'); ctx.fillStyle = '#ffb000'; ctx.fillText('DEEPFOCUS 金融终端', PAD, footTop + 12);
    ctx.font = F(12); ctx.fillStyle = '#8a93a0'; ctx.fillText('扫码访问 · ' + site.replace(/^https?:\/\//, ''), PAD, footTop + 38);
    ctx.font = F(10.5); ctx.fillStyle = '#5f6671'; ctx.fillText('AI 自动汇编 · 仅供参考，非投资建议', PAD, footTop + 58);
    return await new Promise<Blob | null>(res => cv.toBlob(res, 'image/png'));
  }, [macro, heads, qrShareTarget]);

  const shareBrief = useCallback(async () => {
    setBriefBusy(true);
    try {
      const blob = await drawBriefCard();
      if (!blob) { showToast('⚠️ 早报生成失败，请重试'); return; }
      pingMetric('brief');
      await presentImage(blob, '✅ 今日早报已复制，可直接粘贴/转发');
    } finally { setBriefBusy(false); }
  }, [drawBriefCard, presentImage, pingMetric, showToast]);

  const closeShareImg = useCallback(() => {
    setShareImgUrl(prev => { if (prev) URL.revokeObjectURL(prev); return ''; });
    setShareImgNote(''); setShareImgCoarse(false);
  }, []);

  // 一键复制解读全文：末尾巧妙带上站点网址，复制即传播
  const copyAiResult = useCallback(async () => {
    const r = aiResult;
    if (!r) return;
    const bull = (r.bullish?.length ? r.bullish : r.key_points) || [];
    const bear = (r.bearish?.length ? r.bearish : r.risks) || [];
    const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
    const L: string[] = [];
    L.push(`📊 AI 解读 | ${aiReport?.title || r.title || ''}`);
    if (aiReport?.date) L.push(`🗓 ${aiReport.date}`);
    const meta = [r.subject && `标的 ${r.subject}`, r.rating && `评级 ${r.rating}`, r.target_price && `目标价 ${r.target_price}`].filter(Boolean);
    if (meta.length) { L.push(''); L.push(meta.join('  |  ')); }
    if (r.instruments?.length) { L.push('', `📈 提及个股：${r.instruments.join('、')}`); }
    if (r.one_liner) { L.push(''); L.push(`💡 ${r.one_liner}`); }
    if (r.summary) { L.push('', '【摘要】', r.summary); }
    if (r.core_logic) { L.push('', '【投资逻辑】', r.core_logic); }
    if (bull.length) { L.push('', '【利好】', ...bull.map((b, i) => `${i + 1}. ${b}`)); }
    if (bear.length) { L.push('', '【利空】', ...bear.map((b, i) => `${i + 1}. ${b}`)); }
    if (r.takeaway) { L.push('', `📌 启示：${r.takeaway}`); }
    if (r.df_take) { L.push('', '【DeepFocus 视角 · 独家点评】', r.df_take); }
    L.push('', '——————————', `DeepFocus 终端 · AI 速读 · 提前发现`, site);
    const text = L.join('\n');
    try {
      if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); }
      else {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
      }
      setAiTextCopied(true); window.setTimeout(() => setAiTextCopied(false), 1800);
      pingMetric('copy_text'); logAct('copy', aiReport?.title); showToast('✅ 已复制文字');
    } catch { setAiTextCopied(false); showToast('⚠️ 复制失败，请手动选择文本'); }
  }, [aiResult, aiReport, pingMetric, logAct, showToast]);

  // ---- 命令面板远程搜索(去抖)----
  // 统一搜索：股票之外顺带聚合 快讯/研报/板块/名词学堂（/api/search/universal，端点缺失时优雅回退纯股票搜索）——
  // 搜索是散户第一动作，此前搜「白酒」只出股票候选，已建的题材导航/研报库/学堂在搜索里全是暗资产。
  const [uniHits, setUniHits] = useState<{ news: any[]; reports: any[]; terms: any[]; boards: any[] }>({ news: [], reports: [], terms: [], boards: [] });
  useEffect(() => {
    if (!paletteOpen) { setRemoteHits([]); setUniHits({ news: [], reports: [], terms: [], boards: [] }); setPaletteLoading(false); return; }
    const q = pq.trim();
    if (q.length < 1) { setRemoteHits([]); setUniHits({ news: [], reports: [], terms: [], boards: [] }); setPaletteLoading(false); return; }
    let cancelled = false;
    setPaletteLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const resp = await apiGet<{ candidates: SearchCandidate[] }>('/api/market/search', { params: { q } });
        if (cancelled) return;
        const hits = (resp.candidates || []).filter(c => c.code && !watchlist.includes(c.code)).slice(0, 8);
        setRemoteHits(hits);
      } catch { if (!cancelled) setRemoteHits([]); }
      finally { if (!cancelled) setPaletteLoading(false); }
      // 聚合搜索并行补充（失败静默——旧后端没有该端点时体验与原先完全一致）
      try {
        const u = await apiGet<any>('/api/search/universal', { params: { q } });
        if (!cancelled && u) setUniHits({ news: u.news || [], reports: u.reports || [], terms: u.terms || [], boards: u.boards || [] });
      } catch { /* 静默回退 */ }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [pq, paletteOpen, watchlist]);
  // 输入变化 / 打开时，高亮回到第一项
  useEffect(() => { setPaletteActive(0); }, [pq, paletteOpen]);

  const mergeMessages = useCallback((incoming: RealtimeMessageRecord[]) => {
    setMessages(prev => {
      const fresh = incoming.filter(m => m && !seen.current.has(m.id));
      // 已见 id 的重广播 = 字段更新（如 AI 情绪回填升级标签）→ 原地替换
      const updates = incoming.filter(m => m && seen.current.has(m.id));
      let next = prev;
      if (updates.length) {
        const byId = new Map(updates.map(m => [m.id, m]));
        next = next.map(m => byId.get(m.id) || m);
      }
      if (!fresh.length) return updates.length ? next : prev;
      fresh.forEach(m => seen.current.add(m.id));
      return [...fresh, ...next].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, MAX_KEEP);
    });
  }, []);
  const loadInitial = useCallback(async () => {
    try {
      const initial = await listRealtimeMessages({ limit: 150 });
      seen.current = new Set(initial.map(m => m.id));
      setMessages([...initial].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, MAX_KEEP));
      setFeedLoadError(false);
    } catch { setFeedLoadError(true); } finally { setFeedBooted(true); }  // 首批拉取结束（成败皆算）→ 撤掉骨架屏
  }, []);
  useEffect(() => {
    loadInitial();
    // 实时信号同时喂两条线：①渲染快讯列表；②盯盘召回——自选股出快讯/异动时把用户叫回来。
    // evaluateAndNotify 内部按最新偏好自判：未开启盯盘(browserEnabled=false)时为廉价空操作，
    // 开启后仅对自选命中 + 级别匹配 + 标签页不可见时弹一次(模块级去重)。读 watchlistRef 取最新自选。
    const stream = createRealtimeMessageStream({
      onMessage: m => { mergeMessages([m]); evaluateAndNotify(m, watchlistRef.current); },
      onStatus: setStatus,
    });
    return () => stream.close();
  }, [loadInitial, mergeMessages]);

  // 离线召回补订阅:对「已开启盯盘 + 已授权通知」的用户(尤其 Web Push/VAPID 上线前点过盯盘的老用户),
  // 打开页面即静默补上 Web Push 订阅(subscribeWebPush 幂等、失败安静)。这是把关页用户叫回来的命脉,
  // 否则历史上「开启盯盘」只存了本地偏好、从不注册离线订阅 → recall 订阅恒为 0。
  const webpushArmedRef = useRef(false);
  useEffect(() => {
    if (webpushArmedRef.current || !feedBooted) return;
    try {
      if (getNotificationPermission() === 'granted' && loadRecallPrefs().browserEnabled) {
        webpushArmedRef.current = true;
        void subscribeWebPush({ symbols: watchlistRef.current });
      }
    } catch { /* */ }
  }, [feedBooted]);

  // 文章在实时流里稀疏（最近 200 条里常只有几篇）→ 单独按 topic 拉全量文章，
  // 用于角标真实计数 + 「文章」标签展示全部，而非只展示混进窗口的那几篇。
  const [articles, setArticles] = useState<RealtimeMessageRecord[]>([]);
  const loadArticles = useCallback(async () => {
    try { setArticles(await listRealtimeMessages({ topic: '文章', limit: 200 })); } catch { /* */ }
  }, []);
  useEffect(() => {
    loadArticles();
    const t = window.setInterval(loadArticles, 120000);
    return () => window.clearInterval(t);
  }, [loadArticles]);

  useEffect(() => { if (messages.length) latestTsRef.current = messages[0].created_at || latestTsRef.current; }, [messages]);

  // 实时兜底：SSE 断线/切后台时，每 5s 增量轮询(只取更新的，响应极小)+ 回前台/网络恢复立即拉取。
  // SSE 健康(live)时新消息已经实时推过来了 → 兜底放缓到 30s 校对一次，请求量降 83%。
  const sseLiveRef = useRef(false);
  useEffect(() => { sseLiveRef.current = status === 'live'; }, [status]);
  useEffect(() => {
    let cancelled = false;
    let lastPollAt = 0;
    const poll = async (force = false) => {
      if (!force && sseLiveRef.current && Date.now() - lastPollAt < 30000) return;
      lastPollAt = Date.now();
      try {
        const since = latestTsRef.current;
        const latest = await listRealtimeMessages(since ? { since, limit: 60 } : { limit: 60 });
        if (!cancelled && latest.length) mergeMessages(latest);
      } catch { /* 忽略，下轮重试 */ }
    };
    const timer = window.setInterval(() => poll(), 5000);
    const onVisible = () => { if (document.visibilityState === 'visible') poll(true); };  // 回前台立即校对，不受放缓约束
    const onOnline = () => poll(true);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onOnline);
    window.addEventListener('focus', onVisible);
    return () => {
      cancelled = true; window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('focus', onVisible);
    };
  }, [mergeMessages]);

  // 何时改用「服务端取数」：搜索/选股时(全量历史检索)，或在「文章」标签(文章在实时流里稀疏，需按主题向服务器要)
  const useServerFeed = useMemo(() => newsSearching || feedFilter === '文章' || feedFilter === '自选', [newsSearching, feedFilter]);

  // 服务端取数：手输→q(AND)；选股→anyq(多别名OR)；文章/快讯标签→topic；取最近 200 条（密集含历史）
  const _aliasKey = newsAliases.join(',');
  useEffect(() => {
    if (!useServerFeed) { setSearchMsgs([]); setSearchLoading(false); return; }
    setSearchLoading(true);
    let cancelled = false;
    const run = async () => {
      try {
        const params: RealtimeMessageFilters = { limit: feedFilter === '自选' ? 400 : 200 };
        if (newsManual) params.q = newsManual;
        else if (feedFilter === '自选') { if (watchlistAliasKey) params.anyq = watchlistAliasKey; }
        else if (_aliasKey) params.anyq = _aliasKey;
        if (feedFilter === '文章' || feedFilter === '快讯') params.topic = feedFilter;
        const r = await listRealtimeMessages(params);
        if (!cancelled) setSearchMsgs(r);
      } catch { if (!cancelled) setSearchMsgs([]); }
      finally { if (!cancelled) setSearchLoading(false); }
    };
    const t = window.setTimeout(run, newsManual ? 300 : 0);  // 手输去抖；选股/切标签即时
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [useServerFeed, newsManual, _aliasKey, feedFilter, watchlistAliasKey]);

  const feed = useMemo(() => {
    if (feedFilter === '自选') return watchlistFeed;   // 自选 tab：匹配自选股的快讯/文章，时间倒序
    // 服务端取数态用服务端结果；常态(ALL/快讯)用实时流已加载消息
    let base = (useServerFeed ? dedupeMessages(searchMsgs) : dedupedMessages)
      .filter(m => m.topic !== '信号' && m.source_type !== 'dao-signal' && m.topic !== '研报');
    if (feedFilter === 'all') {
      // 「全部」浏览态 = 实时流(快讯+文章) ∪ 全量文章（文章在窗口里稀疏，并入后口径一致）。
      // ⚠ 搜索/选股态绝不并入：searchMsgs 已是服务端按关键词过滤的结果，再混入未过滤的全量文章
      //    会让搜索结果里出现大量无关条目（如搜「红利」却列出最新所有文章）。
      if (!newsSearching) {
        base = dedupeMessages([...base, ...articles]).sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
      }
    } else {
      base = base.filter(m => m.topic === feedFilter);
    }
    return base;
  }, [dedupedMessages, searchMsgs, useServerFeed, feedFilter, watchlistFeed, articles, newsSearching]);

  // 资讯列表（ALL/快讯/文章 排除已置顶头条；自选 tab 与选股/搜索态显示全部命中）
  const newsRows = useMemo(
    () => (feedFilter === '自选' || active || newsQuery.trim()) ? feed : feed.filter(m => !pinnedIds.has(m.id)),
    [feed, feedFilter, active, newsQuery, pinnedIds]
  );
  // 翻页实际数据源：实时流(全部/快讯)在第 2 页起按锚点冻结(剔除比锚点更新的新到快讯),分页稳定不前移;
  // 第 1 页或服务端态(文章/自选/搜索,本就不实时插入)不冻结。created_at 为 ISO,字典序==时间序。
  const pagedRows = useMemo(
    () => (!useServerFeed && feedAnchor) ? newsRows.filter(m => (m.created_at || '') <= feedAnchor) : newsRows,
    [newsRows, useServerFeed, feedAnchor]
  );
  // 当前页（夹紧到有效范围）。头条/自选相关只在第 1 页置顶，翻页看历史时不再霸占顶部。
  const newsPageCur = useMemo(
    () => Math.min(newsPage, Math.max(1, Math.ceil(pagedRows.length / pageSize))),
    [newsPage, pagedRows.length, pageSize]
  );
  // 资讯翻页：翻到已加载末页且服务器可能还有更旧 → 先回源拉一批历史，再翻过去
  const goNewsPage = useCallback(async (p: number) => {
    // 离开第 1 页 → 冻结此刻最新时间为锚点(没有则用首行时间);回到第 1 页 → 解冻恢复实时
    if (p <= 1) setFeedAnchor(null);
    else setFeedAnchor(prev => prev || (newsRows[0]?.created_at || null));
    if (!useServerFeed && p * pageSize > newsRows.length && !histDone && !histLoading) {
      setHistLoading(true);
      try {
        const known = new Set(messages.map(m => m.id));
        const oldest = messages.length ? messages[messages.length - 1].created_at : undefined;
        const older = await listRealtimeMessages({ ...(oldest ? { before: oldest } : {}), limit: 200 });
        const fresh = older.filter(o => !known.has(o.id));
        if (fresh.length) mergeMessages(older);
        // <200 或回源没带来任何新行(全是重复) → 标记到底,避免「下一页」点了没反应卡死
        if (older.length < 200 || fresh.length === 0) setHistDone(true);
      } catch { /* 下轮再试 */ } finally { setHistLoading(false); }
    }
    setNewsPage(Math.max(1, p));
  }, [useServerFeed, pageSize, newsRows, histDone, histLoading, messages, mergeMessages]);

  // 切换标签/搜索/选股/改每页 时，资讯回到第 1 页(并解冻锚点)
  useEffect(() => { setNewsPage(1); setFeedAnchor(null); }, [feedFilter, newsManual, active, pageSize]);
  // 资讯搜索：去抖后记一条流水（≥2 字，避免逐键噪音）
  useEffect(() => {
    const q = newsQuery.trim();
    if (q.length < 2) return;
    const t = window.setTimeout(() => logAct('search', '资讯:' + q), 700);
    return () => window.clearTimeout(t);
  }, [newsQuery, logAct]);

  const reportFeed = useMemo(() => {
    if (resSearchKw) return reports;   // 在线全量检索（手输 或 选中个股）：直接用服务端历史结果
    // 默认视图：把 AI 评选的研报头条真正排到最顶部（其余保持原顺序）
    if (ybHeadKeys.size) {
      const top: ResearchWireItem[] = [], rest: ResearchWireItem[] = [];
      for (const r of reports) (((r.file_id && ybHeadKeys.has(r.file_id)) || ybHeadKeys.has(r.id)) ? top : rest).push(r);
      if (top.length) return [...top, ...rest];
    }
    return reports;
  }, [reports, resSearchKw, ybHeadKeys]);
  const isResearch = feedFilter === '研报';        // 研报标签：信息流面板切换为研报视图
  // 名人观点：仅白名单(lx199710)在资讯流加一个「名人观点」标签（研报旁），切到内联名人观点视图。
  const isCelebUser = IFIND_USERS.has((authUser || '').toLowerCase());
  const feedFilters = isCelebUser ? [...FEED_FILTERS, { key: '名人观点', label: '名人观点' }] : FEED_FILTERS;
  const isCelebrity = isCelebUser && feedFilter === '名人观点';  // 非白名单恒 false（防 localStorage 残留越权）

  // ⭐已取消「A股 / 港美股」分市场:研报标题中英混杂、源头无市场字段,classifyMarket 启发式误分多,
  // 与其分错不如不分——统一一个列表(用户决策)。resFiltered 即全量 reportFeed。
  const resFiltered = reportFeed;
  useEffect(() => { setResPage(1); }, [resSearchKw, feedFilter, pageSize]);
  // 研报日期手风琴:某天是否展开 = 用户显式展开 ∨ (它是最新一天 ∧ 没被用户收起)。点头部切换。
  const resDayOpen = useCallback((day: string, isLatest: boolean) =>
    resOpenDays.has(day) || (isLatest && !resClosedDays.has(day)), [resOpenDays, resClosedDays]);
  const toggleResDay = useCallback((day: string, isLatest: boolean) => {
    const open = resOpenDays.has(day) || (isLatest && !resClosedDays.has(day));
    setResOpenDays(prev => { const n = new Set(prev); if (open) n.delete(day); else n.add(day); return n; });
    setResClosedDays(prev => { const n = new Set(prev); if (open) n.add(day); else n.delete(day); return n; });
  }, [resOpenDays, resClosedDays]);

  // 研报「提及标的」标签：标题下方独占一行、可换行全部显示；超过 10 个折叠成「+N」
  const INST_MAX = 10;
  const instChips = (insts?: string[]) => (insts && insts.length > 0)
    ? <span className="bbt-rticks">
        {insts.slice(0, INST_MAX).map((t, i) => <span key={t + i} className="bbt-rtick">{t}</span>)}
        {insts.length > INST_MAX && <span className="bbt-rtick bbt-rtick--more">+{insts.length - INST_MAX}</span>}
      </span>
    : null;

  // 研报行（研报 tab 与 ALL 选股态共用）
  const renderResearchRow = (r: ResearchWireItem, isHead = false) => (
    <div key={r.id + r.filename} className={`bbt-rrow${isHead ? ' bbt-rrow--pin' : ''}`} onClick={() => runAiAnalysis(r)} title="点开 AI 解读">
      <span className="bbt-rdate">{isHead ? <><span className="bbt-pin-badge">★ 头条</span><span className="bbt-rdate-d">{(r.date || fmtReportDate(r.created_at) || '').slice(-5)}</span></> : (r.date || fmtReportDate(r.created_at))}</span>
      <span className="bbt-rmid"><span className="bbt-rtitle">{stripUrls(r.title) || r.title}</span>{instChips(r.instruments)}</span>
      <span className="bbt-nact">
        <button className={'bbt-nbm' + (bookmarks.has(bmId(r)) ? ' on' : '')} aria-label="收藏" aria-pressed={bookmarks.has(bmId(r))} title={bookmarks.has(bmId(r)) ? '取消收藏' : '收藏'} onClick={e => { e.stopPropagation(); toggleBookmark(r, '研报'); }}>{bookmarks.has(bmId(r)) ? '★' : '☆'}</button>
        <button className="bbt-nai" title="AI 解读" onClick={e => { e.stopPropagation(); runAiAnalysis(r); }}>AI 解读</button>
        {r.preview_url && canViewResearchOriginal && <button className="bbt-nsrc" title="查看研报原文 PDF（会员）" disabled={pdfLoadingUrl === r.preview_url} onClick={e => { e.stopPropagation(); openResearchOriginal(r.preview_url); }}>{pdfLoadingUrl === r.preview_url ? '加载中…' : '原文'}</button>}
      </span>
    </div>
  );

  // 头条行（快讯/文章/研报通用）
  const headlineRow = (kind: 'kx' | 'wz' | 'yb', m: any) => {
    const tag = kind === 'kx' ? '快讯' : kind === 'wz' ? '文章' : '研报';
    // 时间与非头条一致：快讯/文章=发布时刻 HH:MM:SS（前导列）；研报=日期 MM-DD
    const time = kind === 'yb'
      ? ((m.date || fmtReportDate(m.created_at) || '').length >= 10 ? (m.date || '').slice(5) : (m.date || fmtReportDate(m.created_at) || ''))
      : fmtTimeSmart(m.created_at);
    const onClick = kind === 'yb' ? () => runAiAnalysis(m) : kind === 'wz' ? () => runNewsAi(m) : () => copyNews(m);
    return (
      <div key={'hl-' + kind + (m.id || m.filename)} className={`bbt-nrow bbt-nrow--click bbt-nrow--pin pin-${kind}`} onClick={onClick} title={kind === 'kx' ? '点击复制' : '点开 AI 解读'}>
        <span className="bbt-pin-badge">★ 头条</span>
        <span className="bbt-ntime">{time}</span>
        <span className={`bbt-htag c-${kind}`}>{tag}</span>
        <div className="bbt-hmain">
          <div className="bbt-htitle">{stripUrls(m.title) || m.title}{kind === 'yb' ? instChips(m.instruments) : null}</div>
          {m.why && <div className="bbt-hwhy">💡 {m.why}</div>}
        </div>
        <span className="bbt-nact">
          {/* 头条均可收藏（用户拍板：头条也支持） */}
          <button className={'bbt-nbm' + (bookmarks.has(bmId(m)) ? ' on' : '')} aria-label="收藏" aria-pressed={bookmarks.has(bmId(m))} title={bookmarks.has(bmId(m)) ? '取消收藏' : '收藏'} onClick={e => { e.stopPropagation(); toggleBookmark(m, tag); }}>{bookmarks.has(bmId(m)) ? '★' : '☆'}</button>
          {kind === 'kx'
            ? <button className="bbt-nsrc" title="复制" onClick={e => { e.stopPropagation(); copyNews(m); }}>{copiedNewsId === m.id ? '✓' : '复制'}</button>
            : <>
                <button className="bbt-nai" title="AI 解读" onClick={e => { e.stopPropagation(); kind === 'yb' ? runAiAnalysis(m) : runNewsAi(m); }}>AI 解读</button>
                {kind === 'yb' && m.preview_url && canViewResearchOriginal && <button className="bbt-nsrc" title="查看研报原文 PDF（会员）" disabled={pdfLoadingUrl === m.preview_url} onClick={e => { e.stopPropagation(); openResearchOriginal(m.preview_url); }}>{pdfLoadingUrl === m.preview_url ? '加载中…' : '原文'}</button>}
                {kind === 'wz' && (articleOriginalUrl(m)
                  ? <button className="bbt-nsrc" title="查看原文" onClick={e => { e.stopPropagation(); openOriginal(m); }}>原文</button>
                  : (stripUrls(m.content) && stripUrls(m.content) !== (m.title || '').trim() ? <button className="bbt-nsrc" title="读全文" onClick={e => { e.stopPropagation(); requireMember(() => { logAct('open_news', m.title); setNewsPreview(m); }, '开通会员即可读全文原文'); }}>全文</button> : null))}
                {/* 头条文章也可分享（与普通文章行一致：公开落地页 /article/{id} 软墙引流）；研报不给分享(第三方版权) */}
                {kind === 'wz' && (
                  <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex' }}>
                    <ShareButton
                      className="bbt-nsrc"
                      modalTitle="分享文章"
                      tooltip="分享这篇文章"
                      simple
                      target={() => {
                        logAct('share_click', '文章分享·头条');
                        const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
                        const t = stripUrls(m.title) || m.title;
                        const body = stripUrls(m.content);
                        return {
                          kind: 'article',
                          title: t,
                          summary: body && body !== t ? (body.length > 80 ? body.slice(0, 80) + '…' : body) : '',
                          url: `${site}/article/${m.id}`,
                        };
                      }}
                    >分享</ShareButton>
                  </span>
                )}
              </>}
        </span>
      </div>
    );
  };

  // 头条区块：带「收起/展开」开关（默认展开）；收起后只留一条标题栏，省版面。
  const renderHeads = (rows: any[]) => {
    if (!rows.length) return null;
    return (
      <div className="bbt-heads">
        <div className="bbt-heads-h" onClick={() => setHeadsHidden(v => !v)} title={headsHidden ? '展开头条' : '收起头条'}>
          <button className="bbt-collapse-btn" aria-label="今日头条" aria-expanded={!headsHidden} title={headsHidden ? '展开头条' : '收起头条'} onClick={e => { e.stopPropagation(); setHeadsHidden(v => !v); }}>{headsHidden ? '▸' : '▾'}</button>
          🔥 今日头条 · {rows.length} 条{headsHidden ? '（已收起）' : ''}
        </div>
        {!headsHidden && rows}
      </div>
    );
  };

  // 查看原文：图片型原文(整篇截图) → 站内查看器(自动适配宽度、可读)；普通链接 → 新标签页打开
  const openOriginal = (m: RealtimeMessageRecord) => {
    requireMember(() => {
      logAct('open_news', m.title);
      const u = articleOriginalUrl(m);  // url 字段为空时兜底取正文里的链接(如飞书纪要)
      if (isImageUrl(u)) setNewsPreview(u === m.url ? m : ({ ...m, url: u } as RealtimeMessageRecord));
      else if (u) window.open(u, '_blank', 'noopener');
      else setNewsPreview(m);
    }, '开通会员即可读全文原文');
  };

  const renderNewsRow = (m: RealtimeMessageRecord, pinned = false, wlSyms?: string[]) => {
    const isFlash = (m.topic || '') === '快讯';
    const canBookmark = (m.topic || '') === '文章' || (m.topic || '') === '研报';  // 快讯不收藏（用户拍板）
    const isBm = bookmarks.has(bmId(m));
    return (
      <div key={m.id} className={`bbt-nrow bbt-nrow--click sev-${m.severity}${pinned ? ' bbt-nrow--pin' : ''}${wlSyms && wlSyms.length ? ' bbt-nrow--wl' : ''}`}
        onClick={() => isFlash ? copyNews(m) : runNewsAi(m)}
        title={isFlash ? '点击复制' : '点开 AI 解读'}>
        {pinned && <span className="bbt-pin-badge">★ 头条</span>}
        {wlSyms && wlSyms.length > 0 && <span className="bbt-wl-badge" title={`点击查看 ${nameOf(wlSyms[0])}`} onClick={e => { e.stopPropagation(); selectStock(wlSyms[0]); }}>★ 自选 {wlSyms.slice(0, 2).map(s => nameOf(s)).join('·')}{wlSyms.length > 2 ? ` +${wlSyms.length - 2}` : ''}</span>}
        <span className="bbt-ntime">{fmtTimeSmart(m.created_at)}</span>
        {/* 中性「资讯」标签无信息量、纯噪音 → 不显示；只在利好/利空/紧急时才标，凸显真正的信号。
            AI 判定（metadata.ai_sentiment）优先于关键词；悬浮显示 AI 的多空细分（利好xxx，利空xxx） */}
        {(m.severity === 'critical' || m.severity === 'warning' || m.severity === 'success') && (
          <span
            className={`bbt-ntag tag-${m.severity}`}
            title={String(m.metadata?.ai_impact || '') || undefined}
          >{SEV_TAG[m.severity]}</span>
        )}
        <span className="bbt-ntopic">{`{${m.topic || '资讯'}}`}</span>
        {(() => { const t = stripUrls(m.title) || m.title; const body = stripUrls(m.content); return <span className="bbt-ntext">{t}{body && body !== t ? `　${body.slice(0, 100)}` : ''}</span>; })()}
        <span className="bbt-nact">
          {canBookmark && <button className={'bbt-nbm' + (isBm ? ' on' : '')} aria-label="收藏" aria-pressed={isBm} title={isBm ? '取消收藏' : '收藏'} onClick={e => { e.stopPropagation(); toggleBookmark(m); }}>{isBm ? '★' : '☆'}</button>}
          {isFlash ? (
            <>
              <button className="bbt-nsrc" title="复制" onClick={e => { e.stopPropagation(); copyNews(m); }}>{copiedNewsId === m.id ? '✓ 已复制' : '复制'}</button>
              {/* 金十式快讯图卡：微信群里流通的是截图，卡片自带二维码回流入口 */}
              <button className="bbt-nsrc" title="生成快讯图卡（带二维码，适合发微信群）" onClick={e => { e.stopPropagation(); void saveNewsImage(m); }}>存图</button>
            </>
          ) : (
            <>
              <button className="bbt-nai" title="AI 解读" onClick={e => { e.stopPropagation(); runNewsAi(m); }}>AI 解读</button>
              {/* 研报：原文按钮（会员专享 PDF）。文章：有外链→"原文"新标签；无外链→"全文"站内阅读器(文章始终显示，内容空时至少能看标题)。 */}
              {m.topic === '研报'
                ? null
                : (articleOriginalUrl(m)
                  ? <button className="bbt-nsrc" title="查看原文" onClick={e => { e.stopPropagation(); openOriginal(m); }}>原文</button>
                  : (m.topic === '文章'
                    ? <button className="bbt-nsrc" title="读全文" onClick={e => { e.stopPropagation(); requireMember(() => { logAct('open_news', m.title); setNewsPreview(m); }, '开通会员即可读全文原文'); }}>全文</button>
                    : null))}
              {/* 文章分享：链接指向公开落地页 /article/{id}（软墙，登录看全文）。span 兜住冒泡，不触发整行的 AI 解读 */}
              {m.topic === '文章' && (
                <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex' }}>
                  <ShareButton
                    className="bbt-nsrc"
                    modalTitle="分享文章"
                    tooltip="分享这篇文章"
                    simple
                    target={() => {
                      logAct('share_click', '文章分享');
                      const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
                      const t = stripUrls(m.title) || m.title;
                      const body = stripUrls(m.content);
                      // 正文与标题不同才作摘要(钩子,超 80 字带省略号),避免标题重复;不带来源 byline(品牌归属由文案脚注承担,且不外露内部聚合源名)
                      return {
                        kind: 'article',
                        title: t,
                        summary: body && body !== t ? (body.length > 80 ? body.slice(0, 80) + '…' : body) : '',
                        url: `${site}/article/${m.id}`,
                      };
                    }}
                  >分享</ShareButton>
                </span>
              )}
            </>
          )}
        </span>
      </div>
    );
  };

  // 行情排序后的显示顺序
  const displayList = useMemo(() => {
    if (!sortKey) return watchlist;
    const val = (s: string) => {
      const q = quotes[s];
      if (!q) return -Infinity;
      if (sortKey === 'pct') return Number(q.change_percent || 0);
      if (sortKey === 'price') return Number(q.price || 0);
      return Number(q.volume || 0);
    };
    return [...watchlist].sort((a, b) => (val(a) - val(b)) * sortDir);
  }, [watchlist, quotes, sortKey, sortDir]);
  useEffect(() => { navRef.current = displayList; }, [displayList]);
  // 自选按市场分组(A股/港股/美股)——更清晰、专业；组内沿用当前排序/顺序
  const groupedWatch = useMemo(() => {
    const mkt = (s: string): 'A' | 'HK' | 'US' => /^\d{6}$/.test(s) ? 'A' : /^\d{3,5}$/.test(s) ? 'HK' : 'US';
    const g: Record<string, string[]> = { A: [], HK: [], US: [] };
    displayList.forEach(s => { g[mkt(s)].push(s); });
    const LABEL: Record<string, string> = { A: 'A股', HK: '港股', US: '美股' };
    return (['A', 'HK', 'US'] as const).map(k => ({ key: k, label: LABEL[k], syms: g[k] })).filter(x => x.syms.length);
  }, [displayList]);
  // 单行渲染（分组复用）：无行情=灰行，有行情=完整行
  const renderQRow = (sym: string) => {
    const q = quotes[sym];
    const isActive = active === sym;
    const xBtn = (
      <button className="bbt-qrow-x" aria-label={`移除自选 ${sym}`} title="从自选删除"
        onClick={e => { e.stopPropagation(); requireLogin(() => { if (window.confirm(`确定从「我的自选」删除 ${nameOf(sym)}（${sym}）？`)) removeSymbol(sym); }, '管理自选股票'); }}>✕</button>
    );
    if (!q) return (
      <div key={sym} className={`bbt-qrow bbt-dim ${isActive ? 'active' : ''}`} onClick={() => selectStock(sym)}>
        <span className="c-name"><span className="c-name-t">{sym} {nameOf(sym)}</span>{xBtn}</span>
        <span className="c-num">—</span><span className="c-num eq-extra">—</span><span className="c-num">—</span><span className="c-range eq-extra">—</span><span className="c-num eq-extra">—</span>
      </div>
    );
    const pc = Number(q.change_percent || 0); const dir = pc > 0 ? 'up' : pc < 0 ? 'down' : 'flat';
    const heat = Math.min(0.42, Math.abs(pc) / 8);
    const lo = Number(q.low || 0), hi = Number(q.high || 0);
    const pos = hi > lo ? Math.max(0, Math.min(1, (q.price - lo) / (hi - lo))) : 0.5;
    // iFinD 实时基本面（仅白名单账号的 A股行带 pe_ttm；其他行/用户恒无此字段 → 不渲染，零打扰）
    const fundCap = (v?: number | null) => typeof v === 'number' && v > 0
      ? (v >= 1e12 ? (v / 1e12).toFixed(2) + '万亿' : v >= 1e8 ? (v / 1e8).toFixed(0) + '亿' : (v / 1e4).toFixed(0) + '万') : '—';
    const hasFund = q.pe_ttm != null || q.pb != null || q.total_capital != null;
    return (
      <React.Fragment key={sym}>
        <div className={`bbt-qrow ${flash[sym] ? `flash-${flash[sym]}` : ''} ${isActive ? 'active' : ''}`} onClick={() => selectStock(sym)}>
          <span className="c-name"><span className="c-name-t"><b>{q.symbol}</b> {nameOf(sym)}</span>{xBtn}</span>
          <span className={`c-num bbt-${dir}`}>{q.price}</span>
          <span className={`c-num bbt-${dir} eq-extra`}>{pc > 0 ? '+' : ''}{Number(q.change || 0).toFixed(2)}</span>
          <span className={`c-num bbt-${dir}`} style={{ background: pc > 0 ? `rgba(43,217,106,${heat})` : pc < 0 ? `rgba(255,69,58,${heat})` : 'transparent' }}>{pc > 0 ? '▲' : pc < 0 ? '▼' : ''}{pc > 0 ? '+' : ''}{pc.toFixed(2)}</span>
          <span className="c-range eq-extra" title={`低 ${lo} · 高 ${hi}`}>
            <span className="bbt-range"><span className={`bbt-range-mark bbt-${dir}-bg`} style={{ left: `${pos * 100}%` }} /></span>
          </span>
          <span className="c-num bbt-mute eq-extra">{fmtVol(q.volume)}</span>
        </div>
        {hasFund && (
          <div className="bbt-qrow-fund" title="同花顺 iFinD 实时基本面">
            {q.pe_ttm != null && <span>PE <b>{q.pe_ttm.toFixed(1)}</b></span>}
            {q.pb != null && <span>PB <b>{q.pb.toFixed(2)}</b></span>}
            {q.total_capital != null && <span>市值 <b>{fundCap(q.total_capital)}</b></span>}
            {q.turnover_ratio != null && <span>换手 <b>{q.turnover_ratio.toFixed(2)}%</b></span>}
            <span className="bbt-qrow-fund-src">iFinD</span>
          </div>
        )}
      </React.Fragment>
    );
  };
  const toggleSort = (k: Exclude<SortKey, null>) => {
    if (sortKey === k) { if (sortDir === -1) setSortDir(1); else { setSortKey(null); } }
    else { setSortKey(k); setSortDir(-1); }
  };
  const sortMark = (k: Exclude<SortKey, null>) => (sortKey === k ? (sortDir === -1 ? ' ▼' : ' ▲') : '');

  const qList = watchlist.map(s => quotes[s]).filter(Boolean) as Quote[];
  const breadthUp = qList.filter(q => Number(q.change_percent || 0) > 0).length;
  const breadthDown = qList.filter(q => Number(q.change_percent || 0) < 0).length;
  const activeName = active ? nameOf(active) : null;

  const paletteCommands = [
    { id: 'news', label: 'NEWS · 全部资讯', run: () => { setFeedFilter('all'); setActive(null); } },
    { id: 'kx', label: '快讯 · 只看快讯', run: () => setFeedFilter('快讯') },
    { id: 'wz', label: '文章 · 只看文章', run: () => setFeedFilter('文章') },
    { id: 'clr', label: 'CLR · 清除标的/筛选', run: () => { setActive(null); setFeedFilter('all'); } },
  ];
  const pqLow = pq.trim().toLowerCase();
  // 空查询时不堆自选/命令(否则"搜索添加"一打开先列一堆已有股，像在跳转而非添加)；输入后才出匹配
  const paletteSyms = !pqLow ? [] : watchlist
    .map(s => ({ sym: s, label: `${s}　${nameOf(s)}`, q: `${s} ${nameOf(s)} ${keysOf(s).join(' ')}`.toLowerCase(), quote: quotes[s] }))
    .filter(it => it.q.includes(pqLow));
  const paletteCmds = !pqLow ? [] : paletteCommands.filter(c => c.label.toLowerCase().includes(pqLow));
  const runItem = (run: () => void) => { run(); setPaletteOpen(false); setPq(''); };
  // 统一成一条有序列表，供 ↑↓ 导航 + 回车选中（自选 → 搜索结果 → 命令）
  type PItem =
    | { key: string; type: 'go'; sym: string; label: string; quote?: Quote; run: () => void }
    | { key: string; type: 'add'; code: string; name: string; exch: string; run: () => void }
    | { key: string; type: 'uni'; label: string; run: () => void }
    | { key: string; type: 'cmd'; label: string; run: () => void };
  const paletteItems: PItem[] = [
    ...paletteSyms.map((it): PItem => ({ key: 's' + it.sym, type: 'go', sym: it.sym, label: it.label, quote: it.quote, run: () => selectStock(it.sym) })),
    // 「查看」与「收藏」分离：点结果=匿名可下钻看行情K线（查股是散户第一动作，价值未展示先要账号=摩擦前置）；
    // 登录墙留给行尾「＋添加」这类真正需要身份的时刻（注册意愿反而更强）。
    ...remoteHits.map((it): PItem => ({ key: 'r' + it.code, type: 'add', code: it.code, name: it.name, exch: it.exchange || it.market || '', run: () => { setNames(prev => ({ ...prev, [it.code]: it.name })); selectStock(it.code); } })),
    // 统一搜索补充组：板块/快讯/研报/名词学堂（已建资产接进搜索这个第一动作）
    ...uniHits.boards.slice(0, 3).map((b: any, i: number): PItem => ({
      key: 'ub' + i, type: 'uni', label: `📊 ${b.name || b.board_name || ''} 板块 · 问 AI 受益股`,
      run: () => { const q = `${b.name || b.board_name} 板块今天表现怎么样？有哪些受益股？`; openAi(); setAiInput(q); void askAi(q); },
    })),
    ...uniHits.news.slice(0, 4).map((n: any, i: number): PItem => ({
      key: 'un' + i, type: 'uni', label: `📰 ${String(n.title || '').slice(0, 40)}`,
      run: () => setNewsPreview(n as RealtimeMessageRecord),
    })),
    ...uniHits.reports.slice(0, 3).map((r: any, i: number): PItem => ({
      key: 'ur' + i, type: 'uni', label: `📑 ${String(r.title || '').slice(0, 40)}`,
      run: () => { setFeedFilter('研报'); setResQuery(pq.trim()); },
    })),
    ...uniHits.terms.slice(0, 2).map((t: any, i: number): PItem => ({
      key: 'ut' + i, type: 'uni', label: `📖 ${t.term || t.slug}：什么意思？`,
      run: () => { try { window.open(`/learn/${t.slug}`, '_blank', 'noopener'); } catch { /* */ } },
    })),
    ...paletteCmds.map((c): PItem => ({ key: 'c' + c.id, type: 'cmd', label: c.label, run: c.run })),
  ];
  const paletteActiveClamped = Math.max(0, Math.min(paletteActive, paletteItems.length - 1));

  return (
    <div className="bbt">
      {pdfLoadingUrl && (
        <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.45)',zIndex:9999,display:'flex',alignItems:'center',justifyContent:'center'}}>
          <div style={{background:'#141e30',border:'1px solid #263348',borderRadius:12,padding:'28px 40px',textAlign:'center',boxShadow:'0 8px 32px rgba(0,0,0,.5)'}}>
            <div style={{fontSize:28,marginBottom:10}}>📄</div>
            <div style={{color:'#e2e8f0',fontSize:15,fontWeight:600}}>研报处理中…</div>
            <div style={{color:'#64748b',fontSize:12,marginTop:6}}>首次加载约 3–8 秒，请稍候</div>
            <div style={{marginTop:14,height:3,borderRadius:2,background:'#263348',overflow:'hidden'}}>
              <div style={{height:'100%',width:'40%',background:'linear-gradient(90deg,#3b82f6,#60a5fa)',borderRadius:2,animation:'bbt-pdf-bar 1.2s ease-in-out infinite alternate'}}/>
            </div>
          </div>
        </div>
      )}
      <div className="bbt-cmd">
        <span className="bbt-cmd-key">DEEPFOCUS</span>
        <span className="bbt-cmd-amber">金融终端</span>
        {/* 价值主张常驻品牌区：新用户 10 秒内知道"这是干嘛的"（窄屏由 CSS 隐藏） */}
        <span className="bbt-cmd-tagline" title="A股快讯 + AI 解读，比券商 App 早一步">快讯早一步 · AI 帮研判</span>
        <span className="bbt-cmd-input" onClick={() => { setPaletteOpen(true); setPq(''); }} title="搜索股票 / 命令（点击，或按 /）"><span className="bbt-cmd-mag">🔍</span>{active ? <b>{active} {activeName}</b> : <span className="bbt-cmd-ph">搜索股票 / 命令</span>}<span className="bbt-cmd-kbd">/</span></span>
        <span className="bbt-cmd-right">
          {/* 今日早报按钮已按需求隐藏 */}
          <button className="bbt-theme-btn" onClick={() => { logAct('theme', theme === 'dark' ? 'light' : 'dark'); toggleTheme(); }} aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'} title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}>
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
          {ttsSupported && (
            <button className={'bbt-tts-btn' + (ttsOn ? ' on' : '')} onClick={toggleTts}
                    aria-label={ttsOn ? '快讯语音播报：开，点击关闭' : '快讯语音播报：关，点击开启'} aria-pressed={ttsOn}
                    title={ttsOn ? '快讯语音播报：开（点击关闭）' : '快讯语音播报：关（点击开启）'}>
              {ttsOn ? '🔊' : '🔈'}
            </button>
          )}
          <button className="bbt-review-entry" onClick={() => openReview()} title="A股每日收盘复盘 · DeepFocus 提前发现">📊 复盘</button>
          {/* 连续签到 streak 常驻可见：损失厌恶是最强次日回访钩子（引擎/奖励早已建成，此前只藏在复盘弹层标题栏） */}
          {authUser && checkin && checkin.streak > 0 && (
            <button className="bbt-review-entry" onClick={() => openReview()}
              title={checkin.checked_today
                ? `已连续看盘 ${checkin.streak} 天${checkin.days_to_next ? ` · 再坚持 ${checkin.days_to_next} 天送 ${checkin.next_reward_days ?? ''} 天会员` : ''}`
                : `今天还没看复盘——连续 ${checkin.streak} 天的记录今晚就断了！`}>
              {checkin.checked_today ? `🔥${checkin.streak}天` : `⏳${checkin.streak}天`}
            </button>
          )}
          <button className="bbt-review-entry bbt-aifund-entry" onClick={() => { logAct('open_aifund', 'AI模拟盘'); window.location.href = '/ai-fund'; }} title="AI 模拟盘：阿尔法直播操盘（K线/五维打分/操盘解说）· 点击进入独立页">🤖 AI 模拟盘</button>
          {groupCfg?.enabled !== false && (
            <span className="bbt-grp-wrap">
              <button className={'bbt-review-entry bbt-group-entry' + (groupSeen ? '' : ' pulse')} onClick={openGroup} aria-label="加入用户交流群" title="DeepFocus 用户交流群 · 免费开放给所有人，聊行情/追快讯/唠复盘">💬 交流群{!groupSeen && <span className="bbt-group-hot">免费</span>}</button>
              {!groupSeen && !groupHintSess && !groupOpen && (
                <span className="bbt-grp-hint" role="note">
                  <span className="bbt-grp-hint-txt">👋 免费进官方交流群 · 聊行情 / 抢第一手快讯</span>
                  <button className="bbt-grp-hint-go" onClick={openGroup}>立即进群</button>
                  <button className="bbt-grp-hint-x" onClick={dismissGroupHint} aria-label="关闭">✕</button>
                </span>
              )}
            </span>
          )}
          {IFIND_USERS.has((authUser || '').toLowerCase()) && (
            <button className="bbt-review-entry bbt-ifind-entry" onClick={openIfind} title="同花顺 iFinD A股实时行情+基本面（专业数据）">📡 iFinD</button>
          )}
          {/* hidden→locked：旗舰入口对匿名访客也可见，点击才要登录——登录墙挂在"我想问 AI"这个高意向动作上，
              比挂在被动浏览上转化率高得多（pendingActionRef 登录后自动续做） */}
          <button className="bbt-review-entry bbt-aiqa-entry"
            onClick={() => (canAskAi ? openAi() : requireLogin(openAi, 'AI 投研问答'))}
            title="AI 投研问答：自动调行情/估值 + 检索我们的快讯/研报/复盘">🤖 AI 问答</button>
          {membership?.tier !== 'lifetime' && (() => {
            const isMember = membership?.tier === 'premium';   // 已是尊享会员 → 显示「续费」（可能提前续期），永久会员不显示
            const promoActive = Date.now() < FOUNDING_PROMO_END;  // 限时福利进行中
            // 角标优先级：新人(注册前3天)专享 > 限时福利期 > 默认
            const hot = isNewUser ? '🎁新人福利' : promoActive ? '⏳限时福利' : (isMember ? '提前续' : '解锁全部');
            return (
              <button className={'bbt-buy-cta' + ((isMember && !isNewUser && !promoActive) ? '' : ' attract')}
                      onClick={() => { logAct('open_buy', isMember ? '顶部续费CTA' : '顶部开通会员CTA'); requireLogin(openBuy, isMember ? '续费会员' : '开通会员'); }}
                      aria-label={isMember ? '续费会员' : '开通尊享会员'}
                      title={isNewUser ? '新人限时福利：低至4折 + 年卡加赠1个月/半年卡加赠15天' : (isMember ? '限时福利期·提前续费更划算' : '开通尊享会员 · 限时低至4折 · 解锁 AI 无限解读/微信快讯推送/文章全文')}>
                💎 {isMember ? '续费会员' : '开通会员'}<span className="bbt-buy-cta-hot">{hot}</span>
              </button>
            );
          })()}
          <button className={'bbt-ref-entry' + (refOpened ? '' : ' attract')} onClick={() => { logAct('invite_click', '邀请得会员'); requireLogin(openReferral, '邀请得会员'); }} aria-label="邀请好友得会员" title="邀请好友 · 累计解锁会员卡，最高免费拿 ¥698 年卡">🎁 邀请得会员<span className="bbt-ref-entry-hot">免费拿年卡</span>{refAvail > 0 && <span className="bbt-ref-entry-badge">{refAvail}</span>}</button>
          <button className="bbt-help-btn bbt-manual-btn" onClick={() => setShowHelp(true)} aria-label="产品说明书" title="产品说明书">📖</button>
          <button className="bbt-help-btn" onClick={() => setShowOnb(true)} aria-label="新手引导" title="新手引导">❔</button>
          {authUser
            ? (() => {
                const isLifetime = membership?.tier === 'lifetime';
                const isVip = membership?.tier === 'premium' || isLifetime;
                return (
                  <span className="bbt-acctwrap">
                    <button className={'bbt-acct-trigger' + (isVip ? ' vip' : '')} title="账号 · 会员 · 设置中心"
                            onClick={() => { const n = !acctOpen; setAcctOpen(n); if (n) { refreshMembership(); dismissAcctHint(); } }}>
                      <span className={'bbt-avatar' + (isVip ? ' vip' : '')} aria-hidden="true">
                        {authUser.slice(0, 1).toUpperCase()}
                        {isVip && <span className="bbt-avatar-crown">👑</span>}
                      </span>
                      <span className="bbt-acct-trigger-txt">
                        <span className="bbt-acct-trigger-name">{authUser}</span>
                        <span className="bbt-acct-trigger-sub">{isLifetime ? '永久会员 · 设置' : isVip ? '尊享会员 · 设置' : '账号 · 设置中心'}</span>
                      </span>
                      {supportUnread > 0 && <span className="bbt-avatar-dot" title="管理员有新回复" />}
                      <span className="bbt-acct-trigger-caret" aria-hidden="true">▾</span>
                    </button>
                    {showAcctHint && !acctOpen && (
                      <div className="bbt-acct-hint" role="note">
                        <span className="bbt-acct-hint-txt">👆 会员 · 绑定微信 · 邀请好友 · 收藏 · 设置 都在这里</span>
                        <button className="bbt-acct-hint-ok" onClick={dismissAcctHint}>知道了</button>
                      </div>
                    )}
                    {acctOpen && (
                      <>
                        <div className="bbt-acct-mask" onClick={() => setAcctOpen(false)} />
                        <div className="bbt-acct-pop" onClick={e => e.stopPropagation()}>
                          <div className="bbt-acct-pop-head">
                            <span className={'bbt-avatar lg' + (isVip ? ' vip' : '')}>{authUser.slice(0, 1).toUpperCase()}</span>
                            <div className="bbt-acct-pop-id"><b>{authUser}</b>{isAdmin && <span className="bbt-acct-pop-role">🛡 管理员</span>}</div>
                          </div>
                          <div className={'bbt-mcard' + (isVip ? ' vip' : '')}>
                            {isLifetime
                              ? <><div className="bbt-mcard-t"><span className="bbt-mcard-ico">👑</span>永久会员</div>
                                  <div className="bbt-mcard-sub">永久有效 · 尊享全部权益 ✨</div></>
                              : isVip
                              ? <><div className="bbt-mcard-t"><span className="bbt-mcard-ico">👑</span>尊享会员</div>
                                  <div className="bbt-mcard-sub">剩余 <b>{memDaysLeft ?? '—'}</b> 天{membership?.expires_at ? ` · ${membership.expires_at.slice(0, 10)} 到期` : ''}</div></>
                              : <><div className="bbt-mcard-t"><span className="bbt-mcard-ico">🎟️</span>体验期</div>
                                  <div className="bbt-mcard-sub">体验全部功能 · 升级尊享会员解锁更多权益</div></>}
                          </div>
                          <button className="bbt-acct-row hl" onClick={openBuy}>💎 开通 / 续费会员</button>
                          <button className="bbt-acct-row" onClick={() => { setAcctOpen(false); setRedeemInput(''); setRedeemOpen(true); }}>🎟️ 兑换会员码</button>
                          <button className="bbt-acct-row" onClick={openBookmarks}>⭐ 我的收藏</button>
                          {/* 微信是唯一能天天触达免费用户的自有渠道——绑定入口对全体登录用户开放(非会员绑后有每日晨报+试吃问答，聊天窗里天然升级) */}
                          <button className="bbt-acct-row" onClick={() => { setAcctOpen(false); logAct('weixin_bind'); setShowWeixinBind(true); }}>🟢 绑定微信 · 收快讯{isVip || isAdmin ? '' : '（免费试用）'}</button>
                          <button className="bbt-acct-row" onClick={() => { setAcctOpen(false); openInvite(); }}>🎁 我的邀请</button>
                          {authUser === 'lx199710' && <button className="bbt-acct-row" onClick={openDashboard}>📊 运营看板</button>}
                          <button className="bbt-acct-row" onClick={openSupport}>💬 联系管理员{supportUnread > 0 && <span className="bbt-acct-badge">{supportUnread}</span>}</button>
                          <button className="bbt-acct-row danger" onClick={onLogout}>↩ 退出登录</button>
                        </div>
                      </>
                    )}
                  </span>
                );
              })()
            : <button className="bbt-acct-in" title="登录解锁 AI 解读" onClick={() => { setAuthReason('AI 解读'); setAuthOpen(true); }}>登录</button>}
        </span>
      </div>

      {/* 管理员专属：有用户未读私信 → 主页醒目提醒，点击打开运营看板处理 */}
      {isAdmin && adminUnread > 0 && (
        <div className="bbt-support-banner bbt-support-banner--admin" onClick={openDashboard} role="button" title="打开运营看板回复用户">
          <span className="bbt-support-banner-ico">📨</span>
          <span className="bbt-support-banner-text">有 <b>{adminUnread}</b> 条用户私信待回复 · 点击打开看板处理</span>
          <button className="bbt-support-banner-btn" onClick={e => { e.stopPropagation(); openDashboard(); }}>去处理</button>
        </div>
      )}
      {/* 管理员回复未读：最高优先级横幅（用户直接相关），点击直达私信 */}
      {authUser && supportUnread > 0 && (
        <div className="bbt-support-banner" onClick={openSupport} role="button" title="查看管理员回复">
          <span className="bbt-support-banner-ico">💬</span>
          <span className="bbt-support-banner-text">管理员回复了你（<b>{supportUnread}</b> 条未读）· 点击查看</span>
          <button className="bbt-support-banner-btn" onClick={e => { e.stopPropagation(); openSupport(); }}>查看回复</button>
        </div>
      )}
      {/* ⭐单一 promo 槽：一次只显一条（匿名=战绩证据优先、无数据回退价值主张+领会员；登录=仅领会员待领）——
          此前最多 5 层转化组件同屏打架，反而稀释了唯一有说服力的「提前发现」证据 */}
      {!authUser && (trackRecord && trackRecord.hit_count > 0 ? (
        <div className="bbt-tr-hero" onClick={() => { logAct('tr_hero_cta', '首屏战绩'); openReview(); }} role="button" title="看今天的 A 股复盘 · DeepFocus 提前发现的资讯">
          <span className="bbt-tr-hero-ico">📡</span>
          <span className="bbt-tr-hero-text">
            近 {trackRecord.days} 天，DeepFocus 用快讯/研报<b> 提前覆盖 {trackRecord.hit_count} 次</b>异动 · 平均提前 <b>{trackRecord.avg_lead_hours}h</b> — 关键消息比你的券商 App 早一步
          </span>
          <button className="bbt-tr-hero-btn" onClick={e => { e.stopPropagation(); logAct('tr_hero_cta', '首屏战绩按钮'); openReview(); }}>看今天的复盘 →</button>
        </div>
      ) : (
        <div className="bbt-trial-banner" onClick={onClaimTrial} role="button" title="登录即享体验会员，邀好友得 ¥698 年度会员">
          <span className="bbt-trial-gift">📡</span>
          <span className="bbt-trial-text">比券商 App 早一步的 A股快讯 + AI 解读终端 · 登录领<b>体验会员</b></span>
          <button className="bbt-trial-btn" disabled={trialClaiming} onClick={e => { e.stopPropagation(); onClaimTrial(); }}>
            {trialClaiming ? '处理中…' : '登录'}
          </button>
        </div>
      ))}
      {authUser && trialClaimable && (
        <div className="bbt-trial-banner" onClick={onClaimTrial} role="button" title="领取体验会员，邀好友得 ¥698 年度会员">
          <span className="bbt-trial-gift">👑</span>
          <span className="bbt-trial-text"><b>3 天会员</b>待领 · 邀友得 <b>¥698 年卡</b></span>
          <button className="bbt-trial-btn" disabled={trialClaiming} onClick={e => { e.stopPropagation(); onClaimTrial(); }}>
            {trialClaiming ? '处理中…' : '领取'}
          </button>
        </div>
      )}
      {/* 到期转化：尊享会员剩 ≤2 天 → 醒目续费条（最高意向时刻），可本次关闭 */}
      {authUser && membership?.tier === 'premium' && typeof memDaysLeft === 'number' && memDaysLeft <= 2 && !expiryDismissed && (
        <div className="bbt-expiry-banner" onClick={openBuy} role="button" title="续费会员，不中断 AI 解读 / 复盘 / 微信推送">
          <span className="bbt-expiry-ico">⏰</span>
          <span className="bbt-expiry-text">
            你的尊享会员{memDaysLeft <= 0 ? <> <b>今天到期</b></> : memDaysLeft === 1 ? <> <b>明天到期</b></> : <> 仅剩 <b>{memDaysLeft} 天</b></>}
            {' · '}续费立享 AI 解读 / 复盘 / 原文不中断
          </span>
          <button className="bbt-expiry-btn" onClick={e => { e.stopPropagation(); openBuy(); }}>立即续费</button>
          <button className="bbt-expiry-x" onClick={e => { e.stopPropagation(); setExpiryDismissed(true); }} title="本次不再提示">✕</button>
        </div>
      )}

      <div className={'bbt-macro' + (macroOpen ? '' : ' closed')}>
        <span className="bbt-macro-tag" role="button" onClick={() => setMacroOpen(v => !v)} title={macroOpen ? '收起宏观指标' : '展开宏观指标'}>MACRO{macroOpen ? '' : ' ▸'}</span>
        {macroOpen && [{ k: 'vix', l: 'VIX' }, { k: 'ten_year', l: 'US10Y' }, { k: 'dxy', l: 'DXY' }, { k: 'gold', l: '黄金' }, { k: 'oil', l: 'WTI' }, { k: 'bitcoin', l: 'BTC' }, { k: 'spx', l: '标普' }].map(m => {
          const ind = macro[m.k];
          if (!ind || !ind.value) return null;
          const pc = Number(ind.change_pct || 0);
          const dir = pc > 0 ? 'up' : pc < 0 ? 'down' : 'flat';
          const sig = ind.signal === 'bullish' ? 'up' : ind.signal === 'bearish' ? 'down' : 'flat';
          return (
            <span key={m.k} className="bbt-mac" title={ind.status || ind.name}>
              <span className={`bbt-mac-dot bbt-${sig}-bg`} />
              <b>{m.l}</b>
              <span className="bbt-mac-v">{ind.value}{ind.unit === '%' ? '%' : ''}</span>
              {ind.change_pct != null && <span className={`bbt-${dir}`}>{pc > 0 ? '+' : ''}{pc.toFixed(2)}%</span>}
            </span>
          );
        })}
        {Object.keys(macro).length === 0 && <span className="bbt-dim">{macroFailed ? '宏观数据暂不可用，稍后自动重试' : '宏观加载中…'}</span>}
      </div>

      {active && quotes[active] && (() => {
        const aq = quotes[active];
        const pc = Number(aq.change_percent || 0);
        const dir = pc > 0 ? 'up' : pc < 0 ? 'down' : 'flat';
        const mkt = /^\d{6}$/.test(active) ? 'A股' : /^\d{5}$/.test(active) ? '港股' : '美股';
        const lo = Number(aq.low || 0), hi = Number(aq.high || 0);
        const pos = hi > lo ? Math.max(0, Math.min(1, (aq.price - lo) / (hi - lo))) : 0.5;
        return (
          <div className="bbt-detail">
            <span className="bbt-detail-sym"><b>{active}</b> {nameOf(active)} <span className="bbt-detail-mkt">{mkt} · {aq.currency}</span></span>
            <span className={`bbt-detail-px bbt-${dir}`}>{aq.price}　<span>{pc > 0 ? '+' : ''}{Number(aq.change || 0).toFixed(2)} ({pc > 0 ? '+' : ''}{pc.toFixed(2)}%)</span></span>
            <span className="bbt-detail-f">开 {aq.open_price ?? '-'}</span>
            <span className="bbt-detail-f">高 {aq.high ?? '-'}</span>
            <span className="bbt-detail-f">低 {aq.low ?? '-'}</span>
            <span className="bbt-detail-f">昨 {aq.previous_close ?? '-'}</span>
            <span className="bbt-detail-f">量 {fmtVol(aq.volume)}</span>
            <span className="bbt-detail-range" title={`低 ${lo} · 高 ${hi}`}><span className="bbt-range"><span className={`bbt-range-mark bbt-${dir}-bg`} style={{ left: `${pos * 100}%` }} /></span></span>
            {/* 行情新鲜度：源挂掉价格静默冻结是信任毁灭级破绽——真拿到价才刷新时间戳，超 90s 显式提示 */}
            {(() => {
              const at = quoteAtRef.current[active];
              const ageS = at ? Math.round((Date.now() - at) / 1000) : null;
              if (ageS !== null && ageS > 90) return <span className="bbt-detail-f" style={{ color: '#f59e0b' }} title="行情源可能延迟，价格为最近一次成功获取的数据">⚠ 更新于 {ageS < 3600 ? `${Math.round(ageS / 60)} 分钟前` : '较早前'}</span>;
              return null;
            })()}
            {/* 查股→结论一步直达：AI 解读是产品的"哇时刻"，此前离主路径隔 3 步+重复输入 */}
            <button className="bbt-nai" title="让 AI 综合行情/估值/资金/研报速判这只股"
              onClick={() => { openAi(); const q = `${nameOf(active) || active}(${active}) 现在怎么样？`; setAiInput(q); void askAi(q); }}>⚡ AI 速判</button>
            <button className="bbt-clear bbt-detail-x" onClick={() => setActive(null)}>✕ 取消</button>
          </div>
        );
      })()}

      {active && <TerminalKline symbol={active} name={nameOf(active)} />}
      {/* 个股面板：速判卡9维+龙虎榜/一致预期/分红/新闻——把只有 AI 能调的数据能力接到用户主路径 */}
      {active && <TerminalStockPanel symbol={active} name={nameOf(active)} loggedIn={!!authUser}
        onRequireLogin={why => requireLogin(() => { /* 登录后用户再点一次展开 */ }, why)} />}

      <div ref={gridRef} className={`bbt-grid${maxed ? ' bbt-grid--maxed' : ''}`} style={{ ['--eqw' as any]: `${eqW}px` }}>
        {/* 行情监视 */}
        <section className={`bbt-panel${maxed && maxed !== 'eq' ? ' bbt-hide' : ''}${collapsed.eq ? ' bbt-panel--collapsed' : ''}${(eqNarrow && maxed !== 'eq') ? ' bbt-eq--narrow' : ''}`}>
          <div className="bbt-ph" onClick={e => { if ((e.target as HTMLElement).closest('button')) return; if (window.innerWidth <= 820) toggleCollapse('eq'); }}>
            <button className="bbt-collapse-btn" aria-label="自选股票" aria-expanded={!collapsed.eq} title={collapsed.eq ? '展开' : '收起'} onClick={() => toggleCollapse('eq')}>{collapsed.eq ? '▸' : '▾'}</button>
<span className="bbt-eq-en">WATCHLIST · </span>自选股票 <span className="bbt-breadth"><b className="bbt-up">{breadthUp}▲</b> <b className="bbt-down">{breadthDown}▼</b></span>
            <button className="bbt-max-btn" title={eqNarrow ? '加宽（显示全部列）' : '收窄'} onClick={() => setEqW(eqNarrow ? 460 : 260)}>{eqNarrow ? '⇥' : '⇤'}</button>
            <button className="bbt-max-btn" title="搜索/添加标的" onClick={() => { setPaletteOpen(true); setPq(''); }}>＋</button>
            <button className="bbt-max-btn" title={maxed === 'eq' ? '还原' : '最大化'} onClick={() => setMaxed(maxed === 'eq' ? null : 'eq')}>{maxed === 'eq' ? '⤡' : '⤢'}</button>
          </div>
          <div className="bbt-qhead">
            <span className="c-name">代码/名称</span>
            <span className="c-num bbt-th" onClick={() => toggleSort('price')}>最新{sortMark('price')}</span>
            <span className="c-num eq-extra">涨跌</span>
            <span className="c-num bbt-th" onClick={() => toggleSort('pct')}>涨幅%{sortMark('pct')}</span>
            <span className="c-range-h eq-extra">日内区间</span>
            <span className="c-num bbt-th eq-extra" onClick={() => toggleSort('vol')}>量{sortMark('vol')}</span>
          </div>
          <div className="bbt-qbody">
            {quotesError && Object.keys(quotes).length === 0 && watchlist.length > 0 && (
              <div className="bbt-quotes-warn">⚠ 行情源暂时不可用，正在自动重试…</div>
            )}
            {displayList.length === 0 && <div className="bbt-empty">自选为空 · 点下方「＋ 添加自选」搜索标的</div>}
            {groupedWatch.map(grp => (
              <React.Fragment key={grp.key}>
                {groupedWatch.length > 1 && <div className="bbt-qgroup">{grp.label}<span className="bbt-qgroup-n">{grp.syms.length}</span></div>}
                {grp.syms.map(renderQRow)}
              </React.Fragment>
            ))}
            {watchlist.length > 0 && <button className="bbt-qadd" onClick={() => { setPaletteOpen(true); setPq(''); }}>＋ 添加自选</button>}
          </div>
          <div className="bbt-pf">东财/新浪/Google · A股港股近实时·美股约延迟 · 开盘5s刷新 · 点行选标的 · 表头排序 · 点 ✕ 删除(需确认)</div>
        </section>

        {/* 可拖拽分隔条：拖动调整行情监视列宽 */}
        <div className="bbt-split" onMouseDown={startEqDrag} onTouchStart={startEqDrag} title="拖动调整宽度" />

        {/* 资讯 + 研报（研报作为「快讯」旁的标签，统一信息流）*/}
        <section className={`bbt-panel${maxed && maxed !== 'news' ? ' bbt-hide' : ''}${collapsed.news ? ' bbt-panel--collapsed' : ''}`}>
          <div className="bbt-ph">
            <button className="bbt-collapse-btn" aria-label="实时资讯" aria-expanded={!collapsed.news} title={collapsed.news ? '展开' : '收起'} onClick={() => toggleCollapse('news')}>{collapsed.news ? '▸' : '▾'}</button>
            {isCelebrity ? 'VOICES · 名人观点' : isResearch ? 'RESEARCH · 研报' : 'NEWS WIRE · 实时资讯'}{active && <span className="bbt-active-filter">▣ {activeName} 相关 <button className="bbt-clear" aria-label="清除标的筛选" title="清除筛选" onClick={() => setActive(null)}>✕</button></span>}
            <span className="bbt-filters" role="tablist" aria-label="资讯分类">{feedFilters.map(f => (
              <button key={f.key} role="tab" aria-selected={feedFilter === f.key} data-cat={f.key}
                className={`bbt-chip ${feedFilter === f.key ? 'on' : ''}`}
                onClick={() => { if (feedFilter !== f.key) logAct('tab', f.label); setFeedFilter(f.key); }}>
                {f.label}
              </button>
            ))}</span>
            <button className="bbt-max-btn" title={maxed === 'news' ? '还原' : '最大化'} onClick={() => setMaxed(maxed === 'news' ? null : 'news')}>{maxed === 'news' ? '⤡' : '⤢'}</button>
          </div>

          {isResearch && (
            <div className="bbt-res-bar">
              <span className="bbt-res-search">
                <input className="bbt-res-input" value={resQuery} placeholder="🔍 在线搜全球研报（公司 / 主题）…" onChange={e => setResQuery(e.target.value)} />
                {resQuery && <button className="bbt-clear" aria-label="清除搜索" title="清除搜索" onClick={() => setResQuery('')}>✕</button>}
              </span>
              {resLoading && <span className="bbt-breadth bbt-mute">检索中…</span>}
              <button className="bbt-max-btn" title="刷新研报" onClick={() => loadReports(resQuery)}>⟳</button>
            </div>
          )}

          {!isResearch && !isCelebrity && (
            <div className="bbt-res-bar">
              <span className="bbt-res-search">
                <input className="bbt-res-input" value={newsQuery} placeholder="🔍 搜快讯 / 文章（关键词，可空格多词）…" onChange={e => setNewsQuery(e.target.value)} />
                {newsQuery && <button className="bbt-clear" aria-label="清除搜索" title="清除搜索" onClick={() => setNewsQuery('')}>✕</button>}
              </span>
              {newsQuery.trim() && <span className="bbt-breadth bbt-mute">{feed.length} 条</span>}
            </div>
          )}

          {isCelebrity ? (
            <TerminalCelebrityViews inline />
          ) : isResearch ? (
            <div className="bbt-res">
              {resFiltered.length === 0 && (
                <div className="bbt-empty">{
                  resLoading ? '检索中…'
                    : resQuery.trim() ? `无「${resQuery.trim()}」相关研报`
                      : active ? `无 ${activeName} 相关研报`
                        : reportDq?.level === 'error'
                          ? (reportDq.detail || '研报源暂不稳定，点右上角 ⟳ 重试')
                          : '暂无最新研报 · 可在上方搜索框检索全球研报，或点右上角 ⟳ 刷新'
                }</div>
              )}
              {(() => {
                // 按日期分组成「模块」(reportFeed 已按日期倒序)。每个模块=可折叠的一天:默认仅最新一天展开。
                // 搜索/选股态:命中通常不多且要一眼看全 → 全部展开,不折叠。
                const groups: { day: string; items: ResearchWireItem[] }[] = [];
                const gidx: Record<string, number> = {};
                resFiltered.forEach(r => {
                  const dk = resDayKey(r);
                  if (gidx[dk] === undefined) { gidx[dk] = groups.length; groups.push({ day: dk, items: [] }); }
                  groups[gidx[dk]].items.push(r);
                });
                // ⚠️reportFeed 会把 AI 头条提前(头条可能来自更早一天)→ groups[0] 未必是最新日。
                // 取「日期最大」的有效日期组当「最新一天」(YYYY-MM-DD 字典序==时间序;'其他'桶忽略)。
                let latestIdx = 0, latestKey = '';
                groups.forEach((g, k) => { if (/^\d{4}-\d{2}-\d{2}$/.test(g.day) && g.day > latestKey) { latestKey = g.day; latestIdx = k; } });
                const forceOpen = !!resQuery.trim() || !!active;   // 搜索/选股态:全展开、不可折叠
                const DAY_CAP = 80;   // 单日初始最多渲染数,超出收到「展开本日剩余」,防最新一天数百篇一次性渲染卡顿
                return groups.map((g, gi) => {
                  const isLatest = gi === latestIdx;
                  const open = forceOpen || resDayOpen(g.day, isLatest);
                  const full = forceOpen || resDayFull.has(g.day);
                  const items = full ? g.items : g.items.slice(0, DAY_CAP);
                  return (
                    <div key={`g-${g.day}`} className="bbt-rgroup">
                      <button className={'bbt-rgroup-h' + (open ? ' open' : '') + (forceOpen ? ' static' : '')}
                              aria-expanded={open} aria-disabled={forceOpen || undefined}
                              onClick={() => { if (!forceOpen) toggleResDay(g.day, isLatest); }}>
                        {!forceOpen && <span className="bbt-rgroup-arr" aria-hidden="true">{open ? '▾' : '▸'}</span>}
                        <span className="bbt-rgroup-date">{fmtResGroup(g.day)}</span>
                      </button>
                      {open && items.map((r, i) => {
                        // AI 头条 = ybHeadKeys 命中的报告(在哪天都高亮);无头条名单则退化为「最新一天首条」
                        const isHead = ybHeadKeys.size ? ((!!r.file_id && ybHeadKeys.has(r.file_id)) || ybHeadKeys.has(r.id)) : (isLatest && i === 0);
                        return renderResearchRow(r, isHead);
                      })}
                      {open && !full && g.items.length > DAY_CAP && (
                        <button className="bbt-rmore" onClick={() => setResDayFull(prev => new Set(prev).add(g.day))}>↓ 展开本日剩余研报</button>
                      )}
                    </div>
                  );
                });
              })()}
              {/* 加载更早研报:默认只显最新一档,历史按需翻(归档一条不丢)。搜索/选股态(全量命中)不显 */}
              {!resQuery.trim() && !active && resFiltered.length > 0 && !resHistDone && (
                <button className="bbt-rmore" disabled={resMoreLoading} onClick={loadMoreReports}>
                  {resMoreLoading ? '加载中…' : '↓ 加载全部历史研报'}
                </button>
              )}
            </div>
          ) : (active && feedFilter === 'all') ? (
            // 选股后：快讯 / 文章 / 研报 三列并排，一次性看全该标的的三类信息（各列独立滚动）
            (() => {
              const kx = feed.filter(m => (m.topic || '') === '快讯');
              const wz = feed.filter(m => (m.topic || '') === '文章');
              const yb = reportFeed;
              const col = (cls: string, label: string, n: number, body: React.ReactNode) => (
                <div className={`bbt-stockcol bbt-stockcol--${cls}`}>
                  <div className="bbt-stockcol-h">{label}<span className="bbt-chip-n">{n}</span></div>
                  <div className="bbt-stockcol-body">{body}</div>
                </div>
              );
              const sccols = `${scW.kx ? scW.kx + 'px' : '1fr'} 6px ${scW.wz ? scW.wz + 'px' : '1fr'} 6px minmax(180px, 1.18fr)`;
              return (
                <div className="bbt-stockcols" style={{ ['--sccols' as any]: sccols }}>
                  {col('kx', '快讯', kx.length, kx.length ? kx.map(m => renderNewsRow(m, false)) : <div className="bbt-stockcol-empty">{searchLoading ? '检索中…' : `暂无 ${activeName} 快讯`}</div>)}
                  <div className="bbt-scsplit" onMouseDown={startScDrag('kx')} onTouchStart={startScDrag('kx')} title="拖动调整列宽" />
                  {col('wz', '文章', wz.length, wz.length ? wz.map(m => renderNewsRow(m, false)) : <div className="bbt-stockcol-empty">{searchLoading ? '检索中…' : `暂无 ${activeName} 文章`}</div>)}
                  <div className="bbt-scsplit" onMouseDown={startScDrag('wz')} onTouchStart={startScDrag('wz')} title="拖动调整列宽" />
                  {col('yb', '研报', yb.length, yb.length ? yb.map(r => renderResearchRow(r)) : <div className="bbt-stockcol-empty">{resLoading ? '检索中…' : `暂无 ${activeName} 研报`}</div>)}
                </div>
              );
            })()
          ) : (
            <div className="bbt-news">
              {/* 选股/搜索态：在 ALL 顶部展示相关「研报」段（全量历史在线检索结果），分页浏览，点条目→AI解读 */}
              {!!resSearchKw && feedFilter === 'all' && reportFeed.length > 0 && (() => {
                const cur = Math.min(resPage, Math.max(1, Math.ceil(reportFeed.length / pageSize)));
                const shown = reportFeed.slice((cur - 1) * pageSize, cur * pageSize);
                return (
                  <div className="bbt-allsec">
                    <div className="bbt-allsec-h"><span>📄 研报 · {reportFeed.length} 篇</span><button className="bbt-allsec-more" onClick={() => setFeedFilter('研报')}>独立查看 →</button></div>
                    {shown.map(r => renderResearchRow(r))}
                    <Pager page={cur} total={reportFeed.length} pageSize={pageSize} onPage={setResPage} onSize={setPageSize} />
                  </div>
                );
              })()}
              {!feedBooted && feed.length === 0 && !newsSearching && feedFilter !== '自选' && (
                <div className="bbt-skel" aria-hidden="true">
                  {Array.from({ length: 8 }, (_, i) => (
                    <div className="bbt-skel-row" key={i} style={{ animationDelay: `${i * 0.08}s` }}>
                      <span className="bbt-skel-t" /><span className="bbt-skel-tag" /><span className="bbt-skel-line" style={{ width: `${78 - (i % 4) * 12}%` }} />
                    </div>
                  ))}
                </div>
              )}
              {feedBooted && feed.length === 0 && feedLoadError && !active && !newsQuery.trim() && feedFilter !== '自选' && (
                <div className="bbt-empty">资讯加载失败，请检查网络后重试
                  <button className="bbt-retry-btn" onClick={() => { setFeedBooted(false); loadInitial(); }}>↻ 重新加载</button>
                </div>
              )}
              {feedBooted && feed.length === 0 && !(feedLoadError && !active && !newsQuery.trim() && feedFilter !== '自选') && (feedFilter === '自选' ? !watchlist.length : (newsSearching || (heads.kx.length + heads.wz.length + heads.yb.length) === 0)) && <div className="bbt-empty">{
                feedFilter === '自选' ? '还没有自选股 · 点左侧 ＋ 添加后，这里按个股分组看相关资讯'
                  : searchLoading ? '检索中…'
                    : newsQuery.trim() ? `无「${newsQuery.trim()}」相关快讯/文章`
                      : active ? `无 ${activeName} 相关快讯/文章` : '暂无最新资讯 · 开市后实时滚动更新'}</div>}
              {/* 回访首屏「我的」视角：3 秒看到"与我有关"的变化（数据全现成：quotes+watchlistFeed），点击直切自选 tab */}
              {authUser && watchlist.length > 0 && newsPageCur === 1 && !active && !newsQuery.trim() && feedFilter === 'all' && (() => {
                let up = 0, down = 0;
                watchlist.forEach(s => { const pc = Number(quotes[s]?.change_percent || 0); if (pc > 0) up += 1; else if (pc < 0) down += 1; });
                if (!up && !down && !watchlistFeed.length) return null;
                return (
                  <div className="bbt-activate" role="button" style={{ cursor: 'pointer' }} onClick={() => setFeedFilter('自选')}
                    title="点击只看自选相关资讯">
                    <div className="bbt-activate-h">📌 我的自选今日：<b className="bbt-up">{up} 涨</b> · <b className="bbt-down">{down} 跌</b>{watchlistFeed.length ? <> · 相关快讯 <b>{watchlistFeed.length}</b> 条</> : null} <span style={{ opacity: 0.6, fontWeight: 400 }}>→ 查看</span></div>
                  </div>
                );
              })()}
              {!activateDone && newsPageCur === 1 && !active && !newsQuery.trim() && feedFilter === 'all' && (
                <div className="bbt-activate">
                  <button className="bbt-activate-x" title="稍后" onClick={dismissActivate}>✕</button>
                  <div className="bbt-activate-h">🔔 开启盯盘 · 别错过你关心的行情</div>
                  <div className="bbt-activate-sub">开启后，你自选的股票一出快讯 / 异动，我们第一时间把你叫回来{watchlist.length ? `（你已自选 ${watchlist.length} 只）` : ''}。也可再加几只关心的：</div>
                  <div className="bbt-activate-picks">
                    {ACTIVATE_PICKS.slice(0, 8).map(p => {
                      const inWl = watchlist.includes(p.code);
                      return (
                        <button key={p.code} className={'bbt-activate-chip' + (inWl ? ' on' : '')} disabled={inWl}
                          onClick={() => requireLogin(() => addSymbol(p.code, p.name, false), '添加自选股票')}>{inWl ? '✓ 已加' : '＋ ' + p.name}</button>
                      );
                    })}
                    <button className="bbt-activate-chip bbt-activate-chip--more" onClick={() => { setPaletteOpen(true); setPq(''); }}>🔍 搜索添加</button>
                  </div>
                  <div className="bbt-activate-foot">
                    <button className="bbt-activate-go" onClick={() => requireLogin(armRecall, '开启盯盘提醒')}>开启盯盘提醒</button>
                    <button className="bbt-activate-skip" onClick={dismissActivate}>稍后再说</button>
                  </div>
                </div>
              )}
              {newsPageCur === 1 && !active && !newsQuery.trim() && feedFilter === 'all' && reviewToday && (() => {
                const rIsToday = reviewToday.date === new Date().toLocaleDateString('en-CA');  // 仅当复盘日=真今天才叫「今日」（周末/节假日显示最近交易日，不误标）
                return (
                <div className="bbt-review-card" onClick={() => openReview()} role="button" title={`查看 A股${reviewToday.session_label || '复盘'} · ${reviewToday.date}`}>
                  <span className="bbt-review-card-ico">📊</span>
                  <div className="bbt-review-card-main">
                    <div className="bbt-review-card-t">{rIsToday ? '今日 ' : ''}A股{reviewToday.session_label || '复盘'} · {reviewToday.date}{rIsToday && reviewToday.session === 'midday' ? <span className="bbt-review-card-mid">盘中</span> : null}{(reviewToday.our_edge || []).length > 0 ? <span className="bbt-review-card-edge">含「DeepFocus 提前发现」{(reviewToday.our_edge || []).length} 条</span> : null}</div>
                    <div className="bbt-review-card-sub">{(reviewToday.narrative || {}).one_liner || '点击查看大盘·板块·个股 × 我们的资讯复盘'}</div>
                  </div>
                  <span className="bbt-review-card-go">查看 →</span>
                </div>
                );
              })()}
              {newsPageCur === 1 && !active && !newsQuery.trim() && feedFilter === 'all' && renderHeads([...heads.kx.map((m: any) => headlineRow('kx', m)), ...heads.wz.map((m: any) => headlineRow('wz', m)), ...heads.yb.map((m: any) => headlineRow('yb', m))])}
              {/* 自选相关已独立成「自选」tab，ALL 里不再内嵌 */}
              {newsPageCur === 1 && !active && !newsQuery.trim() && feedFilter === '快讯' && renderHeads(heads.kx.map((m: any) => headlineRow('kx', m)))}
              {newsPageCur === 1 && !active && !newsQuery.trim() && feedFilter === '文章' && renderHeads(heads.wz.map((m: any) => headlineRow('wz', m)))}
              {feedFilter === '自选' && watchlist.length > 0 && wlGroups.every(g => g.total === 0) && (
                <div className="bbt-empty">{searchLoading ? '检索中…' : '自选股暂无相关快讯 / 文章 / 研报'}</div>
              )}
              {feedFilter === '自选' ? (
                // 按个股分组：每只自选股一个专区（吸顶表头 = 代码+名称+实时涨跌+分类计数，可折叠）；
                // 组内按「⚡快讯 / 📰文章 / 📊研报」分节，各节默认露几条、可独立展开全部。
                // 没有任何资讯的股票直接不显示（用户拍板：与其一排「暂无资讯」空壳，不如清爽）。
                wlGroups.filter(g => g.total > 0).map(g => {
                  const q = quotes[g.sym];
                  const pc = q ? Number(q.change_percent ?? 0) : 0;
                  const collapsed = !!wlCollapsed[g.sym];
                  const seenTs = wlSeen[g.sym] || '';
                  const isNew = (m: RealtimeMessageRecord) => !!seenTs && (m.created_at || '') > seenTs;
                  const newKx = g.kx.filter(isNew).length, newWz = g.wz.filter(isNew).length;
                  const newTotal = newKx + newWz;
                  const sec = (key: 'kx' | 'wz', icon: string, label: string, items: RealtimeMessageRecord[], cap: number, unread: number) => {
                    if (!items.length) return null;
                    const k = `${g.sym}#${key}`;
                    const closed = !!wlSecClosed[k];
                    const open = !!wlShowAll[k];
                    const shown = open ? items : items.slice(0, cap);
                    return (
                      <div className="bbt-wlg-sec" key={k}>
                        <div className="bbt-wlg-sech" onClick={() => setWlSecClosed(p => ({ ...p, [k]: !closed }))} title={closed ? '展开本节' : '收起本节'}>
                          <span className="bbt-wlg-arr">{closed ? '▸' : '▾'}</span>
                          {icon} {label}<i>{items.length}</i>
                          {unread > 0 && <em className="bbt-wlg-newdot">{unread} 新</em>}
                        </div>
                        {!closed && (<>
                          {shown.map(m => (
                            <div className={isNew(m) ? 'bbt-wlg-newrow' : undefined} key={m.id}>
                              {renderNewsRow(m, false, undefined)}
                            </div>
                          ))}
                          {items.length > cap && (
                            <button className="bbt-wlg-more" onClick={() => setWlShowAll(p => ({ ...p, [k]: !open }))}>
                              {open ? '▲ 收起' : `▼ 展开全部 ${items.length} 条${label}`}
                            </button>
                          )}
                        </>)}
                      </div>
                    );
                  };
                  return (
                    <div className="bbt-wlg" key={g.sym}>
                      <div className="bbt-wlg-h" onClick={() => setWlCollapsed(p => ({ ...p, [g.sym]: !collapsed }))} title={collapsed ? '展开' : '收起'}>
                        <span className="bbt-wlg-arr">{collapsed ? '▸' : '▾'}</span>
                        <span className="bbt-wlg-star">★</span>
                        <span className="bbt-wlg-name"><b>{g.sym}</b>　{nameOf(g.sym)}</span>
                        {q && q.price != null && (
                          <span className={`bbt-wlg-q ${pc > 0 ? 'bbt-up' : pc < 0 ? 'bbt-down' : ''}`}>
                            {q.price}　{pc > 0 ? '▲' : pc < 0 ? '▼' : '·'}{Math.abs(pc).toFixed(2)}%
                          </span>
                        )}
                        {newTotal > 0 && <span className="bbt-wlg-newdot bbt-wlg-newdot--h">● {newTotal} 新</span>}
                        <span className="bbt-wlg-cnt">
                          {g.kx.length > 0 && <span className="bbt-wlg-n">⚡{g.kx.length}</span>}
                          {g.wz.length > 0 && <span className="bbt-wlg-n">📰{g.wz.length}</span>}
                        </span>
                      </div>
                      {!collapsed && g.total > 0 && (
                        // 快讯+文章都有 → 左右双栏对照（窄屏 CSS 回落纵向）；只有一类 → 单栏占满
                        <div className={`bbt-wlg-body${g.kx.length > 0 && g.wz.length > 0 ? ' bbt-wlg-body--cols' : ''}`}>
                          {sec('kx', '⚡', '快讯', g.kx, 4, newKx)}
                          {sec('wz', '📰', '文章', g.wz, 3, newWz)}
                        </div>
                      )}
                    </div>
                  );
                })
              ) : (() => {
                const hasMore = !useServerFeed && !histDone;  // 实时流态(ALL/快讯)：服务器可能还有更旧历史可翻
                const shown = pagedRows.slice((newsPageCur - 1) * pageSize, newsPageCur * pageSize);
                return (<>
                  {shown.map(m => renderNewsRow(m, false, undefined))}
                  <Pager page={newsPageCur} total={pagedRows.length} pageSize={pageSize} onPage={goNewsPage} onSize={setPageSize} busy={histLoading} hasMore={hasMore} />
                </>);
              })()}
            </div>
          )}
          {isResearch && <div className="bbt-pf">海外投行研报{resQuery.trim() ? ` · 检索「${resQuery.trim()}」` : ''} · <span className={resLoading ? 'bbt-up' : ''}>{resLoading ? '● 同步中…' : `每分钟自动同步${resSyncedAt ? ` · 同步于 ${fmtTime(resSyncedAt.toISOString())}` : ''}`}</span> · 点条目 → AI 解读</div>}
        </section>
      </div>

      <div className="bbt-status">
        <span className="bbt-status-disc">⚠ 仅供研究与教育用途，不构成投资建议，据此操作风险自负</span>
        <span className={`bbt-conn c-${status}`}>● {STATUS_LABEL[status]}</span>
        <span className="bbt-status-counts">NEWS {feed.length} · RES {reports.length} · EQ {Object.keys(quotes).length}/{watchlist.length} <b className="bbt-up">{breadthUp}▲</b>/<b className="bbt-down">{breadthDown}▼</b></span>
        <span className="bbt-status-fn">／ 搜标的 · ↑↓ 切换 · ESC 取消</span>
      </div>


      {newsPreview && (() => {
        const isImg = isImageUrl(newsPreview.url);
        return (
        <div className="bbt-doc-overlay" onMouseDown={() => setNewsPreview(null)}>
          <div className={`bbt-doc ${isImg ? 'bbt-doc--img' : 'bbt-doc--text'}`} onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-doc-bar">
              {(newsPreview.severity === 'critical' || newsPreview.severity === 'warning' || newsPreview.severity === 'success') && <span className={`bbt-ntag tag-${newsPreview.severity}`}>{SEV_TAG[newsPreview.severity]}</span>}
              <span className="bbt-doc-tag">{newsPreview.topic || '资讯'}</span>
              <span className="bbt-doc-title" title={stripUrls(newsPreview.title) || newsPreview.title}>{stripUrls(newsPreview.title) || newsPreview.title}</span>
              <span className="bbt-doc-meta">{fmtTimeSmart(newsPreview.created_at)}</span>
              <button className="bbt-doc-close" onClick={() => setNewsPreview(null)}>✕ 关闭</button>
            </div>
            <div className="bbt-news-actions">
              <button className="bbt-news-btn bbt-news-btn--ai" onClick={() => runNewsAi(newsPreview)}>✦ AI 解读</button>
              {newsPreview.url && <a className="bbt-news-btn bbt-news-btn--src" href={newsPreview.url} target="_blank" rel="noopener noreferrer">{isImg ? '原图新窗口 ↗' : '查看原文 ↗'}</a>}
            </div>
            {isImg ? (
              <div className="bbt-doc-img-wrap">
                <img className="bbt-doc-img" src={newsPreview.url as string} alt={stripUrls(newsPreview.title) || newsPreview.title} loading="lazy" />
                {stripUrls(newsPreview.source_name) && <div className="bbt-doc-src">来源 · {stripUrls(newsPreview.source_name)}</div>}
              </div>
            ) : (
              <div className="bbt-doc-text">
                <h3 className="bbt-doc-h">{stripUrls(newsPreview.title) || newsPreview.title}</h3>
                <div className="bbt-doc-body">{stripUrls(newsPreview.content) || '（暂无正文，点「✦ AI 解读」获取要点）'}</div>
                {stripUrls(newsPreview.source_name) && <div className="bbt-doc-src">来源 · {stripUrls(newsPreview.source_name)}</div>}
              </div>
            )}
          </div>
        </div>
        );
      })()}

      {aiReport && (
        <div className="bbt-doc-overlay" onMouseDown={closeAi}>
          <div className="bbt-doc bbt-doc--ai" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-doc-bar">
              <span className="bbt-doc-tag bbt-ai-tag">✦ AI 解读</span>
              <span className="bbt-doc-title" title={aiReport.title}>{aiReport.title}</span>
              {aiReport.date && <span className="bbt-doc-meta">🗓 {aiReport.date}</span>}
              {aiResult && <button className="bbt-doc-copy" onClick={shareAiImage} disabled={aiImgBusy}>{aiImgBusy ? '生成中…' : '🖼 图片'}</button>}
              <button className="bbt-doc-close" onClick={closeAi} aria-label="关闭">✕ 关闭</button>
            </div>
            <div className="bbt-ai-body">
              {aiLoading && (
                <div className="bbt-ai-loading">
                  <div className="bbt-ai-load-head"><span>✦ AI 正在解读…</span><span className="bbt-ai-load-pct">{Math.round(aiProgress)}%</span></div>
                  <div className="bbt-ai-bar"><div className="bbt-ai-bar-fill" style={{ width: `${aiProgress}%` }} /></div>
                  <div className="bbt-ai-load-hint">文字型约 10s · 图片型研报首次约 30–60s（完成后再看即秒开）</div>
                </div>
              )}
              {!aiLoading && aiError && <div className="bbt-empty bbt-ai-err">⚠ {aiError}</div>}
              {!aiLoading && aiResult && (() => {
                const bull = aiResult.bullish?.length ? aiResult.bullish : (aiResult.key_points || []);
                const bear = aiResult.bearish?.length ? aiResult.bearish : (aiResult.risks || []);
                return (
                <>
                  {(aiResult.subject || aiResult.rating || aiResult.target_price) && (
                    <div className="bbt-ai-tags">
                      {aiResult.subject && <span className="bbt-ai-chip bbt-ai-chip--subject">标的 {aiResult.subject}</span>}
                      {aiResult.rating && <span className="bbt-ai-chip bbt-ai-chip--rating">评级 {aiResult.rating}</span>}
                      {aiResult.target_price && <span className="bbt-ai-chip bbt-ai-chip--target">目标价 {aiResult.target_price}</span>}
                    </div>
                  )}
                  {!!aiResult.instruments?.length && (
                    <div className="bbt-ai-insts">
                      <span className="bbt-ai-insts-h">📈 提及个股</span>
                      {aiResult.instruments.map((t, i) => <span key={t + i} className="bbt-ai-inst">{t}</span>)}
                    </div>
                  )}
                  {aiResult.one_liner && <div className="bbt-ai-oneliner">💡 {aiResult.one_liner}</div>}
                  {aiResult.summary && <><div className="bbt-ai-h">一句话看懂</div><div className="bbt-ai-sum">{aiResult.summary}</div></>}
                  {aiResult.core_logic && <><div className="bbt-ai-h bbt-ai-h--logic">🔑 投资逻辑</div><div className="bbt-ai-sum">{aiResult.core_logic}</div></>}
                  {bull.length > 0 && <><div className="bbt-ai-h bbt-ai-h--bull">✅ 利好 · 看涨理由</div><ul className="bbt-ai-list bbt-ai-bull">{bull.map((k, i) => <li key={i}>{k}</li>)}</ul></>}
                  {bear.length > 0 && <><div className="bbt-ai-h bbt-ai-h--bear">⚠️ 利空 · 风险点</div><ul className="bbt-ai-list bbt-ai-risk">{bear.map((k, i) => <li key={i}>{k}</li>)}</ul></>}
                  {aiResult.takeaway && <div className="bbt-ai-takeaway"><b>📌 一句话启示</b>{aiResult.takeaway}</div>}
                  {aiResult.df_take && (() => {
                    const dft = aiResult.df_take!.trim();
                    const long = dft.length > 220;
                    const show = dfExpanded || !long;
                    return (
                      <div className="bbt-ai-dftake">
                        <div className="bbt-ai-dftake-h"><b>DeepFocus 视角</b><span className="bbt-ai-dftake-badge">独家点评</span></div>
                        <div className={`bbt-ai-dftake-body${show ? '' : ' bbt-ai-dftake-body--clip'}`}>{dft}</div>
                        {long && <button className="bbt-ai-dftake-more" onClick={() => setDfExpanded(v => !v)}>{show ? '收起 ▴' : '展开全文 ▾'}</button>}
                      </div>
                    );
                  })()}
                  <div className="bbt-ai-foot">{aiResult.provider || 'AI'} 解读 · 仅供参考、非投资建议、无逐句溯源{typeof aiResult.confidence === 'number' ? ` · 置信 ${Math.round((aiResult.confidence || 0) * 100)}%` : ''}{aiResult.source_note ? ` · ${aiResult.source_note}` : ''}</div>
                </>
                );
              })()}
            </div>
            {!aiLoading && (
              <div className="bbt-ai-actions">
                {aiResult && <button className="bbt-ai-btn bbt-ai-btn--img" onClick={shareAiImage} disabled={aiImgBusy}>{aiImgBusy ? '生成图片中…' : (aiCopied ? '✓ 已复制图片' : '🖼 复制为图片')}</button>}
                {aiResult && <button className="bbt-ai-btn bbt-ai-btn--copy" onClick={copyAiResult}>{aiTextCopied ? '✓ 已复制文字' : '⧉ 复制为文字'}</button>}
                {/* 研报解读才给「分享」和「原文」；文章解读不给（文章走行内 /article 分享） */}
                {aiResult && aiReportMeta && <button className="bbt-ai-btn bbt-ai-btn--copy" onClick={shareReportInsight} disabled={reportShareBusy}>{reportShareBusy ? '生成链接中…' : '🔗 分享解读'}</button>}
                {aiReportMeta?.preview_url && canViewResearchOriginal && <button className="bbt-ai-btn bbt-ai-btn--src" onClick={() => openResearchOriginal(aiReportMeta!.preview_url!)} disabled={!!pdfLoadingUrl}>{pdfLoadingUrl === aiReportMeta.preview_url ? '加载中…' : '原文 ↗'}</button>}
                {aiError && <button className="bbt-ai-btn bbt-ai-btn--copy" onClick={() => aiRetryRef.current?.()}>↻ 重试</button>}
                <button className="bbt-ai-btn bbt-ai-btn--close" onClick={closeAi}>关闭</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 研报解读分享弹窗（极简：可复制文案 + 公开落地页链接）；落地页软墙→登录看完整解读 */}
      <ShareModal
        visible={shareModal.open}
        content={shareModal.target ?? undefined}
        modalTitle="分享研报解读"
        simple
        onCancel={() => setShareModal({ open: false, target: null })}
      />

      {shareImgUrl && (
        <div className="bbt-doc-overlay" onMouseDown={closeShareImg}>
          <div className="bbt-shareimg" onMouseDown={e => e.stopPropagation()}>
            <div className={`bbt-shareimg-tip${shareImgCoarse ? ' bbt-shareimg-tip--big' : ''}`}>{shareImgNote || '👇 长按图片，选择「存储图像 / 保存到相册」或「分享」'}</div>
            <img className="bbt-shareimg-img" src={shareImgUrl} alt="AI 解读分享图" />
            <div className="bbt-ai-actions">
              {!shareImgCoarse && <a className="bbt-ai-btn bbt-ai-btn--img" href={shareImgUrl} download="AI解读.png">⬇ 下载图片</a>}
              <button className="bbt-ai-btn bbt-ai-btn--close" onClick={closeShareImg}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className="bbt-toast">{toast}</div>}

      {pushNudge && (
        <div className="bbt-pushnudge" role="dialog" aria-label="开启盯盘提醒">
          <span className="bbt-pushnudge-ico" aria-hidden="true">🔔</span>
          <span className="bbt-pushnudge-txt">已加自选 · 开启盯盘,有快讯/异动第一时间叫你<b>(关掉页面也能收到)</b></span>
          <button className="bbt-pushnudge-go" onClick={() => requireLogin(armRecall, '开启盯盘提醒')}>开启</button>
          <button className="bbt-pushnudge-x" onClick={() => setPushNudge(false)} aria-label="稍后">稍后</button>
        </div>
      )}

      {paletteOpen && (
        <div className="bbt-palette-overlay" onMouseDown={() => setPaletteOpen(false)}>
          <div className="bbt-palette" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-palette-bar">
              <span className="bbt-palette-prompt">🔍</span>
              <input
                ref={paletteInput} className="bbt-palette-input" value={pq}
                placeholder="搜索代码或名称（茅台 / 600519 / NVDA），↑↓ 选择，回车确认"
                onChange={e => setPq(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setPaletteActive(i => Math.min(paletteItems.length - 1, i + 1)); }
                  else if (e.key === 'ArrowUp') { e.preventDefault(); setPaletteActive(i => Math.max(0, i - 1)); }
                  else if (e.key === 'Enter') { const it = paletteItems[paletteActiveClamped]; if (it) runItem(it.run); }
                }}
              />
              {paletteLoading && <span className="bbt-palette-spin" />}
              <span className="bbt-palette-esc">ESC</span>
            </div>
            <div className="bbt-palette-list">
              {paletteItems.map((it, i) => {
                const prevType = i > 0 ? paletteItems[i - 1].type : null;
                const header = it.type !== prevType
                  ? (it.type === 'go' ? '自选 · 跳转' : it.type === 'add' ? '搜索结果 · 回车直接查看 · ＋加自选' : it.type === 'uni' ? '相关 · 板块/快讯/研报/学堂' : '命令 · COMMANDS')
                  : null;
                const isActive = i === paletteActiveClamped;
                return (
                  <React.Fragment key={it.key}>
                    {header && <div className="bbt-palette-sec">{header}</div>}
                    <div ref={isActive ? paletteActiveRef : undefined}
                      className={`bbt-palette-row${isActive ? ' active' : ''}`}
                      onMouseMove={() => setPaletteActive(i)} onMouseDown={() => runItem(it.run)}>
                      {it.type === 'go' && (() => {
                        const pc = Number(it.quote?.change_percent || 0);
                        const dir = pc > 0 ? 'up' : pc < 0 ? 'down' : 'flat';
                        return (<>
                          <span className="bbt-palette-label">{it.label}</span>
                          {it.quote && <span className={`bbt-${dir} bbt-palette-px`}>{it.quote.price}　{pc > 0 ? '+' : ''}{pc.toFixed(2)}%</span>}
                          <span className="bbt-palette-add added" title="已在你的自选里">✓ 已自选</span>
                          <span className="bbt-palette-go">GO ›</span>
                        </>);
                      })()}
                      {it.type === 'add' && (<>
                        <span className="bbt-palette-label">{it.code}　{it.name} <span className="bbt-dim">{it.exch}</span></span>
                        {watchlist.includes(it.code)
                          ? <span className="bbt-palette-add added">✓ 已加</span>
                          : <button className="bbt-palette-add" title="加入自选(不跳转)"
                              onMouseDown={e => { e.stopPropagation(); requireLogin(() => addSymbol(it.code, it.name, false), '添加自选股票'); }}>＋ 添加</button>}
                        <span className="bbt-palette-go">查看 ›</span>
                      </>)}
                      {it.type === 'uni' && (<>
                        <span className="bbt-palette-label">{it.label}</span><span className="bbt-palette-go">↵</span>
                      </>)}
                      {it.type === 'cmd' && (<>
                        <span className="bbt-palette-label">{it.label}</span><span className="bbt-palette-go">↵</span>
                      </>)}
                    </div>
                  </React.Fragment>
                );
              })}
              {paletteItems.length === 0 && <div className="bbt-empty">{paletteLoading ? '搜索中…' : pqLow ? `无「${pq.trim()}」匹配 · 试试公司名或代码` : '🔍 输入股票代码或名称（如 茅台 / 600519 / NVDA），找到后点「＋ 添加」加入自选'}</div>}
            </div>
          </div>
        </div>
      )}

      <TerminalAuthModal
        open={authOpen}
        reason={authReason}
        onClose={() => setAuthOpen(false)}
        onAuthed={onAuthed}
      />

      {showOnb && <TerminalOnboarding onClose={() => setShowOnb(false)} />}
      {showHelp && <TerminalHelp onClose={() => setShowHelp(false)} onStartTour={() => { setShowHelp(false); setShowOnb(true); }} />}
      {showReferral && <TerminalReferral onClose={() => setShowReferral(false)} showToast={showToast} onChanged={refreshMembership} />}
      {showAiFund && <TerminalAiFund onClose={() => setShowAiFund(false)} />}
      {showWeixinBind && <TerminalWeixinBind onClose={() => setShowWeixinBind(false)} showToast={showToast} onOpenConsole={authUser === 'lx199710' ? openWeixinConsole : undefined} />}
      {groupOpen && (() => {
        const g = groupCfg || {};
        const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
        const perks: string[] = Array.isArray(g.perks) && g.perks.length
          ? g.perks
          : ['🗣️ 和同样在看盘的人聊行情、唠个股', '⚡ 第一手快讯、每日 A 股收盘复盘，群里同步划重点', '🎁 不定期群友专属福利 / 限时会员口令'];
        const cs = (g.cs_wechat || '').trim();
        const copyCs = async () => {
          try { await navigator.clipboard.writeText(cs); showToast('✓ 已复制客服微信号 · 去微信搜索添加，备注「进群」'); }
          catch { showToast('复制失败，请手动记下：' + cs); }
        };
        return (
          <div className="bbt-doc-overlay" onMouseDown={() => setGroupOpen(false)}>
            <div className="bbt-doc bbt-doc--group" role="dialog" aria-modal="true" aria-label="用户交流群" onMouseDown={e => e.stopPropagation()}>
              <div className="bbt-doc-bar">
                <span className="bbt-doc-tag bbt-group-tag">💬 用户交流群</span>
                <span className="bbt-doc-title">{g.subtitle || '免费 · 对所有用户开放（不用登录、不用会员）'}</span>
                <button className="bbt-doc-close" onClick={() => setGroupOpen(false)}>✕ 关闭</button>
              </div>
              <div className="bbt-grp-body">
                <div className="bbt-grp-h">{g.title || 'DeepFocus 用户交流群'}</div>
                <div className="bbt-grp-qrwrap">
                  {g.group_qr ? (
                    <img className="bbt-grp-qr" src={site + '/api/community/qr/group?t=' + Math.floor(Date.now() / 60000)} alt="微信群二维码" />
                  ) : (
                    <div className="bbt-grp-qr bbt-grp-qr--empty">
                      <div className="bbt-grp-qr-emptyico">💬</div>
                      <div>二维码准备中<br />稍后再来扫码进群</div>
                    </div>
                  )}
                </div>
                <div className="bbt-grp-scan">📱 微信「扫一扫」/ 长按图片识别二维码 进群</div>
                {g.expires_at && <div className="bbt-grp-expire">⏳ 本群二维码 <b>{g.expires_at}</b> 前有效；过期请用下方客服微信进群</div>}
                <ul className="bbt-grp-perks">
                  {perks.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
                {cs && (
                  <div className="bbt-grp-cs">
                    <div className="bbt-grp-cs-t">群满 200 人 / 二维码过期？加客服拉你进群</div>
                    <div className="bbt-grp-cs-row">
                      <span className="bbt-grp-cs-id">微信号：<b>{cs}</b></span>
                      <button className="bbt-grp-cs-copy" onClick={copyCs}>复制微信号</button>
                    </div>
                    {g.cs_qr && <img className="bbt-grp-cs-qr" src={site + '/api/community/qr/cs?t=' + Math.floor(Date.now() / 60000)} alt="客服微信二维码" />}
                    <div className="bbt-grp-cs-tip">添加时备注「进群」，客服会把你拉进群 👍</div>
                  </div>
                )}
                <div className="bbt-grp-disc">{g.disclaimer || '群内为用户自由交流，仅供参考、不构成任何投资建议，请独立判断、理性甄别。'}</div>
              </div>
            </div>
          </div>
        );
      })()}
      {reviewOpen && (() => {
        const r = reviewData;
        const nar = (r && r.narrative) || {};
        const pctCls = (p: number) => p > 0 ? 'bbt-up' : p < 0 ? 'bbt-down' : 'bbt-flat';
        const pctTxt = (p: any) => (typeof p === 'number') ? `${p > 0 ? '+' : ''}${p.toFixed(2)}%` : '—';
        const flowTxt = (v: any) => { if (typeof v !== 'number') return ''; const a = Math.abs(v); return a >= 1e8 ? `${(v / 1e8).toFixed(1)}亿` : a >= 1e4 ? `${(v / 1e4).toFixed(0)}万` : `${v.toFixed(0)}`; };
        const sources: any[] = (r && r.sources) || [];
        // 点开复盘里提及的来源 → 关掉复盘、直接出 AI 解读版本（登录网关 + 每日免费额度，复用文章解读）
        const openSource = (s: any) => { setReviewOpen(false); runNewsAi({ id: s.id || '', title: s.title || '', content: '', url: s.url || '', topic: s.topic || '资讯', severity: 'info', created_at: s.created_at || '', source_name: '', tags: [], metadata: {} } as any); };
        const _norm = (s: string) => (s || '').replace(/[\s·｜|，,。.、:：()（）【】[\]"'《》]/g, '');
        // 把文中的【标题】渲染成可点链接（模糊匹配 sources 标题）
        const linkify = (text: string): React.ReactNode[] => {
          const out: React.ReactNode[] = []; if (!text) return out;
          const re = /【([^】]+)】/g; let last = 0, mm: RegExpExecArray | null, idx = 0;
          while ((mm = re.exec(text))) {
            if (mm.index > last) out.push(text.slice(last, mm.index));
            const inner = mm[1]; const nin = _norm(inner);
            const hit = nin.length >= 3 ? sources.find(s => { const ns = _norm(s.title); return ns && (ns.includes(nin) || nin.includes(ns)); }) : null;
            out.push(hit ? <a key={'lk' + idx++} className="bbt-review-link" onClick={() => openSource(hit)} title="点开看原内容">【{inner}】</a> : ('【' + inner + '】'));
            last = mm.index + mm[0].length;
          }
          if (last < text.length) out.push(text.slice(last));
          return out;
        };
        // 按句切分，每句独立成行（不堆在一起；标题【】仍可点）
        const sentences = (text: string): string[] => text ? text.split(/(?<=[。！；!?])/).map(s => s.trim()).filter(Boolean) : [];
        const hasBreadth = r && r.breadth && (r.breadth.advancers != null || r.breadth.decliners != null);
        return (
        <div className="bbt-doc-overlay" onMouseDown={() => setReviewOpen(false)}>
          <div className="bbt-doc bbt-doc--review" role="dialog" aria-modal="true" aria-label="A股收盘复盘" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-doc-bar">
              <span className="bbt-doc-tag bbt-review-tag">📊 A股{(r && r.session_label) || '收盘复盘'}</span>
              <span className="bbt-doc-title">{r ? r.date : '加载中…'}</span>
              {r && <span className="bbt-review-prov">{r.narrative_provider === 'ai' ? 'AI 综述' : '数据综述'}</span>}
              {checkin && checkin.streak > 0 && (
                <span className="bbt-review-streak" title={`累计看盘 ${checkin.total} 天 · 最长连续 ${checkin.longest} 天`}>
                  🔥 连续 {checkin.streak} 天
                </span>
              )}
              {r && (r.narrative || (r.our_edge || []).length > 0) && <span className="bbt-review-actions">
                {(r.our_edge || []).some((e: any) => typeof e.lead_hours === 'number' && e.lead_hours >= 1) &&
                  <button className="bbt-review-act bbt-review-act--hero" onClick={shareForesight} disabled={reviewImgBusy} title="把『我提前X小时发现』做成战绩图，晒朋友圈">{reviewImgBusy ? '生成中…' : '🎯 晒先知战绩'}</button>}
                <button className="bbt-review-act" onClick={copyReviewText} title="复制复盘文字（带网站）">📋 复制</button>
                <button className="bbt-review-act" onClick={shareReviewImage} disabled={reviewImgBusy} title="生成分享长图（带二维码）">{reviewImgBusy ? '生成中…' : '🖼 生成图片'}</button>
              </span>}
              <button className="bbt-doc-close" onClick={() => setReviewOpen(false)}>✕ 关闭</button>
            </div>
            <div className="bbt-review-body">
              {/* 🎯 「我们提前发现的」量化战绩：只计经 AI 验证的命中，可点开溯源 */}
              {trackRecord && trackRecord.hit_count > 0 && (
                <div className="bbt-tr">
                  <div className="bbt-tr-h">📡 我们提前发现的 · 资讯覆盖<span className="bbt-tr-sub">近 {trackRecord.days} 天 · 仅计经 AI 验证的覆盖样本</span></div>
                  <div className="bbt-tr-stats">
                    <div className="bbt-tr-stat"><b>{trackRecord.hit_count}</b><span>条提前覆盖</span></div>
                    <div className="bbt-tr-stat"><b>{trackRecord.avg_lead_hours}h</b><span>平均提前</span></div>
                    <div className="bbt-tr-stat"><b>{trackRecord.max_lead_hours}h</b><span>最早提前</span></div>
                  </div>
                  {trackRecord.personal && trackRecord.personal.hit_count > 0 && (
                    <div className="bbt-tr-personal">⭐ 你的自选里，我们已为你提前覆盖 <b>{trackRecord.personal.hit_count}</b> 次</div>
                  )}
                  <div className="bbt-tr-list">
                    {trackRecord.recent.slice(0, 6).map((h, k) => {
                      const lead = (typeof h.lead_hours === 'number' && h.lead_hours >= 1) ? (h.lead_hours >= 24 ? `领先${(h.lead_hours / 24).toFixed(0)}天` : `领先${h.lead_hours.toFixed(0)}h`) : '同日捕捉';
                      const s0 = (h.signals || [])[0];
                      return (
                        <button key={k} className="bbt-tr-item" onClick={() => s0 && openSource({ id: s0.id, title: s0.title, url: s0.url, topic: s0.topic })} title={h.reason || '点开看当时的原始资讯证据'}>
                          <span className="bbt-tr-date">{(h.date || '').slice(5)}</span>
                          <span className={`bbt-tr-tag ${(h.pct || 0) < 0 ? 'warn' : 'find'}`}>{(h.pct || 0) < 0 ? '预警' : '发现'}</span>
                          <span className={`bbt-tr-kind ${h.kind === 'stock' ? 'stk' : 'sec'}`}>{h.kind === 'stock' ? '个股' : '板块'}</span>
                          <span className="bbt-tr-name">{h.name}</span>
                          {typeof h.pct === 'number' && <span className={pctCls(h.pct)}>{pctTxt(h.pct)}</span>}
                          <span className="bbt-tr-lead">⚡{lead}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="bbt-tr-disc">仅统计经验证的提前覆盖样本，存在选择偏差；领先时长不代表收益，历史表现不代表未来，不构成投资建议。</div>
                </div>
              )}
              {reviewLoading && !r && <div className="bbt-empty">加载中…</div>}
              {!reviewLoading && !r && reviewError && (
                <div className="bbt-empty">复盘加载失败，请检查网络后重试
                  <button className="bbt-retry-btn" onClick={() => openReview(reviewRetryRef.current)}>↻ 重试</button>
                </div>
              )}
              {!reviewLoading && !r && !reviewError && <div className="bbt-empty">今日复盘尚未生成（交易日 15:35 后自动更新）</div>}
              {r && <>
                {nar.one_liner && <div className="bbt-review-oneliner">{nar.one_liner}</div>}

                {/* 指数 */}
                <div className="bbt-review-idx">
                  {(r.indices || []).map((i: any, k: number) => (
                    <div key={k} className="bbt-review-idx-cell">
                      <div className="bbt-review-idx-name">{i.name}</div>
                      <div className="bbt-review-idx-close">{i.close}</div>
                      <div className={`bbt-review-idx-pct ${pctCls(i.pct)}`}>{pctTxt(i.pct)}</div>
                    </div>
                  ))}
                </div>
                {/* 涨跌家数：缺数据的项不显示，避免「涨 — 跌 —」 */}
                <div className="bbt-review-breadth">
                  {hasBreadth && <><span className="bbt-up">涨 {r.breadth.advancers ?? '—'}</span><span className="bbt-down">跌 {r.breadth.decliners ?? '—'}</span></>}
                  <span className="bbt-up">涨停 {r.breadth?.limit_up ?? '—'}</span>
                  <span className="bbt-down">跌停 {r.breadth?.limit_down ?? '—'}</span>
                </div>

                {/* 小白解读：大白话讲清今天发生了什么，放最前面 */}
                {nar.plain && <div className="bbt-review-plain">
                  <div className="bbt-review-plain-h">💡 导读</div>
                  {sentences(nar.plain).map((s, k) => <div key={k} className="bbt-review-plain-line">{linkify(s)}</div>)}
                </div>}

                {/* 大盘 */}
                {nar.market && <div className="bbt-review-card-sec">
                  <div className="bbt-review-h">📈 大盘</div>
                  {sentences(nar.market).map((s, k) => <div key={k} className="bbt-review-line">{linkify(s)}</div>)}
                </div>}

                {/* 板块 */}
                {(nar.sectors || (r.sectors?.top || []).length > 0) && <div className="bbt-review-card-sec">
                  <div className="bbt-review-h">🧩 板块</div>
                  {(r.sectors?.top || []).length > 0 && <div className="bbt-review-chips">
                    {(r.sectors.top || []).map((b: any, k: number) => (
                      <span key={'t' + k} className="bbt-sec-chip up" title={`主力 ${flowTxt(b.main_flow)} · 领涨 ${b.leader || ''}`}>{b.name} <b>{pctTxt(b.pct)}</b></span>
                    ))}
                    {(r.sectors.bottom || []).map((b: any, k: number) => (
                      <span key={'b' + k} className="bbt-sec-chip down">{b.name} <b>{pctTxt(b.pct)}</b></span>
                    ))}
                  </div>}
                  {sentences(nar.sectors).map((s, k) => <div key={k} className="bbt-review-line">{linkify(s)}</div>)}
                </div>}

                {/* 资金面 */}
                {nar.funds && <div className="bbt-review-card-sec">
                  <div className="bbt-review-h">💰 资金面</div>
                  {sentences(nar.funds).map((s, k) => <div key={k} className="bbt-review-line">{linkify(s)}</div>)}
                </div>}

                {/* ⭐ 我们提前发现的：突出信息价值 + 快讯领先时长 + 可点开原文 */}
                {(nar.our_value || (r.our_edge || []).length > 0) && <div className="bbt-review-edge">
                  <div className="bbt-review-edge-h">⭐ DeepFocus 提前发现{(r.our_edge || []).length > 0 ? <span className="bbt-review-edge-n">{(r.our_edge || []).length} 条线索</span> : null}</div>
                  {nar.our_value && sentences(nar.our_value).map((s, k) => <div key={'ov' + k} className="bbt-review-edge-val">{linkify(s)}</div>)}
                  {(r.our_edge || []).slice(0, 8).map((e: any, k: number) => {
                    const lead = e.lead_hours;
                    const leadTxt = (typeof lead === 'number' && lead >= 1) ? (lead >= 24 ? `领先约${(lead / 24).toFixed(0)}天` : `领先约${lead.toFixed(0)}小时`) : '同日捕捉';
                    return (
                      <div key={k} className="bbt-review-edge-item">
                        <div className="bbt-review-edge-top">
                          <span className={`bbt-tr-tag ${(e.direction === 'down' || (e.pct || 0) < 0) ? 'warn' : 'find'}`}>{(e.direction === 'down' || (e.pct || 0) < 0) ? '预警' : '发现'}</span>
                          <span className={`bbt-review-edge-kind ${e.kind === 'stock' ? 'stk' : 'sec'}`}>{e.kind === 'stock' ? '个股' : '板块'}</span>
                          <span className="bbt-review-edge-name">{e.name}</span>
                          {typeof e.pct === 'number' && <span className={pctCls(e.pct)}>{pctTxt(e.pct)}</span>}
                          {typeof e.evidence === 'number' && e.evidence > 0 && <span className="bbt-review-edge-ev">{e.evidence} 条佐证</span>}
                          <span className="bbt-review-edge-lead">⚡ {leadTxt}</span>
                        </div>
                        {e.reason && <div className="bbt-review-edge-reason">💬 {e.reason}</div>}
                        <div className="bbt-review-edge-sigs">
                          {(e.signals || []).slice(0, 3).map((s: any, j: number) => (
                            <button key={j} className="bbt-review-edge-sig" title="点开看我们当时发的原内容"
                              onClick={() => openSource({ id: s.id, title: s.title, url: s.url, topic: s.topic, created_at: s.created_at })}>
                              <span className={`bbt-review-edge-sigtag t-${s.topic}`}>{s.topic}</span>
                              <span className="bbt-review-edge-sigtitle">{s.title}</span>
                              {s.lead && <span className="bbt-review-edge-sigwhen">{s.lead}</span>}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>}

                {/* 机构观点：今日异动个股近一个多月的东财券商研报覆盖（公开数据） */}
                {(r.broker_views || []).length > 0 && <div className="bbt-review-card-sec">
                  <div className="bbt-review-h">🏦 机构观点 <span className="bbt-review-edge-n">{(r.broker_views || []).length} 只异动股有研报覆盖</span></div>
                  {(r.broker_views || []).slice(0, 6).map((v: any, k: number) => {
                    const lt = v.latest || {};
                    return (
                      <div key={k} className="bbt-review-broker-item">
                        <div className="bbt-review-broker-top">
                          <span className={`bbt-review-edge-kind stk`}>个股</span>
                          <span className="bbt-review-edge-name">{v.name}</span>
                          {typeof v.pct === 'number' && <span className={pctCls(v.pct)}>{pctTxt(v.pct)}</span>}
                          <span className="bbt-review-edge-ev">近一月 {v.count} 家覆盖</span>
                          {(v.orgs || []).length > 0 && <span className="bbt-review-broker-orgs">{(v.orgs || []).slice(0, 3).join('·')}</span>}
                        </div>
                        {lt.title && <div className="bbt-review-broker-latest bbt-review-broker-latest--static">
                          {lt.rating && <span className="bbt-review-broker-rating">{lt.rating}</span>}
                          <span className="bbt-review-broker-title">{lt.org ? `${lt.org}：` : ''}{lt.title}</span>
                          {lt.date && <span className="bbt-review-edge-sigwhen">{lt.date}</span>}
                        </div>}
                      </div>
                    );
                  })}
                  <div className="bbt-review-srchint">机构研报来自公开券商研报库，仅供研究参考，不构成投资建议。</div>
                </div>}

                {/* 下一交易日关注 */}
                {nar.tomorrow && <div className="bbt-review-card-sec">
                  <div className="bbt-review-h">🔭 下一交易日</div>
                  {sentences(nar.tomorrow).map((s, k) => <div key={k} className="bbt-review-line">{linkify(s)}</div>)}
                </div>}

                {/* 文中【标题】即本站相关资讯，可直接点开（润物细无声地体现信息价值） */}
                {sources.length > 0 && (nar.market || nar.sectors) && <div className="bbt-review-srchint">文中蓝色【标题】可点开查看本站对应的快讯 / 文章 / 研报</div>}

                <div className="bbt-review-disc">本复盘由数据 + AI 综述生成，面向普通投资者，仅供研究参考，不构成投资建议。</div>
              </>}

              {/* 历史复盘 */}
              {reviewList.length > 0 && <div className="bbt-review-sec">
                <div className="bbt-review-h">历史复盘</div>
                <div className="bbt-review-hist">
                  {reviewList.map((it: any, k: number) => (
                    <button key={k} className={`bbt-review-hist-btn${r && r.date === it.date && (r.session || 'close') === (it.session || 'close') ? ' on' : ''}`} onClick={() => openReview(it.date)} title={`${it.date} ${it.session_label || ''}`}>{it.date.slice(5)} <span className="bbt-review-hist-sess">{(it.session_label || '').replace('复盘', '')}</span></button>
                  ))}
                </div>
              </div>}
            </div>
          </div>
        </div>
        );
      })()}
      {bookmarksOpen && (
        <div className="bbt-doc-overlay" onMouseDown={() => setBookmarksOpen(false)}>
          <div className="bbt-doc bbt-doc--review bbt-doc--bm" role="dialog" aria-modal="true" aria-label="我的收藏" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-doc-bar">
              <span className="bbt-doc-tag bbt-review-tag">⭐ 我的收藏</span>
              <span className="bbt-doc-title">{bookmarkList.length} 条 · 文章 / 研报 / 头条</span>
              <button className="bbt-doc-close" onClick={() => setBookmarksOpen(false)}>✕ 关闭</button>
            </div>
            <div className="bbt-bm-body">
              {bookmarkList.length === 0 ? (
                <div className="bbt-bm-empty">
                  <div className="bbt-bm-empty-ico">⭐</div>
                  <div className="bbt-bm-empty-t">还没有收藏</div>
                  <div className="bbt-bm-empty-s">在文章 / 研报 / 头条右侧点 ☆，随时回看你关注的内容</div>
                </div>
              ) : (
                <div className="bbt-bm-list">
                  {bookmarkList.map(b => {
                    const pseudo = { id: b.message_id, file_id: b.message_id, title: b.title, content: '', url: b.url, topic: b.topic, symbol: b.symbol, severity: 'info', created_at: b.created_at } as any;
                    const isYb = (b.topic || '') === '研报';
                    const isFlash = (b.topic || '') === '快讯';
                    const kindCls = isYb ? 'yb' : (b.topic === '文章' ? 'wz' : 'kx');
                    const when = (b.created_at || '').replace('T', ' ').slice(5, 16);
                    const open = () => { setBookmarksOpen(false); if (isYb) runAiAnalysis(pseudo); else if (isFlash) copyNews(pseudo); else runNewsAi(pseudo); };
                    return (
                      <div key={b.message_id} className="bbt-bm-card" onClick={open} title={isFlash ? '点击复制' : '点开 AI 解读'}>
                        <div className="bbt-bm-card-top">
                          <span className={`bbt-bm-badge c-${kindCls}`}>{b.topic || '资讯'}</span>
                          {when && <span className="bbt-bm-time">收藏于 {when}</span>}
                          <button className="bbt-bm-del" title="取消收藏" onClick={async e => {
                            e.stopPropagation();
                            try { await authService.toggleBookmark({ message_id: b.message_id }); } catch { /* */ }
                            setBookmarkList(prev => prev.filter(x => x.message_id !== b.message_id));
                            setBookmarks(prev => { const n = new Set(prev); n.delete(b.message_id); return n; });
                          }}>✕</button>
                        </div>
                        <div className="bbt-bm-title">{b.title}</div>
                        <div className="bbt-bm-actions">
                          <button className="bbt-nai" onClick={e => { e.stopPropagation(); open(); }}>{isFlash ? '复制' : 'AI 解读'}</button>
                          {b.url && <button className="bbt-nsrc" onClick={e => { e.stopPropagation(); setBookmarksOpen(false); openOriginal(pseudo); }}>原文</button>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {upgradeOpen && (
        <div className="bbt-up-overlay" onMouseDown={e => { if (e.target === e.currentTarget) setUpgradeOpen(false); }}>
          <div className="bbt-up-modal" role="dialog" aria-modal="true" aria-label="开通会员">
            <div className="bbt-up-head"><span className="bbt-up-title">⭐ 开通会员 · 解锁全部</span><button className="bbt-up-x" onClick={() => setUpgradeOpen(false)} aria-label="关闭">✕</button></div>
            <div className="bbt-up-body">
              {upgradeReason && <div className="bbt-up-reason">🔓 {upgradeReason}<span style={{ display: 'block', marginTop: 4, fontWeight: 700, color: '#f59e0b' }}>开通会员即可立即解锁，继续你刚才的操作 →</span></div>}
              {/* 权益按撞墙场景排序：与被拦动作强相关的权益置顶加粗——千人一面的静态清单绑不住当下动机 */}
              {(() => {
                const items: Array<[string, string]> = [
                  ['qa', 'AI 投研问答不限次 + 🔬 深度研判（多空辩论式深度报告）'],
                  ['read', 'AI 多模态解读无限次 · 研报 / 文章随便读'],
                  ['push', '关键资讯比别人早一步 · 微信快讯推送第一时间到'],
                  ['review', '每日 A 股收盘复盘全量解锁 · 看 DeepFocus 提前发现的资讯'],
                  ['sync', '自选云端同步 · 连续看复盘冲会员里程碑'],
                ];
                const r = upgradeReason || '';
                const hot = r.includes('问答') || r.includes('研判') ? 'qa'
                  : r.includes('解读') ? 'read'
                  : r.includes('推送') || r.includes('快讯') ? 'push'
                  : r.includes('复盘') ? 'review' : '';
                const ordered = hot ? [...items.filter(i => i[0] === hot), ...items.filter(i => i[0] !== hot)] : items;
                return (
                  <ul className="bbt-up-benefits">
                    {ordered.slice(0, 4).map(([k, txt], i) => <li key={k} style={hot && i === 0 ? { fontWeight: 700 } : undefined}>{txt}</li>)}
                  </ul>
                );
              })()}
            </div>
            <div className="bbt-up-foot">
              {/* 高意向时刻只留一个主 CTA：邀请入口顶栏常驻不受影响，但在撞墙弹窗里给"免费替代品"=自相蚕食
                  （生产真值：自然转化是邀请的 9 倍、邀请流量 68% 低意向） */}
              {/* 「自助下单秒开通」只在配了发卡店铺时承诺——承诺与体验落空=退款纠纷种子 */}
              <button className="bbt-up-buy" onClick={() => { setUpgradeOpen(false); openBuy(); }}>{payCfg?.storefront_url ? '💎 立即开通 · 支持自助下单秒开通' : '💎 立即开通会员'}</button>
              <button className="bbt-up-contact" onClick={() => { setUpgradeOpen(false); openSupport(); }}>💬 还有疑问？问问管理员</button>
              <div style={{ marginTop: 6, textAlign: 'center', fontSize: 12, opacity: 0.65 }}>
                <span style={{ cursor: 'pointer', textDecoration: 'underline' }}
                  onClick={() => { setUpgradeOpen(false); requireLogin(openReferral, '邀请得会员'); }}>不想付费？邀请好友也能得会员</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {aiOpen && (() => {
        const sugg = ['今天 A 股大盘怎么样？', '贵州茅台现在估值贵不贵？', '我们最近覆盖了哪些固态电池/AI 算力的资讯？'];
        // 深度研判从 1 人白名单放开到会员（后端 _require_deep_user 硬门：会员 3 次/天+当日跨用户缓存；白名单不限次+iFinD 取证）
        const canDeep = isMemberVip || isAdmin || IFIND_USERS.has(authUser || '');
        const dv = deepTask?.result;
        const evid = deepTask?.stages.find(s => s.key === 'evidence');
        const sBull = deepTask?.stages.find(s => s.key === 'bull');
        const sBear = deepTask?.stages.find(s => s.key === 'bear');
        const sRisk = deepTask?.stages.find(s => s.key === 'risk');
        const dirCls = (d: string) => (d.indexOf('看多') >= 0 ? 'up' : d.indexOf('看空') >= 0 ? 'down' : 'flat');
        return (
        <div className="bbt-doc-overlay" onMouseDown={() => setAiOpen(false)}>
          <div className="bbt-doc bbt-doc--review bbt-ai-chat" role="dialog" aria-modal="true" aria-label="AI 投研助手" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-doc-bar">
              <span className="bbt-doc-tag bbt-review-tag">🤖 AI 投研助手</span>
              <span className="bbt-doc-title">{deepMode ? '多智能体深度研判 · 取证→多空辩论→风控→裁决' : '自动调行情 / 估值 + 检索我们的快讯·研报·复盘'}</span>
              <span className="bbt-review-prov">内测</span>
              <button className="bbt-doc-close" onClick={() => setAiOpen(false)} aria-label="关闭">✕ 关闭</button>
            </div>
            <div className="bbt-review-body">
              {canDeep ? (
                <div className="bbt-deep-seg">
                  <button className={'bbt-deep-seg-btn' + (!deepMode ? ' on' : '')} onClick={() => setDeepMode(false)}>⚡ 快速问答</button>
                  <button className={'bbt-deep-seg-btn' + (deepMode ? ' on' : '')} onClick={enterDeepMode}>🔬 深度研判<span className="bbt-deep-seg-beta">会员</span></button>
                </div>
              ) : (
                /* 可见的锁比隐形的墙更造欲望：非会员看得到深度研判，点击进升级弹窗 */
                <div className="bbt-deep-seg">
                  <button className="bbt-deep-seg-btn on">⚡ 快速问答</button>
                  <button className="bbt-deep-seg-btn" style={{ opacity: 0.55 }}
                    onClick={() => { setUpgradeReason('🔬 深度研判（取证→多空辩论→风控→投委会裁决的 AI 深度报告）是会员专属功能，开通即用。'); setUpgradeOpen(true); }}>
                    🔬 深度研判 <span className="bbt-deep-seg-beta">🔒会员</span></button>
                </div>
              )}
              {deepMode ? (
              <>
              <div className="bbt-ifind-search">
                <input className="bbt-res-input" value={deepSymbol} autoFocus placeholder="输入股票代码，如 600519 / 300750"
                  onChange={e => setDeepSymbol(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') startDeep(); }} />
                <button className="bbt-review-act bbt-review-act--hero" onClick={() => startDeep(false)} disabled={deepBusy}>{deepBusy ? '研判中…' : '🔬 发起研判'}</button>
              </div>
              <div className="bbt-deep-hint">多智能体辩论：取证 → 多空立论 → 交叉反驳 → 风控 → 投委会裁决（约 1–2 分钟，A股最佳）</div>
              {deepTask && (
                <div className="bbt-deep-steps">
                  {deepTask.stages.map(s => (
                    <div key={s.key} className={`bbt-deep-step bbt-deep-step--${s.status}`}>
                      <span className="bbt-deep-dot" /><span className="bbt-deep-step-lbl">{s.label}</span>
                    </div>
                  ))}
                </div>
              )}
              {dv && (
                <div className="bbt-deep-verdict">
                  <div className="bbt-deep-vhead">
                    <span className={`bbt-deep-dir bbt-deep-dir--${dirCls(dv.direction)}`}>{dv.direction}</span>
                    {typeof dv.confidence === 'number' && <span className="bbt-deep-conf">置信 {Math.round((dv.confidence || 0) * 100)}%</span>}
                    {deepTask?.ifind_used && <span className="bbt-deep-ifind">同花顺 iFinD 实时</span>}
                  </div>
                  {dv.thesis && <div className="bbt-deep-thesis"><Markdownlite text={dv.thesis} /></div>}
                  {dv.core_evidence && dv.core_evidence.length > 0 && <><div className="bbt-ai-h">🔑 核心依据</div><ul className="bbt-ai-list">{dv.core_evidence.map((c, i) => <li key={i}>{c.point}{c.evidence_ref && <span className="bbt-deep-ref">{c.evidence_ref}</span>}</li>)}</ul></>}
                  {dv.key_risks && dv.key_risks.length > 0 && <><div className="bbt-ai-h bbt-ai-h--bear">⚠️ 关键风险</div><ul className="bbt-ai-list bbt-ai-risk">{dv.key_risks.map((r, i) => <li key={i}>{r.risk}{r.severity && <span className="bbt-deep-sev">{r.severity}</span>}</li>)}</ul></>}
                  {dv.watch_levels && (dv.watch_levels.support || dv.watch_levels.resistance || dv.watch_levels.note) && (
                    <div className="bbt-deep-levels">
                      <div className="bbt-ai-h">📐 观察位（非买卖指令）</div>
                      <div className="bbt-deep-lvs">
                        {dv.watch_levels.support && <span className="bbt-deep-lv">支撑 {dv.watch_levels.support}</span>}
                        {dv.watch_levels.resistance && <span className="bbt-deep-lv">压力 {dv.watch_levels.resistance}</span>}
                      </div>
                      {dv.watch_levels.note && <div className="bbt-deep-note">{dv.watch_levels.note}</div>}
                    </div>
                  )}
                  {dv.debate_synthesis && <><div className="bbt-ai-h">⚖️ 多空交锋</div><div className="bbt-ai-sum"><Markdownlite text={dv.debate_synthesis} /></div></>}
                  {dv.data_quality && ((dv.data_quality.gaps && dv.data_quality.gaps.length > 0) || (dv.data_quality.degraded_sources && dv.data_quality.degraded_sources.length > 0)) && (
                    <div className="bbt-deep-dq">⚠ 部分数据缺口/降级：{[...(dv.data_quality.gaps || []), ...(dv.data_quality.degraded_sources || [])].join('、')}</div>
                  )}
                  <div className="bbt-ai-foot">{dv.disclaimer || '本研判由 AI 多角色综合生成，仅供研究参考，不构成投资建议。'}</div>
                  <div className="bbt-share-row">
                    <button className="bbt-share-btn" onClick={shareDeepImage} disabled={aiShareBusy}>{aiShareBusy ? '生成中…' : '🖼 复制为图片'}</button>
                    <button className="bbt-share-btn" onClick={copyDeepText}>⧉ 复制为文字</button>
                    <button className="bbt-share-btn" onClick={() => startDeep(true)} disabled={deepBusy} title="数据有更新时强制重新研判（消耗算力）">🔄 重新研判</button>
                  </div>
                </div>
              )}
              {evid && evid.output && (
                <div className="bbt-deep-stage">
                  <div className="bbt-ai-h">📋 取证</div>
                  {evid.output.fundamental && <div className="bbt-ai-sum"><Markdownlite text={evid.output.fundamental} /></div>}
                  {evid.output.context && <div className="bbt-ai-sum"><Markdownlite text={evid.output.context} /></div>}
                  <ToolTrace trace={evid.output.tool_trace} label={`取证调用了 ${Array.isArray(evid.output.tool_trace) ? evid.output.tool_trace.length : 0} 项数据 / 材料`} />
                </div>
              )}
              {sBull && sBull.output && <div className="bbt-deep-stage"><div className="bbt-ai-h bbt-ai-h--bull">📈 多头</div>{sBull.output.thesis && <div className="bbt-ai-sum">{sBull.output.thesis}</div>}{sBull.output.key_args && sBull.output.key_args.length > 0 && <ul className="bbt-ai-list bbt-ai-bull">{sBull.output.key_args.map((k: any, i: number) => <li key={i}>{k.point}</li>)}</ul>}</div>}
              {sBear && sBear.output && <div className="bbt-deep-stage"><div className="bbt-ai-h bbt-ai-h--bear">📉 空头</div>{sBear.output.thesis && <div className="bbt-ai-sum">{sBear.output.thesis}</div>}{sBear.output.key_args && sBear.output.key_args.length > 0 && <ul className="bbt-ai-list bbt-ai-risk">{sBear.output.key_args.map((k: any, i: number) => <li key={i}>{k.point}</li>)}</ul>}</div>}
              {sRisk && sRisk.output && sRisk.output.key_risks && sRisk.output.key_risks.length > 0 && <div className="bbt-deep-stage"><div className="bbt-ai-h">🛡 风控</div><ul className="bbt-ai-list bbt-ai-risk">{sRisk.output.key_risks.map((r: any, i: number) => <li key={i}>{r.risk}{r.severity && <span className="bbt-deep-sev">{r.severity}</span>}</li>)}</ul></div>}
              {deepErr && <div className="bbt-empty">{deepErr}</div>}
              {deepBusy && !dv && <div className="bbt-empty">🔬 多智能体研判进行中…可关闭弹窗，结果会保留约 30 分钟</div>}
              <div className="bbt-ifind-foot">多角色 AI 综合研判（含 iFinD 实时数据）· 仅供研究参考，不构成投资建议</div>
              </>
              ) : (
              <>
              <div className="bbt-ifind-search">
                <input className="bbt-res-input" value={aiInput} autoFocus placeholder="问点投研问题，如『今天大盘怎么样』『茅台估值贵吗』…"
                  onChange={e => setAiInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') askAi(); }} />
                <button className="bbt-review-act bbt-review-act--hero" onClick={() => askAi()} disabled={aiBusy}>{aiBusy ? '思考中…' : '提问'}</button>
              </div>
              {/* 额度可见化：非会员看得到"今日还剩几次"，撞墙从突袭变成预期内的升级决策 */}
              {!isMemberVip && !isAdmin && aiQuotaLeft !== null && (
                <div className="bbt-deep-hint" style={{ marginTop: 4 }}>
                  今日免费问答剩余 <b>{aiQuotaLeft}</b> 次{aiQuotaLeft === 1 ? ' · 最后 1 次，开通会员不限次' : ''}
                </div>
              )}
              {!aiAnswer && !aiBusy && !aiTools.length && !aiErr && (
                <div className="bbt-ai-sugg">{sugg.map(s => <button key={s} className="bbt-ai-sugg-item" onClick={() => { setAiInput(s); askAi(s); }}>{s}</button>)}</div>
              )}
              {aiQuestion && (aiAnswer || aiBusy || aiErr) && <div className="bbt-ai-q"><span className="bbt-ai-q-tag">问</span>{aiQuestion}</div>}
              {aiBusy && <AiFakeProgress />}
              {aiErr && <div className="bbt-empty">{aiErr}</div>}
              {aiAnswer && <div className="bbt-ai-answer"><Markdownlite text={aiAnswer} /></div>}
              {!aiBusy && aiTools.length > 0 && <ToolTrace trace={aiTools} />}
              {aiAnswer && (
                <div className="bbt-share-row">
                  <button className="bbt-share-btn" onClick={shareQaImage} disabled={aiShareBusy}>{aiShareBusy ? '生成中…' : '🖼 复制为图片'}</button>
                  <button className="bbt-share-btn" onClick={copyQaText}>⧉ 复制为文字</button>
                  {/* 👍👎 反馈闭环：答错了平台才知道；踩会作废共享缓存，坏答案不再被复读 */}
                  <button className="bbt-share-btn" disabled={!!aiFeedback}
                    onClick={() => { setAiFeedback('up'); sendAgentFeedback(aiQuestion, aiAnswer, 'up', aiTools); }}>
                    {aiFeedback === 'up' ? '👍 已赞' : '👍'}</button>
                  <button className="bbt-share-btn" disabled={!!aiFeedback}
                    onClick={() => { setAiFeedback('down'); sendAgentFeedback(aiQuestion, aiAnswer, 'down', aiTools); showToast('已收到反馈，我们会改进这个答案'); }}>
                    {aiFeedback === 'down' ? '👎 已踩' : '👎'}</button>
                </div>
              )}
              {/* 答案后的确定性追问 chips：把单发问答变成多轮研究会话 */}
              {aiAnswer && !aiBusy && aiSugg.length > 0 && (
                <div className="bbt-ai-sugg">{aiSugg.map(s => <button key={s} className="bbt-ai-sugg-item" onClick={() => { setAiInput(s); askAi(s); }}>{s}</button>)}</div>
              )}
              {/* 成功时刻升级卡：刚被答爽、额度同时用尽的那一刻付费意愿最高（RevenueCat 实证），比被拒时刻转化高 */}
              {aiAnswer && !aiBusy && !isMemberVip && !isAdmin && aiQuotaLeft === 0 && (
                <div className="bbt-deep-hint" style={{ marginTop: 8, padding: '10px 12px', border: '1px solid var(--line-2,#233039)', borderRadius: 8 }}>
                  今天 {authUser ? '10' : '1'} 次免费问答已用完——刚才这几问 AI 帮你查了真实行情与研报。
                  <button className="bbt-review-act" style={{ marginLeft: 8 }} onClick={() => { setUpgradeReason('AI 问答不限次 + 深度研判，会员专享'); setUpgradeOpen(true); }}>💎 开通会员不限次</button>
                </div>
              )}
              <div className="bbt-ifind-foot">内容由 AI 生成 · 自动调用平台数据综合作答 · 仅供研究参考，不构成投资建议</div>
              </>
              )}
            </div>
          </div>
        </div>
        );
      })()}

      {ifindOpen && (
        <div className="bbt-doc-overlay" onMouseDown={() => setIfindOpen(false)}>
          <div className="bbt-doc bbt-doc--review bbt-ifind" role="dialog" aria-modal="true" aria-label="iFinD 专业数据" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-doc-bar">
              <span className="bbt-doc-tag bbt-review-tag">📡 iFinD 专业数据</span>
              <span className="bbt-doc-title">同花顺 · A股实时行情 + 基本面</span>
              <span className="bbt-review-prov">仅本账号</span>
              <button className="bbt-doc-close" onClick={() => setIfindOpen(false)} aria-label="关闭">✕ 关闭</button>
            </div>
            <div className="bbt-review-body">
              <div className="bbt-ifind-search">
                <input className="bbt-res-input" value={ifindInput} placeholder="A股代码，逗号分隔（如 600519,300750，裸6位自动补后缀）"
                  onChange={e => setIfindInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') runIfind(ifindInput); }} />
                <button className="bbt-review-act bbt-review-act--hero" onClick={() => runIfind(ifindInput)} disabled={ifindBusy}>{ifindBusy ? '查询中…' : '查询'}</button>
              </div>
              {ifindErr && <div className="bbt-empty">{ifindErr}</div>}
              {!ifindErr && ifindRows.length > 0 && (
                <div className="bbt-ifind-grid">
                  {ifindRows.map(r => {
                    const up = (r.changeRatio || 0) >= 0;
                    const fmt = (v: any, d = 2) => typeof v === 'number' ? v.toFixed(d) : '—';
                    const cap = typeof r.totalCapital === 'number' ? (r.totalCapital >= 1e8 ? (r.totalCapital / 1e8).toFixed(0) + '亿' : (r.totalCapital / 1e4).toFixed(0) + '万') : '—';
                    const amt = typeof r.amount === 'number' ? (r.amount / 1e8).toFixed(2) + '亿' : '—';
                    return (
                      <div className="bbt-ifind-card" key={r.code}>
                        <div className="bbt-ifind-hd"><b>{r.code}</b><span className="bbt-dim">{(r.time || '').slice(11, 16)}</span></div>
                        <div className={'bbt-ifind-px ' + (up ? 'bbt-up' : 'bbt-down')}>{fmt(r.latest)} <span>{up ? '+' : ''}{fmt(r.changeRatio)}%</span></div>
                        <div className="bbt-ifind-rows">
                          <span>PE(TTM) <b>{fmt(r.pe_ttm)}</b></span>
                          <span>PB <b>{fmt(r.pb)}</b></span>
                          <span>总市值 <b>{cap}</b></span>
                          <span>换手 <b>{fmt(r.turnoverRatio)}%</b></span>
                          <span>成交额 <b>{amt}</b></span>
                          <span>振幅 <b>{typeof r.high === 'number' && typeof r.low === 'number' && typeof r.latest === 'number' && r.latest ? (((r.high - r.low) / r.latest) * 100).toFixed(2) + '%' : '—'}</b></span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="bbt-ifind-foot">数据来源 同花顺 iFinD · 实时 A股行情与基本面 · 仅供研究参考，不构成投资建议</div>
            </div>
          </div>
        </div>
      )}

      {buyOpen && (
        <div className="bbt-doc-overlay" onMouseDown={() => closeBuy('点遮罩关闭')}>
          <div className="bbt-buy" role="dialog" aria-modal="true" aria-label="开通 / 续费会员" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-redeem-head">
              <span className="bbt-redeem-title">💎 开通 / 续费会员</span>
              <button className="bbt-support-x" onClick={() => closeBuy('点✕关闭')}>✕</button>
            </div>
            {(() => {  // 创始会员价横幅：最低折扣 + 「限时一周」倒计时（活动期内）
              const discs = (payCfg?.packages || []).filter(p => p.orig && p.orig > p.price).map(p => p.price / (p.orig as number) * 10);
              if (!discs.length) return null;
              const zhe = String(parseFloat(Math.min(...discs).toFixed(1)));  // 4.0→"4"，4.3→"4.3"
              if (promoLeftMs > 0) {
                const s = Math.floor(promoLeftMs / 1000);
                const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
                const p2 = (n: number) => String(n).padStart(2, '0');
                return <div className="bbt-buy-promo bbt-buy-promo--urgent">🔥 新人限时福利 · 低至 <b>{zhe} 折</b> · 限时<span className="bbt-buy-cd">⏳ 仅剩 {d}天 {p2(h)}:{p2(m)}:{p2(sec)}</span></div>;
              }
              return <div className="bbt-buy-promo">🎁 新人福利 · 低至 <b>{zhe} 折</b></div>;
            })()}
            {isNewUser && <div className="bbt-buy-newuser">🎁 新人专享（注册前 3 天）：年卡额外送 <b>1 个月</b> · 半年卡额外送 <b>15 天</b></div>}
            <div className="bbt-buy-pkgs">
              {(() => {
                const pkgs = payCfg?.packages || [];
                // 每天均价最低的套餐（通常是年卡）标绿强调——"每天不到一块钱"的心理锚点
                const bestKey = pkgs.length ? pkgs.reduce((a, b) => (a.price / a.days <= b.price / b.days ? a : b)).key : '';
                return pkgs.map(p => {
                  const hasOff = !!(p.orig && p.orig > p.price);
                  const offPct = hasOff ? Math.round((1 - p.price / (p.orig as number)) * 100) : 0;
                  const perDay = (p.price / p.days).toFixed(2);
                  const bonus = isNewUser ? NEW_USER_BONUS[p.key] : undefined;  // 新人加赠
                  return (
                    <button key={p.key} className={'bbt-buy-pkg' + (buyPkg === p.key ? ' on' : '')} onClick={() => { setBuyPkg(p.key); logAct('buy_pkg_select', `${p.label} ¥${p.price}`); }}>
                      {hasOff && <span className="bbt-buy-pkg-off">-{offPct}%</span>}
                      <span className="bbt-buy-pkg-label">{p.label}</span>
                      {hasOff && <span className="bbt-buy-pkg-orig">定价 ¥{p.orig}</span>}
                      <span className="bbt-buy-pkg-price">¥{p.price}</span>
                      <span className="bbt-buy-pkg-days">{p.days} 天 · <em className={'bbt-buy-pkg-day' + (p.key === bestKey ? ' best' : '')}>¥{perDay}/天</em></span>
                      {bonus && <span className="bbt-buy-pkg-bonus">🎁 {bonus.label}</span>}
                    </button>
                  );
                });
              })()}
            </div>
            {buySel && (
              <div className="bbt-buy-amount">
                <span className="bbt-buy-amount-lbl">本单应付</span>
                <span className="bbt-buy-amount-val">¥{buySel.price}</span>
                <span className="bbt-buy-amount-sub">{buySel.label} · {buySel.days} 天{selBonus ? ` ＋赠 ${selBonus.days} 天` : ''}</span>
                {!!(buySel.orig && buySel.orig > buySel.price) && <span className="bbt-buy-amount-save">较定价省 ¥{buySel.orig - buySel.price}</span>}
                {selBonus && <span className="bbt-buy-amount-bonus">🎁 新人加赠 {selBonus.days} 天，共 {buySel.days + selBonus.days} 天</span>}
              </div>
            )}
            {payCfg?.storefront_url && (
              // 自助秒发卡密：付完店铺自动发卡密 → 回来「兑换码」秒开会员，全程零人工（唯一不依赖管理员在线的即时成交路径）
              <button
                className="bbt-buy-storefront"
                style={{ display: 'block', width: '100%', margin: '10px 0 2px', padding: '11px 12px', borderRadius: 8, border: '1px solid rgba(245,158,11,0.55)', background: 'rgba(245,158,11,0.12)', color: '#f59e0b', fontWeight: 700, fontSize: 14, cursor: 'pointer', textAlign: 'center' }}
                onClick={() => { const u = payCfg?.storefront_url; if (!u) return; logAct('buy_storefront', '自助秒发卡密'); try { window.open(u, '_blank', 'noopener'); } catch { /* 忽略弹窗拦截 */ } }}>
                ⚡ 想立刻开通？店铺自助下单 · 秒发卡密 → 回来用「兑换码」即刻开会员
              </button>
            )}
            {!(payCfg?.wechat || payCfg?.alipay) ? (
              // 未配置收款码：直接走私信咨询
              <>
                <div className="bbt-buy-noqr">收款码暂未配置，请点下方「私信管理员」咨询购买。</div>
                <button className="bbt-redeem-go" onClick={buyContactAdmin}>💬 私信管理员咨询购买</button>
              </>
            ) : !buyPaid ? (
              // 第一步：扫码付款。只讲怎么付，不提"找管理员开通"，避免劝退
              <>
                <div className="bbt-buy-qrs">
                  {payCfg?.wechat && <div className="bbt-buy-qr"><div className="bbt-buy-qr-card"><img src={buyQr.wechatSrc} alt="微信收款码" /></div><span>微信扫码{buySel ? (buyQr.wechatFixed ? ` · 已锁定 ¥${buySel.price}` : ` · 手动输入 ¥${buySel.price}`) : ''}</span></div>}
                  {payCfg?.alipay && <div className="bbt-buy-qr"><div className="bbt-buy-qr-card"><img src={buyQr.alipaySrc} alt="支付宝收款码" /></div><span>支付宝扫码{buySel ? (buyQr.alipayFixed ? ` · 已锁定 ¥${buySel.price}` : ` · 手动输入 ¥${buySel.price}`) : ''}</span></div>}
                </div>
                {/* 第一步只讲怎么付款，不引用服务器 note（曾因 note 含"找管理员"而泄底），固定纯付款文案 */}
                <div className="bbt-buy-note">{buySel
                  ? (buyQr.anyFixed
                    ? `扫码金额已固定为 ¥${buySel.price}，直接付款即可；付款时请在备注里写上你的用户名${authUser ? `「${authUser}」` : ''}。`
                    : `个人收款码不含金额：扫码后请手动输入 ¥${buySel.price}，并在付款备注里写上你的用户名${authUser ? `「${authUser}」` : ''}。`)
                  : '扫码支付对应金额，付款时请在备注里写上你的用户名。'}</div>
                {authUser && (
                  <button
                    type="button"
                    onClick={() => { try { navigator.clipboard?.writeText?.(authUser); showToast(`📋 已复制用户名「${authUser}」`); } catch { /* 忽略剪贴板失败 */ } }}
                    style={{ display: 'inline-block', margin: '0 0 8px', padding: '6px 12px', borderRadius: 6, border: '1px solid rgba(245,158,11,0.45)', background: 'transparent', color: '#f59e0b', fontSize: 13, cursor: 'pointer' }}>
                    📋 复制用户名「{authUser}」→ 粘到付款备注
                  </button>
                )}
                <button className="bbt-redeem-go" onClick={markPaid}>✅ 我已完成付款</button>
              </>
            ) : (
              // 第二步：付款后才揭示——发凭证给管理员核对开通
              <div className="bbt-buy-paid">
                <div className="bbt-buy-paid-msg">
                  🎉 收到你的付款！<b>最后一步</b>：把付款备注 / 截图发给管理员核对，一般<b>几分钟内</b>为你开通会员。
                </div>
                <button className="bbt-redeem-go" onClick={buyContactAdmin}>📩 发送凭证 · 通知管理员开通</button>
                <button className="bbt-buy-paid-back" onClick={() => setBuyPaid(false)}>← 还没付？返回扫码</button>
              </div>
            )}
            {trialClaimable && (
              <button className="bbt-buy-trial" onClick={() => { buyOutcomeRef.current = 'trial'; setBuyOpen(false); onClaimTrial(); }}>
                🎁 还在犹豫？先免费领 <b>3 天体验会员</b>，全部功能随便用，满意再来
              </button>
            )}
            <div className="bbt-buy-trust">
              <span className="bbt-buy-disclaim">内容仅供研究参考，不构成投资建议；会员为信息服务，不承诺任何收益。</span>
            </div>
            <button className="bbt-buy-redeem" onClick={() => { closeBuy('转去兑换码'); setRedeemInput(''); setRedeemOpen(true); }}>🎟️ 已有兑换码？点这里直接兑换</button>
          </div>
        </div>
      )}

      {redeemOpen && (
        <div className="bbt-doc-overlay" onMouseDown={() => setRedeemOpen(false)}>
          <div className="bbt-redeem" role="dialog" aria-modal="true" aria-label="兑换会员码" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-redeem-head">
              <span className="bbt-redeem-title">🎟️ 兑换会员码</span>
              <button className="bbt-support-x" onClick={() => setRedeemOpen(false)}>✕</button>
            </div>
            <div className="bbt-redeem-sub">输入购买得到的兑换码，立即开通对应会员。还没有？点「💬 联系管理员」咨询购买。</div>
            <input className="bbt-redeem-input" value={redeemInput} autoFocus
              placeholder="如 ABCD-EFGH-JKMN（大小写、连字符均可）"
              onChange={e => setRedeemInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') submitRedeem(); }} />
            <button className="bbt-redeem-go" onClick={submitRedeem} disabled={redeemBusy || !redeemInput.trim()}>{redeemBusy ? '兑换中…' : '立即兑换'}</button>
          </div>
        </div>
      )}

      {supportOpen && (
        <div className="bbt-doc-overlay" onMouseDown={() => setSupportOpen(false)}>
          <div className="bbt-support" role="dialog" aria-modal="true" aria-label="联系管理员" onMouseDown={e => e.stopPropagation()}>
            <div className="bbt-support-head">
              <span className="bbt-support-title">💬 联系管理员</span>
              <button className="bbt-support-x" onClick={() => setSupportOpen(false)}>✕</button>
            </div>
            <div className="bbt-support-sub">有问题、建议或想开通会员？给管理员留言，管理员会在后台看到并回复你。</div>
            <div className="bbt-support-msgs">
              {supportMsgs.length === 0
                ? <div className="bbt-support-empty">还没有消息，发第一条吧 👋</div>
                : supportMsgs.map(m => (
                    <div key={m.id} className={'bbt-support-bubble ' + (m.sender === 'user' ? 'me' : 'admin')}>
                      <div className="bbt-support-bubble-c">{m.content}</div>
                      <div className="bbt-support-bubble-t">{m.sender === 'admin' ? '管理员 · ' : ''}{fmtMsgTime(m.created_at)}</div>
                    </div>
                  ))}
            </div>
            <div className="bbt-support-input">
              <textarea value={supportText} placeholder="输入消息…（Ctrl/⌘ + Enter 发送）" rows={2}
                onChange={e => setSupportText(e.target.value)}
                onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); sendSupportMsg(); } }} />
              <button onClick={sendSupportMsg} disabled={supportSending || !supportText.trim()}>{supportSending ? '…' : '发送'}</button>
            </div>
          </div>
        </div>
      )}

      {inviteOpen && (() => {
        const site = (typeof window !== 'undefined' && window.location.origin) || 'https://daocaijing.com';
        const code = inviteData?.code || '';
        const link = code ? `${site}/?ref=${code}` : '';
        const copy = async (text: string, tag: string) => {
          try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
            else { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); }
            setInviteCopied(tag); window.setTimeout(() => setInviteCopied(''), 1600);
          } catch { showToast('⚠️ 复制失败，请长按选择'); }
        };
        return (
          <div className="bbt-auth-backdrop" onClick={() => setInviteOpen(false)}>
            <div className="bbt-invite" onClick={e => e.stopPropagation()}>
              <div className="bbt-auth-head"><span className="bbt-auth-key">DEEPFOCUS</span><span className="bbt-auth-amber">🎁 我的邀请</span><button type="button" className="bbt-auth-x" onClick={() => setInviteOpen(false)}>✕</button></div>
              <div className="bbt-auth-sub">把链接/邀请码发给好友,Ta 注册时填写即与你建立邀请关系</div>
              {!inviteData ? <div className="bbt-empty">加载中…</div> : <>
                <div className="bbt-invite-row"><span className="bbt-invite-lbl">邀请码</span><b className="bbt-invite-code">{code}</b><button className="bbt-invite-btn" onClick={() => copy(code, 'code')}>{inviteCopied === 'code' ? '✓ 已复制' : '复制'}</button></div>
                <div className="bbt-invite-row"><span className="bbt-invite-lbl">邀请链接</span><span className="bbt-invite-link">{link}</span><button className="bbt-invite-btn" onClick={() => copy(link, 'link')}>{inviteCopied === 'link' ? '✓ 已复制' : '复制'}</button></div>
                <div className="bbt-invite-stat">已成功邀请 <b>{inviteData.invited_count}</b> 人</div>
                {inviteData.invited.length > 0 && (
                  <div className="bbt-invite-list">{inviteData.invited.slice(0, 12).map((u, i) => <div key={i} className="bbt-invite-li"><span>{u.username}</span><span className="bbt-mute">{(u.created_at || '').replace('T', ' ').slice(0, 16)}</span></div>)}</div>
                )}
              </>}
            </div>
          </div>
        );
      })()}
    </div>
  );
};

export default FinancialTerminal;
