import type { RealtimeMessageCreate } from '../services/eventService';
import type { PersonProfile, PersonVoiceItem } from '../services/peopleService';

// 人物专题的纯逻辑函数（无 React / DOM / 网络依赖），抽出便于单测与复用。

export type SortKey = 'default' | 'latest' | 'count';

/** 归一化文本用于搜索匹配：转小写并去掉空白与常见标点。 */
export const normalizeText = (value: string): string =>
  value.toLowerCase().replace(/[\s·,，.。/\\-]+/g, '');

/** 人物是否命中搜索词（匹配 名/英文名/职务/机构/id/领域）。空词命中全部。 */
export const matchesQuery = (figure: PersonProfile, query: string): boolean => {
  const q = normalizeText(query);
  if (!q) return true;
  const haystack = normalizeText(
    [figure.name, figure.en_name, figure.role, figure.org, figure.id, ...figure.topics].join(' ')
  );
  return haystack.includes(q);
};

/** 排序：默认保持原序；最新动态按 latest_date 降序；发言数按 item_count 降序。 */
export const sortFigures = (figures: PersonProfile[], sort: SortKey): PersonProfile[] => {
  if (sort === 'default') return figures;
  const copy = [...figures];
  if (sort === 'latest') {
    copy.sort((a, b) => (b.latest_date || '').localeCompare(a.latest_date || ''));
  } else if (sort === 'count') {
    copy.sort((a, b) => b.item_count - a.item_count);
  }
  return copy;
};

/** 按自定义顺序排列：order 里的先按其顺序，其余维持原序追加在后（稳定）。 */
export const applyCustomOrder = (figures: PersonProfile[], order: string[]): PersonProfile[] => {
  if (!order.length) return figures;
  const rank = new Map(order.map((id, i) => [id, i]));
  return figures
    .map((figure, index) => ({ figure, index }))
    .sort((a, b) => {
      const ra = rank.has(a.figure.id) ? rank.get(a.figure.id)! : Number.MAX_SAFE_INTEGER;
      const rb = rank.has(b.figure.id) ? rank.get(b.figure.id)! : Number.MAX_SAFE_INTEGER;
      return ra !== rb ? ra - rb : a.index - b.index;
    })
    .map(entry => entry.figure);
};

/** 今日跨人物要闻：每人取最具代表性的一条（items[0]），再跨人物按时效×影响力排名。 */
export const rankCrossFigureHeadlines = (
  figures: PersonProfile[]
): Array<{ figure: PersonProfile; item: PersonVoiceItem }> => {
  const perFigure = figures.flatMap(figure =>
    figure.items[0] ? [{ figure, item: figure.items[0] }] : []
  );
  perFigure.sort((a, b) => {
    const byDate = (b.item.reported_date || '').localeCompare(a.item.reported_date || '');
    if (byDate !== 0) return byDate;
    return b.item.importance_score - a.item.importance_score;
  });
  return perFigure;
};

/** 统计该人物「今天」的发言条数（reported_date 命中传入的当天日期串 YYYY-MM-DD）。 */
export const countTodayItems = (figure: PersonProfile, today: string): number =>
  figure.items.reduce((n, item) => (item.reported_date === today ? n + 1 : n), 0);

/** 今日热点：跨全部（已关注）人物，统计当天发言里各主题标签的出现次数，降序取 top N。 */
export const aggregateTopicHeat = (
  figures: PersonProfile[],
  today: string,
  limit = 8
): Array<{ tag: string; count: number }> => {
  const counts = new Map<string, number>();
  for (const figure of figures) {
    for (const item of figure.items) {
      if (item.reported_date !== today) continue;
      for (const tag of item.tags) {
        counts.set(tag, (counts.get(tag) || 0) + 1);
      }
    }
  }
  return Array.from(counts.entries())
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => (b.count !== a.count ? b.count - a.count : a.tag.localeCompare(b.tag)))
    .slice(0, limit);
};

/** 把一条人物发言组装成信号流消息（人物墙/要闻/详情共用，避免重复）。 */
export const buildSignalPayload = (
  figure: PersonProfile,
  item: PersonVoiceItem
): RealtimeMessageCreate => ({
  title: `${figure.name}：${item.title}`,
  content: `${figure.name}（${figure.role} · ${figure.org}）近期发言 ｜ 来源：${item.source_name}`,
  topic: 'people-voice',
  severity: item.importance_score >= 80 ? 'warning' : 'info',
  source_name: item.source_name,
  source_type: 'web_page',
  url: item.url,
  tags: [figure.name, ...item.tags],
  metadata: {
    figure_id: figure.id,
    figure_name: figure.name,
    reported_date: item.reported_date,
    importance_score: item.importance_score,
    pushed_from: 'people-spotlight',
  },
});
