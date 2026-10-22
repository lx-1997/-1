/**
 * AI 解读任务的本机恢复与跨端合并「规划器」——纯函数，无副作用。
 *
 * 从 FinancialTerminal 组件抽出的原因：恢复三路分流（done+缓存 / 中断·失败可续跑 /
 * 完结无缓存的幽灵自愈）是回归高发区（StrictMode 双挂载曾把合法记录误判成幽灵删光），
 * 判定逻辑一旦以纯函数形态存在，就能在组件之外用单测穷举边界。
 *
 * 约定：planner 只产出「要注入的任务种子」与「要清除的历史键」，不触碰存储与
 * React 状态；restart 闭包等需要组件能力（跳转/发请求）的部分由调用方映射。
 */

import type { AiTaskHistoryEntry } from './aiInterpretCache';

export const INTERRUPTED_RESUME_TEXT = '上次解读被中断，点击继续';
export const FAILED_RESUME_TEXT = '上次解读失败，点击重试';

/** 注入任务列表的任务种子（组件映射为 AiBgTask） */
export interface RestorableTaskSeed {
  key: string;
  kind: 'report' | 'news';
  title: string;
  report: { title?: string; date?: string } | null;
  meta: { org?: string; symbol?: string; preview_url?: string } | null;
  content?: string;
  startedAt: number;
  status: 'done' | 'error';
  error: string;
  result?: unknown;
  deepDraft?: unknown;
  /** 云端跨端记录：本机无正文缓存，点击重新拉取 */
  cloud?: boolean;
}

export interface RestorePlan {
  tasks: RestorableTaskSeed[];
  /** 已完结但缓存已淘汰的真幽灵：调用方应从历史中移除这些键 */
  staleHistoryKeys: string[];
}

export type CachedInterpretationLike = { result: unknown; deepDraft?: unknown };

/** 本机历史 → 恢复计划。skipKeys：已在任务表中的键（同 key 以内存态优先）。 */
export function planLocalRestores(
  history: readonly AiTaskHistoryEntry[],
  cache: ReadonlyMap<string, CachedInterpretationLike>,
  skipKeys: ReadonlySet<string> = new Set(),
): RestorePlan {
  const tasks: RestorableTaskSeed[] = [];
  const staleHistoryKeys: string[] = [];
  for (const h of history) {
    if (skipKeys.has(h.key)) continue;
    const status = h.status || 'done';
    const cached = cache.get(h.key);
    if (status === 'done' || cached) {
      // 已完结但缓存被淘汰 = 真幽灵（无法渲染也无法续跑参数之外的任何内容）→ 自愈清除
      if (!cached) { staleHistoryKeys.push(h.key); continue; }
      tasks.push({
        key: h.key, kind: h.kind, title: h.title, report: h.report, meta: h.meta,
        startedAt: h.startedAt, status: 'done', error: '',
        result: cached.result, deepDraft: cached.deepDraft,
      });
      continue;
    }
    // 中断（running）/失败（error）：可续跑条目，点击重跑取回正文
    tasks.push({
      key: h.key, kind: h.kind, title: h.title, report: h.report, meta: h.meta,
      content: h.content,
      startedAt: h.startedAt, status: 'error',
      error: status === 'running' ? INTERRUPTED_RESUME_TEXT : (h.error || FAILED_RESUME_TEXT),
    });
  }
  return { tasks, staleHistoryKeys };
}

/** 服务端 memory 记录中与云端解读相关的最小形状 */
export interface CloudInterpretMemory {
  id?: string;
  hall?: string;
  title?: string;
  content?: string;
  created_at?: string;
}

export const AI_INTERPRET_SYNC_HALL = 'ai_interpret';

/** 服务端 ai_interpret memory → 云端任务种子。skipKeys：本机已有（本地优先）。 */
export function planCloudRestores(
  memories: readonly CloudInterpretMemory[],
  skipKeys: ReadonlySet<string> = new Set(),
): RestorableTaskSeed[] {
  const tasks: RestorableTaskSeed[] = [];
  const seen = new Set<string>(skipKeys);
  for (const memory of memories) {
    if (memory.hall !== AI_INTERPRET_SYNC_HALL) continue;
    let payload: { key?: unknown; kind?: unknown; title?: unknown; report?: unknown; meta?: unknown; startedAt?: unknown };
    try { payload = JSON.parse(memory.content || '{}'); } catch { continue; }
    const key = typeof payload?.key === 'string' ? payload.key : '';
    if (!key || payload.kind !== 'report' || !payload.report || seen.has(key)) continue;
    seen.add(key);
    tasks.push({
      key,
      kind: 'report',
      title: typeof payload.title === 'string' && payload.title ? payload.title : (memory.title || ''),
      report: payload.report as RestorableTaskSeed['report'],
      meta: (payload.meta ?? null) as RestorableTaskSeed['meta'],
      startedAt: Date.parse(memory.created_at || '') || (typeof payload.startedAt === 'number' ? payload.startedAt : Date.now()),
      status: 'done',
      error: '',
      cloud: true,   // 无本机正文缓存：点击重新拉取（命中服务端解读缓存通常秒级）
    });
  }
  return tasks;
}
