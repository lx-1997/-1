import React, { useEffect, useMemo, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Empty,
  Input,
  Popconfirm,
  Progress,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography
} from 'antd';
import {
  BellFilled,
  BellOutlined,
  DeleteOutlined,
  DollarOutlined,
  FireOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  TrophyOutlined
} from '@ant-design/icons';
import { Stock } from '../types';
import { MarketRegion, MarketSymbolCandidate, searchMarketSymbols } from '../services/marketService';
import { formatQuoteSourceLine, formatQuoteTimestamp } from '../utils/marketData';
import { MarketSegmentKey, marketSegments, stockBelongsToSegment } from '../utils/marketSegments';

const { Paragraph, Text } = Typography;
const { Search } = Input;

type StockPoolFilter = 'all' | 'subscribed' | MarketRegion;
type StockSearchMarket = 'all' | 'global' | MarketRegion;

interface StockListProps {
  stocks: Stock[];
  onStockSelect: (stock: Stock) => void;
  onAddStock?: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock?: (symbol: string) => void;
  onToggleSubscription?: (symbol: string) => void;
  onRefreshMarketData?: () => void;
  isMarketDataRefreshing?: boolean;
  showHeader?: boolean;
  marketSegment?: MarketSegmentKey;
  onMarketSegmentChange?: (segment: MarketSegmentKey) => void;
}

const marketMeta: Record<MarketRegion, { text: string; color: string; currency: string }> = {
  US: { text: '美股', color: 'blue', currency: 'USD' },
  HK: { text: '港股', color: 'cyan', currency: 'HKD' },
  CN: { text: 'A股', color: 'red', currency: 'CNY' },
  OTHER: { text: '其他', color: 'default', currency: 'USD' }
};

const getFocusLevelColor = (level: string) => {
  switch (level) {
    case 'high': return 'red';
    case 'medium': return 'orange';
    case 'low': return 'green';
    default: return 'blue';
  }
};

const getFocusLevelText = (level: string) => {
  switch (level) {
    case 'high': return '高关注';
    case 'medium': return '中关注';
    case 'low': return '低关注';
    default: return '观察';
  }
};

const marketOf = (stock: Stock): MarketRegion => stock.market || 'US';

const defaultSearchMarketForSegment = (segment: MarketSegmentKey): StockSearchMarket => {
  if (segment === 'a-share') return 'CN';
  if (segment === 'global') return 'global';
  return 'all';
};

const searchMarketOptionsForSegment = (segment: MarketSegmentKey) => {
  if (segment === 'a-share') {
    return [{ label: 'A股', value: 'CN' }];
  }

  if (segment === 'global') {
    return [
      { label: '港美股', value: 'global' },
      { label: '港股', value: 'HK' },
      { label: '美股', value: 'US' }
    ];
  }

  return [
    { label: '全市场', value: 'all' },
    { label: 'A股', value: 'CN' },
    { label: '港美股', value: 'global' },
    { label: '港股', value: 'HK' },
    { label: '美股', value: 'US' }
  ];
};

const formatPrice = (stock: Stock) => {
  const currency = stock.currency || marketMeta[marketOf(stock)].currency;
  const value = Number(stock.currentPrice || 0);
  if (!Number.isFinite(value) || value <= 0) {
    return '--';
  }
  return `${currency} ${value.toFixed(value >= 1000 ? 2 : 3).replace(/\.?0+$/, '')}`;
};

