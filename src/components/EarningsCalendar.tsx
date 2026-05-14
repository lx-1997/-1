import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, Segmented, Select, Space, Spin, Tag, Typography } from 'antd';
import {
  BarChartOutlined,
  CalendarOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  LineChartOutlined,
  ReloadOutlined,
  RiseOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { EarningsCalendarEvent, getEarningsCalendar } from '../services/earningsCalendarService';

const { Text } = Typography;

interface EarningsCalendarProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

type EventFilter = 'all' | 'upcoming' | 'synced' | 'pending';
type Horizon = '3month' | '6month' | '12month';

const timeLabels: Record<string, string> = {
  bmo: '盘前',
  before_market_open: '盘前',
  'time-pre-market': '盘前',
  amc: '盘后',
  after_market_close: '盘后',
  'time-after-hours': '盘后',
  dmh: '盘中',
  during_market_hours: '盘中',
  'time-not-supplied': '时间待确认'
};

const formatDate = (date?: string | null): string => {
  if (!date) {
    return '待同步';
  }

  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return date;
  }

  return parsed.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short'
  });
};

const formatFullDate = (date?: string | null): string => {
  if (!date) {
    return '等待财报 API 返回日期';
  }

  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return date;
  }

  return parsed.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'long'
  });
};

const formatNumber = (value?: number | null, precision = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }

  return value.toFixed(precision);
};

const formatPercent = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }

  return `${value.toFixed(2)}%`;
};

const formatMarketCap = (value?: number): string => {
  if (!value || !Number.isFinite(value)) {
    return '--';
  }

  if (value >= 1_000_000_000_000) {
    return `$${(value / 1_000_000_000_000).toFixed(2)}T`;
  }

  if (value >= 1_000_000_000) {
    return `$${(value / 1_000_000_000).toFixed(1)}B`;
  }

  return `$${value.toLocaleString('en-US')}`;
};

const getEventTimeLabel = (event: EarningsCalendarEvent): string => {
  if (!event.time_of_day) {
    return '时间待确认';
  }

  return timeLabels[event.time_of_day.toLowerCase()] || event.time_of_day;
};

const getConfidenceTag = (event: EarningsCalendarEvent) => {
  if (event.confidence === 'pending_provider') {
    return <Tag color="default">待同步</Tag>;
  }

  if (event.confidence === 'confirmed') {
    return <Tag color="green">已确认</Tag>;
  }

  return <Tag color="blue">预估</Tag>;
};

const getSourceTag = (event: EarningsCalendarEvent) => {
  if (event.provider === 'alpha_vantage') {
    return <Tag color="cyan">Alpha Vantage</Tag>;
  }

  if (event.provider === 'nasdaq_public') {
    return <Tag color="blue">{event.source_name || 'Nasdaq 公共源'}</Tag>;
  }

  return <Tag color="default">{event.source_name}</Tag>;
};

const getProviderLabel = (provider: string): string => {
  if (provider === 'watchlist_template') {
    return '关注池模板';
  }

  if (provider === 'alpha_vantage') {
    return 'Alpha Vantage';
  }

  if (provider === 'nasdaq_public') {
    return 'Nasdaq 公共日历';
  }

  if (provider === 'mixed') {
    return '多数据源';
  }

  return provider;
};

const getWarningMessage = (warnings: string[]): string => {
  const warningText = warnings.join(' | ');
  const warning = warnings[0] || '';
  if (warning.includes('ALPHAVANTAGE_API_KEY')) {
    return '未配置 Alpha Vantage key，当前显示关注池模板，日期和 EPS 预期等待 API 同步。';
  }

  if (
    warningText.includes('Nasdaq public calendar did not return dates for')
    || warningText.includes('Nasdaq public calendar did not return dated events')
  ) {
    return 'Nasdaq 公共日历已同步部分公司，未命中披露日期的标的会继续用 Nasdaq 公共预测补 EPS。';
  }

  if (warningText.includes('Nasdaq public calendar skipped')) {
    return 'Nasdaq 公共日历部分日期请求较慢，当前已展示可用日历和预测数据。';
  }

  if (warningText.includes('Nasdaq public calendar and forecast returned no matching')) {
    return 'Nasdaq 公共日历暂未命中关注池公司，当前用公司观察模板兜底。';
  }

  return warning || '财报日历服务暂不可用';
};

