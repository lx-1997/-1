import { readAiTaskHistory, upsertAiTaskHistory, dropInterpretCache, hydrateInterpretCache, isReusableNewsInterpretation, newsInterpretationCacheKey, persistInterpretEntry, type CachedInterpretation } from '../../utils/aiInterpretCache';
import { accountStorageKey } from '../../utils/accountStorage';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';

// 测试无登录态 → guest 域（与 utils.scopedKey 的计算保持一致）
const STORE_KEY = accountStorageKey('df_ai_interpret_cache_v1', null);
const factualNews = {
  summary: '中国对巴拉圭和伯利兹的部长实施制裁。',
  logic_lines: [{ title: '制裁对象', evidence: '对象为两国部长，原因涉及台湾问题。' }],
};
const boundaryCard = {
  one_liner: '中国因台湾问题对巴拉圭和伯利兹的部长实施制裁',
  summary: '具体措施和影响尚不明确。',
  logic_lines: [{
    title: '信息边界',
    evidence: '原文未形成可独立核对的分项事实。',
    chain: '原文未明确说明事实到影响的传导。',
    impact: '原文未明确提及受益或受压对象。',
    watch: '原文未给出验证条件。',
  }],
};
const signIn = (id: string) => authenticateSession({
  id, username: id, email: null, role: 'viewer', is_active: true, created_at: '',
}, `test-${id}`, 'https://example.test');

