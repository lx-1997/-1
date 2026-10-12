/** AI 解读结果的本机持久缓存：完成后落 localStorage，下次/刷新后点开同一篇直接复用。
 *  全部键按账号分域（accountStorageKey，guest/user 各自命名空间）——同设备多账号互不可见，
 *  与 AI 对话历史（useAccountAiHistory）同一套隔离约定。 */

import { getAuthSnapshot } from '../state/authSession';
import { accountStorageKey } from './accountStorage';

export interface CachedInterpretation {
  result: unknown;
  deepDraft?: unknown;
  savedAt: number;
}

const STORE_BASE = 'df_ai_interpret_cache_v1';
const TTL_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_ENTRIES = 24;
const MAX_ENTRY_CHARS = 300_000;
// localStorage 总预算：24 条 × 300KB 可能顶满 5MB 配额，setItem 会「整体原子失败」
// ——表现为所有新解读都静默丢持久化。按最旧淘汰把总量压回预算内。
const MAX_STORE_CHARS = 2_000_000;
const HISTORY_BASE = 'df_ai_task_history_v1';
const SYNCED_BASE = 'df_ai_task_synced_v1';
// 一次性迁移：分域上线前写入的「无主」数据认领给最先启动的账号域（通常就是同一台设备
// 的日常账号），认领过一次后其余账号不再继承，保证此后严格按账号隔离。
const LEGACY_MIGRATED_MARKER = 'df_ai_interpret_legacy_migrated_v1';
const LEGACY_STORE_KEY = STORE_BASE;
const LEGACY_HISTORY_KEY = HISTORY_BASE;

const NEWS_CACHE_PREFIX = 'news:v5:';
const FAILED_NEWS_STATES = new Set([
  'error', 'failed', 'failure', 'degraded', 'fallback', 'title_only',
  'empty', 'missing', 'unavailable', 'insufficient_source', 'insufficient_content',
]);
// These are generated boundary placeholders, not facts or useful analysis.
const NEWS_PLACEHOLDER = /^(?:信息边界|信息不足|暂无(?:正文|内容|分析|结论)|无(?:相关|明确|可核验|可核对|可验证)|不适用|未知|待补充|详见原文|仅据标题|仅根据标题)|^原文(?:未|没有|不含|不足|缺少|无法)/;
const NEWS_UNKNOWN_ONLY = /^(?:具体)?(?:制裁)?(?:措施|影响)(?:和|与|及)?(?:措施|影响)?(?:尚|仍|暂)?(?:不明确|未知|不明|未明确|未披露|尚未明确)[。.!！\s]*$/;
const newsText = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const hasNewsDetail = (value: unknown): boolean => {
  const text = newsText(value);
  return !!text && !NEWS_PLACEHOLDER.test(text) && !NEWS_UNKNOWN_ONLY.test(text);
};

function newsRepeatsTitle(text: string, title: string): boolean {
  const compact = (value: string) => value
    .replace(/^(?:路透社|路透|彭博社|彭博|Reuters|Bloomberg)\s*[:：]\s*/i, '')
    .replace(new RegExp('[^\\p{L}\\p{N}]', 'gu'), '').toLowerCase();
  const source = compact(title);
  const candidate = compact(text);
  if (!source || !candidate) return false;
  const remainder = candidate.includes(source) ? candidate.replace(source, '') : null;
  return candidate === source
    || (remainder !== null && ['据标题', '标题称', '据报道', '报道称', '消息称', '核心结论', '事件摘要'].includes(remainder))
    || (source.includes(candidate) && candidate.length >= source.length * 0.7);
}

/** Reject title-only and failed-generation cards, while allowing short, factual news. */
export function isReusableNewsInterpretation(result: unknown): boolean {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return false;
  const value = result as Record<string, unknown>;
  // data_quality.level is deliberately excluded: existing successful AI results
  // use "degraded" there to describe source provenance, not generation failure.
  if (['analysis_status', 'source_status', 'status'].some(field => (
    FAILED_NEWS_STATES.has(newsText(value[field]).toLowerCase())
  ))) return false;
  if (value.is_title_only === true || value.title_only === true) return false;
  const sourceNote = newsText(value.source_note);
  if (!/标题(?:和|与|及)导语/.test(sourceNote) && /仅(?:据|根据|有|含|读取|获取|获得).*标题|标题级(?:概括|摘要|解读)/.test(sourceNote)) return false;
  if (typeof value.confidence === 'number' && (!Number.isFinite(value.confidence) || value.confidence <= 0)) return false;
  if (!hasNewsDetail(value.summary) && !hasNewsDetail(value.one_liner)) return false;

  const title = newsText(value.title);
  const additionalDetail = (detail: unknown) => hasNewsDetail(detail) && (!title || !newsRepeatsTitle(newsText(detail), title));
  // A complete short summary needs no invented causal chain. Without a title
  // we cannot distinguish a summary from a title rewrite, so retain the detail check.
  if (title && [value.summary, value.one_liner].some(additionalDetail)) return true;
  const lines = Array.isArray(value.logic_lines) ? value.logic_lines : [];
  if (lines.some(line => line && typeof line === 'object' && !Array.isArray(line)
    && ['evidence', 'chain', 'impact', 'watch'].some(field => additionalDetail((line as Record<string, unknown>)[field])))) return true;
  if (['core_logic', 'takeaway'].some(field => additionalDetail(value[field]))) return true;
  return ['bullish', 'bearish', 'key_points', 'risks'].some(field => (
    Array.isArray(value[field]) && (value[field] as unknown[]).some(additionalDetail)
  ));
}

