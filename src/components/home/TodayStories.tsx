import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import {
  getMyStories, getTodayStories, logStoryEvent,
  type MassBrief, type StoryCard, type StoryHealth,
} from '../../services/storyService';
import './TodayStories.css';

interface TodayStoriesProps {
  loggedIn: boolean;
  onOpenStory?: (storyId: string) => void;
  onOpenSymbol?: (symbol: string) => void;
}

const KIND_LABEL: Record<string, string> = { company: '公司', theme: '主题', macro: '宏观' };

// StoryRow 只读这些字段；完整 StoryCard 与简报条目映射都满足它，避免不安全 cast。
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

const StoryRow: React.FC<{
  card: StoryRowCard;
  index: number;
  surface: string;
  onOpenStory?: (id: string) => void;
  onOpenSymbol?: (symbol: string) => void;
}> = ({ card, index, surface, onOpenStory, onOpenSymbol }) => {
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
    onOpenStory?.(card.story_id);
  };

  const why = (card.why_personal && card.why_personal.length ? card.why_personal : card.why) || [];

  return (
    <li className="dfx-story" ref={ref}>
      <button type="button" className="dfx-story-main" onClick={open}>
        <span className="dfx-story-topline">
          <span className={`dfx-story-kind kind-${card.kind}`}>{KIND_LABEL[String(card.kind)] || '事件'}</span>
          {card.event_label && <span className="dfx-story-event">{card.event_label}</span>}
          <span className="dfx-story-evidence">{card.item_count} 条 · {card.source_count} 来源</span>
        </span>
        <span className="dfx-story-title">{card.title}</span>
        {card.summary && <span className="dfx-story-summary">{card.summary}</span>}
        {why.length > 0 && (
          <span className="dfx-story-why">
            {why.slice(0, 3).map((chip, i) => <em key={i}>{chip}</em>)}
          </span>
        )}
      </button>
      {card.symbol && onOpenSymbol && (
        <button
          type="button"
          className="dfx-story-symbol"
          onClick={() => {
            fire({ story_id: card.story_id, action: 'click', surface, position: index, symbol: card.symbol || '' });
            onOpenSymbol(card.symbol as string);
          }}
        >
          {card.entity_name || card.symbol} →
        </button>
      )}
    </li>
  );
};

const TodayStories: React.FC<TodayStoriesProps> = ({ loggedIn, onOpenStory, onOpenSymbol }) => {
  const [brief, setBrief] = useState<MassBrief | null>(null);
  const [health, setHealth] = useState<StoryHealth | null>(null);
  const [mine, setMine] = useState<StoryCard[] | null>(null);
  const [personalized, setPersonalized] = useState(false);
  const [loading, setLoading] = useState(true);

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
  const personalStories = useMemo(() => (mine || []).slice(0, 8), [mine]);

  if (!brief && !loading) {
    return (
      <section className="dfx-today" aria-label="今日事件看板">
        <div className="dfx-today-empty">今天站内还在聚合事件，稍后再来看看。</div>
      </section>
    );
  }

  return (
    <section className="dfx-today" aria-label="今日事件看板">
      <div className="dfx-today-head">
        <div>
          <span className="dfx-home-card-eyebrow">TODAY&apos;S STORIES</span>
          <h2>今天站内发生了什么</h2>
          <p>把上千条快讯、文章、研报、机构纪要聚成「一件事」，先看结论再点开原文核对。</p>
        </div>
        <button type="button" className="dfx-today-refresh" onClick={() => void load()} disabled={loading}>
          <ReloadOutlined spin={loading} /> 更新
        </button>
      </div>

      {brief?.headline && (
        <div className="dfx-today-headline">{brief.headline}</div>
      )}

      {personalized && personalStories.length > 0 && (
        <div className="dfx-today-block">
          <div className="dfx-today-block-head">
            <h3>为你推荐</h3>
            <span>按你的自选、订阅与浏览记录排序 · 标出「为什么推给你」</span>
          </div>
          <ul className="dfx-story-list">
            {personalStories.map((card, index) => (
              <StoryRow key={card.story_id} card={card} index={index} surface="today_me"
                        onOpenStory={onOpenStory} onOpenSymbol={onOpenSymbol} />
            ))}
          </ul>
        </div>
      )}

      <div className="dfx-today-grid">
        {sections.map(section => (
          <div className="dfx-today-block" key={section.key}>
            <div className="dfx-today-block-head">
              <h3>{section.title}</h3>
              <span>{section.note}</span>
            </div>
            <ul className="dfx-story-list">
              {section.items.slice(0, 4).map((item, index) => {
                const card: StoryRowCard = {
                  story_id: item.story_id, kind: item.kind, title: item.subject || item.title,
                  summary: item.summary, symbol: item.symbol, entity_name: item.entity_name,
                  event_label: item.event_label, item_count: item.item_count,
                  source_count: item.source_count, theme_codes: [],
                  why: item.verify && item.verify.length ? [`待核对：${item.verify[0]}`] : [],
                };
                return (
                  <StoryRow key={item.story_id} card={card} index={index} surface={`today_${section.key}`}
                            onOpenStory={onOpenStory} onOpenSymbol={onOpenSymbol} />
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className="dfx-today-foot">
        {brief?.disclaimer || '以上为站内公开内容的事实聚合，不构成投资建议。'}
        {health ? ` · 已聚合 ${health.stories} 件事、${health.story_items} 条内容` : ''}
      </div>
    </section>
  );
};

export default TodayStories;
