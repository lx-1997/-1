import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, Segmented, Select, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import {
  BarChartOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  DollarCircleOutlined,
  ExclamationCircleOutlined,
  FieldTimeOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  RiseOutlined
} from '@ant-design/icons';
import { AppState, DataQuality, Stock } from '../types';
import CenterShell from './common/CenterShell';
import { EarningsCalendarEvent, getEarningsCalendar } from '../services/earningsService';
import './EarningsCalendar.css';

const { Text } = Typography;
const LARGE_CAP_MARKET_CAP_FLOOR = 50_000_000_000;

interface EarningsCalendarProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

type EventFilter = 'all' | 'next7' | 'next30' | 'premarket' | 'afterhours' | 'confirmed' | 'pending';
type Horizon = '3month' | '6month' | '12month';
type EventSession = 'premarket' | 'afterhours' | 'intraday' | 'unsupplied';

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

const getEventKey = (event: EarningsCalendarEvent): string => {
  return [
    event.symbol,
    event.report_date || 'pending',
    event.fiscal_date_ending || 'fiscal-pending',
    event.provider
  ].join(':');
};

const parseReportDate = (date?: string | null): Date | null => {
  if (!date) {
    return null;
  }

  const parsed = new Date(`${date}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const formatDate = (date?: string | null): string => {
  const parsed = parseReportDate(date);
  if (!parsed) {
    return '待确认';
  }

  return parsed.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short'
  });
};

const formatFullDate = (date?: string | null): string => {
  const parsed = parseReportDate(date);
  if (!parsed) {
    return '等待财报源返回精确披露日';
  }

  return parsed.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'long'
  });
};

const formatFetchedAt = (date?: string | null): string => {
  if (!date) {
    return '待同步';
  }

  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) {
    return date;
  }

  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

const formatNumber = (value?: number | null, precision = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }

  return value.toFixed(precision);
};

const formatCurrency = (value?: number | null, currency = 'USD', precision = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: precision,
    maximumFractionDigits: precision
  }).format(value);
};

const formatMarketCap = (value?: number | null, currency = 'USD'): string => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    return '--';
  }

  const prefix = currency === 'HKD' ? 'HK$' : currency === 'CNY' ? '¥' : '$';
  if (value >= 1_000_000_000_000) {
    return `${prefix}${(value / 1_000_000_000_000).toFixed(2)}T`;
  }

  if (value >= 1_000_000_000) {
    return `${prefix}${(value / 1_000_000_000).toFixed(1)}B`;
  }

  if (value >= 1_000_000) {
    return `${prefix}${(value / 1_000_000).toFixed(1)}M`;
  }

  return `${prefix}${value.toLocaleString('en-US')}`;
};

const getEventSession = (event: EarningsCalendarEvent): EventSession => {
  const time = event.time_of_day?.toLowerCase() || '';
  if (time.includes('pre') || time === 'bmo' || time.includes('before')) {
    return 'premarket';
  }
  if (time.includes('after') || time === 'amc') {
    return 'afterhours';
  }
  if (time.includes('during') || time === 'dmh') {
    return 'intraday';
  }
  return 'unsupplied';
};

const getEventTimeLabel = (event: EarningsCalendarEvent): string => {
  if (!event.time_of_day) {
    return '时间待确认';
  }

  return timeLabels[event.time_of_day.toLowerCase()] || event.time_of_day;
};

const getSessionTag = (event: EarningsCalendarEvent) => {
  const session = getEventSession(event);
  if (session === 'premarket') {
    return <Tag color="gold">盘前</Tag>;
  }
  if (session === 'afterhours') {
    return <Tag color="purple">盘后</Tag>;
  }
  if (session === 'intraday') {
    return <Tag color="processing">盘中</Tag>;
  }
  return <Tag color="default">待定</Tag>;
};

const getConfidenceTag = (event: EarningsCalendarEvent) => {
  if (event.confidence === 'pending_provider') {
    return <Tag color="default">待同步</Tag>;
  }

  if (event.confidence === 'confirmed' || event.is_date_confirmed) {
    return <Tag color="green">日期已确认</Tag>;
  }

  return <Tag color="blue">预测</Tag>;
};

const getSourceTag = (event: EarningsCalendarEvent) => {
  if (event.provider === 'alpha_vantage') {
    return <Tag color="cyan">Alpha Vantage</Tag>;
  }

  if (event.provider === 'nasdaq_public') {
    return <Tag color="blue">{event.source_name || 'Nasdaq'}</Tag>;
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
    return 'Nasdaq 公共源';
  }

  if (provider === 'mixed') {
    return '多数据源';
  }

  if (provider === 'none') {
    return '未连接';
  }

  return provider;
};

const getWarningMessage = (warnings: string[], isFullSearchMode = false): string => {
  const warningText = warnings.join(' | ');
  const warning = warnings[0] || '';
  if (warning.includes('ALPHAVANTAGE_API_KEY')) {
    return '未配置 Alpha Vantage key，当前显示关注池模板，日期和 EPS 预期等待 API 同步。';
  }

  if (
    warningText.includes('Nasdaq public calendar did not return dates for')
    || warningText.includes('Nasdaq public calendar did not return dated events')
  ) {
    return 'Nasdaq 已同步可确认的披露日；未命中精确日期的标的使用 Nasdaq EPS 预测，并在列表中标为预测。';
  }

  if (warningText.includes('Nasdaq public date scan was capped')) {
    return '较远月份会优先显示预测数据；精确披露日会在 Nasdaq 日期日历命中后自动替换。';
  }

  if (warningText.includes('Nasdaq public calendar scan reached the time budget')) {
    return isFullSearchMode
      ? '全量搜索已扫描当前窗口内可用公司；较远日期会在后续刷新中继续补齐。'
      : '已展示当前扫描窗口内命中的 500 亿美元以上公司；较远日期会在后续刷新中继续补齐。';
  }

  if (warningText.includes('market cap >=')) {
    return '默认仅展示 Nasdaq 日历中市值不低于 500 亿美元的公司；搜索框会查全量日历。';
  }

  if (warningText.includes('without a market-cap filter')) {
    return '全量搜索已取消市值过滤，结果来自 Nasdaq 当前扫描窗口。';
  }

  if (warningText.includes('Nasdaq public calendar skipped')) {
    return 'Nasdaq 公共日历部分日期请求较慢，当前已展示可用日历和预测数据。';
  }

  if (warningText.includes('Nasdaq public calendar and forecast returned no matching')) {
    return 'Nasdaq 暂未命中关注池公司，当前用公司观察模板兜底。';
  }

  return warning || '财报日历服务暂不可用';
};

const getDaysUntil = (event: EarningsCalendarEvent): number | null => {
  if (typeof event.days_until_report === 'number' && Number.isFinite(event.days_until_report)) {
    return event.days_until_report;
  }

  const parsed = parseReportDate(event.report_date);
  if (!parsed) {
    return null;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((parsed.getTime() - today.getTime()) / 86_400_000);
};

const getDaysLabel = (event: EarningsCalendarEvent): string => {
  const days = getDaysUntil(event);
  if (days === null) {
    return '待确认';
  }

  if (days === 0) {
    return '今天';
  }

  if (days > 0) {
    return `${days} 天后`;
  }

  return `${Math.abs(days)} 天前`;
};

const isUpcomingEvent = (event: EarningsCalendarEvent): boolean => {
  const days = getDaysUntil(event);
  return days !== null && days >= 0;
};

const isWithinDays = (event: EarningsCalendarEvent, days: number): boolean => {
  const daysUntil = getDaysUntil(event);
  return daysUntil !== null && daysUntil >= 0 && daysUntil <= days;
};

const getEpsRange = (event: EarningsCalendarEvent): string => {
  if (typeof event.eps_low_estimate === 'number' || typeof event.eps_high_estimate === 'number') {
    return `${formatNumber(event.eps_low_estimate)} - ${formatNumber(event.eps_high_estimate)}`;
  }

  return '--';
};

const getRevisionText = (event: EarningsCalendarEvent): string => {
  const up = event.revision_up_count ?? 0;
  const down = event.revision_down_count ?? 0;
  if (up === 0 && down === 0) {
    return '近4周无调整';
  }
  return `上修 ${up} / 下修 ${down}`;
};

const EarningsCalendar: React.FC<EarningsCalendarProps> = ({ appState, onStockSelect }) => {
  const [events, setEvents] = useState<EarningsCalendarEvent[]>([]);
  const [searchEvents, setSearchEvents] = useState<EarningsCalendarEvent[]>([]);
  const [searchWarnings, setSearchWarnings] = useState<string[]>([]);
  const [searchFetchedHorizon, setSearchFetchedHorizon] = useState<Horizon | null>(null);
  const [provider, setProvider] = useState('none');
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [dataQuality, setDataQuality] = useState<DataQuality | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSearchLoading, setIsSearchLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<EventFilter>('all');
  const [sector, setSector] = useState<string>('all');
  const [horizon, setHorizon] = useState<Horizon>('3month');
  const [selectedEventKey, setSelectedEventKey] = useState<string | null>(null);

  const stockBySymbol = useMemo(() => {
    return new Map(appState.stocks.map(stock => [stock.symbol.toUpperCase(), stock]));
  }, [appState.stocks]);

  const loadEvents = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await getEarningsCalendar([], horizon, LARGE_CAP_MARKET_CAP_FLOOR);
      setEvents(response.events);
      setProvider(response.provider);
      setFetchedAt(response.fetched_at);
      setWarnings(response.warnings);
      setDataQuality(response.data_quality ?? null);
      setSelectedEventKey(prev => prev || (response.events[0] ? getEventKey(response.events[0]) : null));
    } catch (error) {
      console.warn('Earnings calendar refresh failed:', error);
      setWarnings(['财报日历服务暂不可用']);
    } finally {
      setIsLoading(false);
    }
  }, [horizon]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    setSearchEvents([]);
    setSearchWarnings([]);
    setSearchFetchedHorizon(null);
  }, [horizon]);

  useEffect(() => {
    const keyword = query.trim();
    if (!keyword || searchFetchedHorizon === horizon) {
      return;
    }

    const timer = window.setTimeout(() => {
      setIsSearchLoading(true);
      getEarningsCalendar([], horizon, undefined, true)
        .then(response => {
          setSearchEvents(response.events);
          setSearchWarnings(response.warnings);
          setSearchFetchedHorizon(horizon);
        })
        .catch(error => {
          console.warn('Full earnings calendar search failed:', error);
        })
        .finally(() => setIsSearchLoading(false));
    }, 350);

    return () => window.clearTimeout(timer);
  }, [horizon, query, searchFetchedHorizon]);

  const sectorOptions = useMemo(() => {
    const sectors = appState.stocks.map(stock => stock.sector).filter(Boolean);
    return Array.from(new Set(sectors));
  }, [appState.stocks]);

  const fullSearchKeyword = query.trim();
  const isFullSearchMode = fullSearchKeyword.length > 0;
  const sourceEvents = isFullSearchMode && searchEvents.length > 0 ? searchEvents : events;

  const filteredEvents = useMemo(() => {
    const keyword = fullSearchKeyword.toLowerCase();

    return sourceEvents.filter(event => {
      const stock = stockBySymbol.get(event.symbol.toUpperCase());
      const session = getEventSession(event);
      const matchesQuery = !keyword
        || event.symbol.toLowerCase().includes(keyword)
        || event.name.toLowerCase().includes(keyword)
        || stock?.name.toLowerCase().includes(keyword);
      const matchesSector = isFullSearchMode || sector === 'all' || stock?.sector === sector;
      const matchesFilter = filter === 'all'
        || (filter === 'next7' && isWithinDays(event, 7))
        || (filter === 'next30' && isWithinDays(event, 30))
        || (filter === 'premarket' && session === 'premarket')
        || (filter === 'afterhours' && session === 'afterhours')
        || (filter === 'confirmed' && Boolean(event.report_date) && (event.confidence === 'confirmed' || event.is_date_confirmed))
        || (filter === 'pending' && (!event.report_date || event.provider === 'watchlist_template'));

      return matchesQuery && matchesSector && matchesFilter;
    });
  }, [filter, fullSearchKeyword, isFullSearchMode, sector, sourceEvents, stockBySymbol]);

  useEffect(() => {
    if (filteredEvents.length === 0) {
      setSelectedEventKey(null);
      return;
    }

    if (!selectedEventKey || !filteredEvents.some(event => getEventKey(event) === selectedEventKey)) {
      setSelectedEventKey(getEventKey(filteredEvents[0]));
    }
  }, [filteredEvents, selectedEventKey]);

  const selectedEvent = filteredEvents.find(event => getEventKey(event) === selectedEventKey) || filteredEvents[0];
  const selectedStock = selectedEvent ? stockBySymbol.get(selectedEvent.symbol.toUpperCase()) : undefined;
  const metricEvents = isFullSearchMode ? filteredEvents : events;
  const syncedCount = metricEvents.filter(event => event.provider !== 'watchlist_template').length;
  const datedCount = metricEvents.filter(event => Boolean(event.report_date)).length;
  const upcomingCount = metricEvents.filter(isUpcomingEvent).length;
  const next7Count = metricEvents.filter(event => isWithinDays(event, 7)).length;
  const confirmedCount = metricEvents.filter(event => event.report_date && (event.confidence === 'confirmed' || event.is_date_confirmed)).length;
  const highFocusCount = metricEvents.filter(event => stockBySymbol.get(event.symbol.toUpperCase())?.focusLevel === 'high').length;
  const averageMarketCap = metricEvents.length > 0
    ? metricEvents.reduce((sum, event) => sum + (event.market_cap || 0), 0) / metricEvents.length
    : 0;
  const marketCapFloorLabel = formatMarketCap(LARGE_CAP_MARKET_CAP_FLOOR);
  const activeWarnings = isFullSearchMode && searchEvents.length > 0 ? searchWarnings : warnings;

  const groupedEvents = useMemo(() => {
    return filteredEvents.reduce<Record<string, EarningsCalendarEvent[]>>((groups, event) => {
      const key = event.report_date || 'pending';
      groups[key] = [...(groups[key] || []), event];
      return groups;
    }, {});
  }, [filteredEvents]);

  return (
    <CenterShell
      eyebrow="EARNINGS CALENDAR"
      title="财报日历"
      dataQuality={dataQuality}
      subtitle={(
        <>
          默认展示 Nasdaq 财报日历中市值超过 {marketCapFloorLabel} 的公司；搜索时查全量日历。
          <div className="earnings-header-meta">
            <span><DatabaseOutlined />{getProviderLabel(provider)}</span>
            <span><ClockCircleOutlined />更新 {formatFetchedAt(fetchedAt)}</span>
            <span><CheckCircleOutlined />{isFullSearchMode ? `${filteredEvents.length} 条全量搜索结果` : `${syncedCount} 家 ${marketCapFloorLabel} 以上公司`}</span>
          </div>
        </>
      )}
      actions={(
        <Space size={8} wrap>
          <Tag color={provider === 'watchlist_template' || provider === 'none' ? 'default' : 'cyan'}>
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
      )}
    >

      {activeWarnings.length > 0 && (
        <Alert
          className="earnings-alert"
          type={syncedCount > 0 ? 'info' : 'warning'}
          showIcon
          message={getWarningMessage(activeWarnings, isFullSearchMode)}
        />
      )}

      {isFullSearchMode && isSearchLoading && (
        <Alert
          className="earnings-alert"
          type="info"
          showIcon
          message="正在全量搜索 Nasdaq 财报日历，结果不会受默认市值门槛限制。"
        />
      )}

      <div className="earnings-summary-grid">
        <div className="metric-tile">
          <div className="metric-label"><CalendarOutlined /><span>{isFullSearchMode ? '搜索结果' : '500亿+公司'}</span></div>
          <div className="metric-value">{metricEvents.length}</div>
          <div className="metric-note">{datedCount} 个精确日期</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><FieldTimeOutlined /><span>7 日内</span></div>
          <div className="metric-value">{next7Count}</div>
          <div className="metric-note">{upcomingCount} 个未来事件</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><CheckCircleOutlined /><span>已确认</span></div>
          <div className="metric-value">{confirmedCount}</div>
          <div className="metric-note">含盘前/盘后标记</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><RiseOutlined /><span>高关注</span></div>
          <div className="metric-value">{highFocusCount}</div>
          <div className="metric-note">均值 {formatMarketCap(averageMarketCap)}</div>
        </div>
      </div>

      <div className="earnings-toolbar">
        <Segmented
          value={filter}
          onChange={(value) => setFilter(value as EventFilter)}
          options={[
            { label: '全部', value: 'all' },
            { label: '7 天内', value: 'next7' },
            { label: '30 天内', value: 'next30' },
            { label: '盘前', value: 'premarket' },
            { label: '盘后', value: 'afterhours' },
            { label: '已确认', value: 'confirmed' },
            { label: '待确认', value: 'pending' }
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
            placeholder="全量搜索代码或公司"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            style={{ width: 220 }}
          />
        </Space>
      </div>

      <Spin spinning={isLoading || isSearchLoading}>
        <div className="earnings-layout">
          <section className="earnings-list-panel">
            {filteredEvents.length === 0 ? (
              <Empty description="没有匹配的财报事件" />
            ) : (
              <>
                <div className="earnings-table-head">
                  <span>公司</span>
                  <span>披露日</span>
                  <span>报告期</span>
                  <span>EPS 共识</span>
                  <span>可信度</span>
                </div>
                {Object.entries(groupedEvents).map(([dateKey, group]) => {
                  const premarketCount = group.filter(event => getEventSession(event) === 'premarket').length;
                  const afterhoursCount = group.filter(event => getEventSession(event) === 'afterhours').length;
                  return (
                    <div className="earnings-day-group" key={dateKey}>
                      <div className="earnings-day-header">
                        <div>
                          <span>{dateKey === 'pending' ? '待确认日期' : formatDate(dateKey)}</span>
                          <em>盘前 {premarketCount} / 盘后 {afterhoursCount}</em>
                        </div>
                        <Text type="secondary">{group.length} 家</Text>
                      </div>
                      {group.map(event => {
                        const stock = stockBySymbol.get(event.symbol.toUpperCase());
                        const isSelected = selectedEvent ? getEventKey(selectedEvent) === getEventKey(event) : false;
                        const currency = event.currency || stock?.currency || 'USD';
                        const rowMeta = stock?.sector || formatMarketCap(event.market_cap, currency);
                        return (
                          <button
                            type="button"
                            className={`earnings-event-row ${isSelected ? 'selected' : ''}`}
                            key={getEventKey(event)}
                            onClick={() => setSelectedEventKey(getEventKey(event))}
                          >
                            <div className="earnings-company-cell">
                              <div className="earnings-event-symbol">{event.symbol}</div>
                              <div className="earnings-event-name">{stock?.name || event.name}</div>
                              <div className="earnings-event-meta">{rowMeta}</div>
                            </div>
                            <div>
                              <div className="earnings-event-date">{formatDate(event.report_date)}</div>
                              <div className="earnings-event-meta">{getDaysLabel(event)}</div>
                            </div>
                            <div>
                              <div className="earnings-event-date">{event.fiscal_date_ending || '--'}</div>
                              <div className="earnings-event-meta">{event.source_name}</div>
                            </div>
                            <div>
                              <div className="earnings-event-eps">{formatCurrency(event.eps_estimate, currency)}</div>
                              <div className="earnings-event-meta">区间 {getEpsRange(event)}</div>
                            </div>
                            <div className="earnings-event-tags">
                              {getSessionTag(event)}
                              {getConfidenceTag(event)}
                              {stock?.focusLevel === 'high' && <Tag color="red">高关注</Tag>}
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  );
                })}
              </>
            )}
          </section>

          <aside className="earnings-detail-panel">
            {selectedEvent ? (
              <>
                <div className="earnings-detail-head">
                  <div>
                    <div className="earnings-detail-symbol">{selectedEvent.symbol}</div>
                    <h3>{selectedStock?.name || selectedEvent.name}</h3>
                    <Text type="secondary">{selectedStock?.sector || `市值 ${formatMarketCap(selectedEvent.market_cap, selectedEvent.currency)}`}</Text>
                  </div>
                  <Space size={6} wrap>
                    {getSourceTag(selectedEvent)}
                    {getConfidenceTag(selectedEvent)}
                    {selectedEvent.source_url && (
                      <Tooltip title="打开数据来源">
                        <Button
                          size="small"
                          icon={<LinkOutlined />}
                          href={selectedEvent.source_url}
                          target="_blank"
                          rel="noreferrer"
                        />
                      </Tooltip>
                    )}
                  </Space>
                </div>

                <div className="earnings-detail-date">
                  <div>
                    <span><CalendarOutlined />披露日</span>
                    <strong>{formatFullDate(selectedEvent.report_date)}</strong>
                  </div>
                  <div>
                    <span><ClockCircleOutlined />时段</span>
                    <strong>{getEventTimeLabel(selectedEvent)}</strong>
                  </div>
                  <div>
                    <span><FieldTimeOutlined />距离</span>
                    <strong>{getDaysLabel(selectedEvent)}</strong>
                  </div>
                </div>

                <div className="earnings-metric-grid">
                  <div className="earnings-metric-box">
                    <span>EPS 共识</span>
                    <strong>{formatCurrency(selectedEvent.eps_estimate, selectedEvent.currency)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>EPS 区间</span>
                    <strong>{getEpsRange(selectedEvent)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>分析师数</span>
                    <strong>{selectedEvent.analyst_count ?? '--'}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>近4周修正</span>
                    <strong>{getRevisionText(selectedEvent)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>去年 EPS</span>
                    <strong>{formatCurrency(selectedEvent.last_year_eps, selectedEvent.currency)}</strong>
                  </div>
                  <div className="earnings-metric-box">
                    <span>市值</span>
                    <strong>{formatMarketCap(selectedEvent.market_cap ?? selectedStock?.marketCap, selectedEvent.currency)}</strong>
                  </div>
                </div>

                <div className="earnings-source-strip">
                  <div>
                    <span><FileSearchOutlined />报告期</span>
                    <strong>{selectedEvent.fiscal_date_ending || '--'}</strong>
                  </div>
                  <div>
                    <span><DatabaseOutlined />来源</span>
                    <strong>{selectedEvent.source_name}</strong>
                  </div>
                  <div>
                    <span><ClockCircleOutlined />源更新</span>
                    <strong>{selectedEvent.data_as_of || formatFetchedAt(fetchedAt)}</strong>
                  </div>
                </div>

                {selectedStock && (
                  <div className="earnings-price-strip">
                    <div>
                      <span>最新价格</span>
                      <strong>{formatCurrency(selectedStock.currentPrice, selectedStock.currency || selectedEvent.currency)}</strong>
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
                  <div className="earnings-box-title"><RiseOutlined />观察重点</div>
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
                    <div className="earnings-box-title"><DollarCircleOutlined />相关标的</div>
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
    </CenterShell>
  );
};

export default EarningsCalendar;
