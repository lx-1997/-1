import React from 'react';
import { Tag, Typography, Space, Button, Progress } from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  DollarOutlined,
  TrophyOutlined
} from '@ant-design/icons';
import { Stock } from '../types';
import { formatQuoteSourceLine, formatQuoteTimestamp } from '../utils/marketData';

const { Text } = Typography;

interface StockListProps {
  stocks: Stock[];
  onStockSelect: (stock: Stock) => void;
  showHeader?: boolean;
}

const StockList: React.FC<StockListProps> = ({ stocks, onStockSelect, showHeader = true }) => {
  const getFocusLevelColor = (level: string) => {
    switch (level) {
      case 'high': return 'red';
      case 'medium': return 'orange';
      case 'low': return 'green';
      default: return 'blue';
    }
  };

  const getFocusLevelIcon = (level: string) => {
    switch (level) {
      case 'high': return <FireOutlined />;
      case 'medium': return <StarOutlined />;
      case 'low': return <StarOutlined />;
      default: return <StarOutlined />;
    }
  };

  const getFocusLevelText = (level: string) => {
    switch (level) {
      case 'high': return '高关注';
      case 'medium': return '中关注';
      case 'low': return '低关注';
      default: return '未知';
    }
  };

  return (
    <div className="stock-directory">
      {showHeader && (
        <div className="section-heading">
          <div>
            <h2>
              <Space>
                <TrophyOutlined style={{ color: '#b7791f' }} />
                <span>个股专区</span>
              </Space>
            </h2>
            <div className="section-description">精选高关注标的，沉淀社区投研和付费观点。</div>
          </div>
          <Button type="primary" icon={<StarOutlined />}>
            申请添加个股
          </Button>
        </div>
      )}

      <div className="stock-grid">
        {stocks.map(stock => (
          <article
            className="stock-card-pro"
            key={stock.symbol}
            onClick={() => onStockSelect(stock)}
          >
            <div className="stock-card-top">
              <div>
                <div className="stock-avatar">{stock.symbol}</div>
                <div className="stock-card-name">{stock.name}</div>
                <Text type="secondary">{stock.sector}</Text>
              </div>
              <Tag color={getFocusLevelColor(stock.focusLevel)}>
                {getFocusLevelIcon(stock.focusLevel)}
                {getFocusLevelText(stock.focusLevel)}
              </Tag>
            </div>

            <div className="stock-card-desc">{stock.description}</div>

            <div style={{ marginTop: 10 }}>
              <Progress
                percent={stock.communityScore}
                size="small"
                showInfo={false}
                strokeColor={stock.communityScore > 80 ? '#12805c' : stock.communityScore > 60 ? '#b7791f' : '#c43e3e'}
              />
            </div>

            <div className="stock-card-metrics">
              <div>
                <span className="mini-metric-label">价格</span>
                <span className="mini-metric-value">${stock.currentPrice.toFixed(2)}</span>
              </div>
              <div>
                <span className="mini-metric-label">涨跌</span>
                <span className={`mini-metric-value ${stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
                  {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
                </span>
              </div>
              <div>
                <span className="mini-metric-label">内容</span>
                <span className="mini-metric-value">
                  <MessageOutlined /> {stock.totalPosts}
                </span>
              </div>
            </div>

            <div className="quote-source-line">
              <span>{formatQuoteSourceLine(stock)}</span>
              <span>{formatQuoteTimestamp(stock)}</span>
            </div>

            <div style={{ marginTop: 12 }}>
              <Space size={6}>
                <Button
                  size="small"
                  type="default"
                  icon={<MessageOutlined />}
                  onClick={(e) => {
                    e.stopPropagation();
                    onStockSelect(stock);
                  }}
                >
                  社区
                </Button>
                <Button
                  size="small"
                  type="default"
                  icon={<DollarOutlined />}
                  onClick={(e) => {
                    e.stopPropagation();
                    onStockSelect(stock);
                  }}
                >
                  {stock.totalPaidPosts} 付费
                </Button>
              </Space>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
};

export default StockList;