const StockList: React.FC<StockListProps> = ({
  stocks,
  onStockSelect,
  onAddStock,
  onRemoveStock,
  onToggleSubscription,
  onRefreshMarketData,
  isMarketDataRefreshing = false,
  showHeader = true,
  marketSegment = 'all',
  onMarketSegmentChange
}) => {
  const { message } = AntdApp.useApp();
  const [activeSegment, setActiveSegment] = useState<MarketSegmentKey>(marketSegment);
  const [poolFilter, setPoolFilter] = useState<StockPoolFilter>('all');
  const [searchMarket, setSearchMarket] = useState<StockSearchMarket>(defaultSearchMarketForSegment(marketSegment));
  const [searchText, setSearchText] = useState('');
  const [searching, setSearching] = useState(false);
  const [candidates, setCandidates] = useState<MarketSymbolCandidate[]>([]);
  const [addingSymbol, setAddingSymbol] = useState<string | null>(null);
  const segmentMeta = marketSegments[activeSegment];

  useEffect(() => {
    setActiveSegment(marketSegment);
  }, [marketSegment]);

  useEffect(() => {
    setPoolFilter(prev => {
      if (prev === 'all' || prev === 'subscribed') {
        return prev;
      }
      return segmentMeta.markets.includes(prev) ? prev : 'all';
    });
    setSearchMarket(defaultSearchMarketForSegment(activeSegment));
    setCandidates([]);
  }, [activeSegment, segmentMeta.markets]);

  const segmentStocks = useMemo(
    () => stocks.filter(stock => stockBelongsToSegment(stock, activeSegment)),
    [activeSegment, stocks]
  );

  const poolStats = useMemo(() => {
    const subscribed = segmentStocks.filter(stock => stock.isSubscribed ?? true).length;
    const byMarket = segmentStocks.reduce<Record<string, number>>((acc, stock) => {
      const market = marketOf(stock);
      acc[market] = (acc[market] || 0) + 1;
      return acc;
    }, {});
    return { subscribed, byMarket };
  }, [segmentStocks]);

  const filteredStocks = useMemo(() => {
    return segmentStocks.filter(stock => {
      if (poolFilter === 'all') return true;
      if (poolFilter === 'subscribed') return stock.isSubscribed ?? true;
      return marketOf(stock) === poolFilter;
    });
  }, [poolFilter, segmentStocks]);

  const poolFilterOptions = useMemo(() => {
    const marketOptions = segmentMeta.markets
      .filter(market => market !== 'OTHER')
      .map(market => ({
        label: `${marketMeta[market].text} ${poolStats.byMarket[market] || 0}`,
        value: market
      }));

    return [
      { label: `全部 ${segmentStocks.length}`, value: 'all' },
      { label: `监控中 ${poolStats.subscribed}`, value: 'subscribed' },
      ...marketOptions
    ];
  }, [poolStats.byMarket, poolStats.subscribed, segmentMeta.markets, segmentStocks.length]);

  const handleSegmentChange = (segment: MarketSegmentKey) => {
    setActiveSegment(segment);
    onMarketSegmentChange?.(segment);
  };

  const handleSearch = async (value = searchText) => {
    const keyword = value.trim();
    if (!keyword) {
      message.warning('请输入股票代码、名称或拼音');
      return;
    }
    setSearching(true);
    try {
      const searchTargets: Array<MarketRegion | 'all'> = searchMarket === 'global'
        ? ['HK', 'US']
        : [searchMarket];
      const results = await Promise.all(
        searchTargets.map(target => searchMarketSymbols(keyword, target))
      );
      const dedupedCandidates = new Map<string, MarketSymbolCandidate>();
      results.forEach(result => {
        result.candidates
          .filter(candidate => stockBelongsToSegment(candidate, activeSegment))
          .forEach(candidate => {
            dedupedCandidates.set(`${candidate.market}:${candidate.symbol}`, candidate);
          });
      });
      const nextCandidates = Array.from(dedupedCandidates.values());
      setCandidates(nextCandidates);
      if (nextCandidates.length === 0) {
        message.info('没有找到匹配标的，可以尝试完整代码，例如 AAPL、00700、600519');
      }
      const warnings = results.reduce<string[]>((acc, result) => acc.concat(result.warnings), []);
      if (warnings.length > 0) {
        console.warn('Market symbol search warnings:', warnings);
      }
    } catch (error: any) {
      message.error(error?.message || '标的搜索失败');
    } finally {
      setSearching(false);
    }
  };

  const handleAddCandidate = async (candidate: MarketSymbolCandidate) => {
    if (!onAddStock) {
      return;
    }
    setAddingSymbol(candidate.symbol);
    try {
      await onAddStock(candidate);
      setSearchText('');
      setCandidates(prev => prev.filter(item => item.symbol !== candidate.symbol));
    } finally {
      setAddingSymbol(null);
    }
  };

  return (
    <div className="stock-directory watchlist-shell">
      {showHeader && (
        <div className="section-heading">
          <div>
            <h2>
              <Space>
                <TrophyOutlined style={{ color: '#b7791f' }} />
                <span>{segmentMeta.label}研究池</span>
              </Space>
            </h2>
            <div className="section-description">{segmentMeta.description}</div>
          </div>
          <Space wrap>
            <Tag color="blue">美股 {poolStats.byMarket.US || 0}</Tag>
            <Tag color="cyan">港股 {poolStats.byMarket.HK || 0}</Tag>
            <Tag color="red">A股 {poolStats.byMarket.CN || 0}</Tag>
            <Button icon={<ReloadOutlined />} loading={isMarketDataRefreshing} onClick={onRefreshMarketData}>
              刷新行情
            </Button>
          </Space>
        </div>
      )}

      <div className="watchlist-segment-panel">
        <Segmented
          value={activeSegment}
          onChange={value => handleSegmentChange(value as MarketSegmentKey)}
          options={[
            { label: '全市场', value: 'all' },
            { label: 'A股', value: 'a-share' },
            { label: '港美股', value: 'global' }
          ]}
        />
        <Space wrap size={6} className="watchlist-segment-chips">
          {segmentMeta.chips.map(chip => (
            <Tag key={chip}>{chip}</Tag>
          ))}
        </Space>
      </div>

      <div className="watchlist-toolbar">
        <Segmented
          value={poolFilter}
          onChange={value => setPoolFilter(value as StockPoolFilter)}
          options={poolFilterOptions}
        />
        {!showHeader && (
          <Button icon={<ReloadOutlined />} loading={isMarketDataRefreshing} onClick={onRefreshMarketData}>
            刷新行情
          </Button>
        )}
      </div>

      <div className="watchlist-add-panel">
        <Segmented
          value={searchMarket}
          onChange={value => setSearchMarket(value as StockSearchMarket)}
          options={searchMarketOptionsForSegment(activeSegment)}
        />
        <Search
          allowClear
          value={searchText}
          onChange={event => setSearchText(event.target.value)}
          onSearch={handleSearch}
          enterButton={<SearchOutlined />}
          loading={searching}
          placeholder={segmentMeta.searchPlaceholder}
        />
      </div>

      {candidates.length > 0 && (
        <div className="watchlist-search-results">
          {candidates.map(candidate => {
            const exists = stocks.some(stock => stock.symbol.toUpperCase() === candidate.symbol.toUpperCase());
            return (
              <div className="watchlist-search-row" key={candidate.symbol}>
                <Space direction="vertical" size={0}>
                  <Space wrap>
                    <Text strong>{candidate.symbol}</Text>
                    <Text>{candidate.name}</Text>
                    <Tag color={marketMeta[candidate.market]?.color}>{marketMeta[candidate.market]?.text}</Tag>
                    {candidate.security_type && <Tag>{candidate.security_type}</Tag>}
                  </Space>
                  <Text type="secondary">{candidate.provider_name} · {candidate.quote_id || candidate.exchange}</Text>
                </Space>
                <Button
                  type={exists ? 'default' : 'primary'}
                  icon={<PlusOutlined />}
                  disabled={exists}
                  loading={addingSymbol === candidate.symbol}
                  onClick={() => handleAddCandidate(candidate)}
                >
                  {exists ? '已在池中' : '加入自选'}
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {filteredStocks.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="当前筛选下暂无自选标的"
        />
      ) : (
        <Table
          className="watchlist-table"
          size="middle"
          rowKey="symbol"
          dataSource={filteredStocks}
          pagination={{ pageSize: 12 }}
          onRow={stock => ({
            onClick: () => onStockSelect(stock)
          })}
          columns={[
            {
              title: '标的',
              dataIndex: 'symbol',
              render: (_, stock) => (
                <Space direction="vertical" size={2}>
                  <Space wrap>
                    <Text strong className="watchlist-symbol">{stock.symbol}</Text>
                    <Text>{stock.name}</Text>
                    <Tag color={marketMeta[marketOf(stock)].color}>{marketMeta[marketOf(stock)].text}</Tag>
                    <Tag color={getFocusLevelColor(stock.focusLevel)}>{getFocusLevelText(stock.focusLevel)}</Tag>
                    {(stock.isSubscribed ?? true) && <Tag color="green">监控中</Tag>}
                  </Space>
                  <Text type="secondary">{stock.exchange || stock.sector}</Text>
                </Space>
              )
            },
            {
              title: '行情',
              width: 210,
              render: (_, stock) => (
                <Space direction="vertical" size={1}>
                  <Text strong>{formatPrice(stock)}</Text>
                  <Text className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}>
                    {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
                    {typeof stock.priceChange === 'number' && ` / ${stock.priceChange >= 0 ? '+' : ''}${stock.priceChange.toFixed(2)}`}
                  </Text>
                  <Text type="secondary">{formatQuoteSourceLine(stock)} · {formatQuoteTimestamp(stock)}</Text>
                </Space>
              )
            },
            {
              title: '热度',
              width: 160,
              render: (_, stock) => (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Progress
                    percent={stock.communityScore}
                    size="small"
                    showInfo={false}
                    strokeColor={stock.communityScore > 80 ? '#12805c' : stock.communityScore > 60 ? '#b7791f' : '#c43e3e'}
                  />
                  <Text type="secondary">
                    <FireOutlined /> {stock.communityScore} · <MessageOutlined /> {stock.totalPosts}
                  </Text>
                </Space>
              )
            },
            {
              title: '提醒',
              width: 86,
              render: (_, stock) => (
                <Tooltip title={(stock.isSubscribed ?? true) ? '暂停监控提醒' : '开启监控提醒'}>
                  <Button
                    shape="circle"
                    icon={(stock.isSubscribed ?? true) ? <BellFilled /> : <BellOutlined />}
                    type={(stock.isSubscribed ?? true) ? 'primary' : 'default'}
                    onClick={event => {
                      event.stopPropagation();
                      onToggleSubscription?.(stock.symbol);
                    }}
                  />
                </Tooltip>
              )
            },
            {
              title: '操作',
              width: 132,
              render: (_, stock) => (
                <Space size={4}>
                  <Tooltip title="打开标的工作区">
                    <Button
                      size="small"
                      shape="circle"
                      icon={<MessageOutlined />}
                      onClick={event => {
                        event.stopPropagation();
                        onStockSelect(stock);
                      }}
                    />
                  </Tooltip>
                  <Tooltip title={`${stock.totalPaidPosts} 篇深度报告`}>
                    <Button
                      size="small"
                      shape="circle"
                      icon={<DollarOutlined />}
                      onClick={event => {
                        event.stopPropagation();
                        onStockSelect(stock);
                      }}
                    />
                  </Tooltip>
                  <Popconfirm
                    title={`移除 ${stock.symbol}？`}
                    okText="移除"
                    cancelText="取消"
                    onConfirm={event => {
                      event?.stopPropagation();
                      onRemoveStock?.(stock.symbol);
                    }}
                  >
                    <Button
                      size="small"
                      danger
                      shape="circle"
                      icon={<DeleteOutlined />}
                      onClick={event => event.stopPropagation()}
                    />
                  </Popconfirm>
                </Space>
              )
            }
          ]}
          expandable={{
            expandedRowRender: stock => (
              <Paragraph style={{ margin: 0 }}>
                {stock.description}
              </Paragraph>
            )
          }}
        />
      )}
    </div>
  );
};

export default React.memo(StockList);
