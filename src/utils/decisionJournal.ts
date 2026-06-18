/**
 * 决策日志（AI 原生「自我进化」基础）。
 *
 * Agent 每次给出明确判断（深研分析评分 / 分析师结论）就自动记账：标的、动作、置信度、论点、时间。
 * 之后可标注兑现情况（对/错，或由价格自动判定），系统据此算「置信度校准」——
 * 自评置信度 vs 实际命中率，识别系统性偏差（过度自信/过度保守），并把
 * 「你对该标的的历史判断 + 校准」召回注入 agent 上下文 → 让模型自我感知、自我纠偏。
 *
 * 与「记忆」(自由对话召回)互补：这里是结构化的「判断 + 兑现」轨迹。
 * 全部客户端、确定性、无第三方依赖；与行情可用性解耦（兑现可由用户标注，价格仅作可选自动判定）。
 */

export type DecisionAction = 'strong_buy' | 'buy' | 'hold' | 'reduce' | 'sell' | 'strong_sell';
export type DecisionOutcome = 'pending' | 'correct' | 'wrong';

export interface DecisionRecord {
  id: string;
  symbol: string;
  action: DecisionAction;
  /** 自评置信度 0–1。 */
  confidence: number;
  thesis: string;
  loggedAt: number;
  /** 记录时价格（可得则填，用于价格自动判定兑现）。 */
  priceAtLog?: number | null;
  outcome: DecisionOutcome;
  /** 兑现标注时间。 */
  resolvedAt?: number;
  /** 兑现判定来源：price=价格自动判定，manual=人工标注。 */
  resolvedBy?: 'price' | 'manual';
}

export interface Calibration {
  total: number;
  resolved: number;
  correct: number;
  /** 已兑现里的命中率 0–1。 */
  hitRate: number | null;
  /** 已兑现判断的平均自评置信度 0–1。 */
  avgConfidence: number | null;
  /** 校准缺口 = 平均置信度 − 命中率（>0 偏过度自信，<0 偏保守）。 */
  gap: number | null;
  tendency: '校准良好' | '偏过度自信' | '偏过度保守' | '样本不足';
}

export interface DecisionRecall {
  past: Array<{ action: DecisionAction; confidence: number; thesis: string; when: string; outcome: DecisionOutcome }>;
  calibration: Calibration;
}

const JOURNAL_STORAGE_KEY = 'dfx_decision_journal_v1';
const MAX_RECORDS = 200;

const ACTION_LABELS: Record<DecisionAction, string> = {
  strong_buy: '强烈看好', buy: '看好', hold: '中性', reduce: '偏谨慎', sell: '看淡', strong_sell: '强烈看淡',
};
export function decisionActionLabel(a: DecisionAction): string {
  return ACTION_LABELS[a] || a;
}

