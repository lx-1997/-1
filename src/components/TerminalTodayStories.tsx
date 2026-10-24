import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  getMyStories, getTodayStories, logStoryEvent,
  type MassBrief, type StoryCard, type StoryHealth,
} from '../services/storyService';
import './TerminalTodayStories.css';

// 终端版「今日事件看板」。生产是 TERMINAL_ONLY，整页只渲染 FinancialTerminal，
// 完整版 HomePage 不进生产包，所以事件簇看板必须在终端内自带一份（bbt- 样式、无 antd 图标，
// 避免把 antd 拉进终端主包）。数据与埋点复用 storyService。

interface TerminalTodayStoriesProps {
  loggedIn: boolean;
  onSelectSymbol: (symbol: string) => void;
  onOpenMarket: () => void;
  onRequireLogin: () => void;
}

const KIND_LABEL: Record<string, string> = { company: '公司', theme: '主题', macro: '宏观' };

// 只读这些字段；完整 StoryCard 与简报条目映射都满足它，避免不安全 cast。
interface StoryRowCard {
  story_id: string;
  kind: string;
  title: string;
  summary?: string;
  symbol?: string;
  entity_name?: string;
  event_label?: string;
  item_count: number;
  source_count: number;
  theme_codes?: string[];
  why?: string[];
  why_personal?: string[];
}

// 埋点即发即忘：失败不打断浏览，也不 await。
function fire(payload: Parameters<typeof logStoryEvent>[0]) {
  void logStoryEvent(payload).catch(() => undefined);
}

// 与 CSS 的 820px 主断点保持一致：移动端收窄每个分区的条数，减少滚动疲劳。
function useIsMobile(): boolean {
  const query = '(max-width: 820px)';
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMobile(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return mobile;
}

const StoryRow: React.FC<{
  card: StoryRowCard;
  index: number;
  surface: string;
  onOpen: (card: StoryRowCard) => void;
  onSelectSymbol: (symbol: string) => void;
}> = ({ card, index, surface, onOpen, onSelectSymbol }) => {
  const ref = useRef<HTMLLIElement | null>(null);
  const impressed = useRef(false);

  useEffect(() => {
    const node = ref.current;
    if (!node || impressed.current) return;
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting && !impressed.current) {
          impressed.current = true;
          fire({
            story_id: card.story_id, action: 'impression', surface, position: index,
            symbol: card.symbol || '', kind: String(card.kind), themes: card.theme_codes || [],
          });
          observer.disconnect();
        }
      });
    }, { threshold: 0.5 });
    observer.observe(node);
    return () => observer.disconnect();
  }, [card, index, surface]);

  const open = () => {
    fire({
      story_id: card.story_id, action: 'click', surface, position: index,
      symbol: card.symbol || '', kind: String(card.kind), themes: card.theme_codes || [],
    });
    onOpen(card);
  };

  const why = (card.why_personal && card.why_personal.length ? card.why_personal : card.why) || [];

  return (
    <li className="bbt-story" ref={ref}>
      <button type="button" className="bbt-story-main" onClick={open}>
        <span className="bbt-story-topline">
          <span className={`bbt-story-kind kind-${card.kind}`}>{KIND_LABEL[String(card.kind)] || '事件'}</span>
          {card.event_label && <span className="bbt-story-event">{card.event_label}</span>}
          <span className="bbt-story-evidence">{card.item_count} 条 · {card.source_count} 来源</span>
        </span>
        <span className="bbt-story-title">{card.title}</span>
        {card.summary && <span className="bbt-story-summary">{card.summary}</span>}
        {why.length > 0 && (
          <span className="bbt-story-why">
            {why.slice(0, 2).map((chip, i) => <em key={i}>{chip}</em>)}
          </span>
        )}
      </button>
      {card.symbol && (
        <button
          type="button"
          className="bbt-story-symbol"
          onClick={() => {
            fire({ story_id: card.story_id, action: 'click', surface, position: index, symbol: card.symbol || '' });
            onSelectSymbol(card.symbol as string);
          }}
        >
          {card.entity_name || card.symbol} →
        </button>
      )}
    </li>
  );
};

