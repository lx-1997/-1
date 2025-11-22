import React, { useState, useEffect } from 'react';
import { Card, Row, Col, Statistic, Typography, Space, Avatar, Tag, Progress } from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  DollarOutlined,
  RiseOutlined,
  UserOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { mockStocks } from '../data/mockData';

const { Title, Text } = Typography;

interface DashboardProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

const Dashboard: React.FC<DashboardProps> = ({ appState, onStockSelect }) => {
  const [selectedStock, setSelectedStock] = useState<Stock | null>(null);

  useEffect(() => {
    if (appState.selectedStock) {
      setSelectedStock(appState.selectedStock);
    }
  }, [appState.selectedStock]);

  const user = appState.user;
  const stocks = appState.stocks;
  const posts = appState.posts;

  // 计算统计数据
  const totalPosts = posts.length;
  const paidPosts = posts.filter(p => p.isPaid).length;
  const totalViews = posts.reduce((sum, p) => sum + p.views, 0);
  const totalLikes = posts.reduce((sum, p) => sum + p.likes, 0);

  return (
    <div style={{ padding: '16px' }}>
      <Title level={2} style={{ marginBottom: '16px', fontSize: '20px' }}>
        欢迎回来，{user?.username}！
      </Title>

      {/* 用户统计 */}
      <Row gutter={[12, 12]} style={{ marginBottom: '16px' }}>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="声誉评分"
              value={user?.reputation || 0}
              suffix="分"
              valueStyle={{ color: (user?.reputation || 0) > 80 ? '#52c41a' : (user?.reputation || 0) > 60 ? '#faad14' : '#ff4d4f' }}
              prefix={<StarOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="发布内容"
              value={totalPosts}
              suffix="篇"
              prefix={<MessageOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="付费内容"
              value={paidPosts}
              suffix="篇"
              prefix={<DollarOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总收益"
              value={user?.totalEarnings || 0}
              prefix="$"
              precision={2}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 内容统计 */}
      <Row gutter={[12, 12]} style={{ marginBottom: '16px' }}>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总浏览量"
              value={totalViews}
              prefix={<RiseOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总点赞"
              value={totalLikes}
              prefix={<StarOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="关注者"
              value={user?.followers || 0}
              prefix={<UserOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} lg={6}>
          <Card>
            <Statistic
              title="关注中"
              value={user?.following || 0}
              prefix={<UserOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* 热门个股 */}
      <Card 
        title={
          <Space>
            <FireOutlined style={{ color: '#ff4d4f' }} />
            <span>热门个股</span>
          </Space>
        }
        style={{ marginBottom: '16px' }}
      >
        <Row gutter={[12, 12]}>
          {stocks.slice(0, 4).map(stock => (
            <Col xs={12} sm={12} lg={6} key={stock.symbol}>
              <Card
                hoverable
                onClick={() => onStockSelect(stock)}
                style={{ cursor: 'pointer' }}
              >
                <div style={{ textAlign: 'center' }}>
                  <Avatar 
                    size={48}
                    style={{ 
                      backgroundColor: stock.focusLevel === 'high' ? '#ff4d4f' : 
                                     stock.focusLevel === 'medium' ? '#faad14' : '#52c41a',
                      fontSize: '18px',
                      fontWeight: 'bold',
                      marginBottom: '8px'
                    }}
                  >
                    {stock.symbol}
                  </Avatar>
                  <div>
                    <Text strong>{stock.name}</Text>
                  </div>
                  <div>
                    <Text style={{ color: stock.changePercent >= 0 ? '#52c41a' : '#ff4d4f' }}>
                      ${stock.currentPrice} ({stock.changePercent >= 0 ? '+' : ''}{stock.changePercent}%)
                    </Text>
                  </div>
                  <div style={{ marginTop: '8px' }}>
                    <Text type="secondary">社区活跃度: {stock.communityScore}分</Text>
                    <Progress 
                      percent={stock.communityScore} 
                      size="small" 
                      showInfo={false}
                      strokeColor={stock.communityScore > 80 ? '#52c41a' : stock.communityScore > 60 ? '#faad14' : '#ff4d4f'}
                    />
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      {/* 最新内容 */}
      <Card 
        title={
          <Space>
            <MessageOutlined style={{ color: '#1890ff' }} />
            <span>最新内容</span>
          </Space>
        }
      >
        <Row gutter={[12, 12]}>
          {posts.slice(0, 3).map(post => (
            <Col xs={24} sm={12} lg={8} key={post.id}>
              <Card size="small">
                <div>
                  <Text strong>{post.title}</Text>
                  <div style={{ marginTop: '8px' }}>
                    <Text type="secondary" ellipsis={{ tooltip: post.summary }}>
                      {post.summary}
                    </Text>
                  </div>
                  <div style={{ marginTop: '8px' }}>
                    <Space>
                      <Text type="secondary">
                        <MessageOutlined /> {post.comments}
                      </Text>
                      <Text type="secondary">
                        <StarOutlined /> {post.likes}
                      </Text>
                      {post.isPaid && (
                        <Tag color="gold">
                          <DollarOutlined /> ${post.price}
                        </Tag>
                      )}
                    </Space>
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
};

export default Dashboard;
