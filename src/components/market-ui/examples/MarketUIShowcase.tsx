import React, { useEffect, useRef, useState } from 'react';
import { useTheme } from '../../../context/ThemeContext';
import { AsyncButton, QuoteItem, WatchlistPanel } from '..';

// Deliberately synthetic fixtures. Never present demo prices as live exchange quotes.
const DEMO_QUOTES: readonly QuoteItem[] = [
  { id: 'HK:0700', symbol: '0700.HK', name: '腾讯控股', market: 'HK', currency: 'HKD', price: 612.5,
    changePercent: 1.86, quality: 'mock', source: '本地演示样例', updatedAt: '2026-10-12T09:35:00+08:00' },
  { id: 'US:AAPL', symbol: 'AAPL', name: '苹果', market: 'US', currency: 'USD', price: 238.72,
    changePercent: -0.64, quality: 'mock', source: '本地演示样例', updatedAt: '2026-10-12T09:35:00+08:00' },
  { id: 'CN:600519', symbol: '600519.SH', name: '贵州茅台', market: 'CN', currency: 'CNY', price: 1568.4,
    changePercent: 0, quality: 'mock', source: '本地演示样例', updatedAt: '2026-10-12T09:35:00+08:00' },
  { id: 'HK:3690', symbol: '3690.HK', name: '美团', market: 'HK', currency: 'HKD', price: null,
    changePercent: null, quality: 'unavailable', source: '本地演示样例', delayNote: '演示报价缺失的显示方式' },
];
type Scenario = 'ready' | 'loading' | 'empty' | 'error' | 'stale';

export default function MarketUIShowcase() {
  const { theme, setTheme } = useTheme();
  const [scenario, setScenario] = useState<Scenario>('ready');
  const [selectedId, setSelectedId] = useState<string>();
  const [watchedIds, setWatchedIds] = useState<readonly string[]>(['HK:0700']);
  const [refreshing, setRefreshing] = useState(false);
  const [failRefresh, setFailRefresh] = useState(false);
  const [notice, setNotice] = useState('');
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const pause = () => new Promise<void>(resolve => window.setTimeout(resolve, 800));
  const refresh = async () => {
    setRefreshing(true);
    setNotice('');
    try {
      await pause();
      if (!mounted.current) return;
      if (failRefresh) throw new Error('演示网络错误');
      setScenario('ready');
      setNotice('演示刷新完成，样例报价保持不变。');
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  };
  const toggleWatch = async (item: QuoteItem) => {
    await pause();
    if (!mounted.current) return;
    setWatchedIds(ids => ids.includes(item.id) ? ids.filter(id => id !== item.id) : [...ids, item.id]);
  };
  const items = scenario === 'empty' || scenario === 'loading' || scenario === 'error' ? [] : DEMO_QUOTES;
  const status = scenario === 'loading' ? 'loading' : scenario === 'error' || scenario === 'stale' ? 'error' : 'ready';

  return (
    <main className="dc-market-showcase">
      <header>
        <p className="dc-market-eyebrow">DAOCAIJING / UI COMPONENTS</p>
        <h1>行情组件工作台</h1>
        <p>在不同数据状态下检查报价、观察池和异步操作。下方所有报价均为演示样例。</p>
      </header>
      <div className="dc-market-showcase-controls">
        <label htmlFor="market-demo-scenario">数据状态
          <select id="market-demo-scenario" value={scenario} onChange={event => setScenario(event.target.value as Scenario)}>
            <option value="ready">有数据</option><option value="loading">首次加载</option><option value="empty">空观察池</option>
            <option value="error">首次加载失败</option><option value="stale">刷新失败保留旧数据</option>
          </select>
        </label>
        <label><input type="checkbox" checked={failRefresh} onChange={event => setFailRefresh(event.target.checked)} />模拟刷新失败</label>
        <AsyncButton onAction={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>切换为{theme === 'dark' ? '浅色' : '深色'}</AsyncButton>
      </div>
      <WatchlistPanel items={items} status={status} refreshing={refreshing}
        error="行情服务暂时不可用" selectedId={selectedId} watchedIds={watchedIds}
        onRefresh={refresh} onSelect={item => { setSelectedId(item.id); setNotice(`已选择 ${item.name}，可由业务容器打开详情。`); }}
        onToggleWatch={toggleWatch} emptyAction={<AsyncButton variant="primary" onAction={() => setScenario('ready')}>载入演示标的</AsyncButton>} />
      <p role="status" className="dc-market-showcase-note">{notice}</p>
      <p className="dc-market-showcase-note">默认红涨绿跌；报价时间统一使用北京时间。支持 Tab、Enter 和原生筛选控件的键盘操作。</p>
    </main>
  );
}
