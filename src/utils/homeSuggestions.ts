import type { Stock } from '../types';
import { listDecisions, decisionActionLabel } from './decisionJournal';
import { memoryPreview } from './conversationMemory';

/**
 * 个性化首页建议 —— AI 原生「记得住你」的入口体验。
 * 从你的自选股 / 历史判断 / 最近讨论生成贴身建议，而非一成不变的示例。
 * 全部确定性、客户端；数据不足时回退到通用示例，永远凑满数量。
 */

const STATIC_FALLBACK = [
  '分析 NVDA 的基本面和估值',
  '今天 A 股怎么看？',
  '当前市场环境下的配置建议',
  '帮我梳理特斯拉最新的财报要点',
];

function fmtPct(p?: number | null): string {
  if (typeof p !== 'number') {
    return '';
  }
  return `${p >= 0 ? '+' : ''}${p.toFixed(2)}%`;
}

/**
 * 生成 4 条建议：优先「复盘历史判断」「自选股异动」「延续最近讨论」，再用通用示例补齐。
 */
export function buildHomeSuggestions(stocks: Stock[], now: number): string[] {
  const out: string[] = [];
  const pushUnique = (s: string) => {
    const t = s.trim();
    if (t && !out.includes(t) && out.length < 4) {
      out.push(t);
    }
  };

  // 1) 复盘最近一次判断（自我进化：回看自己的 call 是否站得住）。
  const decisions = listDecisions();
  if (decisions[0]) {
    const d = decisions[0];
    pushUnique(`复盘 ${d.symbol}：我之前判断「${decisionActionLabel(d.action)}」，现在还成立吗？`);
  }

  // 2) 自选股里异动最大的标的（取 |涨跌幅| 最大）。
  const movers = stocks
    .filter(s => typeof s.changePercent === 'number')
    .sort((a, b) => Math.abs(b.changePercent || 0) - Math.abs(a.changePercent || 0));
  if (movers[0]) {
    const m = movers[0];
    pushUnique(`${m.name || m.symbol}（${fmtPct(m.changePercent)}）今天怎么看？`);
  }

  // 3) 延续最近一次有价值的讨论（自我记忆）。
  const recentMemory = memoryPreview(now, 1)[0];
  if (recentMemory) {
    pushUnique(`继续聊：${recentMemory.title}`);
  }

  // 4) 自选股组合视角（有自选股时）。
  if (stocks.length >= 2) {
    pushUnique('我的自选股现在整体该怎么配置？');
  }

  // 用通用示例补齐到 4 条。
  for (const s of STATIC_FALLBACK) {
    pushUnique(s);
  }
  return out.slice(0, 4);
}