function safeLoad(): DecisionRecord[] {
  try {
    const raw = window.localStorage.getItem(JOURNAL_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function safeSave(list: DecisionRecord[]): void {
  try {
    window.localStorage.setItem(JOURNAL_STORAGE_KEY, JSON.stringify(list.slice(0, MAX_RECORDS)));
  } catch {
    // 配额/隐私模式失败 → 静默放弃。
  }
}

function relativeWhen(ts: number, now: number): string {
  const diff = Math.max(0, now - ts);
  const day = 24 * 60 * 60 * 1000;
  if (diff < 60 * 60 * 1000) return `${Math.max(1, Math.floor(diff / (60 * 1000)))} 分钟前`;
  if (diff < day) return `${Math.floor(diff / (60 * 60 * 1000))} 小时前`;
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`;
  return `${Math.floor(diff / (30 * day))} 个月前`;
}

function normalizeAction(raw: string): DecisionAction {
  const a = (raw || '').toLowerCase().trim();
  if (a in ACTION_LABELS) return a as DecisionAction;
  // 兼容中文/别名
  if (/强烈买|strong.?buy/.test(a)) return 'strong_buy';
  if (/强烈卖|strong.?sell/.test(a)) return 'strong_sell';
  if (/减/.test(a) || a === 'reduce' || a === 'underweight') return 'reduce';
  if (/卖|sell/.test(a)) return 'sell';
  if (/买|buy|overweight/.test(a)) return 'buy';
  return 'hold';
}

/** 记录一次判断（深研完成 / 分析师明确结论时自动调用）。同标的+同动作 12h 内不重复记。 */
export function logDecision(input: {
  symbol: string;
  action: string;
  confidence: number;
  thesis: string;
  now: number;
  priceAtLog?: number | null;
}): void {
  const symbol = (input.symbol || '').toUpperCase().trim();
  if (!symbol) {
    return;
  }
  const action = normalizeAction(input.action);
  const confidence = Math.min(1, Math.max(0, Number(input.confidence) || 0));
  const list = safeLoad();
  // 去抖：同标的同动作 12 小时内只记一次，避免反复深研刷屏。
  const recentDup = list.find(
    r => r.symbol === symbol && r.action === action && input.now - r.loggedAt < 12 * 60 * 60 * 1000
  );
  if (recentDup) {
    return;
  }
  const record: DecisionRecord = {
    id: `d_${input.now.toString(36)}_${symbol}`,
    symbol,
    action,
    confidence,
    thesis: (input.thesis || '').slice(0, 400),
    loggedAt: input.now,
    priceAtLog: input.priceAtLog ?? null,
    outcome: 'pending',
  };
  safeSave([record, ...list]);
}

/** 标注某条判断的兑现结果（人工）。 */
export function setOutcome(id: string, outcome: DecisionOutcome, now: number): void {
  const list = safeLoad();
  const next = list.map(r => (r.id === id
    ? {
        ...r,
        outcome,
        resolvedAt: outcome === 'pending' ? undefined : now,
        resolvedBy: outcome === 'pending' ? undefined : ('manual' as const),
      }
    : r));
  safeSave(next);
}

/**
 * 按价格变动自动判定一条判断的兑现（需 priceAtLog + 当前价）。
 * 方向性动作（买/卖/减仓）按价格是否朝预期方向走超过阈值判定；持有/观望不自动判（语义模糊）。
 * 返回 'correct' | 'wrong' | 'pending'（无价/未达阈值/不可判→pending，保留人工标注空间）。
 */
export function resolveByPrice(
  record: Pick<DecisionRecord, 'action' | 'priceAtLog'>,
  currentPrice: number | null | undefined,
  threshold = 0.03
): DecisionOutcome {
  const p0 = record.priceAtLog;
  if (typeof p0 !== 'number' || p0 <= 0 || typeof currentPrice !== 'number' || currentPrice <= 0) {
    return 'pending';
  }
  const change = (currentPrice - p0) / p0;
  const bullish = record.action === 'buy' || record.action === 'strong_buy';
  const bearish = record.action === 'sell' || record.action === 'strong_sell' || record.action === 'reduce';
  if (bullish) {
    if (change >= threshold) return 'correct';
    if (change <= -threshold) return 'wrong';
    return 'pending';
  }
  if (bearish) {
    if (change <= -threshold) return 'correct';
    if (change >= threshold) return 'wrong';
    return 'pending';
  }
  return 'pending'; // hold / 观望：不自动判
}

/**
 * 用当前价格表批量自动判定 pending 判断（价格可用时调用，行情不可用则零判定，优雅降级）。
 * 返回本次自动判定的条数。自动判定的记录打 resolvedBy='price' 标记。
 */
export function autoResolveWithPrices(priceMap: Record<string, number>, now: number): number {
  const list = safeLoad();
  let resolved = 0;
  const next = list.map(r => {
    if (r.outcome !== 'pending') {
      return r;
    }
    const price = priceMap[r.symbol];
    const verdict = resolveByPrice(r, price);
    if (verdict === 'pending') {
      return r;
    }
    resolved += 1;
    return { ...r, outcome: verdict, resolvedAt: now, resolvedBy: 'price' as const };
  });
  if (resolved > 0) {
    safeSave(next);
  }
  return resolved;
}

/** 计算置信度校准（仅基于已兑现判断）。 */
export function computeCalibration(records: DecisionRecord[]): Calibration {
  const resolved = records.filter(r => r.outcome === 'correct' || r.outcome === 'wrong');
  const correct = resolved.filter(r => r.outcome === 'correct').length;
  const hitRate = resolved.length > 0 ? correct / resolved.length : null;
  const avgConfidence = resolved.length > 0
    ? resolved.reduce((s, r) => s + r.confidence, 0) / resolved.length
    : null;
  const gap = hitRate !== null && avgConfidence !== null ? avgConfidence - hitRate : null;
  let tendency: Calibration['tendency'] = '样本不足';
  if (resolved.length >= 3 && gap !== null) {
    if (gap > 0.12) tendency = '偏过度自信';
    else if (gap < -0.12) tendency = '偏过度保守';
    else tendency = '校准良好';
  }
  return {
    total: records.length,
    resolved: resolved.length,
    correct,
    hitRate,
    avgConfidence,
    gap,
    tendency,
  };
}

/** 按标的召回历史判断 + 校准，供注入 agent 上下文（自我感知）。 */
export function recallDecisions(symbol: string | null | undefined, now: number, limit = 3): DecisionRecall | null {
  const sym = (symbol || '').toUpperCase().trim();
  if (!sym) {
    return null;
  }
  const all = safeLoad();
  const forSymbol = all.filter(r => r.symbol === sym);
  if (forSymbol.length === 0) {
    return null;
  }
  return {
    past: forSymbol.slice(0, limit).map(r => ({
      action: r.action,
      confidence: r.confidence,
      thesis: r.thesis,
      when: relativeWhen(r.loggedAt, now),
      outcome: r.outcome,
    })),
    calibration: computeCalibration(forSymbol),
  };
}

export function listDecisions(): DecisionRecord[] {
  return safeLoad();
}

export function decisionCount(): number {
  return safeLoad().length;
}

export function hasDecisions(): boolean {
  return safeLoad().length > 0;
}

export function hasDecisionsForSymbol(symbol: string | null | undefined): boolean {
  const sym = (symbol || '').toUpperCase().trim();
  return !!sym && safeLoad().some(r => r.symbol === sym);
}

export function clearDecisions(): void {
  try {
    window.localStorage.removeItem(JOURNAL_STORAGE_KEY);
  } catch {
    // ignore
  }
}
