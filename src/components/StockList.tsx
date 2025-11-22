import React from 'react';
import { Card, List, Tag, Typography, Space, Button, Avatar, Progress, Row, Col, Statistic } from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  EyeOutlined,
  DollarOutlined,
  TrophyOutlined
} from '@ant-design/icons';
import { Stock } from '../types';

const { Title, Text, Paragraph } = Typography;

interface StockListProps {
  stocks: Stock[];
  onStockSelect: (stock: Stock) => void;
}

const StockList: React.FC<StockListProps> = ({ stocks, onStockSelect }) => {
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
    <div style={{ padding: '16px', width: '100%', height: '100%' }}>
      <Card 
        title={
          <Space>
            <TrophyOutlined style={{ color: '#faad14' }} />
            <span>个股专区</span>
            <Text type="secondary">精选个股，深度投研</Text>
          </Space>
        }
        extra={
          <Button type="primary" icon={<StarOutlined />}>
            申请添加个股
          </Button>
        }
      >
        <Row gutter={[16, 16]}>
          {stocks.map(stock => (
            <Col xs={24} sm={12} lg={8} xl={6} key={stock.symbol}>
              <Card
                hoverable
                onClick={() => onStockSelect(stock)}
                style={{ cursor: 'pointer' }}
                actions={[
                  <Button 
                    type="link" 
                    icon={<MessageOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      onStockSelect(stock);
                    }}
                  >
                    {stock.totalPosts} 篇
                  </Button>,
                  <Button 
                    type="link" 
                    icon={<DollarOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      onStockSelect(stock);
                    }}
                  >
                    {stock.totalPaidPosts} 付费
                  </Button>
                ]}
              >
                <Card.Meta
                  avatar={
                    <Avatar 
                      size={48}
                      style={{ 
                        backgroundColor: getFocusLevelColor(stock.focusLevel),
                        fontSize: '18px',
                        fontWeight: 'bold'
                      }}
                    >
                      {stock.symbol}
                    </Avatar>
                  }
                  title={
                    <Space>
                      <Text strong>{stock.name}</Text>
                      <Tag color={getFocusLevelColor(stock.focusLevel)}>
                        {getFocusLevelIcon(stock.focusLevel)}
                        {getFocusLevelText(stock.focusLevel)}
                      </Tag>
                    </Space>
                  }
                  description={
                    <div>
                      <Paragraph ellipsis={{ rows: 2 }}>
                        {stock.description}
                      </Paragraph>
                      
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <div>
                          <Text type="secondary">当前价格: </Text>
                          <Text strong style={{ color: stock.changePercent >= 0 ? '#52c41a' : '#ff4d4f' }}>
                            ${stock.currentPrice}
                          </Text>
                          <Text style={{ color: stock.changePercent >= 0 ? '#52c41a' : '#ff4d4f' }}>
                            {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent}%
                          </Text>
                        </div>
                        
                        <div>
                          <Text type="secondary">市值: </Text>
                          <Text>${(stock.marketCap / 1000000000).toFixed(1)}B</Text>
                        </div>
                        
                        <div>
                          <Text type="secondary">行业: </Text>
                          <Text>{stock.sector}</Text>
                        </div>
                        
                        <div style={{ marginTop: 8 }}>
                          <Text type="secondary">社区活跃度: </Text>
                          <Progress 
                            percent={stock.communityScore} 
                            size="small" 
                            showInfo={false}
                            strokeColor={stock.communityScore > 80 ? '#52c41a' : stock.communityScore > 60 ? '#faad14' : '#ff4d4f'}
                          />
                          <Text type="secondary" style={{ fontSize: '12px' }}>
                            {stock.communityScore}分
                          </Text>
                        </div>
                      </Space>
                    </div>
                  }
                />
              </Card>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
};

export default StockList;
