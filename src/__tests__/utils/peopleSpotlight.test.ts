import {
  aggregateTopicHeat,
  applyCustomOrder,
  buildSignalPayload,
  countTodayItems,
  matchesQuery,
  normalizeText,
  rankCrossFigureHeadlines,
  sortFigures,
} from '../../utils/peopleSpotlight';
import type { PersonProfile, PersonVoiceItem } from '../../services/peopleService';

const mkItem = (over: Partial<PersonVoiceItem> = {}): PersonVoiceItem => ({
  id: over.id || 'i1',
  title: over.title ?? '标题',
  summary: '',
  url: over.url ?? 'https://example.com/a',
  source_name: over.source_name ?? '财联社',
  published_at: 'published_at' in over ? over.published_at! : null,
  reported_date: 'reported_date' in over ? over.reported_date! : '2026-06-06',
  tags: over.tags ?? [],
  importance_score: over.importance_score ?? 60,
});

const mkFigure = (over: Partial<PersonProfile> = {}): PersonProfile => ({
  id: over.id || 'huang',
  name: over.name ?? '黄仁勋',
  en_name: over.en_name ?? 'Jensen Huang',
  role: over.role ?? '创始人兼 CEO',
  org: over.org ?? '英伟达 NVIDIA',
  image: '',
  image_credit: '',
  avatar: '',
  monogram: '',
  accent: '#16a34a',
  bio: '',
  topics: over.topics ?? ['AI 算力', 'GPU'],
  why_it_matters: '',
  items: over.items ?? [],
  item_count: over.item_count ?? (over.items ? over.items.length : 0),
  latest_date: over.latest_date ?? null,
  digest: '',
  digest_provider: '',
  warnings: [],
  data_quality: { level: 'live', label: '', detail: '', reasons: [] },
});

describe('normalizeText', () => {
  it('lowercases and strips whitespace/punctuation', () => {
    expect(normalizeText('Jensen · Huang, NVIDIA')).toBe('jensenhuangnvidia');
    expect(normalizeText('AI 算力')).toBe('ai算力');
  });
});

describe('matchesQuery', () => {
  const f = mkFigure();
  it('empty query matches everything', () => {
    expect(matchesQuery(f, '')).toBe(true);
    expect(matchesQuery(f, '   ')).toBe(true);
  });
  it('matches by name / en_name / org / topic / id, case-insensitive', () => {
    expect(matchesQuery(f, '黄仁勋')).toBe(true);
    expect(matchesQuery(f, 'jensen')).toBe(true);
    expect(matchesQuery(f, '英伟达')).toBe(true);
    expect(matchesQuery(f, 'GPU')).toBe(true);
    expect(matchesQuery(f, 'huang')).toBe(true);
  });
  it('returns false for a miss', () => {
    expect(matchesQuery(f, '特斯拉')).toBe(false);
  });
});

describe('sortFigures', () => {
  const a = mkFigure({ id: 'a', latest_date: '2026-06-01', item_count: 5 });
  const b = mkFigure({ id: 'b', latest_date: '2026-06-06', item_count: 2 });
  const c = mkFigure({ id: 'c', latest_date: '2026-06-03', item_count: 9 });

  it('default keeps original order (same reference)', () => {
    const input = [a, b, c];
    expect(sortFigures(input, 'default')).toBe(input);
  });
  it('latest sorts by latest_date desc', () => {
    expect(sortFigures([a, b, c], 'latest').map(f => f.id)).toEqual(['b', 'c', 'a']);
  });
  it('count sorts by item_count desc', () => {
    expect(sortFigures([a, b, c], 'count').map(f => f.id)).toEqual(['c', 'a', 'b']);
  });
  it('does not mutate the input array', () => {
    const input = [a, b, c];
    sortFigures(input, 'latest');
    expect(input.map(f => f.id)).toEqual(['a', 'b', 'c']);
  });
});

describe('applyCustomOrder', () => {
  const figs = ['a', 'b', 'c', 'd'].map(id => mkFigure({ id }));
  it('empty order returns input unchanged', () => {
    expect(applyCustomOrder(figs, [])).toBe(figs);
  });
  it('orders by the given ids, unranked keep original relative order at the end', () => {
    expect(applyCustomOrder(figs, ['c', 'a']).map(f => f.id)).toEqual(['c', 'a', 'b', 'd']);
  });
  it('is stable for items not in the order list', () => {
    expect(applyCustomOrder(figs, ['d']).map(f => f.id)).toEqual(['d', 'a', 'b', 'c']);
  });
});

