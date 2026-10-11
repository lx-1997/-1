import { hydrateInterpretCache, persistInterpretEntry, type CachedInterpretation } from '../../utils/aiInterpretCache';

describe('aiInterpretCache', () => {
  beforeEach(() => localStorage.clear());

  it('round-trips an entry across hydration', () => {
    persistInterpretEntry('report:abc', { result: { one_liner: '结论' }, deepDraft: { title: '深稿' } });
    const into = new Map<string, { result: unknown; deepDraft?: unknown }>();
    hydrateInterpretCache(into, raw => (raw.result ? { result: raw.result, deepDraft: raw.deepDraft } : null));

    expect(into.get('report:abc')).toMatchObject({ result: { one_liner: '结论' }, deepDraft: { title: '深稿' } });
  });

  it('drops entries older than the TTL on hydration', () => {
    const store: Record<string, CachedInterpretation> = {
      fresh: { result: 1, savedAt: Date.now() },
      stale: { result: 2, savedAt: Date.now() - 8 * 24 * 60 * 60 * 1000 },
    };
    localStorage.setItem('df_ai_interpret_cache_v1', JSON.stringify(store));
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);

    expect(into.has('fresh')).toBe(true);
    expect(into.has('stale')).toBe(false);
  });

  it('evicts the oldest entries beyond the cap but never the fresh one', () => {
    for (let i = 0; i < 30; i += 1) {
      persistInterpretEntry(`key-${i}`, { result: i });
    }
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);

    expect(into.has('key-29')).toBe(true);
    expect(into.has('key-0')).toBe(false);
    expect(into.size).toBeLessThanOrEqual(24);
  });

  it('skips oversized entries instead of filling the store', () => {
    persistInterpretEntry('huge', { result: 'x'.repeat(400_000) });
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);

    expect(into.has('huge')).toBe(false);
  });

  it('reports persistence success so callers can decide whether to record history', () => {
    expect(persistInterpretEntry('ok', { result: { a: 1 } })).toBe(true);
    expect(persistInterpretEntry('huge', { result: 'x'.repeat(400_000) })).toBe(false);
  });

  it('keeps total store size within budget by evicting the oldest', () => {
    // 每条 ~200KB：预算 2MB 内最多 ~10 条，写入 15 条后总量必须被压回预算
    for (let i = 0; i < 15; i += 1) {
      persistInterpretEntry(`big-${i}`, { result: 'y'.repeat(200_000) });
    }
    const store = JSON.parse(localStorage.getItem('df_ai_interpret_cache_v1') || '{}');
    const total = Object.values(store).reduce((sum: number, e: any) => sum + JSON.stringify(e).length, 0);
    expect(total).toBeLessThanOrEqual(2_000_000 + 250_000);  // 预算 + 单条容差
    expect(Object.keys(store)).toContain('big-14');           // 最新一条永远在
  });

  it('recovers when the store exceeds browser quota by evicting oldest entries', () => {
    // 预置一个「已经超预算」的存量（绕过写入路径），新写入必须能通过弃置最旧存活
    const bloated: Record<string, unknown> = {};
    for (let i = 0; i < 12; i += 1) {
      bloated[`old-${i}`] = { result: 'z'.repeat(240_000), savedAt: Date.now() - (12 - i) * 1000 };
    }
    localStorage.setItem('df_ai_interpret_cache_v1', JSON.stringify(bloated));
    expect(persistInterpretEntry('fresh', { result: { ok: true } })).toBe(true);

    const store = JSON.parse(localStorage.getItem('df_ai_interpret_cache_v1') || '{}');
    expect(store.fresh).toBeTruthy();
    expect(Object.keys(store).length).toBeLessThan(12);  // 弃置过旧条目
  });

  it('tolerates corrupted store content', () => {
    localStorage.setItem('df_ai_interpret_cache_v1', '{not json');
    const into = new Map<string, unknown>();
    expect(() => hydrateInterpretCache(into, raw => raw.result)).not.toThrow();
    expect(into.size).toBe(0);
  });
});