/** Versioned independently from reports so old news cards cannot mask a corrected response. */
export function newsInterpretationCacheKey(id: string | number | null | undefined, title = '', content = ''): string {
  const identity = String(id ?? '').trim();
  return `${NEWS_CACHE_PREFIX}${identity || `${title}:${content.slice(0, 80)}`}`;
}

function canReuseInterpretation(key: string, result: unknown): boolean {
  return !key.startsWith('news:') || (key.startsWith(NEWS_CACHE_PREFIX) && isReusableNewsInterpretation(result));
}

/** 当前账号域下的真实存储键（未登录 → guest 命名空间）。 */
function scopedKey(base: string): string {
  let accountId: string | null = null;
  try {
    accountId = getAuthSnapshot().account?.id ?? null;
  } catch { accountId = null; }
  return accountStorageKey(base, accountId);
}

function readStore(): Record<string, CachedInterpretation> {
  try {
    const parsed = JSON.parse(localStorage.getItem(scopedKey(STORE_BASE)) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * 一次性认领分域前的无主数据：缓存 + 历史按 startedAt/key 合并进当前账号域，
 * 幂等（marker 落在无主键上，任何账号域都不会重复认领）。
 */
function migrateLegacyIfNeeded(): void {
  try {
    if (localStorage.getItem(LEGACY_MIGRATED_MARKER)) return;
    const legacyCacheRaw = localStorage.getItem(LEGACY_STORE_KEY);
    const legacyHistoryRaw = localStorage.getItem(LEGACY_HISTORY_KEY);
    if (legacyCacheRaw) {
      const store = readStore();
      try {
        const legacy = JSON.parse(legacyCacheRaw);
        if (legacy && typeof legacy === 'object') {
          for (const [key, entry] of Object.entries(legacy as Record<string, CachedInterpretation>)) {
            const cur = store[key];
            if (!cur || (cur.savedAt || 0) < ((entry as CachedInterpretation).savedAt || 0)) store[key] = entry as CachedInterpretation;
          }
        }
      } catch { /* 旧数据损坏即放弃该部分 */ }
      const entries = Object.entries(store);
      localStorage.setItem(scopedKey(STORE_BASE), JSON.stringify(
        Object.fromEntries(entries.sort((a, b) => (b[1].savedAt || 0) - (a[1].savedAt || 0)).slice(0, MAX_ENTRIES))
      ));
    }
    if (legacyHistoryRaw) {
      try {
        const legacy = JSON.parse(legacyHistoryRaw);
        if (Array.isArray(legacy)) {
          const list = readHistory();
          for (const entry of legacy) {
            if (!entry || typeof entry.key !== 'string') continue;
            const cur = list.find(e => e.key === entry.key);
            if (!cur || (cur.startedAt || 0) < (entry.startedAt || 0)) {
              const idx = list.findIndex(e => e.key === entry.key);
              if (idx >= 0) list[idx] = entry; else list.push(entry);
            }
          }
          localStorage.setItem(scopedKey(HISTORY_BASE), JSON.stringify(list.sort((a, b) => b.startedAt - a.startedAt).slice(0, HISTORY_MAX)));
        }
      } catch { /* 同上 */ }
    }
    localStorage.setItem(LEGACY_MIGRATED_MARKER, new Date().toISOString());
  } catch { /* 迁移失败不阻塞主流程 */ }
}

/** 启动时把本机缓存灌回内存 Map；过期条目丢弃。 */
export function hydrateInterpretCache<T>(
  into: Map<string, T>,
  revive: (raw: CachedInterpretation) => T | null,
): void {
  migrateLegacyIfNeeded();
  const store = readStore();
  const now = Date.now();
  for (const [key, entry] of Object.entries(store)) {
    if (!entry || typeof entry.savedAt !== 'number' || now - entry.savedAt > TTL_MS) continue;
    if (!canReuseInterpretation(key, entry.result)) continue;
    const value = revive(entry);
    if (value !== null) into.set(key, value);
  }
}

/** 保存一条解读；返回是否真正落盘。超大条目与配额耗尽时会放弃（调用方据此决定是否入历史）。 */
export function persistInterpretEntry(key: string, entry: { result: unknown; deepDraft?: unknown }): boolean {
  if (!canReuseInterpretation(key, entry.result)) return false;
  try {
    const store = readStore();
    const record: CachedInterpretation = { result: entry.result, deepDraft: entry.deepDraft, savedAt: Date.now() };
    const serialized = JSON.stringify(record);
    if (serialized.length > MAX_ENTRY_CHARS) return false;
    store[key] = record;
    // 数量封顶 + 总量预算：先按条数，再按字符总量，均从最旧淘汰
    let entries = Object.entries(store).sort((a, b) => (a[1].savedAt || 0) - (b[1].savedAt || 0));
    while (entries.length > MAX_ENTRIES) {
      const [oldest] = entries.shift() as [string, CachedInterpretation];
      delete store[oldest];
    }
    const total = () => entries.reduce((sum, [, e]) => sum + JSON.stringify(e).length, 0);
    while (total() > MAX_STORE_CHARS && entries.length > 1) {
      const [oldest] = entries.shift() as [string, CachedInterpretation];
      delete store[oldest];
    }
    try {
      localStorage.setItem(scopedKey(STORE_BASE), JSON.stringify(store));
      return true;
    } catch {
      // 配额不足（存量已超预算/浏览器配额收紧）：从最旧开始逐条弃置重试，保住最新这条
      entries = entries.filter(([k]) => k !== key);
      while (entries.length) {
        const [drop] = entries.shift() as [string, CachedInterpretation];
        delete store[drop];
        try {
          localStorage.setItem(scopedKey(STORE_BASE), JSON.stringify(store));
          return true;
        } catch { /* 继续弃置最旧 */ }
      }
      return false;
    }
  } catch {
    // 存储不可用等异常：缓存是尽力而为的加速层。
    return false;
  }
}

/** Remove the current account's persisted result; callers also remove their in-memory entry. */
export function dropInterpretCache(key: string): void {
  try {
    const store = readStore();
    if (!Object.prototype.hasOwnProperty.call(store, key)) return;
    delete store[key];
    localStorage.setItem(scopedKey(STORE_BASE), JSON.stringify(store));
  } catch { /* Unavailable storage must not block regeneration. */ }
}

/** 解读记录历史：只存展示元数据，解读正文复用上面的结果缓存。 */
export interface AiTaskHistoryEntry {
  key: string;
  kind: 'report' | 'news';
  title: string;
  report: { title?: string; date?: string } | null;
  meta: { org?: string; symbol?: string; preview_url?: string } | null;
  startedAt: number;
  status?: 'running' | 'done' | 'error';  // 缺省视为 done（旧版条目只有完成态）
  error?: string;                          // 失败原因（status=error 时展示）
  content?: string;                        // 快讯原文（重跑需要；研报的 report 里已含重跑参数）
}

const HISTORY_MAX = 24;

function readHistory(): AiTaskHistoryEntry[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(scopedKey(HISTORY_BASE)) || '[]');
    return Array.isArray(parsed)
      ? parsed.filter(e => e && typeof e.key === 'string' && typeof e.startedAt === 'number')
      : [];
  } catch {
    return [];
  }
}

/** 解读完成入历史：按 key 去重、新者在前、封顶淘汰最旧。 */
export function upsertAiTaskHistory(entry: AiTaskHistoryEntry): void {
  try {
    const list = readHistory().filter(e => e.key !== entry.key);
    list.unshift(entry);
    localStorage.setItem(scopedKey(HISTORY_BASE), JSON.stringify(list.slice(0, HISTORY_MAX)));
  } catch {
    // 尽力而为的历史层，存储异常时放弃。
  }
}

export function removeAiTaskHistory(key: string): void {
  try {
    localStorage.setItem(scopedKey(HISTORY_BASE), JSON.stringify(readHistory().filter(e => e.key !== key)));
  } catch {
    // 忽略
  }
}

/** 启动时读取历史；仅返回元数据，结果由调用方对缓存逐条配对。 */
export function readAiTaskHistory(): AiTaskHistoryEntry[] {
  return readHistory().sort((a, b) => b.startedAt - a.startedAt);
}

/** 已完成跨端上行同步的 key（按账号域记录，防同一账号重复上传）。 */
export function readSyncedInterpretKeys(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(scopedKey(SYNCED_BASE)) || '[]');
    return new Set(Array.isArray(raw) ? raw.filter(k => typeof k === 'string') : []);
  } catch { return new Set(); }
}

export function markInterpretSynced(key: string): void {
  try {
    const keys = Array.from(readSyncedInterpretKeys()).concat(key).slice(-200);
    localStorage.setItem(scopedKey(SYNCED_BASE), JSON.stringify(keys));
  } catch { /* 尽力而为 */ }
}