describe('rankCrossFigureHeadlines', () => {
  it('takes one headline per figure, ranked by date then importance, skipping empty', () => {
    const f1 = mkFigure({ id: 'f1', items: [mkItem({ id: 'h1', reported_date: '2026-06-05', importance_score: 90 })] });
    const f2 = mkFigure({ id: 'f2', items: [mkItem({ id: 'h2', reported_date: '2026-06-06', importance_score: 50 })] });
    const f3 = mkFigure({ id: 'f3', items: [mkItem({ id: 'h3', reported_date: '2026-06-06', importance_score: 80 })] });
    const f4 = mkFigure({ id: 'f4', items: [] });
    const ranked = rankCrossFigureHeadlines([f1, f2, f3, f4]);
    // f4 has no items → skipped; 06-06 before 06-05; within 06-06, higher importance first.
    expect(ranked.map(r => r.item.id)).toEqual(['h3', 'h2', 'h1']);
    expect(ranked).toHaveLength(3);
  });
});

describe('countTodayItems', () => {
  it('counts only items whose reported_date matches the given day', () => {
    const f = mkFigure({
      items: [
        mkItem({ id: 'a', reported_date: '2026-06-06' }),
        mkItem({ id: 'b', reported_date: '2026-06-06' }),
        mkItem({ id: 'c', reported_date: '2026-06-05' }),
        mkItem({ id: 'd', reported_date: null }),
      ],
    });
    expect(countTodayItems(f, '2026-06-06')).toBe(2);
    expect(countTodayItems(f, '2026-06-05')).toBe(1);
    expect(countTodayItems(f, '2026-06-01')).toBe(0);
  });
});

describe('aggregateTopicHeat', () => {
  it('aggregates today tags across figures, ranked desc, excluding other days', () => {
    const f1 = mkFigure({
      id: 'f1',
      items: [
        mkItem({ id: 'a', reported_date: '2026-06-06', tags: ['AI', '芯片'] }),
        mkItem({ id: 'b', reported_date: '2026-06-06', tags: ['AI'] }),
        mkItem({ id: 'c', reported_date: '2026-06-05', tags: ['关税'] }), // 非今日 → 不计
      ],
    });
    const f2 = mkFigure({
      id: 'f2',
      items: [mkItem({ id: 'd', reported_date: '2026-06-06', tags: ['AI', '资本'] })],
    });
    const heat = aggregateTopicHeat([f1, f2], '2026-06-06');
    expect(heat[0]).toEqual({ tag: 'AI', count: 3 });
    expect(heat.map(h => h.tag)).not.toContain('关税'); // 昨天的不算今日热点
    expect(heat.find(h => h.tag === '芯片')).toEqual({ tag: '芯片', count: 1 });
  });
  it('respects the limit and returns empty when no today items', () => {
    const f = mkFigure({ items: [mkItem({ reported_date: '2026-06-05', tags: ['AI'] })] });
    expect(aggregateTopicHeat([f], '2026-06-06')).toEqual([]);
    const many = mkFigure({
      items: ['a', 'b', 'c'].map((id, i) => mkItem({ id, reported_date: '2026-06-06', tags: [`t${i}`] })),
    });
    expect(aggregateTopicHeat([many], '2026-06-06', 2)).toHaveLength(2);
  });
});

describe('buildSignalPayload', () => {
  const figure = mkFigure({ id: 'musk', name: '马斯克', role: 'CEO', org: '特斯拉' });
  it('builds a people-voice message with provenance metadata', () => {
    const item = mkItem({ id: 'x', title: 'SpaceX 估值下调', source_name: '虎嗅', url: 'https://h.co/x', tags: ['资本'], importance_score: 70 });
    const p = buildSignalPayload(figure, item);
    expect(p.title).toBe('马斯克：SpaceX 估值下调');
    expect(p.topic).toBe('people-voice');
    expect(p.severity).toBe('info');
    expect(p.url).toBe('https://h.co/x');
    expect(p.tags).toEqual(['马斯克', '资本']);
    expect(p.metadata).toMatchObject({ figure_id: 'musk', importance_score: 70, pushed_from: 'people-spotlight' });
  });
  it('marks high-importance items as warning severity (>=80)', () => {
    expect(buildSignalPayload(figure, mkItem({ importance_score: 80 })).severity).toBe('warning');
    expect(buildSignalPayload(figure, mkItem({ importance_score: 79 })).severity).toBe('info');
  });
});
