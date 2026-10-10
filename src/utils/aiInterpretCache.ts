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

/** 保存一条解读；超大条目跳过（保持内存态），超量按最旧淘汰。 */
export function persistInterpretEntry(key: string, entry: { result: unknown; deepDraft?: unknown }): void {
  try {
    const store = readStore();
    const record: CachedInterpretation = { result: entry.result, deepDraft: entry.deepDraft, savedAt: Date.now() };
    const serialized = JSON.stringify(record);
    if (serialized.length > MAX_ENTRY_CHARS) return;
    store[key] = record;
    const entries = Object.entries(store).sort((a, b) => (a[1].savedAt || 0) - (b[1].savedAt || 0));
    while (entries.length > MAX_ENTRIES) {
      const [oldest] = entries.shift() as [string, CachedInterpretation];
      delete store[oldest];
    }
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    // 存储满/不可用时静默放弃：缓存是尽力而为的加速层。
  }
}
