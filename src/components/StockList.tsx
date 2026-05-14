import React, { useMemo, useState } from 'react';
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
  GlobalOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  StarOutlined,
  TrophyOutlined
} from '@ant-design/icons';
import { Stock } from '../types';
import { MarketRegion, MarketSymbolCandidate, searchMarketSymbols } from '../services/marketDataService';
import { formatQuoteSourceLine, formatQuoteTimestamp } from '../utils/marketData';

const { Paragraph, Text } = Typography;
const { Search } = Input;

type StockPoolFilter = 'all' | 'subscribed' | MarketRegion;
type StockSearchMarket = 'all' | MarketRegion;

interface StockListProps {
  stocks: Stock[];
  onStockSelect: (stock: Stock) => void;
  onAddStock?: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock?: (symbol: string) => void;
  onToggleSubscription?: (symbol: string) => void;
  onRefreshMarketData?: () => void;
  isMarketDataRefreshing?: boolean;
  showHeader?: boolean;
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
  showHeader = true
}) => {
  const { message } = AntdApp.useApp();
  const [poolFilter, setPoolFilter] = useState<StockPoolFilter>('all');
  const [searchMarket, setSearchMarket] = useState<StockSearchMarket>('all');
  const [searchText, setSearchText] = useState('');
  const [searching, setSearching] = useState(false);
  const [candidates, setCandidates] = useState<MarketSymbolCandidate[]>([]);
  const [addingSymbol, setAddingSymbol] = useState<string | null>(null);

  const poolStats = useMemo(() => {
    const subscribed = stocks.filter(stock => stock.isSubscribed ?? true).length;
    const byMarket = stocks.reduce<Record<string, number>>((acc, stock) => {
      const market = marketOf(stock);
      acc[market] = (acc[market] || 0) + 1;
      return acc;
    }, {});
    return { subscribed, byMarket };
  }, [stocks]);

  const filteredStocks = useMemo(() => {
    return stocks.filter(stock => {
      if (poolFilter === 'all') return true;
      if (poolFilter === 'subscribed') return stock.isSubscribed ?? true;
      return marketOf(stock) === poolFilter;
    });
  }, [poolFilter, stocks]);

  const handleSearch = async (value = searchText) => {
    const keyword = value.trim();
    if (!keyword) {
      message.warning('请输入股票代码、名称或拼音');
      return;
    }
    setSearching(true);
    try {
      const result = await searchMarketSymbols(
        keyword,
        searchMarket
      );
      setCandidates(result.candidates);
      if (result.candidates.length === 0) {
        message.info('没有找到匹配标的，可以尝试完整代码，例如 AAPL、00700、600519');
      }
      if (result.warnings.length > 0) {
        console.warn('Market symbol search warnings:', result.warnings);
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
                <span>自选个股池</span>
              </Space>
            </h2>
            <div className="section-description">像自选股一样维护美股、港股、A股，并订阅价格、新闻、财报和研究提醒。</div>
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

      <div className="watchlist-toolbar">
        <Segmented
          value={poolFilter}
          onChange={value => setPoolFilter(value as StockPoolFilter)}
          options={[
            { label: `全部 ${stocks.length}`, value: 'all' },
            { label: `已订阅 ${poolStats.subscribed}`, value: 'subscribed' },
            { label: `美股 ${poolStats.byMarket.US || 0}`, value: 'US' },
            { label: `港股 ${poolStats.byMarket.HK || 0}`, value: 'HK' },
            { label: `A股 ${poolStats.byMarket.CN || 0}`, value: 'CN' }
          ]}
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
          options={[
            { label: '全市场', value: 'all' },
            { label: '美股', value: 'US' },
            { label: '港股', value: 'HK' },
            { label: 'A股', value: 'CN' }
          ]}
        />
        <Search
          allowClear
          value={searchText}
          onChange={event => setSearchText(event.target.value)}
          onSearch={handleSearch}
          enterButton={<SearchOutlined />}
          loading={searching}
          placeholder="添加自选：AAPL / 腾讯 / 00700 / 贵州茅台 / 600519"
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
                    {(stock.isSubscribed ?? true) && <Tag color="green">订阅中</Tag>}
                  </Space>
                  <Text type="secondary">{stock.exchange || stock.sector}</Text>
                </Space>
              )
            },
            {
              title: '行情',
              width: 170,
              render: (_, stock) => (
                <Space direction="vertical" size={0}>
                  <Text strong>{formatPrice(stock)}</Text>
                  <Text className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}>
                    {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
                    {typeof stock.priceChange === 'number' && ` / ${stock.priceChange >= 0 ? '+' : ''}${stock.priceChange.toFixed(2)}`}
                  </Text>
                </Space>
              )
            },
            {
              title: '订阅',
              width: 92,
              render: (_, stock) => (
                <Tooltip title={(stock.isSubscribed ?? true) ? '暂停订阅提醒' : '开启订阅提醒'}>
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
              title: '来源',
              width: 210,
              render: (_, stock) => (
                <Space direction="vertical" size={0}>
                  <Text><GlobalOutlined /> {formatQuoteSourceLine(stock)}</Text>
                  <Text type="secondary">{formatQuoteTimestamp(stock)}</Text>
                </Space>
              )
            },
            {
              title: '操作',
              width: 170,
              render: (_, stock) => (
                <Space size={4}>
                  <Button
                    size="small"
                    icon={<MessageOutlined />}
                    onClick={event => {
                      event.stopPropagation();
                      onStockSelect(stock);
                    }}
                  >
                    社区
                  </Button>
                  <Button
                    size="small"
                    icon={<DollarOutlined />}
                    onClick={event => {
                      event.stopPropagation();
                      onStockSelect(stock);
                    }}
                  >
                    {stock.totalPaidPosts}
                  </Button>
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

export default StockList;
