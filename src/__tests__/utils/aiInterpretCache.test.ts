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

  it('tolerates corrupted store content', () => {
    localStorage.setItem('df_ai_interpret_cache_v1', '{not json');
    const into = new Map<string, unknown>();
    expect(() => hydrateInterpretCache(into, raw => raw.result)).not.toThrow();
    expect(into.size).toBe(0);
  });
});
