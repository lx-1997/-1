import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { AsyncButton } from './AsyncButton';
import { AsyncState, MarketLoadStatus } from './AsyncState';
import { QuoteCard } from './QuoteCard';
import { QuoteItem } from './types';
import { formatQuoteTime } from './formatQuote';

export interface WatchlistPanelProps {
  items: readonly QuoteItem[];
  title?: string;
  status?: MarketLoadStatus;
  refreshing?: boolean;
  error?: string;
  selectedId?: string;
  watchedIds?: readonly string[];
  pendingIds?: readonly string[];
  colorConvention?: 'cn' | 'international';
  onRefresh?: () => void | Promise<void>;
  onSelect?: (item: QuoteItem) => void;
  onToggleWatch?: (item: QuoteItem) => void | Promise<void>;
  emptyAction?: React.ReactNode;
  className?: string;
}

type SortOrder = 'default' | 'change-desc' | 'change-asc' | 'name';
const hasChange = (item: QuoteItem) => item.quality !== 'unavailable' && typeof item.changePercent === 'number' && Number.isFinite(item.changePercent);
const hasNewerTime = (previous: QuoteItem, next: QuoteItem) => {
  const before = formatQuoteTime(previous.updatedAt), after = formatQuoteTime(next.updatedAt);
  return Boolean(before && after && new Date(after.iso).getTime() > new Date(before.iso).getTime());
};

/** Presentation only: requests, account isolation and polling belong to the business container. */
export function WatchlistPanel({ items, title = '观察池', status = 'ready', refreshing = false,
  error, selectedId, watchedIds = [], pendingIds = [], colorConvention = 'cn',
  onRefresh, onSelect, onToggleWatch, emptyAction, className }: WatchlistPanelProps) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortOrder>('default');
  const [actionError, setActionError] = useState('');
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [internalRefreshing, setInternalRefreshing] = useState(false);
  const [internalPendingIds, setInternalPendingIds] = useState<readonly string[]>([]);
  const refreshLock = useRef(false);
  const watchLocks = useRef(new Set<string>());
  const failedSnapshot = useRef<readonly QuoteItem[]>([]);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!refreshFailed) return;
    const recovered = failedSnapshot.current.every(previous => {
      const next = items.find(item => item.id === previous.id);
      return !next || hasNewerTime(previous, next);
    });
    if (recovered) {
      setRefreshFailed(false);
      setActionError('');
    }
  }, [items, refreshFailed]);
  const keyword = query.trim().toLocaleLowerCase();
  const watched = useMemo(() => new Set(watchedIds), [watchedIds]);
  const pending = useMemo(() => new Set([...pendingIds, ...internalPendingIds]), [pendingIds, internalPendingIds]);
  const filtered = useMemo(() => {
    const result = items.filter(item => `${item.name} ${item.symbol}`.toLocaleLowerCase().includes(keyword));
    if (sort === 'name') return result.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    if (sort === 'default') return result;
    return result.sort((a, b) => {
      const aValid = hasChange(a), bValid = hasChange(b);
      if (!aValid || !bValid) return Number(bValid) - Number(aValid);
      return (a.changePercent! - b.changePercent!) * (sort === 'change-desc' ? -1 : 1);
    });
  }, [items, keyword, sort]);
  const refreshingNow = refreshing || status === 'loading' || internalRefreshing;
  const refresh = async () => {
    if (!onRefresh || refreshLock.current || refreshing || status === 'loading') return;
    refreshLock.current = true;
    setInternalRefreshing(true);
    setActionError('');
    setRefreshFailed(false);
    try {
      await onRefresh();
    } catch {
      if (mounted.current) {
        failedSnapshot.current = items;
        setRefreshFailed(true);
        setActionError('刷新失败，请稍后重试。当前数据可能已过期。');
      }
    } finally {
      refreshLock.current = false;
      if (mounted.current) setInternalRefreshing(false);
    }
  };
  const toggleWatch = onToggleWatch ? async (item: QuoteItem) => {
    if (pending.has(item.id) || watchLocks.current.has(item.id)) return;
    watchLocks.current.add(item.id);
    setInternalPendingIds(Array.from(watchLocks.current));
    setActionError('');
    try {
      await onToggleWatch(item);
    } catch {
      if (mounted.current) setActionError('监控设置未保存，请重试。');
    } finally {
      watchLocks.current.delete(item.id);
      if (mounted.current) setInternalPendingIds(Array.from(watchLocks.current));
    }
  } : undefined;
  const displayStatus = refreshingNow && status === 'ready' ? 'loading' : status;
  const staleAfterFailure = (item: QuoteItem) => {
    if (!refreshFailed) return false;
    const previous = failedSnapshot.current.find(quote => quote.id === item.id);
    return Boolean(previous && !hasNewerTime(previous, item));
  };

  return (
    <section className={`dc-market-panel ${className || ''}`.trim()} aria-labelledby={`${id}-title`}>
      <header className="dc-market-panel__header">
        <div><p className="dc-market-eyebrow">稻草财经 · 行情监控</p><h2 id={`${id}-title`}>{title}</h2></div>
        {onRefresh && <AsyncButton onAction={refresh} busy={refreshingNow} busyLabel="正在刷新行情"
          >刷新行情</AsyncButton>}
      </header>
      <div className="dc-market-toolbar">
        <div className="dc-market-search"><label htmlFor={`${id}-search`}>搜索观察池</label>
          <input id={`${id}-search`} type="search" value={query} placeholder="名称或代码"
            onChange={event => setQuery(event.target.value)} />
        </div>
        <div className="dc-market-sort"><label htmlFor={`${id}-sort`}>排序</label>
          <select id={`${id}-sort`} value={sort} onChange={event => setSort(event.target.value as SortOrder)}>
            <option value="default">原始顺序</option><option value="change-desc">涨幅优先</option>
            <option value="change-asc">跌幅优先</option><option value="name">名称</option>
          </select>
        </div>
      </div>
      {actionError && <p role="alert" className="dc-market-action-error">{actionError}</p>}
      <div className="dc-market-data" aria-busy={refreshingNow || undefined}>
        <AsyncState status={displayStatus} hasData={items.length > 0} error={error} onRetry={onRefresh ? refresh : undefined}>
          {filtered.length > 0 ? (
            <ul className="dc-market-grid">
              {filtered.map(item => <li key={item.id}><QuoteCard item={
                (refreshingNow || status === 'error' || staleAfterFailure(item)) && item.quality === 'live'
                  ? { ...item, quality: 'stale' } : item}
                selected={item.id === selectedId} watched={watched.has(item.id)} pending={pending.has(item.id)}
                colorConvention={colorConvention} onSelect={onSelect} onToggleWatch={toggleWatch}
                onActionError={() => setActionError('监控设置未保存，请重试。')} /></li>)}
            </ul>
          ) : (
            <div className="dc-market-empty"><span aria-hidden="true" className="dc-market-empty__icon">◎</span>
              <h3>{items.length ? '没有匹配的标的' : '观察池为空'}</h3>
              <p>{items.length ? '试试其他名称或代码。' : '添加关注的标的，在这里查看报价与数据来源。'}</p>
              {keyword ? <AsyncButton onAction={() => setQuery('')}>清除搜索</AsyncButton> : emptyAction}
            </div>
          )}
        </AsyncState>
      </div>
      <p role="status" aria-live="polite" aria-atomic="true" className="dc-market-panel__summary">
        {refreshingNow ? items.length ? '正在更新行情，保留上次数据' : '正在连接行情' : `显示 ${filtered.length} / ${items.length} 个标的`}
      </p>
    </section>
  );
}