describe('aiInterpretCache', () => {
  beforeEach(() => { invalidateAuthSession(); localStorage.clear(); });
  afterEach(() => invalidateAuthSession());

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
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
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
    const store = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
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
    localStorage.setItem(STORE_KEY, JSON.stringify(bloated));
    expect(persistInterpretEntry('fresh', { result: { ok: true } })).toBe(true);

    const store = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    expect(store.fresh).toBeTruthy();
    expect(Object.keys(store).length).toBeLessThan(12);  // 弃置过旧条目
  });

  it('tolerates corrupted store content', () => {
    localStorage.setItem(STORE_KEY, '{not json');
    const into = new Map<string, unknown>();
    expect(() => hydrateInterpretCache(into, raw => raw.result)).not.toThrow();
    expect(into.size).toBe(0);
  });

  it('caches a short factual news item without requiring a confidence score', () => {
    const key = newsInterpretationCacheKey('short');
    expect(isReusableNewsInterpretation(factualNews)).toBe(true);
    expect(persistInterpretEntry(key, { result: factualNews })).toBe(true);
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);
    expect(into.get(key)).toEqual(factualNews);
  });

  it('caches a complete summary with concrete measures beyond its title and no logic lines', () => {
    const result = {
      title: '路透社：中国对两国部长实施制裁',
      one_liner: '中国对两国部长实施制裁。',
      summary: '制裁措施包括禁止入境，以及冻结境内财产。',
      logic_lines: [], confidence: 0.8,
    };
    const key = newsInterpretationCacheKey('summary-facts');
    expect(isReusableNewsInterpretation(result)).toBe(true);
    expect(persistInterpretEntry(key, { result })).toBe(true);
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);
    expect(into.get(key)).toEqual(result);
  });

  it('allows a one-line conclusion that adds a concrete measure beyond the title', () => {
    expect(isReusableNewsInterpretation({
      title: '中国对两国部长实施制裁', one_liner: '制裁包含禁止两国部长入境。',
      summary: '', logic_lines: [],
    })).toBe(true);
  });

  it.each([
    '中国对两国部长实施制裁。',
    '据报道：中国对两国部长实施制裁！',
    '事件摘要：中国对两国部长实施制裁',
    '中国对两国部长实施制裁',
  ])('rejects a summary that only repeats the title after publisher/punctuation normalization: %s', summary => {
    const result = { title: '路透社：中国对两国部长实施制裁', summary, logic_lines: [] };
    expect(isReusableNewsInterpretation(result)).toBe(false);
    expect(persistInterpretEntry(newsInterpretationCacheKey('repeat'), { result })).toBe(false);
  });

  it.each(['具体措施和影响尚不明确。', '影响未知。', '原文未明确说明。', '信息边界'])('rejects unknown-only prose despite having a title: %s', summary => {
    expect(isReusableNewsInterpretation({
      title: '路透社：中国对两国部长实施制裁',
      one_liner: '中国对两国部长实施制裁。', summary, logic_lines: [],
    })).toBe(false);
  });

  it('does not treat the repeated title in a logic row as additional evidence', () => {
    const title = '中国对两国部长实施制裁';
    expect(isReusableNewsInterpretation({
      title, summary: title, logic_lines: [{ title: '制裁', evidence: title }],
    })).toBe(false);
  });

  it('rejects zero-confidence cards even when the source note claims full text', () => {
    const result = { ...factualNews, confidence: 0, source_note: '已读取原文全文' };
    const key = newsInterpretationCacheKey('zero');
    expect(isReusableNewsInterpretation(result)).toBe(false);
    expect(persistInterpretEntry(key, { result })).toBe(false);
    expect(localStorage.getItem(STORE_KEY)).toBeNull();
  });

  it.each([0, -0.1, Number.NaN, Number.POSITIVE_INFINITY])('rejects invalid confidence %s', confidence => {
    expect(isReusableNewsInterpretation({ ...factualNews, confidence })).toBe(false);
  });

  it('rejects boundary placeholders and summary-only cards', () => {
    expect(isReusableNewsInterpretation(boundaryCard)).toBe(false);
    expect(persistInterpretEntry(newsInterpretationCacheKey('boundary'), { result: boundaryCard })).toBe(false);
    expect(isReusableNewsInterpretation({ summary: factualNews.summary, one_liner: '部长受到制裁' })).toBe(false);
    expect(isReusableNewsInterpretation({ ...factualNews, logic_lines: [{ title: '信息边界' }] })).toBe(false);
  });

  it('keeps concrete evidence when other lines acknowledge an information gap', () => {
    const result = { ...factualNews, logic_lines: [...boundaryCard.logic_lines, ...factualNews.logic_lines] };
    expect(isReusableNewsInterpretation(result)).toBe(true);
  });

  it.each(['⚠ 仅据标题概括', '仅根据标题解读', '仅读取标题，未获取正文', '标题级摘要'])('rejects title-only source note %s', source_note => {
    expect(isReusableNewsInterpretation({ ...factualNews, source_note })).toBe(false);
  });

  it('keeps factual short leads readable while rejecting failed lead-only shells', () => {
    const source_note = '⚠ 原文信息有限，仅据现有标题和导语整理';
    expect(isReusableNewsInterpretation({ ...factualNews, confidence: 0.8, source_note })).toBe(true);
    expect(isReusableNewsInterpretation({ ...boundaryCard, source_note })).toBe(false);
  });

  it.each([
    { analysis_status: 'failed' }, { analysis_status: 'degraded' },
    { source_status: 'title_only' }, { status: 'error' }, { is_title_only: true },
  ])('honors explicit failed-generation or source status %s', status => {
    expect(isReusableNewsInterpretation({ ...factualNews, ...status })).toBe(false);
  });

  it('does not mistake source-provenance data quality for generation failure', () => {
    const result = { ...factualNews, confidence: 0.75, data_quality: { level: 'degraded', label: 'AI 解读' } };
    expect(isReusableNewsInterpretation(result)).toBe(true);
  });

  it('ignores old-version and invalid persisted news but keeps report behavior', () => {
    const currentKey = newsInterpretationCacheKey('current');
    localStorage.setItem(STORE_KEY, JSON.stringify({
      'news:legacy': { result: factualNews, savedAt: Date.now() },
      'news:v4:previous': { result: factualNews, savedAt: Date.now() },
      [currentKey]: { result: factualNews, savedAt: Date.now() },
      [newsInterpretationCacheKey('bad')]: { result: boundaryCard, savedAt: Date.now() },
      'report:unchanged': { result: { confidence: 0, summary: '标题级旧研报' }, savedAt: Date.now() },
    }));
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);
    expect(Array.from(into.keys()).sort()).toEqual([currentKey, 'report:unchanged'].sort());
    expect(persistInterpretEntry('news:legacy', { result: factualNews })).toBe(false);
    expect(persistInterpretEntry('report:zero', { result: { confidence: 0 } })).toBe(true);
  });

  it('uses the same id-first key with a versioned title/content fallback', () => {
    expect(newsInterpretationCacheKey('123', 'Title', 'Body')).toBe('news:v5:123');
    expect(newsInterpretationCacheKey('', 'Title', 'x'.repeat(90))).toBe(`news:v5:Title:${'x'.repeat(80)}`);
  });

  it('keeps news TTL enforcement alongside the quality check', () => {
    const key = newsInterpretationCacheKey('expired');
    localStorage.setItem(STORE_KEY, JSON.stringify({
      [key]: { result: factualNews, savedAt: Date.now() - 8 * 24 * 60 * 60 * 1000 },
    }));
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);
    expect(into.has(key)).toBe(false);
  });

  it('leaves unowned legacy data untouched and never claims it for a login', () => {
    localStorage.setItem('df_ai_interpret_cache_v1', JSON.stringify({
      'news:legacy': { result: factualNews, savedAt: Date.now() },
      'report:legacy': { result: { summary: '原有研报' }, savedAt: Date.now() },
    }));
    const into = new Map<string, unknown>();
    hydrateInterpretCache(into, raw => raw.result);
    expect(into.has('news:legacy')).toBe(false);
    expect(into.has('report:legacy')).toBe(false);
    expect(localStorage.getItem('df_ai_interpret_cache_v1')).not.toBeNull();
    signIn('B');
    hydrateInterpretCache(into, raw => raw.result);
    expect(into.size).toBe(0);
  });

  it('drops only the requested result in the current account', () => {
    const key = newsInterpretationCacheKey('shared-id');
    signIn('A');
    persistInterpretEntry(key, { result: factualNews });
    persistInterpretEntry('report:keep', { result: { summary: '保留研报' } });
    signIn('B');
    const fromB = new Map<string, unknown>();
    hydrateInterpretCache(fromB, raw => raw.result);
    expect(fromB.size).toBe(0);
    persistInterpretEntry(key, { result: { ...factualNews, summary: 'B 的解读' } });
    dropInterpretCache(key);
    hydrateInterpretCache(fromB, raw => raw.result);
    expect(fromB.has(key)).toBe(false);
    signIn('A');
    const fromA = new Map<string, unknown>();
    hydrateInterpretCache(fromA, raw => raw.result);
    expect(fromA.get(key)).toEqual(factualNews);
    dropInterpretCache(key);
    const remaining = new Map<string, unknown>();
    hydrateInterpretCache(remaining, raw => raw.result);
    expect(remaining.has(key)).toBe(false);
    expect(remaining.has('report:keep')).toBe(true);
  });
});

it('isolates interpretation history between A, B and guests and leaves old metadata unread', () => {
  invalidateAuthSession(); localStorage.clear();
  const entry = { key: 'report:A', kind: 'report' as const, title: 'A private report', report: null, meta: null, startedAt: 1 };
  localStorage.setItem('df_ai_task_history_v1', JSON.stringify([entry]));
  signIn('A');
  expect(readAiTaskHistory()).toEqual([]);
  upsertAiTaskHistory(entry);
  signIn('B');
  expect(readAiTaskHistory()).toEqual([]);
  upsertAiTaskHistory({ ...entry, key: 'report:B', title: 'B private report' });
  invalidateAuthSession();
  expect(readAiTaskHistory()).toEqual([]);
  signIn('A');
  expect(readAiTaskHistory()).toEqual([entry]);
  expect(localStorage.getItem('df_ai_task_history_v1')).not.toBeNull();
  invalidateAuthSession(); localStorage.clear();
});
