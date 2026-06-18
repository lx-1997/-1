import React, { useState } from 'react';
import { Card, Tabs, List, Tag, Typography, Space, Button, Avatar, Badge, Divider, Row, Col, Statistic, Empty, Spin } from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  EyeOutlined,
  DollarOutlined,
  TrophyOutlined,
  LikeOutlined,
  ShareAltOutlined,
  CrownOutlined
} from '@ant-design/icons';
import { Stock, Post, Comment } from '../types';

const { Title, Text, Paragraph } = Typography;
// const { TabPane } = Tabs; // 已弃用，使用items属性

interface StockDetailProps {
  stock: Stock;
  posts: Post[];
  comments: Comment[];
  onBack: () => void;
  onCreatePost: () => void;
  onPostClick: (post: Post) => void;
}

const StockDetail: React.FC<StockDetailProps> = ({ 
  stock, 
  posts, 
  comments, 
  onBack, 
  onCreatePost, 
  onPostClick 
}) => {
  const [activeTab, setActiveTab] = useState('posts');

  const getPostTypeIcon = (type: string) => {
    switch (type) {
      case 'news': return <FireOutlined style={{ color: 'var(--negative)' }} />;
      case 'analysis': return <StarOutlined style={{ color: 'var(--warning)' }} />;
      case 'discussion': return <MessageOutlined style={{ color: 'var(--info)' }} />;
      case 'qa': return <CrownOutlined style={{ color: '#a855f7' }} />;
      default: return <StarOutlined />;
    }
  };

  const getPostTypeText = (type: string) => {
    switch (type) {
      case 'news': return '资讯';
      case 'analysis': return '分析';
      case 'discussion': return '讨论';
      case 'qa': return '问答';
      default: return '其他';
    }
  };

  const getCategoryColor = (category: string) => {
    switch (category) {
      case 'technical': return 'blue';
      case 'fundamental': return 'green';
      case 'market': return 'orange';
      case 'earnings': return 'red';
      case 'product': return 'purple';
      case 'regulatory': return 'cyan';
      default: return 'default';
    }
  };

  const getCategoryText = (category: string) => {
    switch (category) {
      case 'technical': return '技术';
      case 'fundamental': return '基本面';
      case 'market': return '市场';
      case 'earnings': return '财报';
      case 'product': return '产品';
      case 'regulatory': return '监管';
      default: return '其他';
    }
  };

  if (!stock) {
    return <div style={{ padding: 40, textAlign: 'center' }}><Spin size="large" tip="加载中..." /></div>;
  }

  const filteredPosts = posts.filter(post => post.stockSymbol === stock.symbol);
  const filteredComments = comments.filter(comment => 
    filteredPosts.some(post => post.id === comment.postId)
  );

  return (
    <div>
      {/* 返回按钮和股票信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 16 }}>
          <Button onClick={onBack} icon={<StarOutlined />}>
            返回个股列表
          </Button>
          <Button type="primary" icon={<MessageOutlined />} onClick={onCreatePost}>
            记录研究
          </Button>
        </Space>

        <Row gutter={24}>
          <Col span={6}>
            <Avatar 
              size={80}
              style={{ 
                backgroundColor: stock.focusLevel === 'high' ? 'var(--negative)' : 
                               stock.focusLevel === 'medium' ? 'var(--warning)' : 'var(--positive)',
                fontSize: '24px',
                fontWeight: 'bold'
              }}
            >
              {stock.symbol}
            </Avatar>
          </Col>
          <Col span={18}>
            <Title level={2}>
              {stock.name} ({stock.symbol})
              <Tag color={stock.focusLevel === 'high' ? 'red' : stock.focusLevel === 'medium' ? 'orange' : 'green'} style={{ marginLeft: 8 }}>
                {stock.focusLevel === 'high' ? '高关注' : stock.focusLevel === 'medium' ? '中关注' : '低关注'}
              </Tag>
            </Title>
            <Paragraph>{stock.description}</Paragraph>
            
            <Row gutter={16}>
              <Col span={6}>
                <Statistic
                  title="当前价格"
                  value={stock.currentPrice}
                  prefix="$"
                  valueStyle={{ color: stock.changePercent >= 0 ? 'var(--positive)' : 'var(--negative)' }}
                  suffix={
                    <Text style={{ color: stock.changePercent >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                      {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent}%
                    </Text>
                  }
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="市值"
                  value={(stock.marketCap / 1000000000).toFixed(1)}
                  suffix="B"
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="研究资料"
                  value={stock.totalPosts}
                  suffix="篇"
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="深度报告"
                  value={stock.totalPaidPosts}
                  suffix="篇"
                />
              </Col>
            </Row>
          </Col>
        </Row>
      </Card>

      {/* 内容标签页 */}
      <Card>
        <Tabs 
          activeKey={activeTab} 
          onChange={setActiveTab}
          items={[
            {
              key: 'posts',
              label: (
                <span>
                  <MessageOutlined />
                  资讯证据 ({filteredPosts.length})
                </span>
              ),
              children: (

            <List
              dataSource={filteredPosts}
              locale={{ emptyText: <Empty description="暂无相关资讯" /> }}
              renderItem={(post) => (
                <List.Item
                  actions={[
                    <Button 
                      type="link" 
                      icon={<EyeOutlined />}
                      onClick={() => onPostClick(post)}
                    >
                      查看详情
                    </Button>
                  ]}
                >
                  <List.Item.Meta
                    avatar={
                      <Badge
                        count={post.isPaid ? <DollarOutlined style={{ color: 'var(--warning)' }} /> : 0}
                        offset={[-5, 5]}
                      >
                        <Avatar 
                          src={post.author.avatar}
                          size={48}
                        />
                      </Badge>
                    }
                    title={
                      <Space>
                        <Text strong>{post.title}</Text>
                        {post.isPaid && <Tag color="gold">深度报告</Tag>}
                        {post.isPinned && <Tag color="red">置顶</Tag>}
                        {post.isHighlighted && <Tag color="blue">精华</Tag>}
                        <Tag color={getCategoryColor(post.category)}>
                          {getCategoryText(post.category)}
                        </Tag>
                        <Tag color="blue">
                          {getPostTypeText(post.type)}
                        </Tag>
                      </Space>
                    }
                    description={
                      <div>
                        <Space>
                          <Text strong>{post.author.username}</Text>
                          <Tag color={post.author.reputation > 80 ? 'gold' : post.author.reputation > 60 ? 'blue' : 'default'}>
                            {post.author.reputation}分
                          </Tag>
                          <Text type="secondary">
                            {new Date(post.publishTime).toLocaleString()}
                          </Text>
                        </Space>
                        <Paragraph ellipsis={{ rows: 2 }} style={{ marginTop: 8 }}>
                          {post.summary}
                        </Paragraph>
                        
                        <Space size="large" style={{ marginTop: 8 }}>
                          <Text type="secondary">
                            <LikeOutlined /> {post.likes}
                          </Text>
                          <Text type="secondary">
                            <MessageOutlined /> {post.comments}
                          </Text>
                          <Text type="secondary">
                            <ShareAltOutlined /> {post.shares}
                          </Text>
                          <Text type="secondary">
                            <EyeOutlined /> {post.views}
                          </Text>
                          {post.isPaid && (
                            <Text type="secondary">
                              <DollarOutlined /> ${post.price} · {post.paidViewers}人开通
                            </Text>
                          )}
                        </Space>
                        
                        <div style={{ marginTop: 8 }}>
                          {post.tags.map(tag => (
                            <Tag key={tag}>{tag}</Tag>
                          ))}
                        </div>
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
              )
            },
            {
              key: 'comments',
              label: (
                <span>
                  <MessageOutlined />
                  讨论记录 ({filteredComments.length})
                </span>
              ),
              children: (
            <List
              dataSource={filteredComments}
              locale={{ emptyText: <Empty description="暂无讨论内容" /> }}
              renderItem={(comment) => (
                <List.Item>
                  <List.Item.Meta
                    avatar={
                      <Avatar 
                        src={comment.author.avatar}
                        size={40}
                      />
                    }
                    title={
                      <Space>
                        <Text strong>{comment.author.username}</Text>
                        <Tag color={comment.author.reputation > 80 ? 'gold' : comment.author.reputation > 60 ? 'blue' : 'default'}>
                          {comment.author.reputation}分
                        </Tag>
                        {comment.isPaid && <Tag color="gold">深度评论</Tag>}
                      </Space>
                    }
                    description={
                      <div>
                        <Paragraph>{comment.content}</Paragraph>
                        <Space>
                          <Text type="secondary">
                            {new Date(comment.publishTime).toLocaleString()}
                          </Text>
                          <Text type="secondary">
                            <LikeOutlined /> {comment.likes}
                          </Text>
                        </Space>
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
              )
            }
          ]}
        />
      </Card>
    </div>
  );
};

export default React.memo(StockDetail);