const isUpcomingEvent = (event: EarningsCalendarEvent): boolean => {
  if (!event.report_date) {
    return false;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return new Date(`${event.report_date}T00:00:00`).getTime() >= today.getTime();
};

const EarningsCalendar: React.FC<EarningsCalendarProps> = ({ appState, onStockSelect }) => {
  const [events, setEvents] = useState<EarningsCalendarEvent[]>([]);
  const [provider, setProvider] = useState('none');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<EventFilter>('all');
  const [sector, setSector] = useState<string>('all');
  const [horizon, setHorizon] = useState<Horizon>('3month');
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);

  const stockBySymbol = useMemo(() => {
    return new Map(appState.stocks.map(stock => [stock.symbol, stock]));
  }, [appState.stocks]);

  const loadEvents = useCallback(async () => {
    const symbols = appState.stocks.map(stock => stock.symbol);
    if (symbols.length === 0) {
      setEvents([]);
      return;
    }

    setIsLoading(true);
    try {
      const response = await getEarningsCalendar(symbols, horizon);
      setEvents(response.events);
      setProvider(response.provider);
      setWarnings(response.warnings);
      setSelectedSymbol(prev => prev || response.events[0]?.symbol || null);
    } catch (error) {
      console.warn('Earnings calendar refresh failed:', error);
      setWarnings(['财报日历服务暂不可用']);
    } finally {
      setIsLoading(false);
    }
  }, [appState.stocks, horizon]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  const sectorOptions = useMemo(() => {
    const sectors = appState.stocks.map(stock => stock.sector).filter(Boolean);
    return Array.from(new Set(sectors));
  }, [appState.stocks]);

  const filteredEvents = useMemo(() => {
    const keyword = query.trim().toLowerCase();

    return events.filter(event => {
      const stock = stockBySymbol.get(event.symbol);
      const matchesQuery = !keyword
        || event.symbol.toLowerCase().includes(keyword)
        || event.name.toLowerCase().includes(keyword)
        || stock?.name.toLowerCase().includes(keyword);
      const matchesSector = sector === 'all' || stock?.sector === sector;
      const matchesFilter = filter === 'all'
        || (filter === 'upcoming' && isUpcomingEvent(event))
        || (filter === 'synced' && event.provider !== 'watchlist_template')
        || (filter === 'pending' && event.provider === 'watchlist_template');

      return matchesQuery && matchesSector && matchesFilter;
    });
  }, [events, filter, query, sector, stockBySymbol]);

  useEffect(() => {
    if (filteredEvents.length === 0) {
      setSelectedSymbol(null);
      return;
    }

    if (!selectedSymbol || !filteredEvents.some(event => event.symbol === selectedSymbol)) {
      setSelectedSymbol(filteredEvents[0].symbol);
    }
  }, [filteredEvents, selectedSymbol]);

  const selectedEvent = filteredEvents.find(event => event.symbol === selectedSymbol) || filteredEvents[0];
  const selectedStock = selectedEvent ? stockBySymbol.get(selectedEvent.symbol) : undefined;
  const syncedCount = events.filter(event => event.provider !== 'watchlist_template').length;
  const pendingCount = events.length - syncedCount;
  const upcomingCount = events.filter(isUpcomingEvent).length;
  const highFocusCount = events.filter(event => stockBySymbol.get(event.symbol)?.focusLevel === 'high').length;

  const groupedEvents = useMemo(() => {
    return filteredEvents.reduce<Record<string, EarningsCalendarEvent[]>>((groups, event) => {
      const key = event.report_date || 'pending';
      return {
        ...groups,
        [key]: [...(groups[key] || []), event]
      };
    }, {});
  }, [filteredEvents]);

  return (
    <div className="earnings-shell">
      <div className="earnings-header">
        <div>
          <div className="dashboard-eyebrow">EARNINGS CALENDAR</div>
          <h2 className="dashboard-title">财报日历</h2>
          <div className="dashboard-subtitle">关注池公司财报窗口、预期差和公司级观察清单。</div>
        </div>
        <Space size={8} wrap>
          <Tag color={provider === 'watchlist_template' ? 'default' : 'cyan'}>
            {getProviderLabel(provider)}
          </Tag>
          <Select
            value={horizon}
            onChange={setHorizon}
            size="middle"
            style={{ width: 116 }}
            options={[
              { label: '3 个月', value: '3month' },
              { label: '6 个月', value: '6month' },
              { label: '12 个月', value: '12month' }
            ]}
          />
          <Button icon={<ReloadOutlined />} loading={isLoading} onClick={loadEvents}>
            刷新
          </Button>
        </Space>
      </div>

      {warnings.length > 0 && (
        <Alert
          className="earnings-alert"
          type={syncedCount > 0 ? 'info' : 'warning'}
          showIcon
          message={getWarningMessage(warnings)}
        />
      )}

      <div className="earnings-summary-grid">
        <div className="metric-tile">
          <div className="metric-label"><CalendarOutlined /><span>覆盖公司</span></div>
          <div className="metric-value">{events.length}</div>
          <div className="metric-note">{syncedCount} 个已同步</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><ClockCircleOutlined /><span>即将披露</span></div>
          <div className="metric-value">{upcomingCount}</div>
          <div className="metric-note">未来窗口</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><DatabaseOutlined /><span>待同步</span></div>
          <div className="metric-value">{pendingCount}</div>
          <div className="metric-note">等待 API 日期</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><RiseOutlined /><span>高关注</span></div>
          <div className="metric-value">{highFocusCount}</div>
          <div className="metric-note">重点盯盘</div>
        </div>
      </div>

      <div className="earnings-toolbar">
        <Segmented
          value={filter}
          onChange={(value) => setFilter(value as EventFilter)}
          options={[
            { label: '全部', value: 'all' },
            { label: '即将披露', value: 'upcoming' },
            { label: '已同步', value: 'synced' },
            { label: '待同步', value: 'pending' }
          ]}
        />
        <Space size={8} wrap>
          <Select
            value={sector}
            onChange={setSector}
            style={{ width: 140 }}
            options={[
              { label: '全部行业', value: 'all' },
              ...sectorOptions.map(option => ({ label: option, value: option }))
            ]}
          />
          <Input.Search
            allowClear
            placeholder="搜索代码或公司"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            style={{ width: 220 }}
          />
        </Space>
      </div>

      <Spin spinning={isLoading}>
        <div className="earnings-layout">
          <section className="earnings-list-panel">
            {filteredEvents.length === 0 ? (
              <Empty description="没有匹配的财报事件" />
            ) : (
              Object.entries(groupedEvents).map(([dateKey, group]) => (
                <div className="earnings-day-group" key={dateKey}>
                  <div className="earnings-day-header">
                    <span>{dateKey === 'pending' ? '待同步日期' : formatDate(dateKey)}</span>
                    <Text type="secondary">{group.length} 家</Text>
                  </div>
                  {group.map(event => {
                    const stock = stockBySymbol.get(event.symbol);
                    const isSelected = selectedEvent?.symbol === event.symbol;
                    return (
                      <button
                        type="button"
                        className={`earnings-event-row ${isSelected ? 'selected' : ''}`}
                        key={`${event.symbol}-${event.report_date || 'pending'}`}
                        onClick={() => setSelectedSymbol(event.symbol)}
                      >
                        <div>
                          <div className="earnings-event-symbol">{event.symbol}</div>
                          <div className="earnings-event-name">{stock?.name || event.name}</div>
                        </div>
                        <div>
                          <div className="earnings-event-date">{formatDate(event.report_date)}</div>
                          <div className="earnings-event-meta">{getEventTimeLabel(event)}</div>
                        </div>
                        <div>
                          <div className="earnings-event-eps">{formatNumber(event.eps_estimate)}</div>
                          <div className="earnings-event-meta">EPS 预期</div>
                        </div>
                        <div className="earnings-event-tags">
                          {getConfidenceTag(event)}
                          {stock?.focusLevel === 'high' && <Tag color="red">高关注</Tag>}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </section>

          <aside className="earnings-detail-panel">
            {selectedEvent ? (
              <>
                <div className="earnings-detail-head">
                  <div>
                    <div className="earnings-detail-symbol">{selectedEvent.symbol}</div>
                    <h3>{selectedStock?.name || selectedEvent.name}</h3>
                    <Text type="secondary">{selectedStock?.sector || selectedEvent.name}</Text>
                  </div>
                  <Space size={6} wrap>
                    {getSourceTag(selectedEvent)}
                    {getConfidenceTag(selectedEvent)}
                  </Space>
                </div>

                <div className="earnings-detail-date">
                  <CalendarOutlined />
                  <span>{formatFullDate(selectedEvent.report_date)}</span>
                  <span>{getEventTimeLabel(selectedEvent)}</span>
                </div>

                <div className="earnings-metric-grid">
                  <div className="earnings-metric-box">
                    <span>EPS 预期</span>
                    <strong>{formatNumber(selectedEvent.eps_estimate)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>EPS 实际</span>
                    <strong>{formatNumber(selectedEvent.eps_actual)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>惊喜</span>
                    <strong>{formatPercent(selectedEvent.eps_surprise_percent)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>市值</span>
                    <strong>{formatMarketCap(selectedStock?.marketCap)}</strong>
                  </div>
                </div>

                {selectedStock && (
                  <div className="earnings-price-strip">
                    <div>
                      <span>最新价格</span>
                      <strong>${selectedStock.currentPrice.toFixed(2)}</strong>
                    </div>
                    <div>
                      <span>当日变动</span>
                      <strong className={selectedStock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}>
                        {selectedStock.changePercent >= 0 ? '+' : ''}{selectedStock.changePercent.toFixed(2)}%
                      </strong>
                    </div>
                    <Button size="small" onClick={() => onStockSelect(selectedStock)}>
                      个股专区
                    </Button>
                  </div>
                )}

                <div className="earnings-detail-section">
                  <div className="earnings-box-title"><LineChartOutlined />观察重点</div>
                  <div className="earnings-chip-grid">
                    {selectedEvent.watch_items.map(item => <span key={item}>{item}</span>)}
                  </div>
                </div>

                <div className="earnings-detail-section">
                  <div className="earnings-box-title"><BarChartOutlined />核心指标</div>
                  <div className="earnings-chip-grid metric">
                    {selectedEvent.focus_metrics.map(item => <span key={item}>{item}</span>)}
                  </div>
                </div>

                <div className="earnings-detail-section">
                  <div className="earnings-box-title"><ExclamationCircleOutlined />风险提示</div>
                  <div className="earnings-risk-list">
                    {selectedEvent.risk_flags.map(item => <span key={item}>{item}</span>)}
                  </div>
                </div>

                {selectedEvent.related_symbols.length > 0 && (
                  <div className="earnings-detail-section">
                    <div className="earnings-box-title">相关标的</div>
                    <Space size={6} wrap>
                      {selectedEvent.related_symbols.map(symbol => <Tag key={symbol}>{symbol}</Tag>)}
                    </Space>
                  </div>
                )}
              </>
            ) : (
              <Empty description="选择一个公司查看财报信息" />
            )}
          </aside>
        </div>
      </Spin>
    </div>
  );
};

export default EarningsCalendar;
