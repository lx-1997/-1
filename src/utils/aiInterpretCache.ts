/** AI 解读结果的本机持久缓存：完成后落 localStorage，下次/刷新后点开同一篇直接复用。 */

export interface CachedInterpretation {
  result: unknown;
  deepDraft?: unknown;
  savedAt: number;
}

const STORE_KEY = 'df_ai_interpret_cache_v1';
const TTL_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_ENTRIES = 24;
const MAX_ENTRY_CHARS = 300_000;
// localStorage 总预算：24 条 × 300KB 可能顶满 5MB 配额，setItem 会「整体原子失败」
// ——表现为所有新解读都静默丢持久化。按最旧淘汰把总量压回预算内。
const MAX_STORE_CHARS = 2_000_000;

function readStore(): Record<string, CachedInterpretation> {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

/** 启动时把本机缓存灌回内存 Map；过期条目丢弃。 */
export function hydrateInterpretCache<T>(
  into: Map<string, T>,
  revive: (raw: CachedInterpretation) => T | null,
): void {
  const store = readStore();
  const now = Date.now();
  for (const [key, entry] of Object.entries(store)) {
    if (!entry || typeof entry.savedAt !== 'number' || now - entry.savedAt > TTL_MS) continue;
    const value = revive(entry);
    if (value !== null) into.set(key, value);
  }
}

/** 保存一条解读；返回是否真正落盘。超大条目与配额耗尽时会放弃（调用方据此决定是否入历史）。 */
export function persistInterpretEntry(key: string, entry: { result: unknown; deepDraft?: unknown }): boolean {
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
    let total = () => entries.reduce((sum, [, e]) => sum + JSON.stringify(e).length, 0);
    while (total() > MAX_STORE_CHARS && entries.length > 1) {
      const [oldest] = entries.shift() as [string, CachedInterpretation];
      delete store[oldest];
    }
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(store));
      return true;
    } catch {
      // 配额不足（存量已超预算/浏览器配额收紧）：从最旧开始逐条弃置重试，保住最新这条
      entries = entries.filter(([k]) => k !== key);
      while (entries.length) {
        const [drop] = entries.shift() as [string, CachedInterpretation];
        delete store[drop];
        try {
          localStorage.setItem(STORE_KEY, JSON.stringify(store));
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

/** 解读记录历史：只存展示元数据，解读正文复用上面的结果缓存。 */
export interface AiTaskHistoryEntry {
  key: string;
  kind: 'report' | 'news';
  title: string;
  report: { title?: string; date?: string } | null;
  meta: { org?: string; symbol?: string; preview_url?: string } | null;
  startedAt: number;
}

const HISTORY_KEY = 'df_ai_task_history_v1';
const HISTORY_MAX = 24;

function readHistory(): AiTaskHistoryEntry[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
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
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX)));
  } catch {
    // 尽力而为的历史层，存储异常时放弃。
  }
}

export function removeAiTaskHistory(key: string): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(readHistory().filter(e => e.key !== key)));
  } catch {
    // 忽略
  }
}

/** 启动时读取历史；仅返回元数据，结果由调用方对缓存逐条配对。 */
export function readAiTaskHistory(): AiTaskHistoryEntry[] {
  return readHistory().sort((a, b) => b.startedAt - a.startedAt);
}