const TerminalTodayStories: React.FC<TerminalTodayStoriesProps> = ({
  loggedIn, onSelectSymbol, onOpenMarket, onRequireLogin,
}) => {
  const [brief, setBrief] = useState<MassBrief | null>(null);
  const [health, setHealth] = useState<StoryHealth | null>(null);
  const [mine, setMine] = useState<StoryCard[] | null>(null);
  const [personalized, setPersonalized] = useState(false);
  const [loading, setLoading] = useState(true);
  const isMobile = useIsMobile();

  const load = useCallback(async () => {
    setLoading(true);
    const [todayResult, meResult] = await Promise.allSettled([
      getTodayStories(12),
      loggedIn ? getMyStories(8) : Promise.resolve(null),
    ]);
    if (todayResult.status === 'fulfilled') {
      setBrief(todayResult.value.brief);
      setHealth(todayResult.value.health);
    }
    if (meResult.status === 'fulfilled' && meResult.value) {
      setMine(meResult.value.stories);
      setPersonalized(meResult.value.personalized);
    } else {
      setMine(null);
      setPersonalized(false);
    }
    setLoading(false);
  }, [loggedIn]);

  useEffect(() => { void load(); }, [load]);

  const sections = useMemo(() => (brief?.sections || []).filter(s => s.items.length > 0), [brief]);
  const personalLimit = isMobile ? 4 : 6;
  const perSection = isMobile ? 2 : 4;
  const personalStories = useMemo(() => (mine || []).slice(0, personalLimit), [mine, personalLimit]);

  // 点击故事：有标的就选中该标的下钻，否则打开今日复盘。
  const openStory = useCallback((card: StoryRowCard) => {
    if (card.symbol) onSelectSymbol(card.symbol);
    else onOpenMarket();
  }, [onSelectSymbol, onOpenMarket]);

  if (!brief && !loading) {
    return (
      <section className="bbt-stories" aria-label="今日事件看板">
        <div className="bbt-stories-head">
          <span>TODAY&apos;S STORIES</span>
          <h2>今天站内发生了什么</h2>
        </div>
        <div className="bbt-stories-empty">站内正在把快讯、文章、研报、机构纪要聚成「一件事」，稍后再来看看。</div>
      </section>
    );
  }

  return (
    <section className="bbt-stories" aria-label="今日事件看板">
      <div className="bbt-stories-head">
        <div>
          <span>TODAY&apos;S STORIES</span>
          <h2>今天站内发生了什么</h2>
          <p>把上千条快讯、文章、研报、机构纪要聚成「一件事」，先看结论再点开原文核对。</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading}>{loading ? '更新中…' : '更新'}</button>
      </div>

      {brief?.headline && <div className="bbt-stories-headline">{brief.headline}</div>}

      {personalized && personalStories.length > 0 && (
        <div className="bbt-stories-block">
          <div className="bbt-stories-block-head">
            <b>为你推荐</b>
            <small>按你的自选、订阅与浏览记录排序 · 标出「为什么推给你」</small>
          </div>
          <ul className="bbt-story-list">
            {personalStories.map((card, index) => (
              <StoryRow key={card.story_id} card={card} index={index} surface="today_me"
                        onOpen={openStory} onSelectSymbol={onSelectSymbol} />
            ))}
          </ul>
        </div>
      )}

      {!loggedIn && (
        <button type="button" className="bbt-stories-login" onClick={onRequireLogin}>
          登录后，这一栏会按你的自选与浏览记录做「千人千面」排序 →
        </button>
      )}

      <div className="bbt-stories-grid">
        {sections.map(section => (
          <div className="bbt-stories-block" key={section.key}>
            <div className="bbt-stories-block-head">
              <b>{section.title}</b>
              <small>{section.note}</small>
            </div>
            <ul className="bbt-story-list">
              {section.items.slice(0, perSection).map((item, index) => {
                const card: StoryRowCard = {
                  story_id: item.story_id, kind: item.kind, title: item.subject || item.title,
                  summary: item.summary, symbol: item.symbol, entity_name: item.entity_name,
                  event_label: item.event_label, item_count: item.item_count,
                  source_count: item.source_count, theme_codes: [],
                  why: item.verify && item.verify.length ? [`待核对：${item.verify[0]}`] : [],
                };
                return (
                  <StoryRow key={item.story_id} card={card} index={index} surface={`today_${section.key}`}
                            onOpen={openStory} onSelectSymbol={onSelectSymbol} />
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className="bbt-stories-foot">
        {brief?.disclaimer || '以上为站内公开内容的事实聚合，不构成投资建议。'}
        {health ? ` · 已聚合 ${health.stories} 件事、${health.story_items} 条内容` : ''}
      </div>
    </section>
  );
};

export default TerminalTodayStories;
